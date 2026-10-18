# X Crawler: valuable X posts via search engines

> **Deviantly X Studio** (`python3 studio.py`) turns these posts into reviewed posts, quote posts, replies
> and reposts for deviantly.ai. See [Studio](#studio-draft-review-and-publish-to-x) below.

`search_crawler.py` collects X posts on your topics, from any account. It discovers posts through
`site:x.com` searches on several search engines, so it needs no X API. Results
are delayed because search engines index posts hours after they're posted.

Pipeline for each topic keyword:

1. **Discover**: each engine in `--engines` searches `site:x.com <keyword>`; results are merged
   (exact post time is decoded from the post ID).
2. **Enrich**: X's public embed endpoint gives full text, likes, replies and language (free).
3. **Score (0–100)**, for sorting only: rewards substance (long-form, numbers/metrics,
   lessons/how-to/breakdowns, lists, source links, likes and replies) and penalizes promo, spam,
   crypto/NFT shilling, hashtag/emoji stuffing and all caps. **Nothing is rejected**: every post found
   is saved, including replies, other languages, low scores, and posts whose embed lookup fails (those
   keep the search snippet as text).
4. **Summarize**: one sentence per post in the `summary` column. Uses Gemini
   (`gemini-3.1-flash-lite`, the lowest-cost model available to new API keys) when `GEMINI_API_KEY`
   is set, otherwise the post's cleaned-up lead sentence (free).

## Setup

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
```

Put your API keys in `.env` (it's git-ignored). All keys are optional; see `.env.example`:

```bash
GEMINI_API_KEY=...   # Gemini-written summaries (free key: https://aistudio.google.com/apikey)
SERPER_API_KEY=...   # search engine APIs, see "Search engines" below
```

The scripts load `.env` automatically; a variable already set with `export` takes priority.

## Run

```bash
python3 search_crawler.py --limit 100      # up to 100 new posts
python3 search_crawler.py --limit 0        # every post found
```

Only `--since` (post age) and `--limit` (count) limit what's saved. Posts already in the output file
are skipped. The 5 highest-scoring posts are printed at the end.

| Flag | Default | Description |
|---|---|---|
| `--engines` | `ddgs,duckduckgo,yahoo` | Comma-separated search engines (see below) |
| `--topics-file` | `topics.csv` | Topics file |
| `--topics` | all in file | Comma-separated subset of topics |
| `--limit` | `100` | Max new posts in total, split across topics; `0` = no limit |
| `--since` | `24` | Only keep posts newer than N hours |
| `--summary` | `auto` | `gemini`, `simple` (free extractive) or `off`; `auto` = Gemini if the key works |
| `--per-query` | `30` | Results requested per search query |
| `--delay` | `2.0` | Seconds between queries |
| `--out` | `post.csv` | Output file, appended to; known IDs are skipped |

CSV columns: `topic, id, created_at, username, display_name, likes, replies, score, reasons,
summary, text, url, query, source, discovered_at` (`source` = the engine that found the post).

A file written by an older version is converted to the current columns automatically (matched by
name; columns that no longer exist, like `followers`, are dropped).

## Search engines: `engines.py`

| Engine | Kind | Needs | Status in testing (Oct 2026) |
|---|---|---|---|
| `ddgs` | metasearch over the scraped engines below | nothing | works |
| `yahoo` | scraped (Bing-based results) | nothing | works, fastest |
| `duckduckgo` | scraped (Bing-based results) | nothing | works |
| `google` | scraped | nothing | returns nothing (blocked); use `serper` |
| `brave` | scraped | nothing | returns nothing (blocked); use `brave_api` |
| `startpage` | scraped (Google-based) | nothing | returns nothing (blocked) |
| `mojeek` | scraped (own index) | nothing | mostly times out |
| `bing`, `yandex` | scraped | — | disabled in the `ddgs` library; reported as skipped |
| `serper` | API, Google results | `SERPER_API_KEY` | 2,500 free queries |
| `brave_api` | API, Brave's own index | `BRAVE_API_KEY` | $5 free credit/month |
| `tavily` | API, AI search | `TAVILY_API_KEY` | 1,000 free credits/month |
| `exa` | API, neural search with date filter | `EXA_API_KEY` | pay as you go |
| `searxng` | your own self-hosted metasearch | `SEARXNG_URL` | free; aggregates 70+ engines |

API engines without a key are skipped with a message. To add an engine, write a function
`(terms, max_results, window) -> [{"url", "title", "snippet"}]` in `engines.py` and register it.

## Compare engines: `benchmark_engines.py`

Runs the same queries on every usable engine (one engine at a time, taking turns per query), then
checks every post found against X and a quality filter (on-topic, English, not a reply, score ≥ 45),
so engines are compared on useful posts, not just raw links:

```bash
python3 benchmark_engines.py                                   # all usable engines, 2 keywords per topic
python3 benchmark_engines.py --engines yahoo,serper,tavily --keywords-per-topic 4
```

| Column | Meaning |
|---|---|
| `ok` / `err` | Queries that succeeded / failed (blocked, timeout, bad key) |
| `sec/q` | Average seconds per query |
| `links` | x.com post links returned |
| `fresh` | Posts newer than `--since` |
| `live` | Fresh posts that still exist on X |
| `kept` | Live posts that pass the quality filter (`--min-score`, `--lang`) |
| `only` | Kept posts no other engine found |
| `kept/q` | Kept posts per query: **the efficiency number** |
| `age_h` | Median age in hours of fresh posts (lower = indexes faster) |

Each run is appended to `benchmark.csv`, so you can compare runs at different times of day.

Tuning the scoring: edit the regexes (`INSIGHT_RE`, `METRIC_RE`, `SPAM_RE`, `SHILL_RE`) and the weights
in `score_post()` in `search_crawler.py`. The Gemini prompt is `SYSTEM` in `summarize.py`;
set `GEMINI_MODEL` to use a different Gemini model.

## Summaries on their own: `summarize.py`

The crawler summarizes new posts automatically. Run `summarize.py` directly to test your key or
to (re)fill summaries for posts already in the CSV:

```bash
python3 summarize.py --text "We hit $12k MRR after 8 months..."   # test your Gemini key on one post
python3 summarize.py                                               # fill empty summaries in post.csv
python3 summarize.py --redo                                        # re-summarize every row
```

| Flag | Default | Description |
|---|---|---|
| `--text` | | Summarize one text and print it |
| `--in` | `post.csv` | CSV to update in place |
| `--redo` | off | Also re-summarize rows that already have a summary |
| `--mode` | `auto` | `gemini`, `simple` or `auto` |
| `--workers` | `4` | Parallel requests; lower it if you hit rate limits |

Ctrl+C saves the summaries finished so far. Temporary Gemini errors (rate limit, "high demand")
are retried; if they persist, that row gets the simple summary instead.

## Topics: `topics.csv`

Add a row to crawl a new topic, with no code changes:

```csv
topic,keywords,exclude,require
tech,AI|machine learning|LLM,giveaway|airdrop,
fintech,fintech|neobank|payments,,
trending,going viral|just announced,giveaway,AI|startup|app|tech
```

Included topics: `tech`, `startup`, `ecommerce`, `trending`, `high-technology`, `new-technology`.

- `keywords` are `|`-separated; each one becomes its own `site:x.com` search, and multi-word
  ones are searched as `"exact phrase"`. A post must mention at least one keyword to be kept.
- `exclude` (optional) words are negated, e.g. `-giveaway`.
- `require` (optional): a post must also mention one of these words. Use it for broad keywords
  like "going viral" or "just released" so they stay on tech/business topics.
- Lines starting with `#` are ignored.

## Studio: draft, review and publish to X

`studio.py` is a local web app on top of the crawler:

```
Run crawler ─▶ post.csv ─▶ Gemini drafts ─▶ you review / edit / regenerate ─▶ Approve ─▶ published on X
```

```bash
python3 -m pip install -r requirements.txt
python3 studio.py                 # opens http://127.0.0.1:8787
```

**Use it from another device on the same network** (phone, a teammate's laptop):

```bash
python3 studio.py --lan
```

It prints an access code and a link like `http://192.168.1.20:8787/login?code=...`. Opening the link signs that
browser in for 30 days. Your own machine never needs the code. Anyone with the code can publish to your X account,
and the traffic is plain HTTP, so only use `--lan` on a network you trust. Each run makes a new code (which signs
everyone out) unless you set `STUDIO_ACCESS_CODE=...` in `.env`. On macOS, allow Python when the firewall asks.

| Tab | What it does |
|---|---|
| **Crawled posts** | Every row of `post.csv`, with filters (topic, min score, search, "no drafts yet"). Select one to see it as an X post, plus the crawler's summary, score and reasons. Draft a **Post** (original post that shares the news and pitches Deviantly), **Quote post** (repost with your commentary), **Reply** (comment under it) or **Repost** (plain repost), with an optional instruction for Gemini. |
| **Review queue** | Drafts shown as they'll look on X, with an X-style character counter (links count 23). Edit inline, **Regenerate** with an instruction ("shorter", "mention the free trial"), **Reject**, or **Approve & publish**, which posts right away after a confirmation. **Auto-draft** writes drafts for the top-scoring posts that don't have one yet. |
| **Scheduled** | Drafts waiting for their time, soonest first. Change the time, edit the text, unschedule, or post now. |
| **Published** | What the Studio posted, with links, plus rejected drafts (restore or delete). **Write follow-up** on a published post (or **Follow up on another post** with a link to any of your posts) has Gemini draft a comment to post under it: an extra detail, the link, or a question. Only when you ask; the autopilot never writes follow-ups. It goes to To review like any draft. |
| **X account** | Connect the account that publishes (see below) and test the connection. |
| **Brand voice** | Two columns. **Brand brief** (`workflow/brand.md`): the only facts Gemini may use about Deviantly. **Persona** (`workflow/persona.md`): who is writing and how they sound, used for every post, quote and reply. An empty persona means a neutral voice. |

**Autopilot** (its own tab): every few hours it finds new posts (10 by default), has Gemini choose the one that
suits Deviantly best, writes a post, quote or reply about it, and posts it. Off until you switch it on. Guards:
it only runs in the hours you set, posts at most `daily cap` times a day, and anything with a fit below your
minimum (or everything, if you pick "let me approve it first") waits in To review instead. Every run is listed
with what it found, what it chose and why, and what it posted. Settings: `workflow/autopilot.json`.

**Models:** drafts and picks use `gemini-3.8-flash`, falling back to `gemini-3.7-flash` and then
`gemini-3.1-flash-lite` when a model is overloaded. Pro models need billing on your Google AI account; to use
one, set `GEMINI_DRAFT_MODEL=gemini-3.1-pro-preview,gemini-3.8-flash` in `.env`.

**Scheduling:** any draft (post, quote, reply or repost) can be scheduled instead of posted right away.
The Studio publishes scheduled drafts itself, so it has to be running. A draft that came due while it was
closed goes out on the next start if it's under 6 hours late; older ones are marked as not posted so you decide.

**Find new posts** (top right) runs `search_crawler.py` in the background with a live log, and can auto-draft
the best new posts when it finishes. Nothing is ever published without your approval.

Gemini also rates each draft's **fit** (0–10): how naturally the source post connects to Deviantly.
Low-fit posts are usually better as plain reposts or skipped. Gemini only uses facts from the source post
and the brand brief, and treats the crawled post as untrusted text.

### Connecting X

Pick a mode on the **X account** tab (saved to `.env`; secrets are never shown again):

| Mode | Needs | Can do | Notes |
|---|---|---|---|
| `api` | `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET` from [developer.x.com](https://developer.x.com) (app with **Read and write**) | posts, reposts; replies/quotes only where allowed | Official and the most reliable. Pay-per-use billing; posts with a link cost more. Self-serve plans reject API replies and quotes to accounts that didn't mention you. |
| `cookies` | `auth_token` and `ct0` cookies from a browser logged in to x.com | posts, quotes, replies, reposts | Unofficial (twikit). |
| `password` | username, email, password, optional 2FA TOTP secret | posts, quotes, replies, reposts | Unofficial (twikit). Logs in once and saves the session to `workflow/.x_session.json`. |

`cookies` and `password` drive X's web app through [twikit](https://github.com/d60/twikit). That's against
X's automation rules and can get the account locked, so keep volume low. twikit 2.3.3 can't currently sign
requests against X's web app ([twikit#408](https://github.com/d60/twikit/issues/408)). `workflow/publisher.py`
patches around it and sends requests unsigned, but X may reject those. If publishing fails in these modes, use `api`.

### Files

| File | Purpose |
|---|---|
| `studio.py` | Starts the app (local only, `127.0.0.1`; `--lan` shares it on your network with an access code) |
| `config.py` | File paths and every `.env` setting in one place (loads `.env` on import) |
| `.env.example` | Every supported `.env` key, documented. Copy it to `.env` |
| `workflow/server.py` | Creates the FastAPI app: middleware, routers, static UI, starts background loops |
| `workflow/security.py` | Host checks, cross-site protection, LAN access code and `/login` |
| `workflow/jobs.py` | Background work: crawl, auto-draft, publishing, scheduler, autopilot wiring |
| `workflow/routes/` | API endpoints, one module per area: `jobs`, `topics`, `drafts`, `settings` |
| `workflow/drafter.py` | Gemini prompts for choosing a post and writing post / quote / reply; `GEMINI_DRAFT_MODEL` sets the model chain |
| `workflow/autopilot.py` | The autopilot loop and its guards |
| `workflow/publisher.py` | Publishing through the X API or twikit |
| `workflow/store.py` | Drafts in SQLite (`workflow/studio.db`, git-ignored) |
| `workflow/brand.md` | Brand brief Gemini writes from |
| `workflow/persona.md` | Persona (voice) Gemini writes in |
| `workflow/static/` | The UI (plain HTML/CSS/JS) |

Optional `.env` settings: `X_MAX_CHARS` (default 280; raise it for X Premium long posts), `GEMINI_DRAFT_MODEL` and
`STUDIO_PORT`. See `.env.example` for all of them.
