"""Load seed data (taxonomy, category map, rules) into the DB. Idempotent."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SEEDS_FILE = Path(__file__).resolve().parent.parent / "seeds" / "seeds.py"


def _load_seeds():
    spec = importlib.util.spec_from_file_location("ledgerline_seeds", _SEEDS_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def seed(conn) -> None:
    s = _load_seeds()
    conn.executemany(
        "INSERT OR IGNORE INTO categories (name, parent, kind) VALUES (?,?,?)",
        s.CATEGORIES,
    )
    conn.executemany(
        "INSERT OR IGNORE INTO category_map "
        "(account, source_category, category, subcategory, is_transfer, counts_as_spend) "
        "VALUES (?,?,?,?,?,?)",
        s.CATEGORY_MAP,
    )
    # Reload ONLY seed rules so seed-file edits take effect; user-created rules
    # (origin='user') are never touched. This is the fix for rules vanishing on restart.
    conn.execute("DELETE FROM merchant_rules WHERE origin='seed'")
    conn.executemany(
        "INSERT INTO merchant_rules (match_type, pattern, merchant, category, subcategory, priority, origin) "
        "VALUES (?,?,?,?,?,?, 'seed')",
        s.MERCHANT_RULES,
    )
    conn.commit()
