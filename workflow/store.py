"""Drafts awaiting review, kept in SQLite (workflow/studio.db).

Draft lifecycle:  draft -> (edit / regenerate)* -> [scheduled ->] publishing -> posted | failed
                        \\-> rejected
A failed draft can be edited and approved again; a scheduled one can be unscheduled back to draft.
"""

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "studio.db")
KINDS = ("post", "quote", "reply", "repost")
STATUSES = ("draft", "scheduled", "publishing", "posted", "failed", "rejected")

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id       TEXT NOT NULL,          -- crawled post this draft is about
    kind          TEXT NOT NULL,          -- post | quote | reply | repost
    text          TEXT NOT NULL DEFAULT '',
    angle         TEXT NOT NULL DEFAULT '', -- Gemini's one-line explanation of the take
    fit           INTEGER,                -- Gemini's 0-10 rating of how well the post fits Deviantly
    instruction   TEXT NOT NULL DEFAULT '', -- your extra instruction for (re)generation
    model         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'draft',
    source        TEXT NOT NULL DEFAULT '{}', -- JSON snapshot of the crawled post
    published_id  TEXT,
    published_url TEXT,
    error         TEXT,
    scheduled_at  TEXT,                   -- UTC ISO time to publish at (status 'scheduled')
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS drafts_post ON drafts(post_id);
CREATE INDEX IF NOT EXISTS drafts_status ON drafts(status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init() -> None:
    with _lock, _conn() as c:
        c.executescript(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(drafts)")}
        if "scheduled_at" not in cols:  # database from before scheduling existed
            c.execute("ALTER TABLE drafts ADD COLUMN scheduled_at TEXT")


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    d["source"] = json.loads(d["source"] or "{}")
    return d


def create(post: dict, kind: str, text: str, angle: str = "", fit: int | None = None,
           instruction: str = "", model: str = "") -> dict:
    now = _now()
    with _lock, _conn() as c:
        cur = c.execute(
            "INSERT INTO drafts (post_id, kind, text, angle, fit, instruction, model, source, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (post["id"], kind, text, angle, fit, instruction, model, json.dumps(post), now, now),
        )
        return _row(c.execute("SELECT * FROM drafts WHERE id=?", (cur.lastrowid,)).fetchone())


def get(draft_id: int) -> dict | None:
    with _conn() as c:
        return _row(c.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone())


def list_drafts(status: str | None = None, post_id: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM drafts WHERE 1=1", []
    if status:
        q += f" AND status IN ({','.join('?' * len(status.split(',')))})"
        args += status.split(",")
    if post_id:
        q += " AND post_id=?"
        args.append(post_id)
    q += " ORDER BY updated_at DESC, id DESC"
    with _conn() as c:
        return [_row(r) for r in c.execute(q, args).fetchall()]


def update(draft_id: int, **fields) -> dict | None:
    allowed = {"text", "angle", "fit", "instruction", "model", "status", "published_id", "published_url", "error",
               "scheduled_at"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in fields)
    with _lock, _conn() as c:
        c.execute(f"UPDATE drafts SET {sets} WHERE id=?", (*fields.values(), draft_id))
        return _row(c.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone())


def claim_for_publishing(draft_id: int, from_statuses: tuple[str, ...] = ("draft", "failed")) -> dict | None:
    """Atomically move a draft to 'publishing'. None if it's not in from_statuses (e.g. a double click)."""
    with _lock, _conn() as c:
        cur = c.execute(
            f"UPDATE drafts SET status='publishing', error=NULL, updated_at=? WHERE id=? "
            f"AND status IN ({','.join('?' * len(from_statuses))})",
            (_now(), draft_id, *from_statuses),
        )
        if cur.rowcount == 0:
            return None
        return _row(c.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone())


def due(now_iso: str) -> list[dict]:
    """Scheduled drafts whose time has come, oldest first."""
    with _conn() as c:
        rows = c.execute("SELECT * FROM drafts WHERE status='scheduled' AND scheduled_at <= ? ORDER BY scheduled_at",
                         (now_iso,)).fetchall()
        return [_row(r) for r in rows]


def delete(draft_id: int) -> None:
    with _lock, _conn() as c:
        c.execute("DELETE FROM drafts WHERE id=?", (draft_id,))


def summary_by_post() -> dict[str, dict]:
    """post_id -> {"count": n, "statuses": {status: n}, "posted_kinds": [...]}, for badges in the post list."""
    out: dict[str, dict] = {}
    with _conn() as c:
        for r in c.execute("SELECT post_id, kind, status, COUNT(*) n FROM drafts GROUP BY post_id, kind, status"):
            s = out.setdefault(r["post_id"], {"count": 0, "statuses": {}, "posted_kinds": []})
            s["count"] += r["n"]
            s["statuses"][r["status"]] = s["statuses"].get(r["status"], 0) + r["n"]
            if r["status"] == "posted":
                s["posted_kinds"].append(r["kind"])
    return out


def counts() -> dict[str, int]:
    with _conn() as c:
        return {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM drafts GROUP BY status")}


# --------------------------------------------------------------------------- autopilot run history

RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS autopilot_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    trigger     TEXT NOT NULL DEFAULT 'schedule',  -- schedule | manual
    outcome     TEXT NOT NULL DEFAULT 'running',   -- running | posted | queued | skipped | failed
    message     TEXT NOT NULL DEFAULT '',
    found       INTEGER NOT NULL DEFAULT 0,        -- new posts the crawl found
    considered  INTEGER NOT NULL DEFAULT 0,        -- candidates Gemini chose from
    post_id     TEXT,
    draft_id    INTEGER,
    reason      TEXT NOT NULL DEFAULT ''           -- why Gemini picked that post
);
"""


def init_runs() -> None:
    with _lock, _conn() as c:
        c.executescript(RUNS_SCHEMA)
        # A run cut short by a restart never finishes; close it so the history makes sense.
        c.execute("UPDATE autopilot_runs SET outcome='failed', message='Stopped because the Studio closed', "
                  "finished_at=started_at WHERE outcome='running'")


def start_run(trigger: str) -> int:
    with _lock, _conn() as c:
        return c.execute("INSERT INTO autopilot_runs (started_at, trigger) VALUES (?, ?)", (_now(), trigger)).lastrowid


def finish_run(run_id: int, outcome: str, message: str, **fields) -> None:
    allowed = {"found", "considered", "post_id", "draft_id", "reason"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    sets = "".join(f", {k}=?" for k in fields)
    with _lock, _conn() as c:
        c.execute(f"UPDATE autopilot_runs SET outcome=?, message=?, finished_at=?{sets} WHERE id=?",
                  (outcome, message, _now(), *fields.values(), run_id))


def list_runs(limit: int = 30) -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM autopilot_runs ORDER BY id DESC LIMIT ?", (limit,))]


def autopilot_posts_since(since_iso: str) -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM autopilot_runs WHERE outcome='posted' AND started_at >= ?",
                         (since_iso,)).fetchone()[0]
