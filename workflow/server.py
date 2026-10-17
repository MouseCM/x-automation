"""Deviantly X Studio backend (FastAPI). Start it with `python3 studio.py`.

Serves the review UI from workflow/static and a JSON API under /api. Where things live:
  security.py        Host checks, cross-site protection, LAN access code and /login
  jobs.py            background work: crawl, auto-draft, publishing, scheduler, autopilot
  routes/jobs.py     /api/status, /api/crawl, /api/autodraft, /api/autopilot
  routes/topics.py   /api/topics (topics.csv)
  routes/drafts.py   /api/posts, /api/drafts (create, regenerate, edit, approve, schedule, reject)
  routes/settings.py /api/account (X sign-in, saved to .env), /api/brand, /api/persona
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import jobs, security, store
from .routes import ROUTERS

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.init()
    jobs.start_background()  # scheduler + autopilot threads (daemons: they stop with the server)
    yield


app = FastAPI(title="Deviantly X Studio", docs_url=None, redoc_url=None, lifespan=lifespan)
app.middleware("http")(security.guard)
app.include_router(security.router)
for router in ROUTERS:
    app.include_router(router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))
