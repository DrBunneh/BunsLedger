"""WP3: reporting. Every aggregate excludes transfers and is coverage-aware
(periods before an account's data begins are reported so the UI can shade them)."""
from __future__ import annotations

import statistics

from fastapi import APIRouter, Depends, Query

from ..auth import require_session
from ..deps import get_conn

router = APIRouter(prefix="/api")

# non-transfer filter shared by every spend/income aggregate
_SPENDABLE = "is_transfer=0"


def _range(conn, date_from, date_to):
    if not date_to:
        date_to = conn.execute("SELECT MAX(posting_date) FROM transactions").fetchone()[0]
    if not date_from and date_to:
        date_from = conn.execute("SELECT date(?, '-12 months')", (date_to,)).fetchone()[0]
    return date_from, date_to


@router.get("/reports/summary")
def summary(_: None = Depends(require_session), conn=Depends(get_conn),
            date_from: str | None = None, date_to: str | None = None) -> dict:
    date_from, date_to = _range(conn, date_from, date_to)
    r = conn.execute(
        f"SELECT SUM(CASE WHEN amount_pennies>0 THEN amount_pennies ELSE 0 END) income, "
        f"SUM(CASE WHEN amount_pennies<0 THEN amount_pennies ELSE 0 END) spend, COUNT(*) n "
        f"FROM transactions WHERE {_SPENDABLE} AND posting_date BETWEEN ? AND ?",
        (date_from, date_to),
    ).fetchone()
    income, spend = r["income"] or 0, r["spend"] or 0
    # coverage: first data date per account -> aggregates before the latest of these under-count
    cov = [dict(x) for x in conn.execute(
        "SELECT account, MIN(posting_date) start, MAX(posting_date) end FROM transactions GROUP BY account"
    ).fetchall()]
    full_from = max((c["start"] for c in cov), default=None)
    return {"date_from": date_from, "date_to": date_to, "income_pennies": income,
            "spend_pennies": spend, "net_pennies": income + spend, "transactions": r["n"],
            "coverage": cov, "full_coverage_from": full_from}


@router.get("/reports/cashflow")
def cashflow(_: None = Depends(require_session), conn=Depends(get_conn), months: int = 12) -> dict:
    to = conn.execute("SELECT MAX(posting_date) FROM transactions").fetchone()[0]
    frm = conn.execute("SELECT date(?, ?)", (to, f"-{months} months")).fetchone()[0] if to else None
    rows = conn.execute(
        f"SELECT strftime('%Y-%m', posting_date) period, "
        f"SUM(CASE WHEN amount_pennies>0 THEN amount_pennies ELSE 0 END) income, "
        f"SUM(CASE WHEN amount_pennies<0 THEN -amount_pennies ELSE 0 END) spend "
        f"FROM transactions WHERE {_SPENDABLE} AND posting_date > ? GROUP BY period ORDER BY period",
        (frm,),
    ).fetchall()
    return {"months": [{"period": r["period"], "income_pennies": r["income"],
                        "spend_pennies": r["spend"], "net_pennies": r["income"] - r["spend"]}
                       for r in rows]}


@router.get("/reports/spend-by-category")
def spend_by_category(_: None = Depends(require_session), conn=Depends(get_conn),
                      date_from: str | None = None, date_to: str | None = None) -> dict:
    date_from, date_to = _range(conn, date_from, date_to)
    rows = conn.execute(
        f"SELECT COALESCE(category,'(uncategorised)') category, COUNT(*) n, "
        f"SUM(-amount_pennies) spend FROM transactions "
        f"WHERE {_SPENDABLE} AND amount_pennies<0 AND posting_date BETWEEN ? AND ? "
        f"GROUP BY category ORDER BY spend DESC",
        (date_from, date_to),
    ).fetchall()
    return {"date_from": date_from, "date_to": date_to,
            "categories": [{"category": r["category"], "count": r["n"], "spend_pennies": r["spend"]} for r in rows]}


@router.get("/reports/top-merchants")
def top_merchants(_: None = Depends(require_session), conn=Depends(get_conn),
                  limit: int = 15, date_from: str | None = None, date_to: str | None = None) -> dict:
    date_from, date_to = _range(conn, date_from, date_to)
    rows = conn.execute(
        f"SELECT COALESCE(merchant, description_raw) m, COUNT(*) n, SUM(-amount_pennies) spend "
        f"FROM transactions WHERE {_SPENDABLE} AND amount_pennies<0 AND posting_date BETWEEN ? AND ? "
        f"GROUP BY m ORDER BY spend DESC LIMIT ?",
        (date_from, date_to, limit),
    ).fetchall()
    return {"merchants": [{"merchant": r["m"], "count": r["n"], "spend_pennies": r["spend"]} for r in rows]}


@router.get("/reports/recurring")
def recurring(_: None = Depends(require_session), conn=Depends(get_conn),
              min_months: int = 3) -> dict:
    """Heuristic subscription/bill detection: a merchant seen in >= min_months distinct
    months with a stable amount. status = active (seen in last ~45 days) else lapsed."""
    today = conn.execute("SELECT MAX(posting_date) FROM transactions").fetchone()[0]
    rows = conn.execute(
        f"SELECT COALESCE(merchant, description_raw) m, strftime('%Y-%m', posting_date) period, "
        f"-amount_pennies amt, posting_date FROM transactions "
        f"WHERE {_SPENDABLE} AND amount_pennies<0 ORDER BY m, posting_date"
    ).fetchall()
    by_m: dict[str, list] = {}
    for r in rows:
        by_m.setdefault(r["m"], []).append(r)
    out = []
    for m, txns in by_m.items():
        months = {t["period"] for t in txns}
        if len(months) < min_months:
            continue
        amounts = [t["amt"] for t in txns]
        med = statistics.median(amounts)
        if med <= 0:
            continue
        spread = statistics.pstdev(amounts) / med if len(amounts) > 1 else 0
        if spread > 0.25:                      # amount not stable enough to be a subscription
            continue
        last = max(t["posting_date"] for t in txns)
        days_since = conn.execute("SELECT julianday(?)-julianday(?)", (today, last)).fetchone()[0]
        out.append({"merchant": m, "typical_pennies": round(med), "months": len(months),
                    "last_seen": last, "status": "active" if days_since is not None and days_since <= 45 else "lapsed"})
    out.sort(key=lambda x: -x["typical_pennies"])
    active = sum(x["typical_pennies"] for x in out if x["status"] == "active")
    return {"recurring": out, "monthly_committed_pennies": active}
