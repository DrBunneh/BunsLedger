"""SQLite store: schema init, seeding, and the idempotent upsert."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .dedup import txn_id
from .models import CanonicalTxn

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Source-derived fields refreshed on re-import; category/merchant are NOT here so a
# manual decision is never clobbered (further guarded by categorised_by='manual').
_SOURCE_FIELDS = ("balance_after", "fx_amount", "fx_currency", "fx_rate", "status")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()


def upsert_transactions(conn: sqlite3.Connection, txns: list[CanonicalTxn]) -> dict:
    """Insert new rows; on a matching txn_id refresh only source-derived fields and
    never overwrite a manual category. Returns {seen, new, dup}."""
    now = _now()
    seen = new = dup = 0
    for t in txns:
        t.validate()
        tid = txn_id(t)
        seen += 1
        row = conn.execute("SELECT categorised_by FROM transactions WHERE txn_id=?", (tid,)).fetchone()
        if row is None:
            _insert(conn, tid, t, now)
            new += 1
        else:
            _refresh(conn, tid, t, now)
            dup += 1
    conn.commit()
    return {"seen": seen, "new": new, "dup": dup}


def _insert(conn: sqlite3.Connection, tid: str, t: CanonicalTxn, now: str) -> None:
    conn.execute(
        """INSERT INTO transactions (
               txn_id, account, institution, posting_date, effective_date, datetime,
               description_raw, description_extra, amount_pennies, currency,
               fx_amount, fx_currency, fx_rate, balance_after, txn_type, status,
               source_category, source_id, source_file, categorised_by, needs_review,
               created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'none', 1, ?, ?)""",
        (tid, t.account, t.institution, t.posting_date, t.effective_date, t.datetime,
         t.description_raw, t.description_extra, t.amount_pennies, t.currency,
         t.fx_amount, t.fx_currency, t.fx_rate, t.balance_after, t.txn_type, t.status,
         t.source_category, t.source_id, t.source_file, now, now),
    )


def _refresh(conn: sqlite3.Connection, tid: str, t: CanonicalTxn, now: str) -> None:
    sets = ", ".join(f"{f}=?" for f in _SOURCE_FIELDS) + ", updated_at=?"
    values = [getattr(t, f) for f in _SOURCE_FIELDS] + [now, tid]
    conn.execute(f"UPDATE transactions SET {sets} WHERE txn_id=?", values)


def log_import(conn, file, source, counts) -> None:
    conn.execute(
        "INSERT INTO import_log (file, source, rows_seen, rows_new, rows_dup, imported_at)"
        " VALUES (?,?,?,?,?,?)",
        (file, source, counts["seen"], counts["new"], counts["dup"], _now()),
    )
    conn.commit()
