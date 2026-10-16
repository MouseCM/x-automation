"""Deviantly X Studio backend (FastAPI). Start it with `python3 studio.py`.

Serves the review UI from workflow/static and a JSON API under /api:
  crawl     run search_crawler.py in the background and stream its log
  posts     crawled posts from post.csv, with draft badges
  drafts    Gemini drafts: create, regenerate, edit, approve (publishes to X), reject
  account   X sign-in settings (written to .env) and a connection test
  brand     the brand brief Gemini writes from
"""

import csv
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from env import load_env_file
from topics import load_topics

from . import autopilot as ap
from . import publisher, store
from .autopilot import Autopilot
from .drafter import Drafter, load_brand, load_persona, max_chars, save_brand, save_persona, x_length
from .posts import POSTS_CSV, ROOT, load_posts, posts_by_id

load_env_file()
store.init()

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
ENV_PATH = os.path.join(ROOT, ".env")
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}

app = FastAPI(title="Deviantly X Studio", docs_url=None, redoc_url=None)


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Block DNS-rebinding (Host check) and cross-site form posts (custom header forces a CORS preflight)."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0]
    if host not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "forbidden host"}, status_code=403)
    if request.method not in ("GET", "HEAD") and request.headers.get("x-studio") != "1":
        return JSONResponse({"detail": "missing X-Studio header"}, status_code=403)
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"  # UI edits show up on a normal reload
    return response


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _drafter() -> Drafter:
    return Drafter()  # cheap; re-reads the key so a .env change applies without a restart


def _with_meta(d: dict) -> dict:
    d["length"] = x_length(d["text"])
    d["limit"] = max_chars()
    return d


# --------------------------------------------------------------------------- background jobs

class Job:
    """State of one background job (crawl or auto-draft), polled by the UI via /api/status."""

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
            self.started_at, self.finished_at = _now(), None
            self.log.clear()
            self.done, self.total = 0, total
            return True

    def finish(self, ok: bool, message: str) -> None:
        with self.lock:
            self.running, self.ok, self.message, self.finished_at = False, ok, message, _now()

    def state(self) -> dict:
        return {
            "running": self.running, "ok": self.ok, "message": self.message, "started_at": self.started_at,
            "finished_at": self.finished_at, "done": self.done, "total": self.total, "log": list(self.log),
        }


CRAWL, AUTODRAFT = Job("crawl"), Job("autodraft")


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
    drafter, errors = _drafter(), 0

    def one(p):
        nonlocal errors
        try:
            out = drafter.draft(p, opts.kind)
            store.create(p, opts.kind, out.text, out.angle, out.fit, model=drafter.model)
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
                    _publish_claimed(claimed)
        except Exception as e:  # never let one bad row stop the scheduler
            print(f"scheduler: {type(e).__name__}: {e}", file=sys.stderr)
        time.sleep(SCHEDULER_TICK)


threading.Thread(target=_scheduler, daemon=True, name="scheduler").start()


AUTOPILOT = Job("autopilot")


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


autopilot = Autopilot(AUTOPILOT, crawl=_autopilot_crawl, publish=lambda d: _publish_claimed(d))  # defined below
autopilot.start()


# --------------------------------------------------------------------------- routes: status, crawl, posts

@app.get("/api/status")
def status():
    return {
        "crawl": CRAWL.state(), "autodraft": AUTODRAFT.state(), "autopilot": _autopilot_state(), "counts": store.counts(),
        "gemini": _drafter().ready, "x_mode": publisher.current_mode(), "max_chars": max_chars(),
        "next_scheduled": next((d["scheduled_at"] for d in sorted(store.list_drafts("scheduled"),
                                                                    key=lambda d: d["scheduled_at"])), None),
    }


def _autopilot_state() -> dict:
    s = ap.load_settings()
    return {**AUTOPILOT.state(), "enabled": s.enabled, "next_run_at": s.next_run_at if s.enabled else None,
            "in_active_hours": ap.in_active_hours(s), "posted_today": ap.posted_today(), "daily_cap": s.daily_cap}


@app.get("/api/autopilot")
def get_autopilot():
    return {"settings": ap.load_settings().model_dump(), "state": _autopilot_state(), "runs": _runs()}


