"""Background work: crawls, auto-drafting, publishing, the scheduler and the autopilot.

Each long task is a Job whose state the UI polls via /api/status. Nothing here starts a thread on import;
server.py calls start_background() when the app starts.
"""

import os
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from config import POSTS_CSV, ROOT, max_chars

from . import publisher, store
from .autopilot import Autopilot
from .drafter import Drafter, x_length
from .posts import load_posts


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def drafter() -> Drafter:
    return Drafter()  # cheap; re-reads the key so a .env change applies without a restart


class Job:
    """State of one background job (crawl, auto-draft or autopilot), polled by the UI via /api/status."""

    def __init__(self, name: str):
        self.name = name
        self.lock = threading.Lock()
        self.running = False
        self.started_at = self.finished_at = None
        self.ok: bool | None = None
        self.message = ""
        self.log: deque[str] = deque(maxlen=400)
        self.done = self.total = 0

    def start(self, total: int = 0) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running, self.ok, self.message = True, None, ""
            self.started_at, self.finished_at = now_iso(), None
            self.log.clear()
            self.done, self.total = 0, total
            return True

    def finish(self, ok: bool, message: str) -> None:
        with self.lock:
            self.running, self.ok, self.message, self.finished_at = False, ok, message, now_iso()

    def state(self) -> dict:
        return {
            "running": self.running, "ok": self.ok, "message": self.message, "started_at": self.started_at,
            "finished_at": self.finished_at, "done": self.done, "total": self.total, "log": list(self.log),
        }


CRAWL, AUTODRAFT, AUTOPILOT = Job("crawl"), Job("autodraft"), Job("autopilot")


# --------------------------------------------------------------------------- publishing

def publish_claimed(d: dict) -> dict:
    """Publish a draft already moved to 'publishing'; record the result either way."""
    if d["kind"] != "repost" and x_length(d["text"]) > max_chars():
        return store.update(d["id"], status="failed",
                            error=f"Too long: {x_length(d['text'])} of {max_chars()} characters")
    src = d["source"]
    try:
        res = publisher.publish(d["kind"], d["text"], src["id"], src["url"])
    except publisher.PublishError as e:
        return store.update(d["id"], status="failed", error=str(e))
    return store.update(d["id"], status="posted", published_id=res["id"], published_url=res["url"])


# --------------------------------------------------------------------------- auto-draft

class AutoDraftOpts(BaseModel):
    count: int = 5
    kind: str = "quote"
    min_score: int = 50
    topics: list[str] = []
    only_ids: list[str] | None = None  # restrict to these posts (e.g. the ones a crawl just found)


def _autodraft(opts: AutoDraftOpts) -> None:
    """Draft for the best-scoring posts that don't have a draft yet."""
    has_drafts = store.summary_by_post()
    pool = [
        p for p in load_posts()
        if p["id"] not in has_drafts and p["score"] >= opts.min_score
        and (not opts.topics or p["topic"] in opts.topics)
        and (opts.only_ids is None or p["id"] in opts.only_ids)
    ]
    picks = sorted(pool, key=lambda p: -p["score"])[: max(0, opts.count)]
    AUTODRAFT.total = len(picks)
    AUTODRAFT.log.append(f"Writing {opts.kind} drafts for {len(picks)} posts with a score of {opts.min_score} or more")
    writer, errors = drafter(), 0

    def one(p):
        nonlocal errors
        try:
            out = writer.draft(p, opts.kind)
            store.create(p, opts.kind, out.text, out.angle, out.fit, model=writer.model)
            AUTODRAFT.log.append(f"Drafted for @{p['username']} (fit {out.fit}/10)")
        except Exception as e:
            errors += 1
            AUTODRAFT.log.append(f"Couldn't draft for @{p['username']}: {e}")
        AUTODRAFT.done += 1

    with ThreadPoolExecutor(3) as ex:
        list(ex.map(one, picks))
    made = len(picks) - errors
    AUTODRAFT.finish(errors == 0, f"{made} new drafts to review" + (f", {errors} couldn't be written" if errors else ""))


