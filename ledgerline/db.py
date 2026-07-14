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
    # check_same_thread=False: one connection per request, never shared concurrently,
    # but FastAPI may set up the dependency and run the handler on different threads.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent schema migrations for DBs created before a change (CREATE TABLE
    IF NOT EXISTS won't alter an existing table)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(merchant_rules)").fetchall()}
    if cols and "origin" not in cols:
        conn.execute("ALTER TABLE merchant_rules ADD COLUMN origin TEXT NOT NULL DEFAULT 'user'")
        # Rows predating this column were all seed-managed (user rules couldn't survive the
        # old wipe-on-restart), so mark them 'seed' — reseed replaces them cleanly.
        conn.execute("UPDATE merchant_rules SET origin='seed'")

    catcols = {r["name"] for r in conn.execute("PRAGMA table_info(categories)").fetchall()}
    if catcols and "id" not in catcols:                 # pre parent-scoped taxonomy
        _migrate_parent_scoped(conn)


def _rebuild_drop_cat_fk(conn: sqlite3.Connection, tbl: str) -> None:
    """Rebuild `tbl` with the category FKs removed (category names are no longer unique).
    Reads the table's own DDL and strips only ` REFERENCES categories(name)`, preserving
    every other column, constraint and index."""
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (tbl,)).fetchone()[0]
    idx = [r[0] for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
        (tbl,)).fetchall()]
    new_ddl = ddl.replace(" REFERENCES categories(name)", "")
    cols = ",".join(f'"{r[1]}"' for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall())
    conn.execute(f"ALTER TABLE {tbl} RENAME TO _mig_{tbl}")
    conn.execute(new_ddl)
    conn.execute(f"INSERT INTO {tbl}({cols}) SELECT {cols} FROM _mig_{tbl}")
    conn.execute(f"DROP TABLE _mig_{tbl}")              # drops the old table's indexes too
    for s in idx:
        conn.execute(s)


def _migrate_parent_scoped(conn: sqlite3.Connection) -> None:
    """Move to parent-scoped subcategories: categories gets an id PK + composite-unique
    index; the four tables that referenced categories(name) drop those FKs."""
    prev = conn.isolation_level
    conn.isolation_level = None                          # autocommit so PRAGMA foreign_keys takes effect
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("PRAGMA legacy_alter_table=ON")     # don't rewrite child FK refs on RENAME
        conn.execute("BEGIN")
        conn.execute("DROP INDEX IF EXISTS ux_categories_name_parent")
        # Strip the category FKs from the referencing tables FIRST (while categories is still
        # named 'categories', so their DDL still says REFERENCES categories(name)).
        for tbl in ("transactions", "merchant_rules", "merchant_directory", "category_map"):
            _rebuild_drop_cat_fk(conn, tbl)
        # Then give categories an id PK + composite-unique index.
        conn.execute("ALTER TABLE categories RENAME TO _mig_categories")
        conn.execute("CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "name TEXT NOT NULL, parent TEXT, "
                     "kind TEXT NOT NULL CHECK (kind IN ('income','spend','transfer')), "
                     "monthly_budget_pennies INTEGER)")
        conn.execute("CREATE UNIQUE INDEX ux_categories_name_parent ON categories(name, IFNULL(parent,''))")
        conn.execute("INSERT INTO categories(name,parent,kind,monthly_budget_pennies) "
                     "SELECT name,parent,kind,monthly_budget_pennies FROM _mig_categories")
        conn.execute("DROP TABLE _mig_categories")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA legacy_alter_table=OFF")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.isolation_level = prev


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
