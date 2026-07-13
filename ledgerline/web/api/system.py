"""Health, auth, and app metadata endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ... import __version__
from ..auth import COOKIE_NAME, auth_enabled, issue_session, require_session, verify_passphrase
from ..config import settings
from ..deps import get_conn

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, "auth_enabled": auth_enabled()}


class LoginBody(BaseModel):
    passphrase: str = ""


@router.post("/auth/login")
def login(body: LoginBody, response: Response) -> dict:
    if not verify_passphrase(body.passphrase):
        raise HTTPException(status_code=401, detail="incorrect passphrase")
    response.set_cookie(
        COOKIE_NAME, issue_session(), httponly=True, samesite="lax",
        max_age=settings.session_max_age,
    )
    return {"ok": True}


@router.post("/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
def me(_: None = Depends(require_session)) -> dict:
    return {"authenticated": True}


@router.get("/summary")
def summary(_: None = Depends(require_session), conn=Depends(get_conn)) -> dict:
    """Lightweight dashboard header: counts + coverage. Empty-safe on a fresh DB."""
    row = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(is_transfer),0) t, "
        "COALESCE(SUM(needs_review),0) r, MIN(posting_date) lo, MAX(posting_date) hi "
        "FROM transactions"
    ).fetchone()
    return {"transactions": row["n"], "transfers": row["t"], "needs_review": row["r"],
            "date_from": row["lo"], "date_to": row["hi"]}
