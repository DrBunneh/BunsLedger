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

# Cheap transit that shouldn't, by itself, seed a trip (a £3 fare is a commute).
_TRANSPORT = re.compile(
    r"trainline|\btfl\b|transport for london|railway|\brail\b|west midlands trains|"
    r"east mids|east midlands|avanti|\blner\b|cross country|national express|megabus|"
    r"\buber\b|\bbolt\b|\btaxi\b|\btram\b|\bmetro\b|oyster|stagecoach|first bus", re.I)
_COMMUTE_MAX = 1000   # pennies (£10); away transit below this is commute noise, not a trip
_JOURNEY_MIN = 1500   # pennies (£15); a fare this size is intercity travel, not a commute
_CARD_MERCH = re.compile(r"cardmarket|fanfinity|drakkar|lorcan|\btcg\b|pokemon|"
                         r"magic the gathering|star city|troll and toad|collect", re.I)
_ACCOM = re.compile(r"travelodge|premier inn|holiday inn|\bhotel\b|hostel|airbnb|"
                    r"booking\.com|marriott|hilton|\bibis\b|premier|novotel", re.I)


def is_transport(descriptor: str, merchant: str | None, category: str | None) -> bool:
    return category == "Transport" or bool(_TRANSPORT.search(f"{descriptor} {merchant or ''}"))


def is_automated(txn_type: str | None) -> bool:
    return txn_type in ("direct_debit", "standing_order")


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
    transport = is_transport(r["description_raw"], r["merchant"], r["category"])
    return {
        "txn_id": r["txn_id"], "account": r["account"], "date": r["posting_date"],
        "datetime": r["datetime"], "description_raw": r["description_raw"],
        "merchant": r["merchant"], "amount_pennies": r["amount_pennies"],
        "category": r["category"], "is_transfer": r["is_transfer"],
        "place": place, "away": bool(is_away(r["description_raw"], r["fx_currency"], home)),
        "meal": meal_hint(r["datetime"]),
        "transport": transport, "automated": is_automated(r["txn_type"]),
        "abroad": extract_location(r["description_raw"])[0] == "abroad" or bool(r["fx_currency"]),
    }


_SELECT = ("SELECT txn_id, account, posting_date, datetime, description_raw, merchant, "
           "amount_pennies, category, is_transfer, fx_currency, txn_type FROM transactions")


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


def _accom(r: dict) -> bool:
    return bool(_ACCOM.search(f"{r['description_raw']} {r['merchant'] or ''}"))


def _seeds_trip(r: dict) -> bool:
    """Signals strong enough to say 'this was a trip, not a commute':
    foreign spend, geo-located away non-transit spend, a substantial (intercity) fare,
    or an overnight stay. A cheap local transit tap alone does NOT qualify."""
    if r["is_transfer"] or r["automated"]:
        return False
    if r["abroad"]:
        return True
    if r["away"] and not r["transport"]:            # you bought something in another city
        return True
    if r["transport"] and abs(r["amount_pennies"]) >= _JOURNEY_MIN:  # real journey, not a £3 tap
        return True
    return _accom(r)                                 # an overnight is always a trip


def _in_trip(r: dict, home: str | None) -> bool:
    """A transaction belongs to a trip only if it's genuinely part of being away:
    away spend, or a location-less line (a coffee with no city). Home-located and
    automated payments (a Direct Debit that fired mid-trip) are excluded."""
    if r["is_transfer"] or r["automated"]:
        return False
    if r["place"] == home and home is not None:
        return False
    return r["away"] or r["place"] is None


def _guess_purpose(members: list[dict]) -> str | None:
    if any(_CARD_MERCH.search(f"{m['description_raw']} {m['merchant'] or ''}") or m["category"] == "Cards"
           for m in members):
        return "card"
    if any(m["abroad"] for m in members):
        return "holiday"
    return None


def episodes(conn, gap_days: int = 3, min_txns: int = 2, limit: int = 60) -> dict:
    """Cluster away-from-home spend into trips.

    Seeds only on NON-trivial away spend (a £3 commute fare alone isn't a trip), then
    each episode's transactions are the trip-relevant ones in that date span — away or
    location-less lines, excluding home-located and automated (Direct Debit) payments.
    """
    home = infer_home(conn)
    rows = [_row(r, home) for r in conn.execute(_SELECT + " ORDER BY posting_date, datetime").fetchall()]
    seeds = [r for r in rows if _seeds_trip(r)]
    if not seeds:
        return {"home": home, "episodes": []}

    clusters: list[list[dict]] = []
    cur = [seeds[0]]
    for r in seeds[1:]:
        if _daydiff(cur[-1]["date"], r["date"]) <= gap_days:
            cur.append(r)
        else:
            clusters.append(cur); cur = [r]
    clusters.append(cur)

    by_date: dict[str, list] = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)

    eps = []
    for cl in clusters:
        d0, d1 = cl[0]["date"], cl[-1]["date"]
        span = [r for d, rs in by_date.items() if d0 <= d <= d1 for r in rs if _in_trip(r, home)]
        if len(span) < min_txns:
            continue
        span.sort(key=lambda r: (r["date"], r["datetime"] or ""))
        eps.append({
            "date_from": d0, "date_to": d1,
            "places": sorted({r["place"] for r in span if r["place"]}),
            "abroad": any(r["abroad"] for r in cl),
            "count": len(span), "spend_pennies": sum(-r["amount_pennies"] for r in span if r["amount_pennies"] < 0),
            "uncategorised": sum(1 for r in span if r["category"] is None and r["amount_pennies"] < 0),
            "purpose_guess": _guess_purpose(span),
            "transactions": span,
        })
    eps.sort(key=lambda e: e["date_to"], reverse=True)
    return {"home": home, "episodes": eps[:limit]}


def _daydiff(a: str, b: str) -> int:
    from datetime import date
    return abs((date.fromisoformat(b) - date.fromisoformat(a)).days)
