#!/usr/bin/env python3
"""Find valuable X posts via web search engines.

Pipeline, per topic keyword:
  1. discover  `site:x.com <keyword>` on one or more search engines (--engines) -> post URLs
  2. enrich    X's public embed endpoint -> full text, likes, replies, language (free)
  3. score     0-100 value score with reasons, for sorting; nothing is rejected
  4. summarize one-sentence summary per post: Gemini if GEMINI_API_KEY is set
               (--summary), otherwise the post's cleaned-up lead sentence
  5. save      append every post found to CSV, with the score, its reasons and the summary

Only two things limit what's saved: --since (post age) and --limit (count; 0 = no cap).
Posts already in the output file are skipped, since they're saved already.

Engines are listed in engines.py; compare them with benchmark_engines.py.
Output is appended to a CSV; post IDs already in the file are skipped.
"""

import argparse
import csv
import math
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

from engines import ENGINES, EngineError, available
from env import load_env_file
from summarize import Summarizer
from topics import quote_term, select_topics

STATUS_RE = re.compile(r"https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/(\w{1,15})/status(?:es)?/(\d+)")
TWITTER_EPOCH_MS = 1288834974657
HTTP = httpx.Client(timeout=20, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)

CSV_HEADER = [
    "topic", "id", "created_at", "username", "display_name", "likes", "replies",
    "score", "reasons", "summary", "text", "url", "query", "source", "discovered_at",
]


# --------------------------------------------------------------------------- discover

def snowflake_time(post_id: str) -> datetime:
    """X post IDs encode their creation time (ms since the Twitter epoch)."""
    ms = (int(post_id) >> 22) + TWITTER_EPOCH_MS
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


# --------------------------------------------------------------------------- enrich

@dataclass
class Post:
    id: str
    username: str
    created: datetime
    query: str
    source: str = ""  # engine that found it
    snippet: str = ""
    text: str = ""
    display_name: str = ""
    likes: int | None = None
    replies: int | None = None
    lang: str = ""
    is_long: bool = False
    is_reply: bool = False
    has_link: bool = False
    has_media: bool = False
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    summary: str = ""


def _embed_token(post_id: str) -> str:
    """Token the embed endpoint expects: (id / 1e15 * pi) in base 36, zeros and dot removed."""
    n = int(post_id) / 1e15 * math.pi
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    ip, fp, s = int(n), n - int(n), ""
    while ip:
        s, ip = digits[ip % 36] + s, ip // 36
    s = (s or "0") + "."
    for _ in range(12):
        fp *= 36
        s += digits[int(fp)]
        fp -= int(fp)
    return re.sub(r"(0+|\.)", "", s)


def enrich_from_embed(post: Post) -> bool:
    """Fill text/likes/replies/language from X's public embed endpoint. False if gone."""
    try:
        r = HTTP.get(
            "https://cdn.syndication.twimg.com/tweet-result",
            params={"id": post.id, "lang": "en", "token": _embed_token(post.id)},
        )
        if r.status_code != 200:
            return False
        d = r.json()
    except Exception:
        return False
    if d.get("__typename") == "TweetTombstone" or "user" not in d:
        return False

    user = d["user"]
    post.username = user.get("screen_name") or post.username
    post.display_name = user.get("name", "")
    post.text = d.get("text", "") or post.snippet
    post.lang = d.get("lang", "") or ""
    post.likes = d.get("favorite_count", 0)
    post.replies = d.get("conversation_count", 0)
    post.is_long = bool(d.get("note_tweet"))
    post.is_reply = bool(d.get("in_reply_to_status_id_str"))
    ents = d.get("entities") or {}
    post.has_link = bool(ents.get("urls"))
    post.has_media = bool(d.get("mediaDetails") or d.get("photos") or d.get("video"))
    return True


# --------------------------------------------------------------------------- score

INSIGHT_RE = re.compile(
    r"\b(lessons?|learned|learnings|mistakes?|how (?:we|i)|here'?s (?:how|what|why)|playbook|framework|"
    r"case study|breakdown|deep dive|step[- ]by[- ]step|tips?|insights?|takeaways?|thread|guide|"
    r"what worked|what didn'?t|built|shipped|launched|benchmark|results?|data|analysis|tested)\b",
    re.I,
)
METRIC_RE = re.compile(
    r"(\$\s?\d|\d+(?:\.\d+)?\s?(?:%|k\b|m\b|x\b)|\b\d{2,}\b.*\b(?:users|customers|revenue|mrr|arr|signups|"
    r"orders|sales|conversion|downloads|stars)\b|\b(?:mrr|arr|cac|ltv|roas|churn|retention)\b)",
    re.I,
)
SPAM_RE = re.compile(
    r"\b(giveaway|airdrop|follow (?:me|us|back)|dm me|dm for|link in bio|promo code|use code|discount code|"
    r"whitelist|presale|100x|to the moon|free money|join (?:my|our) (?:telegram|discord)|retweet to win|"
    r"comment .{0,20} (?:and|&) i'?ll send|like (?:and|&) retweet)\b",
    re.I,
)
SHILL_RE = re.compile(
    r"(\b(?:gm|wagmi|lfg|depin|tokenomics|memecoin|testnet|mainnet|staking|tge|ido|degen|alpha call|"
    r"not financial advice|nfa|dyor|nfts?|free mint|holders?)\b|\$[A-Z]{2,6}\b)",
    re.I,
)
HASHTAG_RE = re.compile(r"#\w+")
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")
LIST_RE = re.compile(r"(?m)^\s*(?:\d+[.)]|[-•→✅▪︎])\s+\S")
URL_RE = re.compile(r"https?://\S+")


