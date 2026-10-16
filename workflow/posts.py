"""Read crawled posts from post.csv (written by search_crawler.py)."""

import csv
import html
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTS_CSV = os.path.join(ROOT, "post.csv")


def _int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def load_posts(path: str = POSTS_CSV) -> list[dict]:
    """Every row of post.csv, newest discovery first. Text is HTML-unescaped (&amp; -> &)."""
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    posts = []
    for r in rows:
        if not r.get("id"):
            continue
        posts.append({
            "id": r["id"],
            "topic": r.get("topic", ""),
            "created_at": r.get("created_at", ""),
            "username": r.get("username", ""),
            "display_name": html.unescape(r.get("display_name", "")),
            "likes": _int(r.get("likes")),
            "replies": _int(r.get("replies")),
            "score": _int(r.get("score")),
            "reasons": r.get("reasons", ""),
            "summary": r.get("summary", ""),
            "text": html.unescape(r.get("text", "")),
            "url": r.get("url") or f"https://x.com/{r.get('username', 'i')}/status/{r['id']}",
            "source": r.get("source", ""),
            "discovered_at": r.get("discovered_at", ""),
        })
    posts.sort(key=lambda p: p["discovered_at"], reverse=True)
    return posts


def posts_by_id(path: str = POSTS_CSV) -> dict[str, dict]:
    return {p["id"]: p for p in load_posts(path)}
