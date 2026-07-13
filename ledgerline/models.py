"""The canonical transaction record every parser must emit.

Stdlib dataclass (no third-party dependency) so the core stays import-light. Each
parser's job is: source file -> list[CanonicalTxn]. All source-specific mess is
resolved here, so everything downstream sees one clean shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class CanonicalTxn:
    account: str                    # monzo | nationwide | aqua
    posting_date: str               # ISO yyyy-mm-dd
    description_raw: str            # exactly as printed; the hash input; never mutated
    amount_pennies: int             # signed; NEGATIVE = money out

    institution: Optional[str] = None
    effective_date: Optional[str] = None
    datetime: Optional[str] = None
    description_extra: Optional[str] = None
    currency: str = "GBP"
    fx_amount: Optional[float] = None
    fx_currency: Optional[str] = None
    fx_rate: Optional[float] = None
    balance_after: Optional[int] = None
    txn_type: Optional[str] = None
    status: str = "posted"          # posted | pending | declined
    source_category: Optional[str] = None
    source_id: Optional[str] = None
    source_file: str = ""

    # occurrence_index disambiguates genuine same-day identical repeats in id-less
    # PDF sources (e.g. 6x 'Pastimes Booth -£18.67' on one day). Assigned at parse time.
    occurrence_index: int = 0

    def validate(self) -> "CanonicalTxn":
        if not self.description_raw:
            raise ValueError("description_raw is required (it is the hash input)")
        if self.status not in ("posted", "pending", "declined"):
            raise ValueError(f"bad status: {self.status}")
        if not isinstance(self.amount_pennies, int):
            raise ValueError("amount_pennies must be int (pennies)")
        return self

    def as_dict(self) -> dict:
        return asdict(self)
