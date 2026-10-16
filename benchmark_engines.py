#!/usr/bin/env python3
"""Compare search engines for discovering X posts: which one finds the most useful posts?

Every engine runs the same `site:x.com <keyword>` queries, taking turns: query 1 on every engine,
then query 2, and so on. Engines run one at a time because the scraping library shares state
between threads, which would mix up the per-engine numbers. Then every post found is checked once with X's embed endpoint and
run through the crawler's filters and value score, so engines are compared on posts that would
actually be saved, not just on raw links.

Columns:
  ok/err      queries that succeeded / failed (blocked, timeout, bad key)
  sec/q       average seconds per query
  links       x.com post links returned (deduplicated per engine)
  fresh       posts newer than --since (post time is decoded from the ID)
  live        fresh posts that still exist on X
  kept        live posts that pass the crawler's filters and --min-score
  only        kept posts that no other engine found
  kept/q      kept posts per query: the efficiency number
  age_h       median age in hours of fresh posts (lower = indexes faster)

Usage:
  python3 benchmark_engines.py                                  # all engines with keys, 2 keywords/topic
  python3 benchmark_engines.py --engines bing,duckduckgo,yandex --keywords-per-topic 4
"""

import argparse
import csv
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from engines import ENGINES, EngineError, available
from env import load_env_file
from search_crawler import STATUS_RE, Post, enrich_from_embed, reject_reason, score_post, snowflake_time
from topics import quote_term, select_topics


@dataclass
class Stats:
    ok: int = 0
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0
    links: set[str] = field(default_factory=set)
    fresh: set[str] = field(default_factory=set)


def run_query(name: str, topic: str, terms: str, args, cutoff: datetime, st: Stats, found: dict):
    """Run one query on one engine; returns (number of results, error message or None)."""
    t, error = time.time(), None
    try:
        results = ENGINES[name](terms, args.per_query, timedelta(hours=args.since))
        st.ok += 1
    except EngineError as e:
        error = str(e)
        st.errors.append(error)
        results = []
    st.seconds += time.time() - t
    for r in results:
        m = STATUS_RE.search(r["url"])
        if not m:
            continue
        username, post_id = m.groups()
        st.links.add(post_id)
        if snowflake_time(post_id) >= cutoff:
            st.fresh.add(post_id)
            found.setdefault(post_id, (username, topic, r.get("snippet", "")))
    return len(results), error


def main() -> None:
    load_env_file()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--engines", default="", help=f"comma-separated (default: all usable): {','.join(ENGINES)}")
    p.add_argument("--topics-file", default="topics.csv")
    p.add_argument("--topics", default="", help="comma-separated topic names (default: all)")
    p.add_argument("--keywords-per-topic", type=int, default=2, help="queries per topic (first N keywords)")
    p.add_argument("--since", type=float, default=24, help="freshness window in hours")
    p.add_argument("--per-query", type=int, default=20, help="results requested per query")
    p.add_argument("--delay", type=float, default=2.0, help="extra seconds between query rounds")
    p.add_argument("--min-score", type=int, default=45, help="same meaning as in search_crawler.py")
    p.add_argument("--lang", default="en")
    p.add_argument("--out", default="benchmark.csv", help="per-engine results, appended so runs can be compared")
    args = p.parse_args()

    names = [e.strip() for e in args.engines.split(",") if e.strip()] or None
    engines, skipped = available(names)
    for name, why in skipped.items():
        print(f"skipping {name}: {why}")
    if not engines:
        sys.exit("no usable engines")

    topics = select_topics(args.topics_file, args.topics)
    by_name = {t.name: t for t in topics}
    queries = []
    for t in topics:
        excludes = "".join(f" -{quote_term(e)}" for e in t.exclude)
        queries += [(t.name, f"{quote_term(k)}{excludes}") for k in t.keywords[: args.keywords_per_topic]]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.since)
    print(f"{len(engines)} engines x {len(queries)} queries: {', '.join(engines)}\n")

    # 1. search: round-robin over engines, so each engine gets a pause of a full round between queries
    found: dict[str, tuple[str, str, str]] = {}  # post id -> (username, topic, snippet) of first sighting
    stats = {n: Stats() for n in engines}
    for i, (topic, terms) in enumerate(queries):
        print(f"[{i + 1}/{len(queries)}] {terms!r}")
        for n in engines:
            got, err = run_query(n, topic, terms, args, cutoff, stats[n], found)
            print(f"  {n:11} {got:3} results" + (f"  ! {err[:70]}" if err else ""))
        if i + 1 < len(queries):
            time.sleep(args.delay)

    # 2. check every fresh post once: does it exist, and would the crawler keep it?
    print(f"\nchecking {len(found)} fresh posts against X and the crawler filters...")
    posts = {pid: Post(pid, u, snowflake_time(pid), "", snippet=snip) for pid, (u, _, snip) in found.items()}
    with ThreadPoolExecutor(8) as pool:
        live = {pid for pid, ok in zip(posts, pool.map(enrich_from_embed, posts.values())) if ok}
    filt = SimpleNamespace(lang=args.lang, include_replies=False)
    kept = set()
    for pid in live:
        post, topic = posts[pid], by_name[found[pid][1]]
        if reject_reason(post, filt, topic) is None:
            score_post(post)
            if post.score >= args.min_score:
                kept.add(pid)

    # 3. report
    now = datetime.now(timezone.utc)
    rows = []
    for n in engines:
        st = stats[n]
        k = st.fresh & kept
        others = set().union(*(stats[o].fresh for o in engines if o != n))
        ages = [(now - snowflake_time(pid)).total_seconds() / 3600 for pid in st.fresh]
        rows.append({
            "engine": n, "ok": st.ok, "err": len(st.errors),
            "sec/q": round(st.seconds / max(len(queries), 1), 1),
            "links": len(st.links), "fresh": len(st.fresh), "live": len(st.fresh & live),
            "kept": len(k), "only": len(k - others),
            "kept/q": round(len(k) / max(len(queries), 1), 2),
            "age_h": round(statistics.median(ages), 1) if ages else "",
            "first_error": st.errors[0][:80] if st.errors else "",
        })
    rows.sort(key=lambda r: (-r["kept"], -r["only"], r["sec/q"]))

    cols = ["engine", "ok", "err", "sec/q", "links", "fresh", "live", "kept", "only", "kept/q", "age_h"]
    print("\n" + "  ".join(f"{c:>8}" if c != "engine" else f"{c:11}" for c in cols))
    for r in rows:
        print("  ".join(f"{str(r[c]):>8}" if c != "engine" else f"{r[c]:11}" for c in cols))
    print(f"\nall engines together: {len(kept)} kept posts out of {len(found)} fresh links")
    for r in rows:
        if r["first_error"]:
            print(f"  {r['engine']}: {r['err']} errors, e.g. {r['first_error']}")

    new_file = not os.path.exists(args.out)
    with open(args.out, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["run_at", "queries", *cols, "first_error"])
        if new_file:
            w.writeheader()
        for r in rows:
            w.writerow({"run_at": now.isoformat(timespec="seconds"), "queries": len(queries), **r})
    print(f"\nresults appended to {args.out}")


if __name__ == "__main__":
    main()
