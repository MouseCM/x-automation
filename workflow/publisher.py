"""Publish approved drafts to X.

Three ways to sign in (X_AUTH_MODE in .env, or set on the Account tab):

  api       Official X API v2, OAuth 1.0a user tokens (X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN,
            X_ACCESS_TOKEN_SECRET). Most reliable for original posts. Self-serve tiers block replies and
            quote posts to accounts that didn't mention you, so use cookies mode for those.
  cookies   Your logged-in browser session cookies (X_AUTH_TOKEN and X_CT0), via the unofficial
            twikit library. Can do posts, quotes, replies and reposts.
  password  Username/email/password (X_USERNAME, X_EMAIL, X_PASSWORD, optional X_TOTP_SECRET), via twikit.
            Logs in once and saves the session to workflow/.x_session.json; later actions reuse it.

twikit drives X's web app, which is against X's automation rules and can get an account locked:
keep the volume human, and prefer api mode for plain posts.
"""

import asyncio
import re
import os

SESSION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".x_session.json")

MODES = ("api", "cookies", "password")
FIELDS = {
    "api": ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"],
    "cookies": ["X_AUTH_TOKEN", "X_CT0"],
    "password": ["X_USERNAME", "X_EMAIL", "X_PASSWORD", "X_TOTP_SECRET"],
}
OPTIONAL = {"X_TOTP_SECRET", "X_EMAIL"}
SECRET = {"X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET", "X_AUTH_TOKEN", "X_CT0", "X_PASSWORD", "X_TOTP_SECRET"}


class PublishError(Exception):
    pass


def _env(k: str) -> str:
    return (os.environ.get(k) or "").strip()


def configured(mode: str) -> bool:
    return all(_env(k) for k in FIELDS[mode] if k not in OPTIONAL)


def current_mode() -> str | None:
    """X_AUTH_MODE if set, otherwise the first mode whose credentials are all present."""
    mode = _env("X_AUTH_MODE").lower()
    if mode in MODES:
        return mode
    return next((m for m in MODES if configured(m)), None)


def status_fields() -> dict:
    """What's configured, safe to show in the UI: secrets are never returned, only whether they're set."""
    out = {}
    for mode, keys in FIELDS.items():
        out[mode] = {k: ({"set": bool(_env(k))} if k in SECRET else {"set": bool(_env(k)), "value": _env(k)})
                     for k in keys}
    return out


def post_url(post_id: str) -> str:
    return f"https://x.com/i/web/status/{post_id}"


# --------------------------------------------------------------------------- official API (tweepy)

def _tweepy():
    import tweepy

    return tweepy.Client(
        consumer_key=_env("X_API_KEY"), consumer_secret=_env("X_API_SECRET"),
        access_token=_env("X_ACCESS_TOKEN"), access_token_secret=_env("X_ACCESS_TOKEN_SECRET"),
    )


def _api_error(e: Exception) -> PublishError:
    import tweepy

    if isinstance(e, tweepy.HTTPException):
        msgs = "; ".join(str(m) for m in (e.api_messages or [])) or str(e)
        hint = ""
        if e.response is not None and e.response.status_code == 403 and "reply" in msgs.lower():
            hint = " (self-serve X API only allows replies/quotes to accounts that mentioned you; use cookies mode)"
        return PublishError(f"X API {e.response.status_code if e.response is not None else ''}: {msgs}{hint}")
    return PublishError(f"{type(e).__name__}: {e}")


def _api_verify() -> str:
    try:
        return _tweepy().get_me(user_auth=True).data.username
    except Exception as e:
        raise _api_error(e) from e


def _api_publish(kind: str, text: str, source_id: str) -> str | None:
    c = _tweepy()
    try:
        if kind == "repost":
            c.retweet(source_id)
            return None
        kwargs = {"text": text}
        if kind == "reply":
            kwargs["in_reply_to_tweet_id"] = source_id
        elif kind == "quote":
            kwargs["quote_tweet_id"] = source_id
        return str(c.create_tweet(**kwargs).data["id"])
    except Exception as e:
        raise _api_error(e) from e


# --------------------------------------------------------------------------- web session (twikit)

_patched = False
_unsigned = False  # set when twikit had to send requests without a transaction id


def _patch_twikit() -> None:
    """Work around twikit 2.3.3's "Couldn't get KEY_BYTE indices" (https://github.com/d60/twikit/issues/408).

    twikit signs each request with an X-Client-Transaction-Id derived from X's `ondemand.s` script.
    1. The early-2026 page format maps chunk names to ids (`,123:"ondemand.s"` + `,123:"<hash>"`); look the
       hash up in both the old and that format.
    2. The current logged-out x.com (Oct 2026) no longer references ondemand.s at all. Then send requests
       without the header instead of crashing, so you get X's actual answer.
    Once twikit ships a fix, step 1 finds the file the normal way and none of this changes anything.
    """
    global _patched
    if _patched:
        return
    from twikit.x_client_transaction import transaction as tx

    original_init = tx.ClientTransaction.init
    original_generate = tx.ClientTransaction.generate_transaction_id

    async def init(self, session, headers):
        try:
            await original_init(self, session, headers)
            self.unsigned = False
        except Exception:
            global _unsigned
            self.home_page_response, self.unsigned, _unsigned = True, True, True  # truthy: don't retry per request

    def generate_transaction_id(self, *args, **kwargs):
        return "" if getattr(self, "unsigned", False) else original_generate(self, *args, **kwargs)

    tx.ClientTransaction.init = init
    tx.ClientTransaction.generate_transaction_id = generate_transaction_id

    old_file_re = tx.ON_DEMAND_FILE_REGEX
    new_file_re = re.compile(r""",(\d+):["']ondemand\.s["']""")

    def ondemand_hash(page: str) -> str | None:
        if m := old_file_re.search(page):
            return m.group(1)
        if m := new_file_re.search(page):
            if h := re.search(rf',{m.group(1)}:"([0-9a-f]+)"', page):
                return h.group(1)
        return None

    async def get_indices(self, home_page_response, session, headers):
        page = str(self.validate_response(home_page_response) or self.home_page_response)
        file_hash = ondemand_hash(page)
        indices = []
        if file_hash:
            url = f"https://abs.twimg.com/responsive-web/client-web/ondemand.s.{file_hash}a.js"
            js = (await session.request(method="GET", url=url, headers=headers)).text
            indices = [int(m.group(2)) for m in tx.INDICES_REGEX.finditer(js)]
        if not indices:
            raise RuntimeError("couldn't find X's transaction-id key indices")
        return indices[0], indices[1:]

    tx.ClientTransaction.get_indices = get_indices
    _patched = True


