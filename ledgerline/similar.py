"""Find uncategorised transactions that look like a given descriptor, so filing one
merchant surfaces its near-duplicates (slightly different printed names).

Matches on normalised-descriptor similarity (difflib ratio) with a boost when the
leading significant token matches — the merchant name usually leads the descriptor,
so 'Sainsburys S/mkts …' and 'Sainsburys Superma …' share it.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from .normalise import normalise_descriptor

_TOK = re.compile(r"[a-z0-9]+")


def _first_token(norm: str) -> str:
    toks = _TOK.findall(norm.lower())
    return toks[0] if toks else ""


def similar_groups(conn, descriptor: str, threshold: float = 0.72, limit: int = 40) -> list[dict]:
    target = normalise_descriptor(descriptor) or descriptor
    t_low, t_key = target.lower(), _first_token(target)
    rows = conn.execute(
        "SELECT description_raw, amount_pennies, account FROM transactions "
        "WHERE needs_review=1 AND is_transfer=0 AND COALESCE(categorised_by,'none')<>'manual'"
    ).fetchall()
    groups: dict[str, dict] = {}
    for r in rows:
        cand = normalise_descriptor(r["description_raw"]) or r["description_raw"]
        if cand == target:                      # same descriptor already handled by the apply
            continue
        ratio = SequenceMatcher(None, t_low, cand.lower()).ratio()
        shared = bool(t_key) and _first_token(cand) == t_key
        if shared:
            score = max(ratio, 0.82)          # same leading merchant token -> strong signal
        elif ratio >= threshold:
            score = ratio                     # no shared token: require a high whole-string ratio
        else:
            continue                          # drops name-like fuzzy noise (Premier Inn vs Peter Wilson)
        g = groups.setdefault(cand, {"descriptor": cand, "count": 0, "total_pennies": 0,
                                     "accounts": set(), "samples": set(), "score": 0.0})
        g["count"] += 1
        g["total_pennies"] += r["amount_pennies"]
        g["accounts"].add(r["account"])
        if len(g["samples"]) < 2:
            g["samples"].add(r["description_raw"])
        g["score"] = max(g["score"], round(score, 2))
    out = [{**g, "accounts": sorted(g["accounts"]), "samples": sorted(g["samples"])}
           for g in groups.values()]
    out.sort(key=lambda g: (-g["score"], g["total_pennies"]))
    return out[:limit]
