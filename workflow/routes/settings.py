"""X account sign-in (saved to .env), the brand brief and the persona."""

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import save_env

from .. import publisher
from ..drafter import load_brand, load_persona, save_brand, save_persona

router = APIRouter(prefix="/api")


class AccountIn(BaseModel):
    mode: str
    values: dict[str, str] = {}


@router.get("/account")
def account():
    return {"mode": publisher.current_mode(), "fields": publisher.status_fields(),
            "session_saved": os.path.exists(publisher.SESSION_PATH)}


@router.post("/account")
def save_account(body: AccountIn):
    if body.mode not in publisher.MODES:
        raise HTTPException(400, "Unknown sign-in method")
    allowed = set(publisher.FIELDS[body.mode])
    # Blank secret fields mean "keep what's saved" (the UI never receives secrets back).
    values = {k: v.strip() for k, v in body.values.items() if k in allowed and (v.strip() or k not in publisher.SECRET)}
    values["X_AUTH_MODE"] = body.mode
    if body.mode == "password":
        publisher.clear_session()  # new credentials -> log in fresh next time
    save_env(values)
    return account()


@router.post("/account/verify")
def verify_account():
    try:
        return {"ok": True, **publisher.verify()}
    except publisher.PublishError as e:
        return {"ok": False, "error": str(e)}


@router.post("/account/disconnect")
def disconnect():
    keys = [k for ks in publisher.FIELDS.values() for k in ks] + ["X_AUTH_MODE"]
    save_env({k: "" for k in keys})
    publisher.clear_session()
    return account()


class TextIn(BaseModel):
    text: str


@router.get("/persona")
def persona():
    return {"text": load_persona()}


@router.put("/persona")
def put_persona(body: TextIn):
    save_persona(body.text)
    return {"text": load_persona()}


@router.get("/brand")
def brand():
    return {"text": load_brand()}


@router.put("/brand")
def put_brand(body: TextIn):
    save_brand(body.text)
    return {"text": load_brand()}
