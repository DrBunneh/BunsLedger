"""Categorisation cascade over the store. First-match-wins, precedence:

    transfer detection  ->  manual  ->  directory  ->  rule  ->  source_map  ->  needs_review

Runs against stored data (not baked in at import), so you can improve rules and
re-flow history any time. Manual decisions are sticky and never overwritten. The
LLM auto-classifier (spec §6.3) is deliberately NOT built yet - measure the real
uncategorised tail first, then add it only for what genuinely remains.
"""
from __future__ import annotations

from .transfers import flag_transfers


def _apply_rules(conn) -> int:
    rules = conn.execute(
        "SELECT match_type, pattern, merchant, category, subcategory FROM merchant_rules "
        "ORDER BY priority DESC, id ASC"
    ).fetchall()
    n = 0
    for rule in rules:
        if rule["match_type"] != "contains":
            continue  # exact/regex wired in a later pass; contains covers the seed set
        like = f"%{rule['pattern']}%"
        n += conn.execute(
            "UPDATE transactions SET merchant=?, category=?, subcategory=?, "
            "categorised_by='rule', needs_review=0, updated_at=datetime('now') "
            "WHERE description_raw LIKE ? COLLATE NOCASE "
            "AND COALESCE(categorised_by,'none') NOT IN ('manual','rule') "
            "AND is_transfer=0",
            (rule["merchant"], rule["category"], rule["subcategory"], like),
        ).rowcount
    return n


def _apply_source_map(conn) -> int:
    return conn.execute(
        "UPDATE transactions SET "
        "  category=(SELECT category FROM category_map m "
        "            WHERE m.account=transactions.account AND m.source_category=transactions.source_category), "
        "  categorised_by='source_map', needs_review=0, updated_at=datetime('now') "
        "WHERE COALESCE(categorised_by,'none')='none' AND is_transfer=0 "
        "  AND source_category IN (SELECT source_category FROM category_map m2 WHERE m2.account=transactions.account)"
    ).rowcount


def _mark_review(conn) -> int:
    return conn.execute(
        "UPDATE transactions SET needs_review=1, updated_at=datetime('now') "
        "WHERE category IS NULL AND is_transfer=0 AND COALESCE(categorised_by,'none')<>'manual'"
    ).rowcount


def categorise(conn) -> dict:
    transfers = flag_transfers(conn)
    rules = _apply_rules(conn)
    source_map = _apply_source_map(conn)
    review = _mark_review(conn)
    conn.commit()
    return {"transfers": transfers, "rules": rules, "source_map": source_map, "needs_review": review}
