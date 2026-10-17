"""Who may talk to the Studio: Host checks, cross-site protection, and the LAN access code.

By default the Studio listens on 127.0.0.1 only. `studio.py --lan` sets STUDIO_LAN=1 and STUDIO_ACCESS_CODE,
so other devices on the network may connect, but only after signing in with the code. Requests from this
machine itself (loopback) never need it.
"""

import hashlib
import hmac
import ipaddress
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from config import env

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
LAN = env("STUDIO_LAN") == "1"
ACCESS_CODE = env("STUDIO_ACCESS_CODE")
AUTH_COOKIE = "studio_auth"
# The cookie holds an HMAC of the code, not the code; a new code (restart) signs everyone out.
AUTH_TOKEN = hmac.new(ACCESS_CODE.encode(), b"deviantly-studio", hashlib.sha256).hexdigest() if ACCESS_CODE else ""

router = APIRouter()


def host_allowed(host: str) -> bool:
    """Loopback names always; in LAN mode also private IP literals and mDNS names (my-mac.local).

    Any other name could be a DNS-rebinding domain pointing at this machine, so it stays blocked.
    """
    if host in LOOPBACK_HOSTS:
        return True
    if not LAN:
        return False
    if host.endswith(".local"):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_private
    except ValueError:
        return False


def _is_loopback(request: Request) -> bool:
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except (AttributeError, ValueError):
        return False


def authed(request: Request) -> bool:
    return _is_loopback(request) or (
        bool(AUTH_TOKEN) and hmac.compare_digest(request.cookies.get(AUTH_COOKIE, ""), AUTH_TOKEN))


async def guard(request: Request, call_next):
    """Block DNS-rebinding (Host check) and cross-site form posts (custom header forces a CORS preflight).

    In LAN mode, devices other than this one must also sign in with the access code (see /login).
    """
    host = (request.headers.get("host") or "").rsplit(":", 1)[0]
    if not host_allowed(host):
        return JSONResponse({"detail": "forbidden host"}, status_code=403)
    if request.url.path == "/login":
        return await call_next(request)
    if not authed(request):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "sign in with the access code"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    if request.method not in ("GET", "HEAD") and request.headers.get("x-studio") != "1":
        return JSONResponse({"detail": "missing X-Studio header"}, status_code=403)
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"  # UI edits show up on a normal reload
    return response


# --------------------------------------------------------------------------- LAN sign-in page

LOGIN_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Deviantly X Studio</title>
<style>body{font:16px system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0;
background:#0f0f10;color:#eee}form{display:grid;gap:12px;width:min(320px,90vw)}
input,button{font:inherit;padding:10px 14px;border-radius:999px;border:1px solid #444;background:#1b1b1d;color:inherit}
button{background:#eee;color:#111;border:0;cursor:pointer}p{margin:0;color:#f77}</style></head>
<body><form method="post" action="/login"><h2 style="margin:0">Deviantly X Studio</h2>
<input name="code" placeholder="Access code" autocomplete="off" autofocus required>%s
<button>Sign in</button></form></body></html>"""


def _signed_in(code: str) -> RedirectResponse | None:
    if not (AUTH_TOKEN and hmac.compare_digest(code.strip(), ACCESS_CODE)):
        return None
    res = RedirectResponse("/", status_code=303)
    res.set_cookie(AUTH_COOKIE, AUTH_TOKEN, max_age=30 * 86400, httponly=True, samesite="strict")
    return res


@router.get("/login")
def login_page(code: str = ""):
    """The link `studio.py --lan` prints carries ?code=, so opening it signs you in directly."""
    return (code and _signed_in(code)) or HTMLResponse(LOGIN_PAGE % ("<p>Wrong code</p>" if code else ""))


@router.post("/login")
async def login(request: Request):
    code = parse_qs((await request.body()).decode()).get("code", [""])[0]
    return _signed_in(code) or HTMLResponse(LOGIN_PAGE % "<p>Wrong code</p>", status_code=401)
