"""Ledgerline CLI. All commands are idempotent and re-runnable.

    ledgerline init-db
    ledgerline import <file> [<file> ...]
    ledgerline categorise
    ledgerline stats
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import analyse, db, parsers, seeding
from .categorise import categorise
from .dedup import assign_occurrence_index

DEFAULT_DB = "finance.db"


def cmd_init_db(args) -> None:
    conn = db.connect(args.db)
    db.init_db(conn)
    seeding.seed(conn)
    print(f"Initialised {args.db} (schema + seeds).")


def cmd_import(args) -> None:
    conn = db.connect(args.db)
    db.init_db(conn)
    seeding.seed(conn)
    for f in args.files:
        source = parsers.detect(f)
        txns = parsers.parse(f, source)
        assign_occurrence_index(txns)
        counts = db.upsert_transactions(conn, txns)
        db.log_import(conn, Path(f).name, source, counts)
        print(f"  {Path(f).name:<45} [{source}]  "
              f"seen={counts['seen']} new={counts['new']} dup={counts['dup']}")


def cmd_categorise(args) -> None:
    conn = db.connect(args.db)
    result = categorise(conn)
    print(f"Categorise: transfers={result['transfers']} rules={result['rules']} "
          f"source_map={result['source_map']} needs_review={result['needs_review']}")


def cmd_stats(args) -> None:
    conn = db.connect(args.db)
    analyse.print_stats(conn)


def cmd_serve(args) -> None:
    from .web.server import serve
    serve(open_browser=not args.no_browser)


def cmd_app(args) -> None:
    from .web.server import desktop
    desktop()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ledgerline", description=__doc__)
    p.add_argument("--db", default=DEFAULT_DB, help="SQLite path (default finance.db)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db").set_defaults(func=cmd_init_db)
    imp = sub.add_parser("import"); imp.add_argument("files", nargs="+"); imp.set_defaults(func=cmd_import)
    sub.add_parser("categorise").set_defaults(func=cmd_categorise)
    sub.add_parser("stats").set_defaults(func=cmd_stats)
    srv = sub.add_parser("serve", help="run the local web app (browser)")
    srv.add_argument("--no-browser", action="store_true"); srv.set_defaults(func=cmd_serve)
    sub.add_parser("app", help="run as a desktop window (app feel)").set_defaults(func=cmd_app)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
