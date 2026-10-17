"""Autopilot: find posts, pick the best one, write about it and publish, on a timer, with no clicks.

Each run:
  1. crawl     search_crawler.py for up to `crawl_limit` new posts
  2. choose    Gemini picks the post (and format) that best fits Deviantly, from the new posts plus
               recent ones that don't have a draft yet
  3. write     Gemini writes the post / quote / reply
  4. publish   posts it right away, unless a guard says otherwise; then it waits in To review instead:
               publish mode is "review", the fit is below `min_fit`, or today's `daily_cap` is reached

Runs happen every `every_hours` while the Studio is running, only between `active_from` and `active_to`
(local time). Settings live in workflow/autopilot.json; run history in studio.db.
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from pydantic import BaseModel, Field

from config import AUTOPILOT_PATH as SETTINGS_PATH

from . import publisher, store
from .drafter import Drafter
from .posts import load_posts

TICK = 30  # seconds between schedule checks


class Settings(BaseModel):
    enabled: bool = False
    every_hours: float = Field(3, ge=0.5, le=48)
    crawl_limit: int = Field(10, ge=1, le=100)
    since_hours: float = Field(24, ge=1, le=168)
    topics: list[str] = []
    kinds: list[str] = ["post"]          # formats Gemini may choose from: post, quote, reply
    publish: str = "auto"                # auto = post without asking; review = leave it in To review
    min_fit: int = Field(6, ge=0, le=10)  # below this, the draft waits for you instead of posting
    daily_cap: int = Field(4, ge=1, le=50)
    active_from: int = Field(8, ge=0, le=23)   # local hour
    active_to: int = Field(22, ge=1, le=24)    # local hour (exclusive)
    next_run_at: str | None = None       # UTC ISO; set when enabled and after every run


def load_settings() -> Settings:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return Settings.model_validate(json.load(f))
    except (OSError, ValueError):
        return Settings()


def save_settings(s: Settings) -> Settings:
    s.kinds = [k for k in s.kinds if k in ("post", "quote", "reply")] or ["post"]
    if s.publish not in ("auto", "review"):
        s.publish = "auto"
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s.model_dump(), f, indent=2)
    os.replace(tmp, SETTINGS_PATH)
    return s


def _now() -> datetime:
    return datetime.now(timezone.utc)


def in_active_hours(s: Settings, at: datetime | None = None) -> bool:
    hour = (at or datetime.now()).astimezone().hour
    if s.active_from < s.active_to:
        return s.active_from <= hour < s.active_to
    return hour >= s.active_from or hour < s.active_to  # window across midnight, e.g. 20 -> 2


def posted_today() -> int:
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return store.autopilot_posts_since(start.astimezone(timezone.utc).isoformat(timespec="seconds"))


class Autopilot:
    """Runs on its own thread. `crawl(limit, since, topics, log)` returns the new post ids or raises;
    `publish(draft)` publishes a draft already claimed for publishing and returns the updated draft."""

    def __init__(self, job, crawl: Callable, publish: Callable):
        self.job = job
        self._crawl = crawl
        self._publish = publish

    def start(self) -> None:
        store.init_runs()
        threading.Thread(target=self._loop, daemon=True, name="autopilot").start()

    def run_now(self, trigger: str = "manual") -> bool:
        if not self.job.start():
            return False
        threading.Thread(target=self._run, args=(trigger,), daemon=True).start()
        return True

    def _loop(self) -> None:
        while True:
            try:
                s = load_settings()
                due = s.next_run_at is None or _now() >= datetime.fromisoformat(s.next_run_at)
                if s.enabled and due and in_active_hours(s) and not self.job.running:
                    self.run_now("schedule")
            except Exception as e:  # keep the loop alive whatever happens
                self.job.log.append(f"Autopilot check failed: {e}")
            time.sleep(TICK)

    def _log(self, msg: str) -> None:
        self.job.log.append(msg)

    def _run(self, trigger: str) -> None:
        s = load_settings()
        run_id = store.start_run(trigger)
        result = {"outcome": "failed", "message": "", "fields": {}}
        try:
            result = self._steps(s)
        except Exception as e:
            result["message"] = f"{type(e).__name__}: {e}"
            self._log(f"Stopped: {result['message']}")
        finally:
            store.finish_run(run_id, result["outcome"], result["message"], **result["fields"])
            s = load_settings()  # re-read: you may have changed settings during the run
            s.next_run_at = (_now() + timedelta(hours=s.every_hours)).isoformat(timespec="seconds")
            save_settings(s)
            self.job.finish(result["outcome"] != "failed", result["message"])

    def _steps(self, s: Settings) -> dict:
        fields: dict = {}

        # 1. crawl
        self._log(f"Looking for up to {s.crawl_limit} new posts from the last {s.since_hours:g} hours")
        new_ids = self._crawl(s.crawl_limit, s.since_hours, s.topics, self._log)
        fields["found"] = len(new_ids)
        self._log(f"Found {len(new_ids)} new posts")

        # 2. choose: new posts first, topped up with recent ones that have no draft yet
        drafted = store.summary_by_post()
        cutoff = (_now() - timedelta(hours=s.since_hours)).isoformat()
        posts = [p for p in load_posts() if p["id"] not in drafted and p["score"] >= 40
                 and (not s.topics or p["topic"] in s.topics)]
        fresh = [p for p in posts if p["id"] in new_ids]
        recent = [p for p in posts if p["id"] not in new_ids and p["created_at"] >= cutoff]
        candidates = (sorted(fresh, key=lambda p: -p["score"]) + sorted(recent, key=lambda p: -p["score"]))[:15]
        fields["considered"] = len(candidates)
        if not candidates:
            return {"outcome": "skipped", "message": "No new posts worth writing about this time", "fields": fields}

        drafter = Drafter()
        self._log(f"Asking Gemini to choose the best of {len(candidates)} posts")
        pick = drafter.pick(candidates, s.kinds)
        post = next(p for p in candidates if p["id"] == pick.post_id)
        fields.update(post_id=post["id"], reason=pick.reason)
        self._log(f"Chose @{post['username']} as a {pick.kind} (fit {pick.fit}/10): {pick.reason}")

        # 3. write
        out = drafter.draft(post, pick.kind)
        fit = min(pick.fit, out.fit)
        draft = store.create(post, pick.kind, out.text, out.angle, fit, instruction="Written by autopilot",
                             model=drafter.model)
        fields["draft_id"] = draft["id"]
        self._log(f"Wrote it with {drafter.model}: {out.text}")

        # 4. publish, unless a guard says to wait for you
        hold = None
        if s.publish == "review":
            hold = "Autopilot is set to leave drafts for review"
        elif fit < s.min_fit:
            hold = f"Fit was {fit}/10, below your minimum of {s.min_fit}, so it's waiting for you in To review"
        elif posted_today() >= s.daily_cap:
            hold = f"Already posted {s.daily_cap} times today (your daily limit), so it's waiting in To review"
        elif not publisher.current_mode():
            hold = "No X account is connected, so it's waiting in To review"
        if hold:
            self._log(hold)
            return {"outcome": "queued", "message": hold, "fields": fields}

        claimed = store.claim_for_publishing(draft["id"])
        result = self._publish(claimed)
        if result["status"] == "posted":
            self._log(f"Posted: {result['published_url']}")
            return {"outcome": "posted", "message": f"Posted a {pick.kind} about @{post['username']}'s post",
                    "fields": fields}
        self._log(f"X didn't accept it: {result['error']}")
        return {"outcome": "failed", "message": f"X didn't accept it: {result['error']}", "fields": fields}
