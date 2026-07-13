"""WP5: Monzo forward-sync endpoints.

Flow: configure MONZO_CLIENT_ID/SECRET -> GET /api/monzo/connect (visit the URL,
approve in the Monzo app) -> Monzo redirects to /api/monzo/callback (tokens stored)
-> POST /api/monzo/sync pulls new transactions since the cursor and categorises them.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from ...categorise import categorise
from ...monzo import client, store, sync
from ..auth import require_session
from ..config import settings
from ..deps import get_conn

router = APIRouter(prefix="/api/monzo")


def _configured() -> bool:
    return bool(settings.monzo_client_id and settings.monzo_client_secret)


@router.get("/status")
def status(_: None = Depends(require_session), conn=Depends(get_conn)) -> dict:
    auth = store.load(conn)
    return {
        "configured": _configured(),
        "connected": bool(auth and auth.get("access_token")),
        "account_id": auth.get("account_id") if auth else None,
        "cursor": auth.get("cursor") if auth else None,
        "last_sync_at": auth.get("last_sync_at") if auth else None,
        "redirect_uri": settings.monzo_redirect_uri,
    }


@router.get("/connect")
def connect(_: None = Depends(require_session), conn=Depends(get_conn)) -> dict:
    if not _configured():
        raise HTTPException(400, "MONZO_CLIENT_ID / MONZO_CLIENT_SECRET not set")
    url, state = client.authorize_url(settings.monzo_client_id, settings.monzo_redirect_uri)
    store.save(conn, cursor=(store.load(conn) or {}).get("cursor"))  # ensure a row exists
    conn.execute("UPDATE monzo_auth SET user_id=COALESCE(user_id, ?) WHERE id=1", (None,))
    return {"authorize_url": url, "state": state}


@router.get("/callback")
def callback(code: str = "", state: str = "", conn=Depends(get_conn)) -> HTMLResponse:
    # Monzo redirects the browser here; no session cookie is guaranteed, so this
    # endpoint is unauthenticated but only completes a flow the user just initiated.
    if not code or not _configured():
        return HTMLResponse("<p>Missing code or Monzo not configured.</p>", status_code=400)
    resp = client.exchange_code(settings.monzo_client_id, settings.monzo_client_secret,
                                settings.monzo_redirect_uri, code)
    store.store_token_response(conn, resp)
    # pick the first account so sync has a target
    try:
        accts = client.accounts(resp["access_token"])
        if accts:
            store.save(conn, account_id=accts[0]["id"])
    except Exception:
        pass
    return HTMLResponse(
        "<p>Monzo connected. <strong>Approve access in your Monzo app</strong>, then return "
        "to Ledgerline and run a sync.</p>")


@router.post("/sync")
def run_sync(_: None = Depends(require_session), conn=Depends(get_conn)) -> dict:
    token = store.valid_token(conn, settings)
    if not token:
        raise HTTPException(400, "not connected — run /api/monzo/connect first")
    auth = store.load(conn)
    account_id = auth.get("account_id")
    if not account_id:
        account_id = (client.accounts(token) or [{}])[0].get("id")
        store.save(conn, account_id=account_id)
    result = sync.sync(conn, token, account_id, auth.get("cursor"))
    store.save(conn, cursor=result["cursor"], last_sync_at=sync.now_iso())
    if result.get("new"):
        result["categorise"] = categorise(conn)
    return result
