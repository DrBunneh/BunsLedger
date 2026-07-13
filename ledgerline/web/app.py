"""FastAPI application factory. API-first: every client (web SPA now, desktop shell,
future phone app) consumes the same JSON API under /api. Static SPA is served at /."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .. import __version__, db
from .config import settings

STATIC_DIR = Path(__file__).with_name("static")


def create_app() -> FastAPI:
    app = FastAPI(title="Ledgerline", version=__version__)

    # Ensure schema + seeds exist so a fresh install serves immediately.
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    from .. import seeding
    seeding.seed(conn)
    conn.close()

    from .api import system
    app.include_router(system.router)
    # Later WPs register their routers here:
    from .api import ingest, transactions, reports, classify
    app.include_router(ingest.router)
    app.include_router(transactions.router)
    app.include_router(reports.router)
    app.include_router(classify.router)

    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


app = create_app()
