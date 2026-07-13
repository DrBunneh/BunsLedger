# Ledgerline / BunsLedger — Product Roadmap & Work Packages

Living document. Scopes the move from the verified CLI spine to a **local web app**:
upload + Monzo forward-sync ingestion, a backlog-review table, and reporting.

## Assumed architecture decisions

These are defaults; change here and the WPs below follow.

1. **Local web app.** A FastAPI backend runs on your machine and serves a browser UI
   at `localhost`. It reuses the existing `ledgerline` package (parsers, schema, dedup,
   categorise) unchanged. SQLite stays. PDF parsing, Monzo OAuth and the LLM API key all
   live server-side — i.e. on your machine. Nothing goes to the cloud.
   - *Rejected:* fully client-side (would rewrite the verified parsers in JS and can't
     safely hold the Monzo secret / LLM key); hosted cloud (financial PII off-machine).
   - *Optional later:* wrap the same backend in a desktop shell (pywebview/Tauri) — WP6.
2. **Frontend: HTMX + server-rendered**, minimal JS, charts via a locally-bundled lib.
   React remains a per-view swap-in if a specific screen outgrows HTMX. No external CDNs
   (privacy + offline).
3. **Local-only + passphrase.** Backend binds to `127.0.0.1`; a simple passphrase gate
   protects the UI since it exposes financial data.

## Data flow

```
        ┌── upload (CSV/PDF) ──┐
        │                      ▼
 Monzo API ──forward-sync──▶ ingest ──▶ normalise ──▶ dedup ──▶ SQLite
 (recent only)               (existing ledgerline pipeline)        │
 Aqua/Nationwide: no API ─▶ "sources status" nudges you to upload  │
                                                                   ▼
                          categorise cascade (+ LLM tail)  ◀─▶ review table (UI)
                                                                   │
                                                                   ▼
                                                     reporting dashboard (UI)
```

## Monzo integration — constraints to design around

- **Forward-sync fits the API.** Pulling only *new* transactions stays inside Monzo's
  ~90-day window, so the limit never bites; historical backfill stays CSV-driven.
- **Confidential OAuth client.** Register an app → client id/secret in `.env` (never git)
  → one-time browser auth → backend stores + refreshes tokens.
- **Periodic re-auth.** Monzo requires occasional re-approval in the app; the scheduled
  job handles routine pulls between those.
- **Polling first, webhooks later.** Start with a scheduled pull; `transaction.created`
  webhooks are a later upgrade (needs a reachable URL).
- **Aqua & Nationwide have no API** → UI shows last-import date per account and nudges
  you to drag-drop the latest statement.

---

## Work packages

Each WP has a standalone deliverable. ⭐ = core value.

### WP0 · Backend & app skeleton
- **Goal:** FastAPI app that reuses `ledgerline`, serves the UI shell, binds localhost.
- **Build:** app factory; DB connection/session; lightweight schema-migration/versioning;
  `.env` config; passphrase auth middleware; static/template serving; health endpoint.
- **Done when:** `ledgerline serve` starts the backend and opens an (empty) authenticated
  shell that reads live data from the DB.
- **Depends:** —

### WP1 · Upload ingestion + sources status
- **Goal:** Get files in through the browser; show freshness per source.
- **Build:** `POST /ingest` (multipart, multi-file) → `detect → parse → assign
  occurrence_index → upsert → import_log`; returns per-file summary (seen/new/dup).
  Drag-drop UI + results panel. "Sources status" panel: last import date + row count per
  account, staleness badges + nudges for Aqua/Nationwide.
- **Done when:** drag-drop your three files → rows imported, re-upload is idempotent,
  status panel reflects it.
- **Depends:** WP0

