"""Transfer detection (runs first in the cascade).

v1 is deliberately conservative and pattern-based: it flags only movements that are
unmistakably between the owner's own accounts / people. Fuzzy cross-account leg-matching
(pairing an outflow on one account with an inflow on another by amount+date) is the
highest false-positive risk in the whole design and is intentionally deferred - an
over-eager matcher silently corrupts every downstream total.
"""
from __future__ import annotations

import re

from .counterparties import get_or_create
from .normalise import extract_counterparty

# Descriptor patterns that are self/interpersonal transfers, not spend.
_TRANSFER_PATTERNS = [
    re.compile(r"^payment to\b", re.I),          # Nationwide faster payments to a person
    re.compile(r"^payment received", re.I),      # Aqua credit-card payment in
    re.compile(r"\baqua\b", re.I),               # Monzo -> Aqua card payment
]


def flag_transfers(conn) -> int:
    """Set is_transfer / counts_as_spend / counterparty on clear transfers. Never
    touches rows already decided by hand. Returns count flagged."""
    rows = conn.execute(
        "SELECT txn_id, description_raw, source_category, account FROM transactions "
        "WHERE COALESCE(categorised_by,'none') <> 'manual'"
    ).fetchall()
    flagged = 0
    for r in rows:
        desc = r["description_raw"] or ""
        is_transfer = (r["source_category"] in ("transfers", "savings")) or any(
            p.search(desc) for p in _TRANSFER_PATTERNS
        )
        if not is_transfer:
            continue
        cp = extract_counterparty(desc)
        cid = get_or_create(conn, cp, "person") if cp else None
        conn.execute(
            "UPDATE transactions SET is_transfer=1, counts_as_spend=0, category='Transfers', "
            "counterparty_id=COALESCE(?, counterparty_id), categorised_by='rule', needs_review=0, "
            "updated_at=datetime('now') WHERE txn_id=?",
            (cid, r["txn_id"]),
        )
        flagged += 1
    conn.commit()
    return flagged