def _runs() -> list[dict]:
    runs = store.list_runs()
    for r in runs:
        d = store.get(r["draft_id"]) if r["draft_id"] else None
        r["draft"] = _with_meta(d) if d else None
    return runs


@app.put("/api/autopilot")
def put_autopilot(body: ap.Settings):
    old = ap.load_settings()
    if body.enabled and not old.enabled:
        body.next_run_at = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(timespec="seconds")
    elif body.enabled:
        body.next_run_at = old.next_run_at
    else:
        body.next_run_at = None
    return {"settings": ap.save_settings(body).model_dump(), "state": _autopilot_state()}


@app.post("/api/autopilot/run")
def run_autopilot():
    if not _drafter().ready:
        raise HTTPException(400, "Add GEMINI_API_KEY to X-Crawler/.env first")
    if CRAWL.running:
        raise HTTPException(409, "A crawl is already running. Try again when it finishes")
    if not autopilot.run_now("manual"):
        raise HTTPException(409, "The autopilot is already running")
    return _autopilot_state()


TOPICS_CSV = os.path.join(ROOT, "topics.csv")
_topics_lock = threading.Lock()


@app.get("/api/topics")
def topics():
    try:
        return [t.name for t in load_topics(TOPICS_CSV)]
    except Exception:
        return []


@app.get("/api/topics/full")
def topics_full():
    try:
        return [{"name": t.name, "keywords": t.keywords, "exclude": t.exclude, "require": t.require}
                for t in load_topics(TOPICS_CSV)]
    except Exception:
        return []


def _write_topics(rows: list[dict]) -> None:
    """Rewrite topics.csv, keeping any # comment lines at the top."""
    comments = []
    if os.path.exists(TOPICS_CSV):
        with open(TOPICS_CSV, encoding="utf-8") as f:
            comments = [line for line in f if line.lstrip().startswith("#")]
    tmp = TOPICS_CSV + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        f.writelines(comments)
        w = csv.writer(f)
        w.writerow(["topic", "keywords", "exclude", "require"])
        for r in rows:
            w.writerow([r["name"], "|".join(r["keywords"]), "|".join(r["exclude"]), "|".join(r["require"])])
    os.replace(tmp, TOPICS_CSV)


def _terms(items: list[str]) -> list[str]:
    out = []
    for item in items:
        for part in re.split(r"[|,\n]", item):  # accept commas or | between terms
            part = " ".join(part.split()).strip("\"'")
            if part and part.lower() not in (o.lower() for o in out):
                out.append(part)
    return out


class TopicIn(BaseModel):
    name: str
    keywords: list[str]
    exclude: list[str] = []
    require: list[str] = []


@app.post("/api/topics")
def add_topic(body: TopicIn):
    name = re.sub(r"[^a-z0-9]+", "-", body.name.strip().lower()).strip("-")
    if not name:
        raise HTTPException(400, "Give the topic a name")
    keywords = _terms(body.keywords)
    if not keywords:
        raise HTTPException(400, "Add at least one keyword to search for")
    if len(keywords) > 25:
        raise HTTPException(400, "Keep it to 25 keywords or fewer; each one is a separate search")
    with _topics_lock:
        rows = topics_full()
        if any(r["name"] == name for r in rows):
            raise HTTPException(409, f"There's already a topic called {name}")
        rows.append({"name": name, "keywords": keywords, "exclude": _terms(body.exclude), "require": _terms(body.require)})
        _write_topics(rows)
    return topics_full()


@app.delete("/api/topics/{name}")
def delete_topic(name: str):
    with _topics_lock:
        rows = topics_full()
        if not any(r["name"] == name for r in rows):
            raise HTTPException(404, "That topic doesn't exist")
        if len(rows) == 1:
            raise HTTPException(400, "Keep at least one topic so the crawler has something to search for")
        _write_topics([r for r in rows if r["name"] != name])
    return topics_full()


@app.post("/api/crawl")
def crawl(opts: CrawlOpts):
    if not CRAWL.start():
        raise HTTPException(409, "The crawler is already running")
    threading.Thread(target=_crawl, args=(opts,), daemon=True).start()
    return CRAWL.state()


@app.post("/api/autodraft")
def autodraft(opts: AutoDraftOpts):
    if opts.kind not in ("post", "quote", "reply"):
        raise HTTPException(400, "Auto-draft can write posts, quote posts or replies")
    if not _drafter().ready:
        raise HTTPException(400, "Add GEMINI_API_KEY to X-Crawler/.env to write drafts")
    if not start_autodraft(opts):
        raise HTTPException(409, "Gemini is already writing drafts")
    return AUTODRAFT.state()


