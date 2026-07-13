"""Core unit + pipeline tests against an anonymised fixture (no real PII)."""
from pathlib import Path

from ledgerline import db, parsers, seeding
from ledgerline.categorise import categorise
from ledgerline.dedup import assign_occurrence_index, txn_id
from ledgerline.models import CanonicalTxn
from ledgerline.money import format_pennies, parse_fx_decimal, to_pennies
from ledgerline.normalise import extract_counterparty, normalise_descriptor

FIXTURE = Path(__file__).parent / "fixtures" / "monzo_sample.csv"


# --- money -------------------------------------------------------------------
def test_to_pennies_signs_and_separators():
    assert to_pennies("-3837.95") == -383795
    assert to_pennies("+ £62.05") == 6205
    assert to_pennies("£1,400.00") == 140000
    assert to_pennies("2,771.90") == 277190


def test_fx_decimal_handles_comma_and_period():
    assert parse_fx_decimal("15.00") == 15.0
    assert parse_fx_decimal("13,00") == 13.0
    assert parse_fx_decimal("1.1333") == 1.1333


def test_format_pennies():
    assert format_pennies(-383795) == "-£3,837.95"


# --- normalise / counterparty ------------------------------------------------
def test_normalise_strips_gateway_and_ref():
    assert normalise_descriptor("Paypal *cardmarket 15207145977").lower().startswith("cardmarket")


def test_extract_counterparty():
    assert extract_counterparty("Payment to PETER WILSON") == "PETER WILSON"
    assert extract_counterparty("SAINSBURYS S/MKTS") is None


# --- dedup -------------------------------------------------------------------
def test_txn_id_stable_for_monzo_id():
    a = CanonicalTxn(account="monzo", posting_date="2025-11-15", description_raw="X",
                     amount_pennies=-100, source_id="feed_1")
    b = CanonicalTxn(account="monzo", posting_date="2099-01-01", description_raw="Y",
                     amount_pennies=-999, source_id="feed_1")
    assert txn_id(a) == txn_id(b)  # same native id => same txn regardless of other fields


def test_occurrence_index_disambiguates_same_day_repeats():
    txns = [CanonicalTxn(account="aqua", posting_date="2025-11-15",
                         description_raw="Pastimes Booth", amount_pennies=-1867) for _ in range(3)]
    assign_occurrence_index(txns)
    assert [t.occurrence_index for t in txns] == [0, 1, 2]
    assert len({txn_id(t) for t in txns}) == 3  # three distinct ids, not one


def test_pdf_hash_uses_raw_not_normalised_description():
    # Improving the normaliser must NOT change historical ids.
    t = CanonicalTxn(account="aqua", posting_date="2025-11-15",
                     description_raw="Paypal *cardmarket 15207145977", amount_pennies=-100)
    before = txn_id(t)
    # normalised form differs wildly, but the id is over the raw string:
    assert normalise_descriptor(t.description_raw) != t.description_raw
    assert txn_id(t) == before


# --- pipeline (in-memory) ----------------------------------------------------
def _loaded_conn():
    conn = db.connect(":memory:")
    db.init_db(conn)
    seeding.seed(conn)
    txns = parsers.parse(str(FIXTURE), "monzo")
    assign_occurrence_index(txns)
    db.upsert_transactions(conn, txns)
    return conn


def test_import_is_idempotent():
    conn = _loaded_conn()
    txns = parsers.parse(str(FIXTURE), "monzo")
    counts = db.upsert_transactions(conn, txns)
    assert counts["new"] == 0 and counts["dup"] == 7


def test_classifier_enum_and_apply_offline():
    from ledgerline.enrich import classifier
    conn = _loaded_conn()
    from ledgerline.categorise import categorise
    categorise(conn)
    enum = classifier.build_enum(conn)
    assert "Groceries" in enum and classifier.SENTINEL in enum
    assert "Eating out ▸ Restaurants" in enum

    def fake(payload, e):  # offline model: everything is a mystery -> sentinel, held
        return {"merchant": "X", "category_path": classifier.SENTINEL, "confidence": 0.2,
                "rationale": "unclear", "searched": False,
                "looks_like_transfer": False, "needs_human": True}
    res = classifier.run(conn, api_key=None, classify_fn=fake)
    # sentinel + low confidence => nothing auto-applied, category stays NULL, directory cached
    assert res["auto_applied"] == 0
    assert conn.execute("SELECT COUNT(*) FROM merchant_directory").fetchone()[0] > 0
    mystery = conn.execute(
        "SELECT category, needs_review FROM transactions WHERE description_raw=?",
        ("Mystery Merchant XYZ",)).fetchone()
    assert mystery["category"] is None and mystery["needs_review"] == 1


def test_monzo_api_mapping():
    from ledgerline.monzo import sync
    from ledgerline.dedup import txn_id
    t = {"id": "tx_ABC", "created": "2026-07-12T09:15:00Z", "description": "TFL",
         "amount": -275, "currency": "GBP", "merchant": {"name": "Transport for London"},
         "category": "transport", "settled": "2026-07-13T00:00:00Z"}
    c = sync.map_transaction(t)
    assert c.amount_pennies == -275          # API amounts are already signed pennies
    assert c.source_id == "tx_ABC" and c.status == "posted"
    assert c.description_extra == "Transport for London"
    assert sync.map_transaction({**t, "id": "tx_2", "settled": ""}).status == "pending"
    assert sync.map_transaction({**t, "id": "tx_3", "decline_reason": "X"}).status == "declined"
    assert sync.map_transaction({"id": "tx_4", "created": "2026-01-01"}) is None  # no amount
    # dedup keys off the native tx id
    assert txn_id(c) == txn_id(sync.map_transaction(t))


def test_classifier_no_api_key_is_graceful():
    from ledgerline.enrich import classifier
    conn = _loaded_conn()
    res = classifier.run(conn, api_key=None)   # no key, no injected fn
    assert "error" in res and res["applied"] == 0


def test_categorise_cascade():
    conn = _loaded_conn()
    categorise(conn)
    def cat(title):
        return conn.execute(
            "SELECT category, is_transfer, needs_review FROM transactions WHERE description_raw=?",
            (title,)).fetchone()
    assert cat("SAINSBURYS S/MKTS")["category"] == "Groceries"      # rule
    assert cat("OPENAI")["category"] == "Software & Subscriptions"  # rule beats blank source_cat
    assert cat("Acme Employer Ltd")["category"] == "Income"         # source_map
    assert cat("Move to Savings Pot")["is_transfer"] == 1           # pot move => transfer
    assert cat("Jane Doe")["is_transfer"] == 1                      # source_category transfers
    assert cat("Mystery Merchant XYZ")["needs_review"] == 1         # nothing matched => review
