"""API routes, one module per tab of the UI. server.py mounts every `router` listed in ROUTERS."""

from . import drafts, jobs, settings, topics

ROUTERS = [jobs.router, topics.router, drafts.router, settings.router]
