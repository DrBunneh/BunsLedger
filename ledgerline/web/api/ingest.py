"""WP1: upload ingestion + sources status.

Upload one or more files -> content-detect -> parse -> dedup -> upsert -> categorise.
Reuses the exact pipeline the CLI uses, so web and CLI ingestion are identical.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ... import db, parsers
from ...categorise import categorise
from ...dedup import assign_occurrence_index
from ..auth import require_session
from ..deps import get_conn

router = APIRouter(prefix="/api")

# No-API accounts we nudge the user to upload; days after which a statement is "stale".
MANUAL_ACCOUNTS = {"aqua": 40, "nationwide": 35}


@router.post("/ingest")
async def ingest(files: list[UploadFile] = File(...), _: None = Depends(require_session), conn=Depends(get_conn)) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="no files uploaded")
    results = []
    for up in files:
        suffix = Path(up.filename or "upload").suffix or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(await up.read())
            tmp.flush()
            try:
                source = parsers.detect(tmp.name)
                txns = parsers.parse(tmp.name, source)
                for t in txns:
                    t.source_file = up.filename or Path(tmp.name).name
                assign_occurrence_index(txns)
                counts = db.upsert_transactions(conn, txns)
                db.log_import(conn, up.filename or Path(tmp.name).name, source, counts)
                results.append({"file": up.filename, "source": source, "ok": True, **counts})
            except Exception as e:  # a bad file shouldn't sink the whole batch
                results.append({"file": up.filename, "ok": False, "error": str(e)})

    cat = categorise(conn) if any(r.get("ok") and r.get("new") for r in results) else None
    return {"results": results, "categorise": cat}


@router.get("/sources/status")
def sources_status(_: None = Depends(require_session), conn=Depends(get_conn)) -> dict:
    rows = conn.execute(
        "SELECT account, COUNT(*) n, MAX(posting_date) last_txn FROM transactions GROUP BY account"
    ).fetchall()
    by_acct = {r["account"]: r for r in rows}
    last_import = {
        r["source"]: r["imported_at"]
        for r in conn.execute(
            "SELECT source, MAX(imported_at) imported_at FROM import_log GROUP BY source"
        ).fetchall()
    }
    today = conn.execute("SELECT date('now')").fetchone()[0]

    out = []
    for acct in ("monzo", "nationwide", "aqua"):
        r = by_acct.get(acct)
        last_txn = r["last_txn"] if r else None
        stale = False
        if last_txn and acct in MANUAL_ACCOUNTS:
            days = conn.execute("SELECT julianday(?) - julianday(?)", (today, last_txn)).fetchone()[0]
            stale = days is not None and days > MANUAL_ACCOUNTS[acct]
        out.append({
            "account": acct,
            "rows": r["n"] if r else 0,
            "last_txn": last_txn,
            "last_import": last_import.get(acct),
            "manual_upload": acct in MANUAL_ACCOUNTS,
            "stale": stale,
        })
    return {"sources": out, "today": today}
