"""Monzo forward-sync: OAuth, token store, and cursor-based ingestion of NEW
transactions. Because it only ever pulls recent transactions, Monzo's ~90-day
access limit never bites (that limit only blocks historical backfill, which stays
CSV-driven)."""