### WP2 · Transactions + backlog review table ⭐
- **Goal:** Work through the uncategorised backlog fast, your way.
- **Build:**
  - Read API: pagination, sort, filter (account, date range, category, `needs_review`,
    transfer), full-text search on descriptor/merchant/counterparty.
  - Table UI with those controls; **group-by-merchant** review mode.
  - Inline + **bulk** actions: set category/subcategory, mark/unmark transfer, assign
    counterparty (create/merge), add tags.
  - "Make this a **rule** or **directory** entry" from a decision; **retro-apply** the
    decision across all history for that merchant.
  - Period review: pick period (`--next-worst`/`--oldest-unreviewed`), briefing (% done,
    £ + count outstanding), sign-off recorded in `period_review`, burn-down view.
  - Sticky manual: re-running categorise never overwrites hand decisions.
- **Done when:** you can clear a month merchant-by-merchant, spin decisions into rules,
  retro-apply them, and sign the period off.
- **Depends:** WP0, WP1

### WP3 · Reporting dashboard
- **Goal:** Accurate, coverage-aware spend/cashflow insight.
- **Build:** aggregation endpoints + charts — spend by category (month/quarter/year),
  cashflow in/out, trend vs trailing 3/6-mo average, recurring/subscription detection
  (active/new/lapsed), committed-vs-discretionary, net position (Aqua as liability).
  Every aggregate **excludes transfers** and **shades periods before full coverage**.
- **Done when:** a dashboard answers "what did I spend on X this quarter vs trend" and
  never silently sums across incomplete coverage.
- **Depends:** WP0 + data

### WP4 · LLM auto-classifier (the residual tail)
- **Goal:** Auto-resolve what rules/directory miss — cheaply, traceably.
- **Build:** dedupe unknowns by `descriptor_normalised`; assemble sibling signals; one
  Claude call per descriptor with `web_search` + **enum-forced** `submit_classification`
  (category enum injected from the taxonomy, `Unknown ▸ Needs review` sentinel);
  confidence gating (auto-apply ≥ threshold else review); cache to `merchant_directory`;
  log to `enrichment_log`; `--no-search` mode + per-run search budget. Proposals surface
  **inside the WP2 table** with rationale/evidence to accept/edit.
- **Note:** build only after WP2 shows the *real* size/shape of the tail, so this stays a
  small targeted piece, not a leap of faith. Confidence is a review aid, not auto-truth.
- **Depends:** WP2, stable taxonomy

### WP5 · Monzo forward-sync
- **Goal:** Automate ongoing Monzo ingestion.
- **Build:** OAuth confidential client (register app, localhost redirect); secure local
  token store + refresh; "fetch new since cursor" → canonical → existing upsert; scheduler
  (APScheduler) or documented cron; re-auth UX when Monzo demands it; optional
  `transaction.created` webhook receiver later.
- **Done when:** after one-time auth, new Monzo transactions appear without manual export.
- **Depends:** WP0, WP1

### WP6 · Run experience / packaging
- **Goal:** Make it pleasant to launch and (optionally) feel like an app.
- **Build:** one-command launch that starts the backend + opens the browser; `.env`
  template + first-run setup; docs. Optional desktop shell (pywebview/Tauri).
- **Depends:** cross-cutting (basic launch lands in WP0; polish last)

---

## Sequencing

- **Phase 1 — usable core:** WP0 → WP1 → **WP2**. Delivers the #1 goal (upload + work the
  backlog in a real table) before anything else exists.
- **Phase 2 — insight:** WP3.
- **Phase 3 — automation:** WP4 and WP5 (either order).
- WP6: basic launch early, polish last.

## Cross-cutting (every WP)

Secrets discipline (`.env`, never git) · schema migrations · localhost binding +
passphrase · tests (extend the existing suite; add PDF fixtures) · structured errors ·
no external CDNs.

## Open decisions

1. Frontend HTMX vs React (default HTMX; revisit if WP2 table UX needs it).
2. Auto-apply confidence threshold for WP4 (default 0.85, conservative).
3. Mortgage / pot moves: spend or transfer by default (`counts_as_spend` override exists).
4. Desktop shell in WP6 — do you actually want the "app" feel, or is a localhost tab fine?
