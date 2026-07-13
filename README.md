# Ledgerline

A local-first, single-user tool that ingests transactions from **Monzo**, **Nationwide**
and **Aqua**, normalises them into one store, categorises them (rules + memory, with an
LLM auto-classifier planned), detects transfers, and produces spending analysis.

> **Privacy:** every real statement, export and the SQLite database stays out of git
> (see `.gitignore`). Only anonymised fixtures are committed. Nothing leaves your machine.

## Why this exists

Off-the-shelf apps struggle with two things this tool is built around: **identifying
interpersonal / inter-account transfers** across banks, and **backfilling and correcting
a large history** of miscategorised or uncategorised transactions with rules you control.

## Status (v1 — the deterministic spine, verified on real files)

| Stage | State |
|---|---|
| Monzo CSV parser | ✅ 4,411 rows; signs + `source_category` preserved |
| Aqua PDF parser | ✅ credit-card sign flip + FX lines; region-gated |
| Nationwide PDF parser | ✅ positional Out/In/Balance; **balance reconciliation passes (0 mismatches)** |
| Deterministic ids / dedup | ✅ re-import idempotent; same-day repeats survive |
| Transfer detection (pattern-based) | ✅ conservative v1 |
| Categorisation cascade | ✅ transfers → manual → rule → source_map → review |
| Guided period review | ⏳ next |
| LLM auto-classifier (§6.3) | ⏳ deferred until the real uncategorised tail is measured |
| Insights / projections | ⏳ later |

On the sample data, first-pass categorisation reaches **~74% of spendable transactions**
automatically; the remainder is concentrated in Monzo's `general`/blank bucket, which the
review workflow targets.

## Quickstart

```bash
pip install -e .            # installs pymupdf
ledgerline init-db
ledgerline import statements/*.csv statements/*.pdf   # content-detected, any order
ledgerline categorise
ledgerline stats
```

Everything is idempotent: re-import the same or an overlapping file and only genuinely
new rows are added; re-run `categorise` after editing rules and history re-flows —
**manual decisions are never overwritten.**

## Design notes worth knowing

- **Money is integer pennies**, signed, `NEGATIVE = out`, unified across all sources.
- **`txn_id` for id-less PDFs is hashed over the RAW description**, never a normalised
  one — otherwise improving normalisation later would change every historical id and
  silently duplicate the back-catalogue.
- **Nationwide needs positional extraction**: in linear text the Out/In/Balance columns
  collapse to bare numbers; they're only separable by x-coordinate. `reconcile()` walks
  the printed running balance as a self-check.
- **Aqua's sign is inverted** (it's a credit card): printed `+` is a purchase (money out).
- **Cross-account fuzzy transfer matching is deliberately deferred** — it's the highest
  false-positive risk in the design.

See `docs/` / the design spec for the full pipeline, schema and auto-classifier contract.

## Layout

```
ledgerline/
  parsers/     monzo_csv, aqua_pdf, nationwide_pdf   (file -> list[CanonicalTxn])
  models.py    canonical record
  money.py     pennies + FX decimal parsing
  dedup.py     deterministic ids (raw-hash) + occurrence_index
  normalise.py descriptor cleanup + counterparty extraction
  db.py        schema init + idempotent upsert
  seeding.py   load taxonomy / category map / rules
  transfers.py pattern-based transfer flagging
  categorise.py the cascade
  analyse.py   stats / reports
  cli.py       import / categorise / stats
seeds/seeds.py taxonomy, Monzo->taxonomy map, starter rules
sources.yaml   human-readable source registry
```

## Roadmap

1. Guided period-by-period review (burn-down of the uncategorised backlog).
2. LLM auto-classifier for the residual tail (enum-constrained, cached per descriptor).
3. Recurring/subscription detection, projections, net-position.
4. Monzo API top-up ingestion (note: API only returns ~90 days — history stays CSV-driven).