@app.get("/api/posts")
def posts():
    badges = store.summary_by_post()
    out = load_posts()
    for p in out:
        p["drafts"] = badges.get(p["id"], {"count": 0, "statuses": {}, "posted_kinds": []})
    return out


# --------------------------------------------------------------------------- routes: drafts

class NewDraft(BaseModel):
    post_id: str
    kind: str
    instruction: str = ""


class Regenerate(BaseModel):
    instruction: str = ""


class EditDraft(BaseModel):
    text: str


def _get_draft(draft_id: int) -> dict:
    d = store.get(draft_id)
    if not d:
        raise HTTPException(404, "That draft no longer exists")
    return d


@app.get("/api/drafts")
def drafts(status: str | None = None, post_id: str | None = None):
    return [_with_meta(d) for d in store.list_drafts(status, post_id)]


@app.post("/api/drafts")
def new_draft(body: NewDraft):
    if body.kind not in store.KINDS:
        raise HTTPException(400, "Pick post, quote, reply or repost")
    post = posts_by_id().get(body.post_id)
    if not post:
        raise HTTPException(404, "That post is no longer in post.csv")
    drafter = _drafter()
    try:
        out = drafter.draft(post, body.kind, body.instruction)
    except Exception as e:
        raise HTTPException(502, str(e))
    return _with_meta(store.create(post, body.kind, out.text, out.angle, out.fit, body.instruction,
                                   drafter.model if body.kind != "repost" else ""))


@app.post("/api/drafts/{draft_id}/regenerate")
def regenerate(draft_id: int, body: Regenerate):
    d = _get_draft(draft_id)
    if d["status"] not in ("draft", "failed"):
        raise HTTPException(409, f"A {d['status']} draft can't be rewritten")
    drafter = _drafter()
    try:
        out = drafter.draft(d["source"], d["kind"], body.instruction, previous=d["text"])
    except Exception as e:
        raise HTTPException(502, str(e))
    return _with_meta(store.update(draft_id, text=out.text, angle=out.angle, fit=out.fit,
                                   instruction=body.instruction, model=drafter.model, status="draft", error=None))


@app.patch("/api/drafts/{draft_id}")
def edit(draft_id: int, body: EditDraft):
    d = _get_draft(draft_id)
    if d["status"] not in ("draft", "failed", "scheduled"):
        raise HTTPException(409, f"A {d['status']} draft can't be edited")
    if d["status"] == "scheduled" and d["kind"] != "repost" and x_length(body.text) > max_chars():
        raise HTTPException(400, f"The draft is {x_length(body.text)} characters; the limit is {max_chars()}")
    return _with_meta(store.update(draft_id, text=body.text))


def _publish_claimed(d: dict) -> dict:
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


@app.post("/api/drafts/{draft_id}/approve")
def approve(draft_id: int, body: EditDraft | None = None):
    """Approve = publish now. Saves the edited text first if sent along."""
    d = _get_draft(draft_id)
    if body is not None and d["status"] in ("draft", "failed"):
        store.update(draft_id, text=body.text)
    d = store.claim_for_publishing(draft_id)
    if not d:
        raise HTTPException(409, "This draft was already published, scheduled or rejected")
    return _with_meta(_publish_claimed(d))


class ScheduleIn(BaseModel):
    at: str              # ISO time with timezone, e.g. 2026-10-17T09:30:00.000Z
    text: str | None = None


def _parse_time(at: str) -> datetime:
    try:
        t = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "Couldn't read that date and time")
    if t.tzinfo is None:
        raise HTTPException(400, "The time needs a timezone")
    return t.astimezone(timezone.utc)


