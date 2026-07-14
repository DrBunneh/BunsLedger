"""WP2: transactions listing + backlog review + apply decisions.

The review workflow lets you touch each *merchant* once: group the backlog by
normalised descriptor, decide a category, and apply it - optionally as a reusable
rule and/or retro-applied across all history. Manual decisions are sticky.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ... import counterparties
from ...categorise import categorise as run_categorise
from ...normalise import normalise_descriptor
from ...similar import similar_groups
from ..auth import require_session
from ..deps import get_conn

router = APIRouter(prefix="/api")

_SORTABLE = {"posting_date", "amount_pennies", "account", "merchant", "category"}


# --------------------------------------------------------------------------- list
@router.get("/transactions")
def list_transactions(
    _: None = Depends(require_session), conn=Depends(get_conn),
    account: str | None = None, category: str | None = None,
    needs_review: bool | None = None, is_transfer: bool | None = None,
    date_from: str | None = None, date_to: str | None = None,
    q: str | None = None, sort: str = "posting_date", order: str = "desc",
    page: int = 1, page_size: int = Query(50, le=500),
) -> dict:
    where, params = [], []
    if account: where.append("t.account=?"); params.append(account)
    if category: where.append("t.category=?"); params.append(category)
    if needs_review is not None: where.append("t.needs_review=?"); params.append(int(needs_review))
    if is_transfer is not None: where.append("t.is_transfer=?"); params.append(int(is_transfer))
    if date_from: where.append("t.posting_date>=?"); params.append(date_from)
    if date_to: where.append("t.posting_date<=?"); params.append(date_to)
    if q:
        where.append("(t.description_raw LIKE ? COLLATE NOCASE OR t.merchant LIKE ? COLLATE NOCASE "
                     "OR c.canonical_name LIKE ? COLLATE NOCASE)")
        params += [f"%{q}%"] * 3
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sort_col = sort if sort in _SORTABLE else "posting_date"
    sort_dir = "ASC" if order.lower() == "asc" else "DESC"

    base = f"FROM transactions t LEFT JOIN counterparties c ON c.id=t.counterparty_id {clause}"
    total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT t.txn_id, t.account, t.posting_date, t.description_raw, t.merchant, "
        f"t.amount_pennies, t.category, t.subcategory, t.is_transfer, t.needs_review, "
        f"t.categorised_by, t.confidence, t.rationale, c.canonical_name AS counterparty "
        f"{base} ORDER BY t.{sort_col} {sort_dir}, t.txn_id LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size],
    ).fetchall()
    return {"total": total, "page": page, "page_size": page_size,
            "rows": [dict(r) for r in rows]}


# ---------------------------------------------------------------------- categories
@router.get("/categories")
def categories(_: None = Depends(require_session), conn=Depends(get_conn)) -> dict:
    rows = conn.execute("SELECT name, parent, kind FROM categories ORDER BY parent IS NOT NULL, name").fetchall()
    tops = [r["name"] for r in rows if r["parent"] is None]
    subs: dict[str, list[str]] = {}
    for r in rows:
        if r["parent"]:
            subs.setdefault(r["parent"], []).append(r["name"])
    # Flattened "Category ▸ Subcategory" leaves (also the enum for the WP4 classifier).
    paths = []
    for t in tops:
        paths.append(t)
        for s in subs.get(t, []):
            paths.append(f"{t} ▸ {s}")
    return {"top": tops, "subcategories": subs,
            "kinds": {r["name"]: r["kind"] for r in rows}, "paths": paths}


class CategoryBody(BaseModel):
    name: str
    parent: str | None = None
    kind: str = "spend"     # income | spend | transfer


@router.post("/categories")
def add_category(body: CategoryBody, _: None = Depends(require_session), conn=Depends(get_conn)) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name required")
    if body.kind not in ("income", "spend", "transfer"):
        raise HTTPException(400, "kind must be income|spend|transfer")
    if body.parent and conn.execute(
            "SELECT 1 FROM categories WHERE name=? AND parent IS NULL", (body.parent,)).fetchone() is None:
        raise HTTPException(400, f"parent '{body.parent}' does not exist")
    if conn.execute("SELECT 1 FROM categories WHERE name=? AND IFNULL(parent,'')=IFNULL(?,'')",
                    (name, body.parent)).fetchone():
        where = f"under '{body.parent}'" if body.parent else "at top level"
        raise HTTPException(409, f"category '{name}' already exists {where}")
    # a child inherits its parent's kind (names are scoped to parent now)
    kind = conn.execute("SELECT kind FROM categories WHERE name=? AND parent IS NULL",
                        (body.parent,)).fetchone()["kind"] if body.parent else body.kind
    conn.execute("INSERT INTO categories (name, parent, kind) VALUES (?,?,?)", (name, body.parent, kind))
    conn.commit()
    return {"ok": True, "name": name, "parent": body.parent, "kind": kind}


@router.delete("/categories/{name}")
def delete_category(name: str, parent: str | None = None,
                    _: None = Depends(require_session), conn=Depends(get_conn)) -> dict:
    """Delete a category (top-level) or subcategory (pass ?parent=). Blocked while it
    has subcategories or is still used by transactions."""
    if parent is None and conn.execute("SELECT 1 FROM categories WHERE parent=?", (name,)).fetchone():
        raise HTTPException(400, "delete or move its subcategories first")
    if parent:                                  # a specific subcategory of `parent`
        used = conn.execute("SELECT COUNT(*) FROM transactions WHERE category=? AND subcategory=?",
                            (parent, name)).fetchone()[0]
        rule_where, rule_args = "category=? AND subcategory=?", (parent, name)
    else:                                        # a top-level category
        used = conn.execute("SELECT COUNT(*) FROM transactions WHERE category=?", (name,)).fetchone()[0]
        rule_where, rule_args = "category=?", (name,)
    if used:
        raise HTTPException(400, f"{used} transactions still use it — recategorise them first")
    conn.execute(f"DELETE FROM merchant_rules WHERE {rule_where}", rule_args)
    conn.execute("DELETE FROM categories WHERE name=? AND IFNULL(parent,'')=IFNULL(?,'')", (name, parent))
    conn.commit()
    return {"ok": True}


# ------------------------------------------------------------------ merchant review
@router.get("/review/similar")
def review_similar(descriptor: str, _: None = Depends(require_session), conn=Depends(get_conn),
                   threshold: float = 0.6) -> dict:
    """Uncategorised transactions that look like `descriptor` — near-duplicate names
    to file in the same category. Called right after you categorise a merchant."""
    return {"groups": similar_groups(conn, descriptor, threshold=threshold)}


@router.post("/categorise")
def recategorise(_: None = Depends(require_session), conn=Depends(get_conn)) -> dict:
    """Re-flow the cascade over stored data after editing rules/categories.
    Only re-touches rule/auto/uncategorised rows — manual decisions stay put."""
    return run_categorise(conn)


@router.get("/review/merchants")
def review_merchants(_: None = Depends(require_session), conn=Depends(get_conn),
                     limit: int = 100) -> dict:
    """Group the needs-review backlog by normalised descriptor so you decide once
    per merchant. Ordered by £ impact (biggest uncategorised spend first)."""
    rows = conn.execute(
        "SELECT txn_id, description_raw, amount_pennies, account, category, "
        "confidence, rationale, merchant FROM transactions "
        "WHERE needs_review=1 AND is_transfer=0"
    ).fetchall()
    groups: dict[str, dict] = {}
    for r in rows:
        key = normalise_descriptor(r["description_raw"]) or r["description_raw"]
        g = groups.setdefault(key, {"descriptor": key, "count": 0, "total_pennies": 0,
                                     "accounts": set(), "samples": set(),
                                     "proposal": r["category"], "rationale": r["rationale"],
                                     "confidence": r["confidence"]})
        g["count"] += 1
        g["total_pennies"] += r["amount_pennies"]
        g["accounts"].add(r["account"])
        if len(g["samples"]) < 3:
            g["samples"].add(r["description_raw"])
    out = []
    for g in groups.values():
        g["accounts"] = sorted(g["accounts"]); g["samples"] = sorted(g["samples"])
        out.append(g)
    out.sort(key=lambda g: g["total_pennies"])  # most-negative (biggest spend) first
    return {"groups": out[:limit], "total_groups": len(out)}


# ------------------------------------------------------------------- apply decision
class ApplyBody(BaseModel):
    descriptor: str | None = None       # normalised descriptor to match (from review group)
    txn_ids: list[str] | None = None    # or explicit ids
    category: str | None = None
    subcategory: str | None = None
    merchant: str | None = None
    counterparty: str | None = None
    is_transfer: bool | None = None
    counts_as_spend: bool | None = None
    tags: list[str] | None = None
    note: str | None = None             # your explanation of what this is (persists; feeds the AI)
    create_rule: bool = False
    retro_apply: bool = False           # apply across ALL history, not just the backlog


def _resolve_ids(conn, body: ApplyBody) -> list[str]:
    if body.txn_ids:
        return body.txn_ids
    if not body.descriptor:
        raise HTTPException(400, "provide descriptor or txn_ids")
    scope = "" if body.retro_apply else "WHERE needs_review=1"
    rows = conn.execute(f"SELECT txn_id, description_raw FROM transactions {scope}").fetchall()
    return [r["txn_id"] for r in rows
            if (normalise_descriptor(r["description_raw"]) or r["description_raw"]) == body.descriptor]


@router.post("/review/apply")
def apply_decision(body: ApplyBody, _: None = Depends(require_session), conn=Depends(get_conn)) -> dict:
    ids = _resolve_ids(conn, body)
    cid = counterparties.get_or_create(conn, body.counterparty) if body.counterparty else None
    updated = 0
    if ids:                                   # apply to matching transactions (may be none yet)
        qmarks = ",".join("?" * len(ids))
        sets = ["categorised_by='manual'", "needs_review=0", "updated_at=datetime('now')"]
        vals: list = []
        for col, val in [("category", body.category), ("subcategory", body.subcategory),
                         ("merchant", body.merchant), ("is_transfer", int(body.is_transfer) if body.is_transfer is not None else None),
                         ("counts_as_spend", int(body.counts_as_spend) if body.counts_as_spend is not None else None),
                         ("counterparty_id", cid)]:
            if val is not None:
                sets.append(f"{col}=?"); vals.append(val)
        updated = conn.execute(
            f"UPDATE transactions SET {', '.join(sets)} WHERE txn_id IN ({qmarks})", vals + ids).rowcount
        if body.tags:
            for tag in body.tags:
                tid = conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,)).lastrowid \
                      or conn.execute("SELECT id FROM tags WHERE name=?", (tag,)).fetchone()[0]
                conn.executemany("INSERT OR IGNORE INTO transaction_tags (txn_id, tag_id) VALUES (?,?)",
                                 [(i, tid) for i in ids])
        if body.note:
            conn.execute(f"UPDATE transactions SET notes=? WHERE txn_id IN ({qmarks})", [body.note] + ids)

    # Remember this merchant + your explanation so it's never re-asked and can feed the AI.
    if body.descriptor:
        conn.execute(
            "INSERT INTO merchant_directory (raw_pattern, merchant, category, subcategory, status, "
            " source, confidence, rationale, notes, resolved_at) "
            "VALUES (?,?,?,?, 'approved', 'manual', 1.0, ?, ?, datetime('now')) "
            "ON CONFLICT(raw_pattern) DO UPDATE SET merchant=excluded.merchant, category=excluded.category, "
            " subcategory=excluded.subcategory, status='approved', source='manual', notes=excluded.notes, "
            " resolved_at=excluded.resolved_at",
            (body.descriptor, body.merchant, body.category, body.subcategory, body.note, body.note),
        )

    rule_created = False
    if body.create_rule and body.descriptor and body.category:
        conn.execute(
            "INSERT INTO merchant_rules (match_type, pattern, merchant, category, subcategory, priority, origin) "
            "VALUES ('contains', ?, ?, ?, ?, 100, 'user')",
            (body.descriptor, body.merchant, body.category, body.subcategory),
        )
        rule_created = True
    conn.commit()
    return {"updated": updated, "rule_created": rule_created}


# ---------------------------------------------------------------------- single edit
class EditBody(BaseModel):
    category: str | None = None
    subcategory: str | None = None
    merchant: str | None = None
    counterparty: str | None = None
    is_transfer: bool | None = None
    counts_as_spend: bool | None = None
    notes: str | None = None


@router.post("/transactions/{txn_id}")
def edit_transaction(txn_id: str, body: EditBody,
                     _: None = Depends(require_session), conn=Depends(get_conn)) -> dict:
    if conn.execute("SELECT 1 FROM transactions WHERE txn_id=?", (txn_id,)).fetchone() is None:
        raise HTTPException(404, "not found")
    cid = counterparties.get_or_create(conn, body.counterparty) if body.counterparty else None
    sets = ["categorised_by='manual'", "needs_review=0", "updated_at=datetime('now')"]
    vals: list = []
    for col, val in [("category", body.category), ("subcategory", body.subcategory),
                     ("merchant", body.merchant), ("notes", body.notes),
                     ("is_transfer", int(body.is_transfer) if body.is_transfer is not None else None),
                     ("counts_as_spend", int(body.counts_as_spend) if body.counts_as_spend is not None else None),
                     ("counterparty_id", cid)]:
        if val is not None:
            sets.append(f"{col}=?"); vals.append(val)
    conn.execute(f"UPDATE transactions SET {', '.join(sets)} WHERE txn_id=?", vals + [txn_id])
    conn.commit()
    return {"ok": True}


# ------------------------------------------------------------------- period review
@router.get("/periods")
def periods(_: None = Depends(require_session), conn=Depends(get_conn), worst_first: bool = True) -> dict:
    rows = conn.execute(
        "SELECT strftime('%Y-%m', posting_date) period, "
        "COUNT(*) total, "
        "SUM(CASE WHEN is_transfer=0 AND category IS NOT NULL THEN 1 ELSE 0 END) categorised, "
        "SUM(CASE WHEN needs_review=1 THEN 1 ELSE 0 END) to_review, "
        "SUM(CASE WHEN needs_review=1 AND amount_pennies<0 THEN amount_pennies ELSE 0 END) review_pennies "
        "FROM transactions GROUP BY period ORDER BY period DESC"
    ).fetchall()
    signed = {r["period"]: r["reviewed_at"] for r in
              conn.execute("SELECT period, reviewed_at FROM period_review").fetchall()}
    out = [dict(r, signed_off=signed.get(r["period"])) for r in rows]
    if worst_first:
        out.sort(key=lambda p: p["review_pennies"])   # most uncategorised spend first
    return {"periods": out}


@router.post("/periods/{period}/signoff")
def signoff(period: str, _: None = Depends(require_session), conn=Depends(get_conn)) -> dict:
    r = conn.execute(
        "SELECT COUNT(*) total, SUM(CASE WHEN is_transfer=0 AND category IS NOT NULL THEN 1 ELSE 0 END) cat "
        "FROM transactions WHERE strftime('%Y-%m', posting_date)=?", (period,)
    ).fetchone()
    pct = round((r["cat"] or 0) / r["total"] * 100, 1) if r["total"] else 0.0
    conn.execute(
        "INSERT INTO period_review (period, pct_categorised_at_signoff, reviewed_at) "
        "VALUES (?,?,datetime('now')) "
        "ON CONFLICT(period) DO UPDATE SET pct_categorised_at_signoff=excluded.pct_categorised_at_signoff, "
        "reviewed_at=excluded.reviewed_at", (period, pct),
    )
    conn.commit()
    return {"period": period, "pct_categorised": pct}
