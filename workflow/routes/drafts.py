"""Crawled posts and their drafts: create, regenerate, edit, approve (publishes to X), schedule, reject."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import max_chars

from .. import store
from ..drafter import x_length
from ..jobs import drafter, publish_claimed
from ..posts import load_posts, posts_by_id

router = APIRouter(prefix="/api")


def with_meta(d: dict) -> dict:
    d["length"] = x_length(d["text"])
    d["limit"] = max_chars()
    return d


def _check_length(text: str) -> None:
    if x_length(text) > max_chars():
        raise HTTPException(400, f"The draft is {x_length(text)} characters; the limit is {max_chars()}")


@router.get("/posts")
def posts():
    badges = store.summary_by_post()
    out = load_posts()
    for p in out:
        p["drafts"] = badges.get(p["id"], {"count": 0, "statuses": {}, "posted_kinds": []})
    return out


class NewDraft(BaseModel):
    post_id: str
    kind: str
    instruction: str = ""


class Regenerate(BaseModel):
    instruction: str = ""


class EditDraft(BaseModel):
    text: str


class ScheduleIn(BaseModel):
    at: str              # ISO time with timezone, e.g. 2026-10-17T09:30:00.000Z
    text: str | None = None


def _get_draft(draft_id: int) -> dict:
    d = store.get(draft_id)
    if not d:
        raise HTTPException(404, "That draft no longer exists")
    return d


@router.get("/drafts")
def drafts(status: str | None = None, post_id: str | None = None):
    return [with_meta(d) for d in store.list_drafts(status, post_id)]


@router.post("/drafts")
def new_draft(body: NewDraft):
    if body.kind not in store.KINDS:
        raise HTTPException(400, "Pick post, quote, reply or repost")
    post = posts_by_id().get(body.post_id)
    if not post:
        raise HTTPException(404, "That post is no longer in post.csv")
    writer = drafter()
    try:
        out = writer.draft(post, body.kind, body.instruction)
    except Exception as e:
        raise HTTPException(502, str(e))
    return with_meta(store.create(post, body.kind, out.text, out.angle, out.fit, body.instruction,
                                  writer.model if body.kind != "repost" else ""))


@router.post("/drafts/{draft_id}/regenerate")
def regenerate(draft_id: int, body: Regenerate):
    d = _get_draft(draft_id)
    if d["status"] not in ("draft", "failed"):
        raise HTTPException(409, f"A {d['status']} draft can't be rewritten")
    writer = drafter()
    try:
        out = writer.draft(d["source"], d["kind"], body.instruction, previous=d["text"])
    except Exception as e:
        raise HTTPException(502, str(e))
    return with_meta(store.update(draft_id, text=out.text, angle=out.angle, fit=out.fit,
                                  instruction=body.instruction, model=writer.model, status="draft", error=None))


@router.patch("/drafts/{draft_id}")
def edit(draft_id: int, body: EditDraft):
    d = _get_draft(draft_id)
    if d["status"] not in ("draft", "failed", "scheduled"):
        raise HTTPException(409, f"A {d['status']} draft can't be edited")
    if d["status"] == "scheduled" and d["kind"] != "repost":
        _check_length(body.text)
    return with_meta(store.update(draft_id, text=body.text))


@router.post("/drafts/{draft_id}/approve")
def approve(draft_id: int, body: EditDraft | None = None):
    """Approve = publish now. Saves the edited text first if sent along."""
    d = _get_draft(draft_id)
    if body is not None and d["status"] in ("draft", "failed"):
        store.update(draft_id, text=body.text)
    d = store.claim_for_publishing(draft_id)
    if not d:
        raise HTTPException(409, "This draft was already published, scheduled or rejected")
    return with_meta(publish_claimed(d))


def _parse_time(at: str) -> datetime:
    try:
        t = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "Couldn't read that date and time")
    if t.tzinfo is None:
        raise HTTPException(400, "The time needs a timezone")
    return t.astimezone(timezone.utc)


@router.post("/drafts/{draft_id}/schedule")
def schedule(draft_id: int, body: ScheduleIn):
    """Schedule (or reschedule) a draft. The Studio has to be running at that time to publish it."""
    d = _get_draft(draft_id)
    if d["status"] not in ("draft", "failed", "scheduled"):
        raise HTTPException(409, f"A {d['status']} draft can't be scheduled")
    at = _parse_time(body.at)
    if at < datetime.now(timezone.utc) + timedelta(minutes=1):
        raise HTTPException(400, "Pick a time at least a minute from now")
    text = d["text"] if body.text is None else body.text
    if d["kind"] != "repost":
        if not text.strip():
            raise HTTPException(400, "The draft is empty")
        _check_length(text)
    return with_meta(store.update(draft_id, text=text, status="scheduled", error=None,
                                  scheduled_at=at.isoformat(timespec="seconds")))


@router.post("/drafts/{draft_id}/unschedule")
def unschedule(draft_id: int):
    d = _get_draft(draft_id)
    if d["status"] != "scheduled":
        raise HTTPException(409, "This draft isn't scheduled")
    return with_meta(store.update(draft_id, status="draft", scheduled_at=None))


@router.post("/drafts/{draft_id}/reject")
def reject(draft_id: int):
    d = _get_draft(draft_id)
    if d["status"] not in ("draft", "failed", "scheduled"):
        raise HTTPException(409, f"A {d['status']} draft can't be rejected")
    return with_meta(store.update(draft_id, status="rejected", scheduled_at=None))


@router.post("/drafts/{draft_id}/restore")
def restore(draft_id: int):
    d = _get_draft(draft_id)
    if d["status"] != "rejected":
        raise HTTPException(409, "Only rejected drafts can be restored")
    return with_meta(store.update(draft_id, status="draft"))


@router.delete("/drafts/{draft_id}")
def remove(draft_id: int):
    d = _get_draft(draft_id)
    if d["status"] in ("posted", "publishing"):
        raise HTTPException(409, "Published drafts are kept as history")
    store.delete(draft_id)
    return {"ok": True}
