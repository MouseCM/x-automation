"""Search engines that can discover X posts (`site:x.com <terms>`).

Every engine is a function (terms, max_results, window) -> list of {"url", "title", "snippet"}.
"terms" is the query without the site: part (e.g. `startup -giveaway`); each engine restricts to
x.com in its own way. Engines raise EngineError on failure and return [] when nothing matched.

Scraped engines (free, no key, can be rate-limited or blocked; via the `ddgs` library):
  ddgs        metasearch: queries several engines below and merges results (the original default)
  google, brave, duckduckgo, mojeek, yahoo, startpage
  bing, yandex  listed but disabled in the current ddgs version (it silently fell back to "auto"),
                so they're reported as unavailable. Yahoo and DuckDuckGo serve Bing-based results;
                for Google results through an API use serper.

API engines (official, need a key or URL in .env):
  brave_api   Brave Search API             BRAVE_API_KEY    $5/month free credit
  serper      Google results via Serper    SERPER_API_KEY   2,500 free queries
  tavily      Tavily search API            TAVILY_API_KEY   1,000 free credits/month
  exa         Exa search API               EXA_API_KEY      pay as you go
  searxng     your own SearXNG instance    SEARXNG_URL      free, self-hosted (aggregates 70+ engines)
"""

import os
from datetime import datetime, timedelta, timezone

import httpx

HTTP = httpx.Client(timeout=20, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)


class EngineError(Exception):
    pass


def _bucket(window: timedelta, day: str, week: str, month: str) -> str:
    return day if window <= timedelta(days=1) else week if window <= timedelta(days=7) else month


# --------------------------------------------------------------------------- scraped (ddgs)

SCRAPED = ["google", "bing", "brave", "duckduckgo", "yandex", "mojeek", "yahoo", "startpage"]


def _ddgs_enabled() -> set[str]:
    from ddgs.engines import ENGINES as DDGS_ENGINES

    return set(DDGS_ENGINES.get("text", {}))


def _ddgs(backend: str):
    def search(terms: str, max_results: int, window: timedelta) -> list[dict]:
        from ddgs import DDGS
        from ddgs.exceptions import DDGSException

        # ddgs silently switches to "auto" for an unknown/disabled backend; refuse instead
        if backend != "auto" and backend not in _ddgs_enabled():
            raise EngineError(f"{backend} is disabled in the installed ddgs version")

        try:
            results = DDGS(timeout=15).text(
                f"site:x.com {terms}", backend=backend, max_results=max_results,
                timelimit=_bucket(window, "d", "w", "m"),
            )
        except DDGSException as e:
            if "No results" in str(e):
                return []
            raise EngineError(str(e)[:150]) from e
        except Exception as e:
            raise EngineError(f"{type(e).__name__}: {str(e)[:150]}") from e
        return [{"url": r.get("href", ""), "title": r.get("title", ""), "snippet": r.get("body", "")} for r in results]

    return search


# --------------------------------------------------------------------------- APIs

def _post_json(url: str, body: dict, headers: dict) -> dict:
    try:
        r = HTTP.post(url, json=body, headers=headers)
    except httpx.HTTPError as e:
        raise EngineError(f"{type(e).__name__}: {e}") from e
    if r.status_code != 200:
        raise EngineError(f"{r.status_code} {r.text[:150].strip()}")
    return r.json()


def _key(name: str) -> str:
    key = os.environ.get(name)
    if not key:
        raise EngineError(f"{name} is not set")
    return key


def brave_api(terms: str, max_results: int, window: timedelta) -> list[dict]:
    try:
        r = HTTP.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": f"site:x.com {terms}", "count": min(max_results, 20),
                    "freshness": _bucket(window, "pd", "pw", "pm")},
            headers={"Accept": "application/json", "X-Subscription-Token": _key("BRAVE_API_KEY")},
        )
    except httpx.HTTPError as e:
        raise EngineError(str(e)) from e
    if r.status_code != 200:
        raise EngineError(f"{r.status_code} {r.text[:150].strip()}")
    return [
        {"url": x.get("url", ""), "title": x.get("title", ""), "snippet": x.get("description", "")}
        for x in r.json().get("web", {}).get("results", [])
    ]


