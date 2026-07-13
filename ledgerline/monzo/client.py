"""Thin Monzo API client: OAuth confidential-client flow + read endpoints.

Docs: https://docs.monzo.com. httpx is imported lazily so the rest of the package
stays importable without the optional dependency.
"""
from __future__ import annotations

import secrets
from urllib.parse import urlencode

AUTH_URL = "https://auth.monzo.com/"
API = "https://api.monzo.com"


def authorize_url(client_id: str, redirect_uri: str, state: str | None = None) -> tuple[str, str]:
    """Return (url, state). Send the user here; Monzo redirects back with ?code=&state=."""
    state = state or secrets.token_urlsafe(16)
    q = {"client_id": client_id, "redirect_uri": redirect_uri,
         "response_type": "code", "state": state}
    return f"{AUTH_URL}?{urlencode(q)}", state


def _post(path: str, data: dict) -> dict:
    import httpx
    r = httpx.post(f"{API}{path}", data=data, timeout=30)
    r.raise_for_status()
    return r.json()


def _get(path: str, token: str, params: dict | None = None) -> dict:
    import httpx
    r = httpx.get(f"{API}{path}", headers={"Authorization": f"Bearer {token}"},
                  params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()


def exchange_code(client_id: str, client_secret: str, redirect_uri: str, code: str) -> dict:
    return _post("/oauth2/token", {
        "grant_type": "authorization_code", "client_id": client_id,
        "client_secret": client_secret, "redirect_uri": redirect_uri, "code": code})


def refresh(client_id: str, client_secret: str, refresh_token: str) -> dict:
    return _post("/oauth2/token", {
        "grant_type": "refresh_token", "client_id": client_id,
        "client_secret": client_secret, "refresh_token": refresh_token})


def whoami(token: str) -> dict:
    return _get("/ping/whoami", token)


def accounts(token: str) -> list[dict]:
    return _get("/accounts", token).get("accounts", [])


def transactions(token: str, account_id: str, since: str | None = None) -> list[dict]:
    """List transactions, expanding merchant. `since` is a transaction id (forward-sync
    cursor) or an RFC3339 timestamp; omit for the initial (<=90-day) window."""
    params = {"account_id": account_id, "expand[]": "merchant"}
    if since:
        params["since"] = since
    return _get("/transactions", token, params).get("transactions", [])
