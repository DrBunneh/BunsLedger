"""Nationwide FlexDirect PDF parser - positional extraction.

Why positional: in linear text the £Out / £In / £Balance columns collapse to bare
numbers on their own lines, indistinguishable by order. They ARE separable by the
right edge (x1) of each right-aligned number:
    x1 < 320            -> £Out      (money out => NEGATIVE)
    320 <= x1 < 375     -> £In       (money in  => POSITIVE)
    x1 >= 375           -> £Balance  (running balance, not a movement)

Other quirks handled: the year sits on its own header row (carry it down, roll over
Dec->Jan); dates are day+month only; a running balance prints on only some rows.

Self-check: `reconcile()` walks start_balance + cumulative signed amounts and asserts
it matches every printed balance - a hard proof the Out/In signs are right.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ..models import CanonicalTxn
from ..money import to_pennies

_MONEY = re.compile(r"^[\d,]+\.\d{2}$")
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
_YEAR = re.compile(r"^(20\d{2})$")

OUT_MAX, IN_MAX = 320.0, 375.0


def _column(x1: float) -> str:
    if x1 < OUT_MAX:
        return "out"
    if x1 < IN_MAX:
        return "in"
    return "balance"


def _rows(path: Path):
    """Yield (page_index, list_of_rows); each row = list of word tuples sorted by x, grouped by y."""
    import fitz
    with fitz.open(str(path)) as doc:
        for pageno, page in enumerate(doc):
            words = page.get_text("words")            # x0,y0,x1,y1,text,block,line,word
            rows: dict[int, list] = {}
            for w in words:
                key = round(w[1] / 4)                 # ~4px y-tolerance bucket
                rows.setdefault(key, []).append(w)
            for key in sorted(rows):
                yield pageno, sorted(rows[key], key=lambda w: w[0])


def _date_from_row(row, year: int | None) -> str | None:
    toks = [w[4] for w in row if w[0] < 90]
    if len(toks) >= 2 and re.fullmatch(r"\d{1,2}", toks[0]) and toks[1][:3] in _MONTHS and year:
        return f"{year:04d}-{_MONTHS[toks[1][:3]]:02d}-{int(toks[0]):02d}"
    return None


def _parse(path: Path) -> tuple[list[CanonicalTxn], int | None]:
    out: list[CanonicalTxn] = []
    year: int | None = None
    cur_date: str | None = None
    last_month: int | None = None
    opening_balance: int | None = None

    for _pageno, row in _rows(path):
        text = " ".join(w[4] for w in row).strip()

        # Year lives as the first token of the date column (e.g. on the opening-balance
        # row "2026 Balance from statement 184 ..."), not always on a line of its own.
        for w in row:
            if w[0] < 90 and _YEAR.fullmatch(w[4]):
                year = int(w[4])

        # Opening-balance row: capture the start balance, don't emit a transaction.
        if text.startswith("2026 Balance from statement") or "Balance from statement" in text:
            bal = [w for w in row if _MONEY.fullmatch(w[4]) and _column(w[2]) == "balance"]
            if bal:
                opening_balance = to_pennies(bal[0][4])
            continue

        d = _date_from_row(row, year)
        if d:
            month = int(d[5:7])
            if last_month == 12 and month == 1 and year:        # Dec -> Jan rollover
                year += 1
                d = f"{year:04d}-{d[5:]}"
            cur_date, last_month = d, month

        desc_words = [w[4] for w in row if 90 <= w[0] < 270]
        description = " ".join(desc_words).strip()

        for w in row:
            if not _MONEY.fullmatch(w[4]):
                continue
            col = _column(w[2])                                  # w[2] = x1 (right edge)
            pennies = to_pennies(w[4])
            if col == "balance":
                if out:                                          # attach to the last movement on/above this row
                    out[-1].balance_after = pennies
                continue
            if not description or cur_date is None:
                continue
            out.append(CanonicalTxn(
                account="nationwide",
                institution="Nationwide",
                posting_date=cur_date,
                description_raw=description,
                amount_pennies=-pennies if col == "out" else pennies,
                txn_type="direct_debit" if description.lower().startswith("direct debit") else None,
                source_file=path.name,
            ).validate())
    return out, opening_balance


def parse(path: str | Path) -> list[CanonicalTxn]:
    return _parse(Path(path))[0]


def reconcile(path: str | Path, start_balance_pennies: int | None = None) -> dict:
    """Walk printed balances against cumulative signed amounts. Returns match stats;
    a mismatch means the Out/In column assignment is wrong for some row. Start balance
    is auto-read from the statement's opening-balance row unless overridden."""
    txns, opening = _parse(Path(path))
    running = start_balance_pennies if start_balance_pennies is not None else (opening or 0)
    checks = mismatches = 0
    for t in txns:
        running += t.amount_pennies
        if t.balance_after is not None:
            checks += 1
            if t.balance_after != running:
                mismatches += 1
    return {"txns": len(txns), "balance_checks": checks, "mismatches": mismatches}
