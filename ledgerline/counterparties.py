"""Counterparty resolution: collapse spelling variants ("PETER WILSON" / "P T
Wilson") onto one entity you can rename and sum against."""
from __future__ import annotations


def get_or_create(conn, name: str, kind: str = "unknown") -> int:
    name = name.strip()
    row = conn.execute(
        "SELECT counterparty_id FROM counterparty_aliases WHERE alias=? COLLATE NOCASE", (name,)
    ).fetchone()
    if row:
        return row[0]
    row = conn.execute(
        "SELECT id FROM counterparties WHERE canonical_name=? COLLATE NOCASE", (name,)
    ).fetchone()
    if row:
        cid = row[0]
    else:
        cid = conn.execute(
            "INSERT INTO counterparties (canonical_name, kind, created_at) VALUES (?,?,datetime('now'))",
            (name, kind),
        ).lastrowid
    conn.execute(
        "INSERT OR IGNORE INTO counterparty_aliases (alias, counterparty_id) VALUES (?,?)", (name, cid)
    )
    return cid


def merge(conn, keep_id: int, drop_id: int) -> None:
    """Point everything at keep_id and delete the duplicate. Aliases follow."""
    conn.execute("UPDATE transactions SET counterparty_id=? WHERE counterparty_id=?", (keep_id, drop_id))
    conn.execute("UPDATE counterparty_aliases SET counterparty_id=? WHERE counterparty_id=?", (keep_id, drop_id))
    conn.execute("DELETE FROM counterparties WHERE id=?", (drop_id,))
    conn.commit()


def rename(conn, cid: int, new_name: str) -> None:
    conn.execute("UPDATE counterparties SET canonical_name=? WHERE id=?", (new_name.strip(), cid))
    conn.execute("INSERT OR IGNORE INTO counterparty_aliases (alias, counterparty_id) VALUES (?,?)",
                 (new_name.strip(), cid))
    conn.commit()
