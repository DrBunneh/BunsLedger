-- Ledgerline canonical store. SQLite. Money is stored as signed integer pennies
-- (NEGATIVE = money out) across every source. Format to £ only for display.
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Taxonomy
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS categories (
    name                   TEXT PRIMARY KEY,
    parent                 TEXT REFERENCES categories(name),
    kind                   TEXT NOT NULL CHECK (kind IN ('income', 'spend', 'transfer')),
    monthly_budget_pennies INTEGER
);

-- ---------------------------------------------------------------------------
-- Counterparties: first-class so "P T Wilson" / "PETER WILSON" / "Peter Wilson"
-- collapse to one entity you can rename and sum against (interpersonal transfers).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS counterparties (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT NOT NULL UNIQUE,
    kind           TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (kind IN ('person', 'self', 'merchant', 'institution', 'unknown')),
    notes          TEXT,
    created_at     TEXT NOT NULL
);

-- Alternate spellings that resolve to one counterparty (merge history without re-tagging).
CREATE TABLE IF NOT EXISTS counterparty_aliases (
    alias           TEXT PRIMARY KEY,
    counterparty_id INTEGER NOT NULL REFERENCES counterparties(id)
);

-- Links the two legs of a transfer (interpersonal / inter-account / pot / card payment).
CREATE TABLE IF NOT EXISTS transfer_groups (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT,   -- inter_account | interpersonal | pot | card_payment
    note       TEXT,
    created_at TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Transactions: one row per transaction, one category per row (no splits -
-- bank-rec granularity can't support line-item allocation).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transactions (
    txn_id            TEXT PRIMARY KEY,     -- deterministic hash over RAW fields (see dedup.py)
    account           TEXT NOT NULL,        -- monzo | nationwide | aqua
    institution       TEXT,                 -- Monzo | Nationwide | Aqua (NewDay)
    posting_date      TEXT NOT NULL,        -- ISO yyyy-mm-dd
    effective_date    TEXT,                 -- value date where distinct (Nationwide "Effective Date")
    datetime          TEXT,                 -- full ISO timestamp where available (Monzo)
    description_raw   TEXT NOT NULL,        -- exactly as printed; hash input; NEVER mutated
    description_extra TEXT,                 -- Monzo subtitle / statement reference
    merchant          TEXT,                 -- cleaned canonical name
    counterparty_id   INTEGER REFERENCES counterparties(id),
    amount_pennies    INTEGER NOT NULL,     -- signed; NEGATIVE = money out (unified across sources)
    currency          TEXT NOT NULL DEFAULT 'GBP',
    fx_amount         REAL,                 -- original amount, e.g. 15.00
    fx_currency       TEXT,                 -- e.g. USD, EUR
    fx_rate           REAL,                 -- e.g. 1.3464
    balance_after     INTEGER,              -- pennies; printed on some rows (Nationwide) - parser checksum
    txn_type          TEXT,                 -- card|direct_debit|faster_payment|standing_order|
                                            --   bank_credit|interest|fee|transfer|purchase|unknown
    status            TEXT NOT NULL DEFAULT 'posted'
                          CHECK (status IN ('posted', 'pending', 'declined')),
    category          TEXT REFERENCES categories(name),
    subcategory       TEXT REFERENCES categories(name),
    source_category   TEXT,                 -- the bank's OWN label (Monzo `categories`), preserved as a signal
    is_transfer       INTEGER NOT NULL DEFAULT 0,   -- money between your own accounts; excluded from spend
    transfer_group    INTEGER REFERENCES transfer_groups(id),
    counts_as_spend   INTEGER,              -- override; NULL => derive from category.kind (mortgage/pot edge cases)
    source_id         TEXT,                 -- native id (Monzo feed id)
    source_file       TEXT NOT NULL,        -- provenance
    categorised_by    TEXT,                 -- rule|directory|source_map|manual|auto|none
    confidence        REAL,                 -- 0..1; drives auto-apply vs review gating
    rationale         TEXT,                 -- why this category was chosen (esp. `auto`)
    evidence_url      TEXT,                 -- source found when it searched, if any
    needs_review      INTEGER NOT NULL DEFAULT 0,
    notes             TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_txn_posting_date ON transactions(posting_date);
CREATE INDEX IF NOT EXISTS ix_txn_account      ON transactions(account);
CREATE INDEX IF NOT EXISTS ix_txn_merchant     ON transactions(merchant);
CREATE INDEX IF NOT EXISTS ix_txn_needs_review ON transactions(needs_review);

-- ---------------------------------------------------------------------------
-- Tags: many-to-many cross-cutting labels (japan-2025, wedding, reimbursable)
-- on top of the single category. This is where custom analysis lives.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tags (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE,
    kind  TEXT,     -- project | trip | flag | ...
    notes TEXT
);
CREATE TABLE IF NOT EXISTS transaction_tags (
    txn_id TEXT NOT NULL REFERENCES transactions(txn_id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (txn_id, tag_id)
);

-- ---------------------------------------------------------------------------
-- Categorisation memory & rules
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS merchant_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    match_type  TEXT NOT NULL CHECK (match_type IN ('exact', 'contains', 'regex')),
    pattern     TEXT NOT NULL,
    merchant    TEXT,
    category    TEXT REFERENCES categories(name),
    subcategory TEXT REFERENCES categories(name),
    priority    INTEGER NOT NULL DEFAULT 100,
    origin      TEXT NOT NULL DEFAULT 'user'   -- 'seed' (reloadable) | 'user' (yours, preserved)
);

CREATE TABLE IF NOT EXISTS merchant_directory (
    raw_pattern TEXT PRIMARY KEY,
    merchant    TEXT,
    category    TEXT REFERENCES categories(name),
    subcategory TEXT REFERENCES categories(name),
    status      TEXT NOT NULL DEFAULT 'proposed'
                    CHECK (status IN ('proposed', 'approved', 'auto')),
    source      TEXT CHECK (source IN ('manual', 'inference', 'web')),
    confidence  REAL,
    rationale   TEXT,
    evidence_url TEXT,
    notes       TEXT,
    resolved_at TEXT
);

-- Bank's own category -> our taxonomy (bootstrap). Applied only for unambiguous maps.
CREATE TABLE IF NOT EXISTS category_map (
    account         TEXT NOT NULL,
    source_category TEXT NOT NULL,
    category        TEXT REFERENCES categories(name),
    subcategory     TEXT REFERENCES categories(name),
    is_transfer     INTEGER NOT NULL DEFAULT 0,
    counts_as_spend INTEGER,
    PRIMARY KEY (account, source_category)
);

CREATE TABLE IF NOT EXISTS enrichment_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_pattern    TEXT,
    query          TEXT,
    result_summary TEXT,
    evidence_url   TEXT,
    searched_at    TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Provenance & coverage
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS import_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file        TEXT NOT NULL,
    source      TEXT,
    rows_seen   INTEGER,
    rows_new    INTEGER,
    rows_dup    INTEGER,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_coverage (
    account       TEXT PRIMARY KEY,
    complete_from TEXT,
    complete_to   TEXT,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS period_review (
    period                    TEXT PRIMARY KEY,   -- yyyy-mm
    accounts                  TEXT,
    pct_categorised_at_signoff REAL,
    reviewed_at               TEXT,
    notes                     TEXT
);

-- Monzo OAuth state + forward-sync cursor (single-user, local-only; DB is git-ignored).
CREATE TABLE IF NOT EXISTS monzo_auth (
    id            INTEGER PRIMARY KEY CHECK (id = 1),   -- single row
    access_token  TEXT,
    refresh_token TEXT,
    expires_at    TEXT,          -- ISO; when the access token expires
    account_id    TEXT,          -- the Monzo account we sync
    user_id       TEXT,
    cursor        TEXT,          -- last synced transaction id (forward-sync watermark)
    last_sync_at  TEXT,
    updated_at    TEXT
);