def score_post(p: Post) -> None:
    """0-100 value score from content substance and engagement."""
    text = p.text or p.snippet
    words = URL_RE.sub("", text)
    s, why = 30, []

    n = len(words.strip())
    if p.is_long or n >= 240:
        s += 15; why.append("long-form")
    elif n >= 140:
        s += 8; why.append("substantial")
    elif n < 60:
        s -= 15; why.append("too short")

    insights = len(set(m.lower() for m in INSIGHT_RE.findall(words)))
    if insights:
        s += min(insights * 6, 18); why.append(f"insight words x{insights}")
    if METRIC_RE.search(words):
        s += 10; why.append("concrete numbers")
    if len(LIST_RE.findall(text)) >= 2:
        s += 8; why.append("structured list")
    if p.has_link and n >= 80:
        s += 4; why.append("has source link")

    if p.replies:
        s += min(int(math.log2(p.replies + 1) * 3), 10); why.append(f"{p.replies} replies")
    if p.likes:
        s += min(int(math.log2(p.likes + 1) * 2), 12); why.append(f"{p.likes} likes")

    spam = len(SPAM_RE.findall(words))
    if spam:
        s -= 25 * spam; why.append("promo/spam phrases")
    shill = len(set(m[0].lower() for m in SHILL_RE.findall(words)))
    if shill:
        s -= min(25 * shill, 50); why.append("crypto shill terms")
    tags = len(HASHTAG_RE.findall(words))
    if tags > 3:
        s -= 4 * (tags - 3); why.append(f"{tags} hashtags")
    if len(EMOJI_RE.findall(words)) > 6:
        s -= 8; why.append("emoji-heavy")
    letters = [c for c in words if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.5:
        s -= 10; why.append("mostly caps")

    p.score, p.reasons = max(0, min(100, s)), why


def mentions_any(p: Post, words: list[str]) -> bool:
    text = p.text or p.snippet
    return any(re.search(rf"(?<!\w){re.escape(k)}(?!\w)", text, re.I) for k in words)


def reject_reason(p: Post, args, topic) -> str | None:
    """Why a post would be out of scope (language/off-topic/reply), or None.

    The crawler saves every post; this is only used by benchmark_engines.py to compare engines
    on how many on-topic posts they find.
    """
    if args.lang and p.lang and p.lang != args.lang:
        return f"lang {p.lang}"
    if not mentions_any(p, topic.keywords):
        return "off-topic (no topic keyword in text)"
    if topic.require and not mentions_any(p, topic.require):
        return "off-topic (no required term in text)"
    if p.is_reply and not args.include_replies:
        return "reply"
    return None


# --------------------------------------------------------------------------- main

def load_seen_ids(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)
    if fields and fields != CSV_HEADER:
        if "id" not in fields:
            sys.exit(f"{path} is not a crawler output file. Use --out with a new file.")
        upgrade_csv(path, fields, rows)
    return {row["id"] for row in rows if row.get("id")}


def upgrade_csv(path: str, old_fields: list[str], rows: list[dict]) -> None:
    """Rewrite a file from an older version with the current columns (matched by name)."""
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in CSV_HEADER} for r in rows)
    os.replace(tmp, path)
    added = [c for c in CSV_HEADER if c not in old_fields]
    dropped = [c for c in old_fields if c not in CSV_HEADER]
    print(f"{path}: updated column layout for {len(rows)} rows (added {added or 'none'}, dropped {dropped or 'none'})")


def discover(engine: str, terms: str, per_query: int, window: timedelta) -> list[dict]:
    """Run one engine; log and return [] on failure so one broken engine doesn't stop the run."""
    try:
        return ENGINES[engine](terms, per_query, window)
    except EngineError as e:
        print(f"  ! {engine}: {e}", file=sys.stderr)
        return []


