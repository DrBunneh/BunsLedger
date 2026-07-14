"""Temporal + geographic context: turn a flat ledger into episodes.

A single transaction rarely explains itself; the story is in what surrounds it.
This module extracts a location from each descriptor, infers your home city, flags
away-from-home spend, clusters bursts of activity into "episodes" (trips), and pulls
the ±window of transactions around any one line.

Location extraction is a heuristic over a small UK gazetteer + country codes + FX.
It's strongest on card statements (which print "Merchant City") and extendable.
"""
from __future__ import annotations

import re
from collections import Counter

# Small, extendable gazetteer. Home is inferred (usually the modal city), so this
# only needs to recognise the *away* places well enough to flag a trip.
UK_PLACES = {
    "birmingham", "london", "islington", "euston", "camden", "shoreditch", "soho",
    "westminster", "kensington", "greenwich", "croydon", "manchester", "leeds",
    "liverpool", "sheffield", "bristol", "nottingham", "leicester", "coventry",
    "glasgow", "edinburgh", "aberdeen", "dundee", "cardiff", "swansea", "newport",
    "belfast", "oxford", "cambridge", "brighton", "bournemouth", "portsmouth",
    "southampton", "reading", "york", "bath", "chester", "exeter", "plymouth",
    "norwich", "hull", "derby", "wolverhampton", "stoke", "sunderland", "newcastle",
    "gateshead", "preston", "blackpool", "bolton", "watford", "luton", "milton",
    "birkenhead", "gloucester", "worcester",
}
# 3-letter tail codes / tokens that mean "not in the UK".
ABROAD_TOKENS = {
    "fra", "deu", "esp", "ita", "nld", "prt", "bel", "che", "aut", "usa", "can",
    "irl", "pol", "swe", "nor", "dnk", "jpn", "aus",
}
_WORD = re.compile(r"[a-z]+")


def extract_location(descriptor: str) -> tuple[str | None, str | None]:
    """Return (scope, place): ('uk', 'london') | ('abroad', 'fra') | (None, None)."""
    toks = _WORD.findall((descriptor or "").lower())
    for t in toks:
        if t in ABROAD_TOKENS:
            return "abroad", t
    for t in reversed(toks):          # the location usually trails the merchant name
        if t in UK_PLACES:
            return "uk", t
    return None, None


def infer_home(conn) -> str | None:
    counts: Counter = Counter()
    for (desc,) in conn.execute("SELECT description_raw FROM transactions"):
        scope, place = extract_location(desc)
        if scope == "uk" and place:
            counts[place] += 1
    return counts.most_common(1)[0][0] if counts else None


def is_away(descriptor: str, fx_currency: str | None, home: str | None) -> bool:
    if fx_currency:
        return True
    scope, place = extract_location(descriptor)
    if scope == "abroad":
        return True
    return scope == "uk" and place is not None and place != home


def meal_hint(dt: str | None) -> str | None:
    """Rough meal-of-day from a full timestamp (Monzo only; date-only rows return None)."""
    if not dt or "T" not in dt:
        return None
    try:
        hour = int(dt.split("T")[1][:2])
    except (ValueError, IndexError):
        return None
    if 5 <= hour < 11:
        return "breakfast"
    if 11 <= hour < 15:
        return "lunch"
    if 15 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 23:
        return "dinner"
    return "late night"


def _row(r, home: str) -> dict:
    scope, place = extract_location(r["description_raw"])
    return {
        "txn_id": r["txn_id"], "account": r["account"], "date": r["posting_date"],
        "datetime": r["datetime"], "description_raw": r["description_raw"],
        "merchant": r["merchant"], "amount_pennies": r["amount_pennies"],
        "category": r["category"], "is_transfer": r["is_transfer"],
        "place": place, "away": bool(is_away(r["description_raw"], r["fx_currency"], home)),
        "meal": meal_hint(r["datetime"]),
    }


_SELECT = ("SELECT txn_id, account, posting_date, datetime, description_raw, merchant, "
           "amount_pennies, category, is_transfer, fx_currency FROM transactions")


def surrounding(conn, txn_id: str, days: int = 2) -> dict:
    """Transactions within ±days of the given one (all accounts), ordered in time —
    'what else was happening around this?'."""
    home = infer_home(conn)
    base = conn.execute(_SELECT + " WHERE txn_id=?", (txn_id,)).fetchone()
    if base is None:
        return {"center": None, "window": []}
    rows = conn.execute(
        _SELECT + " WHERE posting_date BETWEEN date(?, ?) AND date(?, ?) "
        "ORDER BY posting_date, datetime",
        (base["posting_date"], f"-{days} days", base["posting_date"], f"+{days} days"),
    ).fetchall()
    return {"home": home, "center": txn_id, "window": [_row(r, home) for r in rows]}


def episodes(conn, gap_days: int = 3, min_txns: int = 2, limit: int = 60) -> dict:
    """Cluster away-from-home spend into trips. An episode is a run of away transactions
    no more than gap_days apart; its transactions are everything in that date span (so the
    train out and the taxi home are included, not just the away legs)."""
    home = infer_home(conn)
    rows = [_row(r, home) for r in conn.execute(_SELECT + " ORDER BY posting_date, datetime").fetchall()]
    away = [r for r in rows if r["away"] and not r["is_transfer"]]
    if not away:
        return {"home": home, "episodes": []}

    # gap-cluster the away transactions by date
    clusters: list[list[dict]] = []
    cur = [away[0]]
    for r in away[1:]:
        if _daydiff(cur[-1]["date"], r["date"]) <= gap_days:
            cur.append(r)
        else:
            clusters.append(cur); cur = [r]
    clusters.append(cur)

    by_date = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)

    eps = []
    for cl in clusters:
        d0, d1 = cl[0]["date"], cl[-1]["date"]
        span = [r for d, rs in by_date.items() if d0 <= d <= d1 for r in rs if not r["is_transfer"]]
        if len(span) < min_txns:
            continue
        span.sort(key=lambda r: (r["date"], r["datetime"] or ""))
        places = sorted({r["place"] for r in cl if r["place"]})
        spend = sum(-r["amount_pennies"] for r in span if r["amount_pennies"] < 0)
        eps.append({
            "date_from": d0, "date_to": d1, "places": places,
            "abroad": any(extract_location(r["description_raw"])[0] == "abroad" for r in cl),
            "count": len(span), "spend_pennies": spend,
            "uncategorised": sum(1 for r in span if r["category"] is None and r["amount_pennies"] < 0),
            "transactions": span,
        })
    eps.sort(key=lambda e: e["date_to"], reverse=True)
    return {"home": home, "episodes": eps[:limit]}


def _daydiff(a: str, b: str) -> int:
    from datetime import date
    return abs((date.fromisoformat(b) - date.fromisoformat(a)).days)
