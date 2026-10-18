"""Read crawled posts from post.csv (written by search_crawler.py), and look up our own posts for follow-ups."""

import csv
import html
import os
import re
from datetime import datetime, timezone

from config import POSTS_CSV


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


# x.com/<user>/status/<id>, and x.com/i/web/status/<id> (the links the Studio saves for what it publishes)
OWN_POST_RE = re.compile(r"(?:x|twitter)\.com/(?:i/web|\w{1,15})/status(?:es)?/(\d+)")


def own_post(post_id: str, text: str = "", url: str = "") -> dict | None:
    """One of our own posts, shaped like a load_posts() row, for writing a follow-up under it.

    Text, likes and the @handle come from X's public embed endpoint (free, no sign-in). `text` and `url`, when
    known (a post the Studio published), are kept as they are and used if the lookup fails. None if the post
    can't be loaded and there's no text to fall back on.
    """
    from search_crawler import Post, enrich_from_embed, snowflake_time  # the crawler's free embed lookup

    created = snowflake_time(post_id)
    p = Post(post_id, "", created, query="")
    if not enrich_from_embed(p) and not text:
        return None
    return {
        "id": post_id, "topic": "your post", "created_at": created.isoformat(timespec="seconds"),
        "username": p.username, "display_name": html.unescape(p.display_name),
        "likes": p.likes or 0, "replies": p.replies or 0, "score": 0, "reasons": "", "summary": "",
        "text": text or html.unescape(p.text),
        "url": url or (f"https://x.com/{p.username}/status/{post_id}" if p.username else f"https://x.com/i/web/status/{post_id}"),
        "source": "", "discovered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "own": True,
    }
