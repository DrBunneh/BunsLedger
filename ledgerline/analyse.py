"""Reporting. All spend analytics exclude transfers and respect account coverage."""
from __future__ import annotations

from .money import format_pennies


def coverage_summary(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT account, COUNT(*) n, MIN(posting_date) lo, MAX(posting_date) hi "
        "FROM transactions GROUP BY account ORDER BY account"
    ).fetchall()
    return [dict(r) for r in rows]


def categorisation_health(conn) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    transfers = conn.execute("SELECT COUNT(*) FROM transactions WHERE is_transfer=1").fetchone()[0]
    review = conn.execute("SELECT COUNT(*) FROM transactions WHERE needs_review=1").fetchone()[0]
    categorised = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE category IS NOT NULL AND is_transfer=0"
    ).fetchone()[0]
    spendable = total - transfers
    pct = (categorised / spendable * 100) if spendable else 0.0
    return {"total": total, "transfers": transfers, "categorised": categorised,
            "needs_review": review, "pct_categorised": round(pct, 1)}


def spend_by_category(conn, limit: int = 15) -> list[tuple[str, int, int]]:
    rows = conn.execute(
        "SELECT COALESCE(category,'(uncategorised)') c, COUNT(*) n, SUM(amount_pennies) s "
        "FROM transactions WHERE is_transfer=0 AND amount_pennies<0 "
        "GROUP BY c ORDER BY s ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return [(r["c"], r["n"], r["s"]) for r in rows]


def print_stats(conn) -> None:
    print("\nCoverage by account")
    for r in coverage_summary(conn):
        print(f"  {r['account']:<12} {r['n']:>6} txns   {r['lo']}  ->  {r['hi']}")
    h = categorisation_health(conn)
    print(f"\nCategorisation health")
    print(f"  {h['total']} txns | {h['transfers']} transfers | "
          f"{h['categorised']} categorised | {h['needs_review']} need review | "
          f"{h['pct_categorised']}% of spendable categorised")
    print("\nTop spend by category (excl. transfers)")
    for c, n, s in spend_by_category(conn):
        print(f"  {c:<28} {n:>5} txns  {format_pennies(s):>14}")
