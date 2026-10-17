"""Load topics.csv: the list of topics and keywords the crawler searches for.

Format (header required, lists are "|"-separated, # starts a comment):

    topic,keywords,exclude,require
    tech,AI|machine learning|LLM,giveaway|airdrop,
    trending,going viral|just announced,,AI|startup|app

  keywords  each one is searched; a post must mention at least one
  exclude   (optional) words negated in the search, e.g. -giveaway
  require   (optional) a post must ALSO mention one of these, to keep broad
            keywords like "going viral" on-topic
"""

import csv
import os
import sys
from dataclasses import dataclass, field

from config import TOPICS_CSV


@dataclass
class Topic:
    name: str
    keywords: list[str]
    exclude: list[str] = field(default_factory=list)
    require: list[str] = field(default_factory=list)

    def query(self) -> str:
        """X search query, e.g. (AI OR "machine learning") -giveaway"""
        q = "(" + " OR ".join(quote_term(k) for k in self.keywords) + ")"
        return q + "".join(f" -{quote_term(e)}" for e in self.exclude)


def quote_term(s: str) -> str:
    return f'"{s}"' if any(c in s for c in " -") else s


def _split(s: str | None) -> list[str]:
    return [p.strip() for p in (s or "").split("|") if p.strip()]


def load_topics(path: str = TOPICS_CSV) -> list[Topic]:
    with open(path, newline="", encoding="utf-8") as f:
        lines = (line for line in f if not line.lstrip().startswith("#"))
        reader = csv.DictReader(lines)
        cols = {(h or "").strip().lower(): h for h in reader.fieldnames or []}
        if "topic" not in cols or "keywords" not in cols:
            raise ValueError(f"{path}: header must contain topic,keywords")
        topics = []
        for row in reader:
            name = (row.get(cols["topic"]) or "").strip()
            kws = _split(row.get(cols["keywords"]))
            if name and kws:
                optional = {c: _split(row.get(cols[c])) if c in cols else [] for c in ("exclude", "require")}
                topics.append(Topic(name, kws, **optional))
    if not topics:
        raise ValueError(f"{path} has no topics")
    return topics


def save_topics(topics: list[Topic], path: str = TOPICS_CSV) -> None:
    """Rewrite the topics file atomically, keeping any # comment lines at the top."""
    comments = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            comments = [line for line in f if line.lstrip().startswith("#")]
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        f.writelines(comments)
        w = csv.writer(f)
        w.writerow(["topic", "keywords", "exclude", "require"])
        for t in topics:
            w.writerow([t.name, "|".join(t.keywords), "|".join(t.exclude), "|".join(t.require)])
    os.replace(tmp, path)


def select_topics(path: str, names: str | None) -> list[Topic]:
    """Load topics from path, keeping only the comma-separated names (all if empty)."""
    try:
        topics = load_topics(path)
    except (OSError, ValueError) as e:
        sys.exit(str(e))
    if not names:
        return topics
    by_name = {t.name: t for t in topics}
    out = []
    for n in (n.strip() for n in names.split(",")):
        if n not in by_name:
            sys.exit(f"unknown topic {n!r} (available in {path}: {','.join(by_name)})")
        out.append(by_name[n])
    return out
