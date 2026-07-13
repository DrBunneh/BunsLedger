"""Shared FastAPI dependencies."""
from __future__ import annotations

from typing import Iterator

from .. import db
from .config import settings


def get_conn() -> Iterator:
    """One SQLite connection per request (sqlite3 is not safe to share across threads)."""
    conn = db.connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()
