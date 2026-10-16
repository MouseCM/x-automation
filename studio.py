#!/usr/bin/env python3
"""Deviantly X Studio: review UI for turning crawled X posts into posts, quotes, replies and reposts.

    python3 studio.py              # http://127.0.0.1:8787 (opens your browser)
    python3 studio.py --port 9000 --no-browser
"""

import argparse
import threading
import webbrowser

import uvicorn


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--no-browser", action="store_true", help="don't open the browser")
    args = p.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    print(f"Deviantly X Studio on {url}  (Ctrl+C to stop)")
    # Local only: the Studio holds your X session, so it never listens on other interfaces.
    uvicorn.run("workflow.server:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