async def _twikit_client(mode: str):
    import twikit

    _patch_twikit()
    client = twikit.Client("en-US")
    if mode == "cookies":
        client.set_cookies({"auth_token": _env("X_AUTH_TOKEN"), "ct0": _env("X_CT0")})
    else:
        # With cookies_file, twikit logs in only when the saved session doesn't exist yet.
        await client.login(
            auth_info_1=_env("X_USERNAME"), auth_info_2=_env("X_EMAIL") or None, password=_env("X_PASSWORD"),
            totp_secret=_env("X_TOTP_SECRET") or None, cookies_file=SESSION_PATH,
        )
    return client


# twikit's User/Tweet classes expect X's older response layout (e.g. KeyError: 'urls' on profiles), and
# create_tweet() parses the new post only AFTER X has published it. So these use twikit's raw calls and read
# the few fields needed from the JSON; otherwise a successful post would be reported as failed.

async def _twikit_verify(mode: str) -> str:
    client = await _twikit_client(mode)
    settings, _ = await client.v11.settings()
    if not isinstance(settings, dict) or not settings.get("screen_name"):
        raise PublishError(f"Unexpected response from X account settings: {str(settings)[:200]}")
    return settings["screen_name"]


def _new_post_id(response: dict) -> str | None:
    """rest_id of the post X just created, from either the normal or long-post (note tweet) response."""
    data = response.get("data") or {}
    results = (data.get("create_tweet") or data.get("notetweet_create") or {}).get("tweet_results") or {}
    result = results.get("result") or {}
    result = result.get("tweet", result)  # TweetWithVisibilityResults wraps the post
    return result.get("rest_id") or (result.get("legacy") or {}).get("id_str")


async def _twikit_publish(mode: str, kind: str, text: str, source_id: str, source_url: str) -> str | None:
    from twikit.errors import raise_exceptions_from_response

    client = await _twikit_client(mode)
    if kind == "repost":
        await client.retweet(source_id)
        return None
    response, _ = await client.gql.create_tweet(
        False, text, [], None,                            # is_note_tweet, text, media, poll
        source_id if kind == "reply" else None,           # reply_to
        source_url if kind == "quote" else None,          # attachment_url (makes it a quote post)
        None, False, None, None, None,                    # community, share_with_followers, richtext, edit, limit_mode
    )
    if not isinstance(response, dict):
        raise PublishError(f"Unexpected response from X: {str(response)[:200]}")
    if response.get("errors"):
        raise_exceptions_from_response(response["errors"])
        raise PublishError(f"X refused the post: {response['errors'][0].get('message', response['errors'][0])}")
    # No errors means X accepted it. If the id isn't where we expect, still report success (never retry a post).
    return _new_post_id(response) or ""


def _run(coro):
    try:
        return asyncio.run(coro)
    except PublishError:
        raise
    except Exception as e:
        msg = " ".join(f"{type(e).__name__}: {e}".split())
        if type(e).__name__ == "Unauthorized":
            msg += " (X rejected the session: the cookies or password are wrong or expired)"
        if _unsigned:
            msg += (" [twikit can't sign requests against X's current web app (twikit issue #408), so they were sent"
                    " unsigned; if this keeps failing, use api mode or update twikit]")
        raise PublishError(msg) from e


# --------------------------------------------------------------------------- public

def _require_mode() -> str:
    mode = current_mode()
    if not mode:
        raise PublishError("No X account connected. Add credentials on the Account tab.")
    if not configured(mode):
        missing = [k for k in FIELDS[mode] if k not in OPTIONAL and not _env(k)]
        raise PublishError(f"{mode} mode is missing {', '.join(missing)}")
    return mode


def verify() -> dict:
    """Sign in and return {"mode", "username"}; raises PublishError."""
    mode = _require_mode()
    username = _api_verify() if mode == "api" else _run(_twikit_verify(mode))
    return {"mode": mode, "username": username}


def publish(kind: str, text: str, source_id: str, source_url: str) -> dict:
    """Publish one draft. Returns {"id", "url"} of the new post (the source post for a plain repost)."""
    mode = _require_mode()
    if kind != "repost" and not text.strip():
        raise PublishError("Draft text is empty")
    if mode == "api":
        new_id = _api_publish(kind, text, source_id)
    else:
        new_id = _run(_twikit_publish(mode, kind, text, source_id, source_url))
    if new_id is None:  # plain repost: link to the post we reposted
        return {"id": source_id, "url": source_url}
    if not new_id:  # posted, but X's response didn't include the new id: link to the account's posts
        return {"id": "", "url": "https://x.com/home"}
    return {"id": new_id, "url": post_url(new_id)}


def clear_session() -> bool:
    """Forget the saved password-mode session so the next action logs in again."""
    if os.path.exists(SESSION_PATH):
        os.remove(SESSION_PATH)
        return True
    return False
