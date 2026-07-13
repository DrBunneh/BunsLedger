"""Passphrase gate for the local UI. Financial data on localhost still deserves a
lock. If no passphrase is configured, the app runs unlocked (dev mode)."""
from __future__ import annotations

import hmac

from fastapi import Cookie, HTTPException
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

COOKIE_NAME = "ledgerline_session"
_serializer = URLSafeTimedSerializer(settings.secret_key, salt="ledgerline-session")


def auth_enabled() -> bool:
    return settings.passphrase is not None


def verify_passphrase(candidate: str) -> bool:
    if not auth_enabled():
        return True
    return hmac.compare_digest(candidate or "", settings.passphrase or "")


def issue_session() -> str:
    return _serializer.dumps({"ok": True})


def _session_valid(token: str | None) -> bool:
    if not auth_enabled():
        return True
    if not token:
        return False
    try:
        _serializer.loads(token, max_age=settings.session_max_age)
        return True
    except (BadSignature, SignatureExpired):
        return False


def require_session(ledgerline_session: str | None = Cookie(default=None)) -> None:
    """FastAPI dependency: 401 unless authenticated (or auth disabled)."""
    if not _session_valid(ledgerline_session):
        raise HTTPException(status_code=401, detail="authentication required")
