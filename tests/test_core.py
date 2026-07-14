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


def test_similar_matches_variants_not_name_noise():
    from ledgerline import db, seeding
    from ledgerline.similar import similar_groups
    conn = db.connect(":memory:"); db.init_db(conn); seeding.seed(conn)
    now = "2025-01-01T00:00:00"
    rows = [("Sainsburys S/mkts Birmingham Co", -1250), ("Sainsburys Superma Islington St", -540),
            ("SAINSBURY'S SMKT", -300), ("Premier Inn London", -8300), ("Peter Wilson", -2000)]
    for i, (desc, amt) in enumerate(rows):
        conn.execute(
            "INSERT INTO transactions (txn_id, account, posting_date, description_raw, amount_pennies, "
            "source_file, needs_review, created_at, updated_at) VALUES (?,?,?,?,?,?,1,?,?)",
            (f"t{i}", "monzo", "2025-01-01", desc, amt, "x", now, now))
    conn.commit()
    hits = {g["descriptor"] for g in similar_groups(conn, "Sainsburys S/mkts Birmingham Co")}
    assert any("Superma" in h for h in hits)          # variant name caught
    assert any("SAINSBURY" in h.upper() for h in hits)
    # a different person's name must NOT be suggested for "Peter Wilson"
    noise = {g["descriptor"] for g in similar_groups(conn, "Peter Wilson")}
    assert "Premier Inn London" not in noise


def test_context_episodes_and_location():
    from ledgerline import db, seeding, context
    conn = db.connect(":memory:"); db.init_db(conn); seeding.seed(conn)
    # home = birmingham (5 rows), a 2-day London trip, and one unrelated home day later
    rows = [
        ("2025-11-01", "Sainsburys S/mkts Birmingham Co", -1200, "2025-11-01T12:00:00"),
        ("2025-11-02", "Greggs Birmingham", -300, "2025-11-02T09:00:00"),
        ("2025-11-03", "Aldi Birmingham", -2500, "2025-11-03T17:00:00"),
        ("2025-11-15", "Trainline London", -4500, "2025-11-15T08:30:00"),
        ("2025-11-15", "Tabac Cafe London", -1900, "2025-11-15T20:15:00"),
        ("2025-11-16", "Travelodge London", -8900, "2025-11-16T09:00:00"),
        ("2025-12-20", "Wm Morrison Birmingham", -3300, "2025-12-20T18:00:00"),
    ]
    for i, (d, desc, amt, dt) in enumerate(rows):
        conn.execute(
            "INSERT INTO transactions (txn_id, account, posting_date, datetime, description_raw, "
            "amount_pennies, source_file, needs_review, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,1,?,?)", (f"t{i}", "monzo", d, dt, desc, amt, "x", dt, dt))
    conn.commit()
    assert context.infer_home(conn) == "birmingham"
    assert context.is_away("Trainline London", None, "birmingham") is True
    assert context.is_away("Greggs Birmingham", None, "birmingham") is False
    assert context.meal_hint("2025-11-15T20:15:00") == "dinner"
    eps = context.episodes(conn, gap_days=3, min_txns=2)["episodes"]
    london = [e for e in eps if "london" in e["places"]]
    assert len(london) == 1                       # the 15-16 Nov trip clusters as one episode
    assert london[0]["count"] == 3 and london[0]["date_from"] == "2025-11-15"


def test_trip_seeding_excludes_commute_and_home_dd():
    from ledgerline import db, seeding, context
    conn = db.connect(":memory:"); db.init_db(conn); seeding.seed(conn)
    def ins(i, d, desc, amt, dt=None, txn_type=None):
        conn.execute(
            "INSERT INTO transactions (txn_id, account, posting_date, datetime, description_raw, "
            "amount_pennies, txn_type, source_file, needs_review, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,1,?,?)", (f"t{i}", "monzo", d, dt, desc, amt, txn_type, "x", d, d))
    # Establish Birmingham as home (dominant location).
    for j, d in enumerate(["2025-09-01", "2025-09-08", "2025-09-15", "2025-09-22", "2025-09-29"]):
        ins(100 + j, d, "Sainsburys Birmingham", -1500)
    # A commute-only London day: cheap TfL + a home lunch -> should NOT seed a trip.
    ins(0, "2025-10-01", "Transport for London", -290)
    ins(1, "2025-10-01", "Greggs Birmingham", -320)
    # A real trip: a £45 intercity fare + away food + an overnight, plus a home DD mid-trip.
    ins(2, "2025-10-10", "Trainline", -4500)                 # £45 journey -> seeds
    ins(3, "2025-10-10", "Tabac Cafe London", -1900)          # away food
    ins(4, "2025-10-10", "Travelodge London", -8900)          # overnight
    ins(5, "2025-10-10", "Octopus Energy", -9000, txn_type="direct_debit")  # home DD mid-trip
    conn.commit()
    eps = context.episodes(conn, gap_days=3, min_txns=2)["episodes"]
    days = {e["date_from"] for e in eps}
    assert "2025-10-01" not in days           # commute-only day is not a trip
    assert "2025-10-10" in days               # the real journey is
    trip = next(e for e in eps if e["date_from"] == "2025-10-10")
    descs = {t["description_raw"] for t in trip["transactions"]}
    assert "Octopus Energy" not in descs      # automated home DD excluded from the trip
    assert "Travelodge London" in descs


def test_parent_scoped_subcategories():
    from ledgerline import db, seeding
    conn = db.connect(":memory:"); db.init_db(conn); seeding.seed(conn)
    # seeds now include the same leaf name under multiple parents
    rows = conn.execute("SELECT parent FROM categories WHERE name='Food' ORDER BY parent").fetchall()
    parents = {r["parent"] for r in rows}
    assert {"Cards", "Holiday", "Work"} <= parents
    # the composite-unique index blocks a true duplicate but allows a new parent
    import sqlite3
    try:
        conn.execute("INSERT INTO categories(name,parent,kind) VALUES ('Food','Cards','spend')")
        assert False, "duplicate (Food, Cards) should be blocked"
    except sqlite3.IntegrityError:
        pass
    conn.execute("INSERT INTO categories(name,parent,kind) VALUES ('Food','Personal','spend')")  # ok


def test_trip_tag_split_and_untag():
    from ledgerline import db, seeding
    from ledgerline.web.api.context import tag_trip, untag_trip, TagTripBody, UntagTripBody
    conn = db.connect(":memory:"); db.init_db(conn); seeding.seed(conn)
    now = "2025-01-01T00:00:00"
    for i in range(6):
        conn.execute("INSERT INTO transactions (txn_id, account, posting_date, description_raw, "
                     "amount_pennies, source_file, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (f"t{i}", "monzo", "2025-01-01", f"m{i}", -100, "x", now, now))
    conn.commit()
    # split: two halves -> two trips
    tag_trip(TagTripBody(txn_ids=["t0", "t1", "t2"], name="A", purpose="card"), None, conn)
    tag_trip(TagTripBody(txn_ids=["t3", "t4", "t5"], name="B", purpose="holiday"), None, conn)
    def count(name):
        return conn.execute(
            "SELECT COUNT(*) FROM transaction_tags tt JOIN tags t ON t.id=tt.tag_id WHERE t.name=?",
            (name,)).fetchone()[0]
    assert count("A") == 3 and count("B") == 3
    # merge B into A, then untag from B
    tag_trip(TagTripBody(txn_ids=["t3", "t4", "t5"], name="A", purpose="card"), None, conn)
    untag_trip(UntagTripBody(txn_ids=["t3", "t4", "t5"], name="B"), None, conn)
    assert count("A") == 6 and count("B") == 0


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
