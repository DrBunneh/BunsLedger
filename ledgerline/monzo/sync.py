"""Map Monzo API transactions to the canonical schema and forward-sync into the store.

NOTE — id spaces differ between sources. The Monzo CSV export uses `feed_*` ids while
the API uses `tx_*` ids, so the same transaction imported both ways would dedup to two
different rows. Forward-sync is therefore designed to pull only transactions AFTER your
last CSV export (tracked by the stored cursor). Overlapping a CSV export with an API sync
of the same period can double-count; sync forward, don't backfill.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..db import upsert_transactions
from ..models import CanonicalTxn


def map_transaction(t: dict) -> CanonicalTxn | None:
    """One Monzo API transaction dict -> CanonicalTxn. Returns None for £0 / declined
    top-ups that carry no spend signal (amount 0 with a decline reason)."""
    amount = t.get("amount")
    if amount is None:
        return None
    created = t.get("created") or ""
    merchant = t.get("merchant") or {}
    merchant_name = merchant.get("name") if isinstance(merchant, dict) else None
    status = "declined" if t.get("decline_reason") else ("posted" if t.get("settled") else "pending")
    # merchant is set by categorisation (as with every source); keep the API's clean name
    # in description_extra so it isn't lost, falling back to Monzo notes.
    extra = merchant_name or (t.get("notes") or "").strip() or None
    return CanonicalTxn(
        account="monzo",
        institution="Monzo",
        posting_date=created[:10],
        datetime=created or None,
        description_raw=(t.get("description") or "").strip(),
        description_extra=extra,
        amount_pennies=int(amount),          # API amounts are already signed pennies
        currency=(t.get("currency") or "GBP"),
        source_category=(t.get("category") or None),
        source_id=t.get("id"),               # tx_* id (distinct from CSV feed_* ids)
        status=status,
        source_file="monzo-api",
    ).validate()


def sync(conn, token: str, account_id: str, cursor: str | None) -> dict:
    """Fetch transactions since `cursor`, upsert, and return the new cursor + counts."""
    from . import client
    raw = client.transactions(token, account_id, since=cursor)
    txns = [m for m in (map_transaction(t) for t in raw) if m is not None]
    counts = upsert_transactions(conn, txns) if txns else {"seen": 0, "new": 0, "dup": 0}
    new_cursor = raw[-1]["id"] if raw else cursor
    return {"fetched": len(raw), **counts, "cursor": new_cursor}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
