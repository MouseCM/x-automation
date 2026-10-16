"""Deviantly X Studio backend (FastAPI). Start it with `python3 studio.py`.

Serves the review UI from workflow/static and a JSON API under /api:
  crawl     run search_crawler.py in the background and stream its log
  posts     crawled posts from post.csv, with draft badges
  drafts    Gemini drafts: create, regenerate, edit, approve (publishes to X), reject
  account   X sign-in settings (written to .env) and a connection test
  brand     the brand brief Gemini writes from
"""

import os
import subprocess
import sys
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from env import load_env_file
from topics import load_topics

from . import publisher, store
from .drafter import Drafter, load_brand, max_chars, save_brand, x_length
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
    AUTODRAFT.log.append(f"drafting {opts.kind} for {len(picks)} posts (min score {opts.min_score})")
    drafter, errors = _drafter(), 0

    def one(p):
        nonlocal errors
        try:
            out = drafter.draft(p, opts.kind)
            store.create(p, opts.kind, out.text, out.angle, out.fit, model=drafter.model)
            AUTODRAFT.log.append(f"✓ @{p['username']}: fit {out.fit}/10")
        except Exception as e:
            errors += 1
            AUTODRAFT.log.append(f"✗ @{p['username']}: {e}")
        AUTODRAFT.done += 1

    with ThreadPoolExecutor(3) as ex:
        list(ex.map(one, picks))
    made = len(picks) - errors
    AUTODRAFT.finish(errors == 0, f"{made} drafts ready for review" + (f", {errors} failed" if errors else ""))


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


def _crawl(opts: CrawlOpts) -> None:
    before = {p["id"] for p in load_posts()}
    cmd = [sys.executable, "-u", "search_crawler.py", "--limit", str(opts.limit), "--since", str(opts.since),
           "--summary", opts.summary, "--out", POSTS_CSV]
    if opts.topics:
        cmd += ["--topics", ",".join(opts.topics)]
    if opts.engines.strip():
        cmd += ["--engines", opts.engines.strip()]
    CRAWL.log.append("$ " + " ".join(cmd[1:]))
    try:
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                bufsize=1, env={**os.environ, "PYTHONUNBUFFERED": "1"})
        for line in proc.stdout:
            CRAWL.log.append(line.rstrip())
        code = proc.wait()
    except Exception as e:
        CRAWL.finish(False, f"could not start crawler: {e}")
        return
    new_ids = [p["id"] for p in load_posts() if p["id"] not in before]
    CRAWL.done = len(new_ids)
    CRAWL.finish(code == 0, f"{len(new_ids)} new posts" if code == 0 else f"crawler exited with code {code}")
    if code == 0 and opts.autodraft and new_ids:
        opts.autodraft.only_ids = new_ids
        start_autodraft(opts.autodraft)


# --------------------------------------------------------------------------- routes: status, crawl, posts

@app.get("/api/status")
def status():
    return {
        "crawl": CRAWL.state(), "autodraft": AUTODRAFT.state(), "counts": store.counts(),
        "gemini": _drafter().ready, "x_mode": publisher.current_mode(), "max_chars": max_chars(),
    }


@app.get("/api/topics")
def topics():
    try:
        return [t.name for t in load_topics(os.path.join(ROOT, "topics.csv"))]
    except Exception:
        return []


@app.post("/api/crawl")
def crawl(opts: CrawlOpts):
    if not CRAWL.start():
        raise HTTPException(409, "the crawler is already running")
    threading.Thread(target=_crawl, args=(opts,), daemon=True).start()
    return CRAWL.state()


@app.post("/api/autodraft")
def autodraft(opts: AutoDraftOpts):
    if opts.kind not in ("post", "quote", "reply"):
        raise HTTPException(400, "kind must be post, quote or reply")
    if not _drafter().ready:
        raise HTTPException(400, "GEMINI_API_KEY is not set")
    if not start_autodraft(opts):
        raise HTTPException(409, "auto-draft is already running")
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
        raise HTTPException(404, "draft not found")
    return d


@app.get("/api/drafts")
def drafts(status: str | None = None, post_id: str | None = None):
    return [_with_meta(d) for d in store.list_drafts(status, post_id)]


@app.post("/api/drafts")
def new_draft(body: NewDraft):
    if body.kind not in store.KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(store.KINDS)}")
    post = posts_by_id().get(body.post_id)
    if not post:
        raise HTTPException(404, "post not found in post.csv")
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
        raise HTTPException(409, f"can't regenerate a {d['status']} draft")
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
    if d["status"] not in ("draft", "failed"):
        raise HTTPException(409, f"can't edit a {d['status']} draft")
    return _with_meta(store.update(draft_id, text=body.text))


@app.post("/api/drafts/{draft_id}/approve")
def approve(draft_id: int, body: EditDraft | None = None):
    """Approve = publish now. Saves the edited text first if sent along."""
    d = _get_draft(draft_id)
    if body is not None and d["status"] in ("draft", "failed"):
        store.update(draft_id, text=body.text)
    d = store.claim_for_publishing(draft_id)
    if not d:
        raise HTTPException(409, "this draft was already published or rejected")
    if d["kind"] != "repost" and x_length(d["text"]) > max_chars():
        store.update(draft_id, status="failed", error=f"Too long: {x_length(d['text'])}/{max_chars()} characters")
        raise HTTPException(400, f"draft is {x_length(d['text'])} characters; the limit is {max_chars()}")
    src = d["source"]
    try:
        res = publisher.publish(d["kind"], d["text"], src["id"], src["url"])
    except publisher.PublishError as e:
        return _with_meta(store.update(draft_id, status="failed", error=str(e)))
    return _with_meta(store.update(draft_id, status="posted", published_id=res["id"], published_url=res["url"]))


@app.post("/api/drafts/{draft_id}/reject")
def reject(draft_id: int):
    d = _get_draft(draft_id)
    if d["status"] not in ("draft", "failed"):
        raise HTTPException(409, f"can't reject a {d['status']} draft")
    return _with_meta(store.update(draft_id, status="rejected"))


@app.post("/api/drafts/{draft_id}/restore")
def restore(draft_id: int):
    d = _get_draft(draft_id)
    if d["status"] != "rejected":
        raise HTTPException(409, "only rejected drafts can be restored")
    return _with_meta(store.update(draft_id, status="draft"))


@app.delete("/api/drafts/{draft_id}")
def remove(draft_id: int):
    d = _get_draft(draft_id)
    if d["status"] in ("posted", "publishing"):
        raise HTTPException(409, "published drafts are kept as history")
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
        raise HTTPException(400, "unknown mode")
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
