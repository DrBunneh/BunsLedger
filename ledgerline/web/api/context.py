"""Contextual views: trips/episodes and the ±window around a transaction."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ... import context
from ..auth import require_session
from ..deps import get_conn

router = APIRouter(prefix="/api")


@router.get("/trips")
def trips(_: None = Depends(require_session), conn=Depends(get_conn),
          gap_days: int = 3, min_txns: int = 3) -> dict:
    """Away-from-home spend clustered into trips/episodes, newest first."""
    return context.episodes(conn, gap_days=gap_days, min_txns=min_txns)


@router.get("/transactions/{txn_id}/context")
def txn_context(txn_id: str, _: None = Depends(require_session), conn=Depends(get_conn),
                days: int = 2) -> dict:
    """What else was happening around this transaction (±days, all accounts)."""
    return context.surrounding(conn, txn_id, days=days)
