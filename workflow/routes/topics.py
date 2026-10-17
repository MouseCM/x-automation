"""Topics the crawler searches for (topics.csv)."""

import os
import re
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import TOPICS_CSV
from topics import Topic, load_topics, save_topics

router = APIRouter(prefix="/api")
_lock = threading.Lock()


def _as_dict(t: Topic) -> dict:
    return {"name": t.name, "keywords": t.keywords, "exclude": t.exclude, "require": t.require}


def _load_for_edit() -> list[Topic]:
    """Current topics, or an error: never treat an unreadable file as empty, or saving would wipe it."""
    if not os.path.exists(TOPICS_CSV):
        return []
    try:
        return load_topics(TOPICS_CSV)
    except (OSError, ValueError) as e:
        raise HTTPException(500, f"Couldn't read topics.csv, so nothing was changed: {e}")


@router.get("/topics")
def topics():
    try:
        return [t.name for t in load_topics(TOPICS_CSV)]
    except Exception:
        return []


@router.get("/topics/full")
def topics_full():
    try:
        return [_as_dict(t) for t in load_topics(TOPICS_CSV)]
    except Exception:
        return []


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


@router.post("/topics")
def add_topic(body: TopicIn):
    name = re.sub(r"[^a-z0-9]+", "-", body.name.strip().lower()).strip("-")
    if not name:
        raise HTTPException(400, "Give the topic a name")
    keywords = _terms(body.keywords)
    if not keywords:
        raise HTTPException(400, "Add at least one keyword to search for")
    if len(keywords) > 25:
        raise HTTPException(400, "Keep it to 25 keywords or fewer; each one is a separate search")
    with _lock:
        current = _load_for_edit()
        if any(t.name == name for t in current):
            raise HTTPException(409, f"There's already a topic called {name}")
        save_topics(current + [Topic(name, keywords, _terms(body.exclude), _terms(body.require))], TOPICS_CSV)
    return topics_full()


@router.delete("/topics/{name}")
def delete_topic(name: str):
    with _lock:
        current = _load_for_edit()
        if not any(t.name == name for t in current):
            raise HTTPException(404, "That topic doesn't exist")
        if len(current) == 1:
            raise HTTPException(400, "Keep at least one topic so the crawler has something to search for")
        save_topics([t for t in current if t.name != name], TOPICS_CSV)
    return topics_full()
