"""Descriptor normalisation and counterparty extraction.

`descriptor_normalised` is a grouping/lookup key only - it is NEVER fed into the
txn_id hash (see dedup.py). Strip payment-facilitator prefixes, geo/country suffixes
and long reference numbers to collapse '{Paypal *cardmarket 15207145977}' and
'{Paypal *cardmarket 17682120887}' onto one merchant key.
"""
from __future__ import annotations

import re

# Gateway / facilitator prefixes: PAYPAL*, SUMUP*, ZETTLE*, SP*, EB*, TM*, SNP* ...
_GATEWAY = re.compile(r"^(paypal|sumup|zettle|sq|sp|eb|tm|snp|wp|pp)\*?[\s_]*", re.I)
_TRAILING_REF = re.compile(r"\b[0-9]{5,}\b")           # long reference numbers
_GEO_SUFFIX = re.compile(
    r"\b(fra|eng|lnd|lon|deu|de|usa|us|ca|gbr|gb|nld|esp|ita)\b\.?$", re.I
)
_PHONE = re.compile(r"\+?\d[\d ]{6,}\d")
_MULTISPACE = re.compile(r"\s+")


def normalise_descriptor(raw: str) -> str:
    s = raw.strip()
    s = _GATEWAY.sub("", s)
    s = _PHONE.sub("", s)
    s = _TRAILING_REF.sub("", s)
    for _ in range(2):                                  # strip up to two stacked geo suffixes
        s = _GEO_SUFFIX.sub("", s.strip())
    s = _MULTISPACE.sub(" ", s).strip(" -*,")
    return s or raw.strip()


# --- Counterparty extraction -------------------------------------------------
# Nationwide prints "Payment to PETER WILSON", "To mortgage a/c PETER WILSON".
_PAYMENT_TO = re.compile(r"^payment to\s+(.+)$", re.I)
_TO_ACCT = re.compile(r"^to\s+(.+?a/c)\s+(.+)$", re.I)


def extract_counterparty(description_raw: str) -> str | None:
    """Return a raw counterparty name if the descriptor clearly names a person/account,
    else None. Resolution to a canonical entity happens against counterparty_aliases."""
    s = description_raw.strip()
    m = _PAYMENT_TO.match(s)
    if m:
        return m.group(1).strip()
    m = _TO_ACCT.match(s)
    if m:
        return m.group(2).strip()
    return None
