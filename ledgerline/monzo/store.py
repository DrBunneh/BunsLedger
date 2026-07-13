"""Persistence + token-refresh for Monzo auth (single row in monzo_auth)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import client, sync


def load(conn) -> dict | None:
    row = conn.execute("SELECT * FROM monzo_auth WHERE id=1").fetchone()
    return dict(row) if row else None


def save(conn, **fields) -> None:
    cur = load(conn)
    fields["updated_at"] = sync.now_iso()
    if cur is None:
        cols = ", ".join(["id"] + list(fields))
        conn.execute(f"INSERT INTO monzo_auth ({cols}) VALUES (1, {', '.join('?' * len(fields))})",
                     list(fields.values()))
    else:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE monzo_auth SET {sets} WHERE id=1", list(fields.values()))
    conn.commit()


def store_token_response(conn, resp: dict) -> None:
    expires = datetime.now(timezone.utc) + timedelta(seconds=int(resp.get("expires_in", 0)))
    save(conn, access_token=resp.get("access_token"), refresh_token=resp.get("refresh_token"),
         expires_at=expires.isoformat(timespec="seconds"), user_id=resp.get("user_id"))


def _expired(expires_at: str | None) -> bool:
    if not expires_at:
        return True
    return datetime.now(timezone.utc) >= datetime.fromisoformat(expires_at) - timedelta(minutes=2)


def valid_token(conn, settings) -> str | None:
    """Return a live access token, refreshing if near expiry. None if not connected."""
    auth = load(conn)
    if not auth or not auth.get("access_token"):
        return None
    if _expired(auth.get("expires_at")) and auth.get("refresh_token"):
        resp = client.refresh(settings.monzo_client_id, settings.monzo_client_secret,
                              auth["refresh_token"])
        store_token_response(conn, resp)
        return resp.get("access_token")
    return auth["access_token"]
