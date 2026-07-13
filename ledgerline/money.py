"""Money parsing. Everything internal is signed integer pennies; NEGATIVE = out."""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

_CLEAN = re.compile(r"[£$€\s,]")


def to_pennies(value: str | float | int | Decimal) -> int:
    """Parse a GBP amount to signed integer pennies.

    Handles '£1,400.00', '-3837.95', '+ £62.05', '2,771.90'. Thousands separators
    are stripped; a leading +/- (or a Decimal/number) sets the sign.
    """
    if isinstance(value, (int, float, Decimal)):
        dec = Decimal(str(value))
    else:
        s = value.strip()
        sign = -1 if s.startswith("-") else 1
        s = _CLEAN.sub("", s).lstrip("+-")
        if not s:
            raise ValueError(f"empty money value: {value!r}")
        dec = Decimal(s) * sign
    return int((dec * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_fx_decimal(value: str) -> float:
    """Parse a foreign amount that may use a European comma decimal ('13,00' or '15.00')."""
    s = value.strip().replace(" ", "")
    # If both separators appear, the last one is the decimal point.
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    return float(s)


def format_pennies(pennies: int) -> str:
    sign = "-" if pennies < 0 else ""
    return f"{sign}£{abs(pennies) / 100:,.2f}"
