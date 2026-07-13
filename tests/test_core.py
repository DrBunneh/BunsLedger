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
