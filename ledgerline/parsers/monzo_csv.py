"""Monzo CSV parser - the clean reference source.

Columns: id, created, title, subtitle, amount, currency, categories
- `amount` is signed major units (negative = money out) -> matches our convention.
- `id` is a stable native id -> deterministic txn_id with no occurrence_index needed.
- `categories` is Monzo's OWN label; preserved as source_category, never taken as ours.
"""
from __future__ import annotations

import csv
from pathlib import Path

from ..models import CanonicalTxn
from ..money import to_pennies

HEADER = ["id", "created", "title", "subtitle", "amount", "currency", "categories"]


def parse(path: str | Path) -> list[CanonicalTxn]:
    out: list[CanonicalTxn] = []
    fname = Path(path).name
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            created = (r.get("created") or "").strip()
            amount = (r.get("amount") or "").strip()
            if not created or amount == "":
                continue
            out.append(
                CanonicalTxn(
                    account="monzo",
                    institution="Monzo",
                    posting_date=created[:10],
                    datetime=created,
                    description_raw=(r.get("title") or "").strip(),
                    description_extra=(r.get("subtitle") or "").strip() or None,
                    amount_pennies=to_pennies(amount),
                    currency=(r.get("currency") or "GBP").strip() or "GBP",
                    source_category=(r.get("categories") or "").strip() or None,
                    source_id=(r.get("id") or "").strip() or None,
                    source_file=fname,
                ).validate()
            )
    return out