@app.post("/api/drafts/{draft_id}/schedule")
def schedule(draft_id: int, body: ScheduleIn):
    """Schedule (or reschedule) a draft. The Studio has to be running at that time to publish it."""
    d = _get_draft(draft_id)
    if d["status"] not in ("draft", "failed", "scheduled"):
        raise HTTPException(409, f"A {d['status']} draft can't be scheduled")
    at = _parse_time(body.at)
    if at < datetime.now(timezone.utc) + timedelta(minutes=1):
        raise HTTPException(400, "Pick a time at least a minute from now")
    text = d["text"] if body.text is None else body.text
    if d["kind"] != "repost" and not text.strip():
        raise HTTPException(400, "The draft is empty")
    if d["kind"] != "repost" and x_length(text) > max_chars():
        raise HTTPException(400, f"The draft is {x_length(text)} characters; the limit is {max_chars()}")
    return _with_meta(store.update(draft_id, text=text, status="scheduled", error=None,
                                   scheduled_at=at.isoformat(timespec="seconds")))


@app.post("/api/drafts/{draft_id}/unschedule")
def unschedule(draft_id: int):
    d = _get_draft(draft_id)
    if d["status"] != "scheduled":
        raise HTTPException(409, "This draft isn't scheduled")
    return _with_meta(store.update(draft_id, status="draft", scheduled_at=None))


@app.post("/api/drafts/{draft_id}/reject")
def reject(draft_id: int):
    d = _get_draft(draft_id)
    if d["status"] not in ("draft", "failed", "scheduled"):
        raise HTTPException(409, f"A {d['status']} draft can't be rejected")
    return _with_meta(store.update(draft_id, status="rejected", scheduled_at=None))


@app.post("/api/drafts/{draft_id}/restore")
def restore(draft_id: int):
    d = _get_draft(draft_id)
    if d["status"] != "rejected":
        raise HTTPException(409, "Only rejected drafts can be restored")
    return _with_meta(store.update(draft_id, status="draft"))


@app.delete("/api/drafts/{draft_id}")
def remove(draft_id: int):
    d = _get_draft(draft_id)
    if d["status"] in ("posted", "publishing"):
        raise HTTPException(409, "Published drafts are kept as history")
    store.delete(draft_id)
    return {"ok": True}


# --------------------------------------------------------------------------- routes: account, brand

def write_env(values: dict[str, str]) -> None:
    """Set KEY=value lines in .env (adding missing keys under an X account section) and in os.environ."""
    lines = open(ENV_PATH, encoding="utf-8").read().splitlines() if os.path.exists(ENV_PATH) else []
    remaining = dict(values)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].removeprefix("export ").strip()
        if "=" in line and not line.lstrip().startswith("#") and key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"
    if remaining:
        if not any(l.startswith("# X account") for l in lines):
            lines += ["", "# X account for the Studio (workflow/publisher.py). Never commit this file."]
        lines += [f"{k}={v}" for k, v in remaining.items()]
    tmp = ENV_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, ENV_PATH)
    for k, v in values.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


class AccountIn(BaseModel):
    mode: str
    values: dict[str, str] = {}


@app.get("/api/account")
def account():
    return {"mode": publisher.current_mode(), "fields": publisher.status_fields(),
            "session_saved": os.path.exists(publisher.SESSION_PATH)}


@app.post("/api/account")
def save_account(body: AccountIn):
    if body.mode not in publisher.MODES:
        raise HTTPException(400, "Unknown sign-in method")
    allowed = set(publisher.FIELDS[body.mode])
    # Blank secret fields mean "keep what's saved" (the UI never receives secrets back).
    values = {k: v.strip() for k, v in body.values.items() if k in allowed and (v.strip() or k not in publisher.SECRET)}
    values["X_AUTH_MODE"] = body.mode
    if body.mode == "password":
        publisher.clear_session()  # new credentials -> log in fresh next time
    write_env(values)
    return account()


@app.post("/api/account/verify")
def verify_account():
    try:
        return {"ok": True, **publisher.verify()}
    except publisher.PublishError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/account/disconnect")
def disconnect():
    keys = [k for ks in publisher.FIELDS.values() for k in ks] + ["X_AUTH_MODE"]
    write_env({k: "" for k in keys})
    publisher.clear_session()
    return account()


class BrandIn(BaseModel):
    text: str


@app.get("/api/persona")
def persona():
    return {"text": load_persona()}


@app.put("/api/persona")
def put_persona(body: BrandIn):
    save_persona(body.text)
    return {"text": load_persona()}


@app.get("/api/brand")
def brand():
    return {"text": load_brand()}


@app.put("/api/brand")
def put_brand(body: BrandIn):
    save_brand(body.text)
    return {"text": load_brand()}


# --------------------------------------------------------------------------- UI

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))
