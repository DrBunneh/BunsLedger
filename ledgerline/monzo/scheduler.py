"""Optional periodic forward-sync. Needs `pip install .[monzo]` (APScheduler).

Either run this scheduler in-process, or (simpler, no extra dep) point cron at the
sync endpoint:  */30 * * * *  curl -fsS -X POST http://127.0.0.1:8000/api/monzo/sync
"""
from __future__ import annotations

from .. import db
from ..categorise import categorise
from . import store, sync
from ..web.config import settings


def sync_once() -> dict:
    conn = db.connect(settings.db_path)
    try:
        token = store.valid_token(conn, settings)
        auth = store.load(conn)
        if not token or not auth or not auth.get("account_id"):
            return {"skipped": "not connected"}
        result = sync.sync(conn, token, auth["account_id"], auth.get("cursor"))
        store.save(conn, cursor=result["cursor"], last_sync_at=sync.now_iso())
        if result.get("new"):
            categorise(conn)
        return result
    finally:
        conn.close()


def start(interval_minutes: int = 30):
    """Start a background scheduler (returns it so the caller can keep it alive)."""
    from apscheduler.schedulers.background import BackgroundScheduler
    sched = BackgroundScheduler()
    sched.add_job(sync_once, "interval", minutes=interval_minutes, id="monzo_sync")
    sched.start()
    return sched
