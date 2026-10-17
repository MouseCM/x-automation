"""Status polling, the crawler, auto-draft and the autopilot."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from config import max_chars

from .. import autopilot as ap
from .. import publisher, store
from ..jobs import AUTODRAFT, AUTOPILOT, CRAWL, AutoDraftOpts, CrawlOpts, autopilot, drafter, start_autodraft, start_crawl
from .drafts import with_meta

router = APIRouter(prefix="/api")

NO_GEMINI_KEY = "Add GEMINI_API_KEY to .env first"


@router.get("/status")
def status():
    return {
        "crawl": CRAWL.state(), "autodraft": AUTODRAFT.state(), "autopilot": _autopilot_state(), "counts": store.counts(),
        "gemini": drafter().ready, "x_mode": publisher.current_mode(), "max_chars": max_chars(),
        "next_scheduled": next((d["scheduled_at"] for d in sorted(store.list_drafts("scheduled"),
                                                                    key=lambda d: d["scheduled_at"])), None),
    }


def _autopilot_state() -> dict:
    s = ap.load_settings()
    return {**AUTOPILOT.state(), "enabled": s.enabled, "next_run_at": s.next_run_at if s.enabled else None,
            "in_active_hours": ap.in_active_hours(s), "posted_today": ap.posted_today(), "daily_cap": s.daily_cap}


def _runs() -> list[dict]:
    runs = store.list_runs()
    for r in runs:
        d = store.get(r["draft_id"]) if r["draft_id"] else None
        r["draft"] = with_meta(d) if d else None
    return runs


@router.get("/autopilot")
def get_autopilot():
    return {"settings": ap.load_settings().model_dump(), "state": _autopilot_state(), "runs": _runs()}


@router.put("/autopilot")
def put_autopilot(body: ap.Settings):
    old = ap.load_settings()
    if body.enabled and not old.enabled:
        body.next_run_at = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(timespec="seconds")
    elif body.enabled:
        body.next_run_at = old.next_run_at
    else:
        body.next_run_at = None
    return {"settings": ap.save_settings(body).model_dump(), "state": _autopilot_state()}


@router.post("/autopilot/run")
def run_autopilot():
    if not drafter().ready:
        raise HTTPException(400, NO_GEMINI_KEY)
    if CRAWL.running:
        raise HTTPException(409, "A crawl is already running. Try again when it finishes")
    if not autopilot.run_now("manual"):
        raise HTTPException(409, "The autopilot is already running")
    return _autopilot_state()


@router.post("/crawl")
def crawl(opts: CrawlOpts):
    if not start_crawl(opts):
        raise HTTPException(409, "The crawler is already running")
    return CRAWL.state()


@router.post("/autodraft")
def autodraft(opts: AutoDraftOpts):
    if opts.kind not in ("post", "quote", "reply"):
        raise HTTPException(400, "Auto-draft can write posts, quote posts or replies")
    if not drafter().ready:
        raise HTTPException(400, NO_GEMINI_KEY)
    if not start_autodraft(opts):
        raise HTTPException(409, "Gemini is already writing drafts")
    return AUTODRAFT.state()