def serper(terms: str, max_results: int, window: timedelta) -> list[dict]:
    data = _post_json(
        "https://google.serper.dev/search",
        {"q": f"site:x.com {terms}", "num": min(max_results, 100), "tbs": _bucket(window, "qdr:d", "qdr:w", "qdr:m")},
        {"X-API-KEY": _key("SERPER_API_KEY")},
    )
    return [{"url": x.get("link", ""), "title": x.get("title", ""), "snippet": x.get("snippet", "")}
            for x in data.get("organic", [])]


def tavily(terms: str, max_results: int, window: timedelta) -> list[dict]:
    data = _post_json(
        "https://api.tavily.com/search",
        {"query": terms, "include_domains": ["x.com", "twitter.com"], "max_results": min(max_results, 20),
         "time_range": _bucket(window, "day", "week", "month")},
        {"Authorization": f"Bearer {_key('TAVILY_API_KEY')}"},
    )
    return [{"url": x.get("url", ""), "title": x.get("title", ""), "snippet": x.get("content", "")}
            for x in data.get("results", [])]


def exa(terms: str, max_results: int, window: timedelta) -> list[dict]:
    since = (datetime.now(timezone.utc) - window).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = _post_json(
        "https://api.exa.ai/search",
        {"query": terms, "includeDomains": ["x.com", "twitter.com"], "numResults": min(max_results, 100),
         "startPublishedDate": since, "type": "auto"},
        {"x-api-key": _key("EXA_API_KEY")},
    )
    return [{"url": x.get("url", ""), "title": x.get("title") or "", "snippet": x.get("text") or ""}
            for x in data.get("results", [])]


def searxng(terms: str, max_results: int, window: timedelta) -> list[dict]:
    base = _key("SEARXNG_URL").rstrip("/")
    try:
        r = HTTP.get(f"{base}/search", params={
            "q": f"site:x.com {terms}", "format": "json", "time_range": _bucket(window, "day", "week", "month"),
        })
    except httpx.HTTPError as e:
        raise EngineError(str(e)) from e
    if r.status_code != 200:
        raise EngineError(f"{r.status_code} (is format=json enabled in settings.yml?)")
    return [{"url": x.get("url", ""), "title": x.get("title", ""), "snippet": x.get("content", "")}
            for x in r.json().get("results", [])[:max_results]]


API_ENGINES = {
    "brave_api": (brave_api, "BRAVE_API_KEY"),
    "serper": (serper, "SERPER_API_KEY"),
    "tavily": (tavily, "TAVILY_API_KEY"),
    "exa": (exa, "EXA_API_KEY"),
    "searxng": (searxng, "SEARXNG_URL"),
}

ENGINES = {"ddgs": _ddgs("auto"), **{name: _ddgs(name) for name in SCRAPED},
           **{name: fn for name, (fn, _) in API_ENGINES.items()}}


def available(names: list[str] | None = None) -> tuple[list[str], dict[str, str]]:
    """Split engine names into usable ones and skipped ones (with the reason, e.g. missing key)."""
    names = names or list(ENGINES)
    ok, skipped = [], {}
    for n in names:
        if n not in ENGINES:
            raise SystemExit(f"unknown engine {n!r} (available: {', '.join(ENGINES)})")
        env = API_ENGINES.get(n, (None, None))[1]
        if env and not os.environ.get(env):
            skipped[n] = f"set {env} in .env"
        elif n in SCRAPED and n not in _ddgs_enabled():
            skipped[n] = "disabled in the installed ddgs version (no working scraper)"
        else:
            ok.append(n)
    return ok, skipped
