"""Contextual views: trips/episodes and the ±window around a transaction."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ... import context
from ..auth import require_session
from ..deps import get_conn

router = APIRouter(prefix="/api")


class TagTripBody(BaseModel):
    txn_ids: list[str]
    name: str
    purpose: str | None = None   # card | work | holiday | personal


@router.post("/trips/tag")
def tag_trip(body: TagTripBody, _: None = Depends(require_session), conn=Depends(get_conn)) -> dict:
    """Label a trip: tag its transactions with a trip name + purpose so trip-aware
    reporting can group by it. Purpose is stored on the tag kind ('trip:card')."""
    kind = f"trip:{body.purpose}" if body.purpose else "trip"
    row = conn.execute("SELECT id FROM tags WHERE name=?", (body.name,)).fetchone()
    tid = row[0] if row else conn.execute(
        "INSERT INTO tags (name, kind) VALUES (?,?)", (body.name, kind)).lastrowid
    conn.execute("UPDATE tags SET kind=? WHERE id=?", (kind, tid))
    conn.executemany("INSERT OR IGNORE INTO transaction_tags (txn_id, tag_id) VALUES (?,?)",
                     [(i, tid) for i in body.txn_ids])
    conn.commit()
    return {"ok": True, "trip": body.name, "purpose": body.purpose, "tagged": len(body.txn_ids)}


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
