#!/usr/bin/env python3
"""Deviantly X Studio: review UI for turning crawled X posts into posts, quotes, replies and reposts.

    python3 studio.py              # http://127.0.0.1:8787 (opens your browser)
    python3 studio.py --port 9000 --no-browser
    python3 studio.py --lan        # also reachable from other devices on your network, with an access code
    python3 studio.py --local      # this computer only, even when STUDIO_LAN=1 is set in .env
"""

import argparse
import os
import secrets
import socket
import threading
import webbrowser

import uvicorn

from config import env, save_env


def lan_ip() -> str | None:
    """This machine's address on the local network (the interface used for outbound traffic)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # UDP connect sends nothing; it only picks a route
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=int(env("STUDIO_PORT", "8787")))
    p.add_argument("--no-browser", action="store_true", help="don't open the browser")
    p.add_argument("--lan", action="store_true", default=env("STUDIO_LAN") == "1",
                   help="listen on all interfaces so other devices on your network can open it (access code "
                        "required). STUDIO_LAN=1 in .env makes this the default")
    p.add_argument("--local", dest="lan", action="store_false", help="this computer only, even if STUDIO_LAN=1")
    args = p.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    print(f"Deviantly X Studio on {url}  (Ctrl+C to stop)")

    host = "127.0.0.1"  # Local only by default: the Studio holds your X session and can publish.
    os.environ["STUDIO_LAN"] = "0"  # what workflow/security.py checks; set to 1 below in LAN mode
    if args.lan:
        # The code is saved to .env on first use, so signed-in devices stay signed in across restarts.
        # To sign everyone out, clear STUDIO_ACCESS_CODE in .env (a new one is made next start).
        code = env("STUDIO_ACCESS_CODE")
        if not code:
            code = secrets.token_urlsafe(9)
            save_env({"STUDIO_ACCESS_CODE": code}, section="# Studio server")
        os.environ["STUDIO_LAN"], os.environ["STUDIO_ACCESS_CODE"] = "1", code
        host = "0.0.0.0"
        ip = lan_ip()
        print("\nLAN mode: anyone on this network with the access code can draft AND publish to your X account.")
        print(f"  Access code: {code}")
        if ip:
            print(f"  Open on another device: http://{ip}:{args.port}/login?code={code}")
        print(f"  Or: http://{socket.gethostname().removesuffix('.local')}.local:{args.port}\n")
    uvicorn.run("workflow.server:app", host=host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
