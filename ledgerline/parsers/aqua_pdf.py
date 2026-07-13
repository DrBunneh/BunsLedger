"""Aqua (NewDay) credit-card PDF parser.

Layout (digital text, no OCR): each row is three consecutive lines -
    01 Dec 2025
    Sainsburys S/mkts Birmingham Co
    + £5.70
optionally followed by a foreign-exchange line:
    Currency conversion rate 15.00 USD @ 1.3464

CRITICAL sign flip - it's a credit card, so the printed sign is inverted vs cashflow:
    '+ £x'  = a purchase        => money OUT => NEGATIVE pennies
    '- £x'  = payment received  => money IN  => POSITIVE pennies

We gate to the transaction region (between 'Opening Balance' and 'Your new balance')
so the page-1 summary totals ('New transactions + £1,878.85') are never ingested.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ..models import CanonicalTxn
from ..money import parse_fx_decimal, to_pennies

_DATE = re.compile(r"^\d{2}\s+[A-Za-z]{3}\s+\d{4}$")
_AMOUNT = re.compile(r"^([+-])\s*£\s*([\d,]+\.\d{2})$")
_FX = re.compile(r"Currency conversion rate\s+([\d.,]+)\s+([A-Z]{3})\s+@\s+([\d.,]+)")


def _all_lines(path: Path) -> list[str]:
    import fitz
    lines: list[str] = []
    with fitz.open(str(path)) as doc:
        for page in doc:
            lines += page.get_text().splitlines()
    return lines


def _region(lines: list[str]) -> list[str]:
    start = end = None
    for i, l in enumerate(lines):
        s = l.strip()
        if start is None and s == "Opening Balance":
            start = i + 1
        elif start is not None and s.startswith("Your new balance"):
            end = i
            break
    if start is None:
        return []
    return lines[start:end if end is not None else len(lines)]


def parse(path: str | Path) -> list[CanonicalTxn]:
    p = Path(path)
    lines = _region(_all_lines(p))
    out: list[CanonicalTxn] = []
    cur_date: str | None = None

    for i, line in enumerate(lines):
        s = line.strip()
        if _DATE.match(s):
            cur_date = datetime.strptime(s, "%d %b %Y").strftime("%Y-%m-%d")
            continue
        m = _AMOUNT.match(s)
        if not m or cur_date is None:
            continue
        sign, magnitude = m.group(1), m.group(2)
        pennies = to_pennies(magnitude)
        amount = -pennies if sign == "+" else pennies      # the flip
        description = lines[i - 1].strip() if i > 0 else ""
        if not description or _DATE.match(description) or _AMOUNT.match(description):
            continue

        txn = CanonicalTxn(
            account="aqua",
            institution="Aqua (NewDay)",
            posting_date=cur_date,
            description_raw=description,
            amount_pennies=amount,
            txn_type="purchase" if sign == "+" else "payment",
            source_file=p.name,
        )
        fx = _FX.search(lines[i + 1]) if i + 1 < len(lines) else None
        if fx:
            txn.fx_amount = parse_fx_decimal(fx.group(1))
            txn.fx_currency = fx.group(2)
            txn.fx_rate = parse_fx_decimal(fx.group(3))
        out.append(txn.validate())
    return out
