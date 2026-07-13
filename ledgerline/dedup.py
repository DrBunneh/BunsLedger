"""Deterministic transaction ids so the same physical transaction always maps to
the same id, whichever file it arrives in -> re-imports are idempotent, overlapping
statements add only genuinely new rows.

CRITICAL: the PDF hash is taken over the RAW description, never a normalised one.
If it hashed a normalised string, improving the normaliser later would change every
historical id and silently duplicate the entire back-catalogue on the next import.
"""
from __future__ import annotations

import hashlib

from .models import CanonicalTxn


def _h(*parts: object) -> str:
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


def txn_id(txn: CanonicalTxn) -> str:
    if txn.source_id:                      # Monzo: native id guarantees uniqueness
        return _h(txn.account, txn.source_id)
    # id-less PDFs: raw fields + occurrence_index so identical same-day repeats survive
    return _h(
        txn.account,
        txn.posting_date,
        txn.amount_pennies,
        txn.description_raw,               # RAW, not normalised - see module docstring
        txn.occurrence_index,
    )


def assign_occurrence_index(txns: list[CanonicalTxn]) -> None:
    """Number identical (date, amount, raw-description) tuples 0,1,2,... in file order,
    so genuine same-day repeats get distinct ids while re-imports stay stable."""
    seen: dict[tuple, int] = {}
    for t in txns:
        if t.source_id:                    # id'd sources don't need it
            continue
        key = (t.posting_date, t.amount_pennies, t.description_raw)
        t.occurrence_index = seen.get(key, 0)
        seen[key] = t.occurrence_index + 1