def main() -> None:
    load_env_file()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--topics-file", default="topics.csv", help="CSV of topics (topic,keywords,exclude)")
    p.add_argument("--topics", default="", help="comma-separated topic names (default: all in topics file)")
    p.add_argument("--limit", type=int, default=100, help="max new posts to save in total (0 = no limit)")
    p.add_argument("--since", type=float, default=24, help="only keep posts newer than this many hours")
    p.add_argument("--engines", default="ddgs,duckduckgo,yahoo",
                   help=f"comma-separated search engines to query (available: {','.join(ENGINES)})")
    p.add_argument("--per-query", type=int, default=30, help="search results to request per query")
    p.add_argument("--delay", type=float, default=2.0, help="seconds between queries (be polite)")
    p.add_argument("--summary", choices=["auto", "gemini", "simple", "off"], default="auto",
                   help="how to fill the summary column (auto: Gemini if GEMINI_API_KEY works, else simple)")
    p.add_argument("--out", default="post.csv", help="output CSV (appended to; known IDs are skipped)")
    args = p.parse_args()

    topics = select_topics(args.topics_file, args.topics)
    window = timedelta(hours=args.since)
    cutoff = datetime.now(timezone.utc) - window
    engines, skipped = available([e.strip() for e in args.engines.split(",") if e.strip()])
    for name, why in skipped.items():
        print(f"  ! skipping engine {name}: {why}", file=sys.stderr)
    if not engines:
        sys.exit("no usable engines")
    print(f"engines: {', '.join(engines)}")
    summarize = Summarizer(args.summary)
    print(f"summaries: {summarize.label}")

    seen = load_seen_ids(args.out)
    if seen:
        print(f"{args.out} already has {len(seen)} posts; appending new ones")

    new_file = not os.path.exists(args.out) or os.path.getsize(args.out) == 0
    total = 0
    kept_all: list[tuple[str, Post]] = []
    with open(args.out, "a", newline="", encoding="utf-8") as f, ThreadPoolExecutor(4) as pool:
        w = csv.writer(f)
        if new_file:
            w.writerow(CSV_HEADER)

        for i, topic in enumerate(topics):
            if args.limit:
                quota = args.limit // len(topics) + (1 if i < args.limit % len(topics) else 0)
            else:
                quota = float("inf")
            saved = 0
            excludes = "".join(f" -{quote_term(e)}" for e in topic.exclude)
            # One search per keyword: a single site:x.com query only yields a handful of results.
            for kw in topic.keywords:
                if saved >= quota:
                    break
                terms = f"{quote_term(kw)}{excludes}"
                query = f"site:x.com {terms}"

                # 1. discover (every engine; the first engine to find a post gets the credit)
                found: list[Post] = []
                n_results = 0
                for engine in engines:
                    results = discover(engine, terms, args.per_query, window)
                    n_results += len(results)
                    for r in results:
                        m = STATUS_RE.search(r["url"])
                        if not m or m.group(2) in seen:
                            continue
                        username, post_id = m.groups()
                        created = snowflake_time(post_id)
                        if created < cutoff:
                            continue
                        seen.add(post_id)  # don't save the same post twice in one run
                        found.append(Post(post_id, username, created, query, source=engine, snippet=r["snippet"]))

                accepted = found[: max(0, quota - saved)] if quota != float("inf") else found

                # 2. enrich; if the embed lookup fails (deleted/protected post), keep the search snippet
                for post, ok in zip(accepted, pool.map(enrich_from_embed, accepted)):
                    if not ok:
                        post.text = post.snippet

                # 3. score (informational only)
                for post in accepted:
                    score_post(post)

                # 4. summarize
                for post, summary in zip(accepted, pool.map(lambda p: summarize(p.text, topic.name), accepted)):
                    post.summary = summary

                # 5. save
                for post in accepted:
                    w.writerow([
                        topic.name, post.id, post.created.isoformat(timespec="seconds"), post.username,
                        post.display_name, post.likes, post.replies, post.score, "; ".join(post.reasons),
                        post.summary, post.text, f"https://x.com/{post.username}/status/{post.id}", query,
                        post.source,
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    ])
                    kept_all.append((topic.name, post))
                saved += len(accepted)
                f.flush()
                cap = "" if quota == float("inf") else f"/{quota}"
                print(f"[{topic.name}] {query!r}: {n_results} results, {len(found)} new, "
                      f"saved {len(accepted)} -> {saved}{cap}")
                time.sleep(args.delay + random.uniform(0, 1))

            total += saved
            print(f"[{topic.name}] saved {saved}")

    print(f"\ndone: {total} new posts appended to {args.out}")
    for name, post in sorted(kept_all, key=lambda x: -x[1].score)[:5]:
        print(f"  {post.score:>3} [{name}] @{post.username} via {post.source}: "
              f"{(post.summary or post.text)[:100].replace(chr(10), ' ')}")


if __name__ == "__main__":
    main()