def start_autodraft(opts: AutoDraftOpts) -> bool:
    if not AUTODRAFT.start():
        return False
    threading.Thread(target=_autodraft, args=(opts,), daemon=True).start()
    return True


# --------------------------------------------------------------------------- crawl

class CrawlOpts(BaseModel):
    limit: int = 50
    since: float = 24
    topics: list[str] = []
    engines: str = ""
    summary: str = "auto"
    autodraft: AutoDraftOpts | None = None


def run_crawler(limit: int, since: float, topics: list[str], log, engines: str = "", summary: str = "auto") -> list[str]:
    """Run search_crawler.py, streaming its output to log(line). Returns the ids of new posts; raises on failure."""
    before = {p["id"] for p in load_posts()}
    cmd = [sys.executable, "-u", "search_crawler.py", "--limit", str(limit), "--since", str(since),
           "--summary", summary, "--out", POSTS_CSV]
    if topics:
        cmd += ["--topics", ",".join(topics)]
    if engines.strip():
        cmd += ["--engines", engines.strip()]
    log("$ " + " ".join(cmd[1:]))
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            bufsize=1, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    for line in proc.stdout:
        log(line.rstrip())
    if code := proc.wait():
        raise RuntimeError(f"The crawler stopped with an error (exit code {code})")
    return [p["id"] for p in load_posts() if p["id"] not in before]


def _crawl(opts: CrawlOpts) -> None:
    try:
        new_ids = run_crawler(opts.limit, opts.since, opts.topics, CRAWL.log.append, opts.engines, opts.summary)
    except Exception as e:
        CRAWL.finish(False, str(e))
        return
    CRAWL.done = len(new_ids)
    CRAWL.finish(True, f"Found {len(new_ids)} new posts")
    if opts.autodraft and new_ids:
        opts.autodraft.only_ids = new_ids
        start_autodraft(opts.autodraft)


def start_crawl(opts: CrawlOpts) -> bool:
    if not CRAWL.start():
        return False
    threading.Thread(target=_crawl, args=(opts,), daemon=True).start()
    return True


# --------------------------------------------------------------------------- scheduler

# Scheduled drafts publish only while the Studio runs. A draft that comes due while it's closed is still
# published on the next start if it's under MISSED_GRACE late; older ones are marked failed so you decide.
MISSED_GRACE = timedelta(hours=6)
SCHEDULER_TICK = 20  # seconds


def _scheduler() -> None:
    while True:
        try:
            now = datetime.now(timezone.utc)
            for d in store.due(now.isoformat(timespec="seconds")):
                late = now - datetime.fromisoformat(d["scheduled_at"])
                if late > MISSED_GRACE:
                    store.update(d["id"], status="failed", error=(
                        f"Missed its scheduled time by {int(late.total_seconds() // 3600)} hours because the Studio "
                        "wasn't running. Publish it now or pick a new time."))
                    continue
                claimed = store.claim_for_publishing(d["id"], from_statuses=("scheduled",))
                if claimed:
                    publish_claimed(claimed)
        except Exception as e:  # never let one bad row stop the scheduler
            print(f"scheduler: {type(e).__name__}: {e}", file=sys.stderr)
        time.sleep(SCHEDULER_TICK)


# --------------------------------------------------------------------------- autopilot

def _autopilot_crawl(limit: int, since: float, topics: list[str], log) -> list[str]:
    """The autopilot's crawl also counts as the crawler running, so a manual crawl can't overlap it."""
    if not CRAWL.start():
        raise RuntimeError("A crawl is already running; the autopilot will try again next time")
    try:
        new_ids = run_crawler(limit, since, topics, lambda line: (CRAWL.log.append(line), log(line)))
    except Exception as e:
        CRAWL.finish(False, str(e))
        raise
    CRAWL.finish(True, f"Found {len(new_ids)} new posts")
    return new_ids


autopilot = Autopilot(AUTOPILOT, crawl=_autopilot_crawl, publish=publish_claimed)


def start_background() -> None:
    """Start the scheduler and autopilot loops. Called once when the app starts."""
    threading.Thread(target=_scheduler, daemon=True, name="scheduler").start()
    autopilot.start()
