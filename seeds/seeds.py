"""Seed data: taxonomy, Monzo-category bootstrap map, and starter rules.

Python (not YAML) so the core has no third-party dependency to load its seeds.
Everything here lands in editable DB tables - change categories/rules at runtime,
re-run `categorise`, and history re-flows (manual decisions stay sticky).
"""

# (name, parent, kind). Subcategories carry parent's kind.
CATEGORIES = [
    ("Income", None, "income"),
    ("Salary/Client", "Income", "income"),
    ("Interest", "Income", "income"),
    ("Cashback", "Income", "income"),
    ("Refunds", "Income", "income"),
    ("Housing", None, "spend"),
    ("Mortgage", "Housing", "spend"),
    ("Service charge", "Housing", "spend"),
    ("Help to Buy", "Housing", "spend"),
    ("Utilities", None, "spend"),
    ("Energy", "Utilities", "spend"),
    ("Water", "Utilities", "spend"),
    ("Broadband/Mobile", "Utilities", "spend"),
    ("Insurance", None, "spend"),
    ("Life", "Insurance", "spend"),
    ("Pet insurance", "Insurance", "spend"),
    ("Groceries", None, "spend"),
    ("Eating out", None, "spend"),
    ("Restaurants", "Eating out", "spend"),
    ("Takeaway", "Eating out", "spend"),
    ("Coffee/Snacks", "Eating out", "spend"),
    ("Transport", None, "spend"),
    ("Rail", "Transport", "spend"),
    ("Rideshare", "Transport", "spend"),
    ("Parking/Airport", "Transport", "spend"),
    ("Transit", "Transport", "spend"),
    ("Software & Subscriptions", None, "spend"),
    ("Hobbies", None, "spend"),
    ("Trading cards", "Hobbies", "spend"),
    ("Games", "Hobbies", "spend"),
    ("Events", "Hobbies", "spend"),
    ("Shopping", None, "spend"),
    ("Marketplace", "Shopping", "spend"),
    ("Books", "Shopping", "spend"),
    ("General", "Shopping", "spend"),
    ("Entertainment", None, "spend"),
    ("Health", None, "spend"),
    ("Pets", None, "spend"),
    ("Crypto", None, "spend"),                       # Coinbase etc. (asset purchase)
    # Trading-card hobby, sliced by what the spend was for (Travel/Accom/Food/Supplies/Cards).
    # NB: subcategory names are globally unique, so the card-purchase leaf is "Card purchases".
    ("Cards", None, "spend"),
    ("Travel", "Cards", "spend"),
    ("Accommodation", "Cards", "spend"),
    ("Food", "Cards", "spend"),
    ("Supplies", "Cards", "spend"),
    ("Card purchases", "Cards", "spend"),
    ("Fees & Interest", None, "spend"),
    ("Transfers", None, "transfer"),
]

# Bank's own category -> ours. Only UNAMBIGUOUS mappings; ambiguous Monzo buckets
# (general, bills, holidays, finances, ...) are deliberately left for rules/review.
# (account, source_category, category, subcategory, is_transfer, counts_as_spend)
CATEGORY_MAP = [
    ("monzo", "groceries", "Groceries", None, 0, None),
    ("monzo", "eating out", "Eating out", None, 0, None),
    ("monzo", "transport", "Transport", None, 0, None),
    ("monzo", "income", "Income", None, 0, None),
    ("monzo", "shopping", "Shopping", None, 0, None),
    ("monzo", "entertainment", "Entertainment", None, 0, None),
    ("monzo", "transfers", "Transfers", None, 1, 0),
    ("monzo", "savings", "Transfers", None, 1, 0),   # pot moves are transfers, not spend
]

# (match_type, pattern, merchant, category, subcategory, priority). contains = case-insensitive substring.
MERCHANT_RULES = [
    ("contains", "SAINSBURYS", "Sainsbury's", "Groceries", None, 100),
    ("contains", "ALDI", "Aldi", "Groceries", None, 100),
    ("contains", "MORRISON", "Morrisons", "Groceries", None, 100),
    ("contains", "OCTOPUS ENERGY", "Octopus Energy", "Utilities", "Energy", 100),
    ("contains", "STWATER", "Severn Trent Water", "Utilities", "Water", 100),
    ("contains", "SEVERN TRENT", "Severn Trent Water", "Utilities", "Water", 100),
    ("contains", "TRAINLINE", "Trainline", "Transport", "Rail", 100),
    ("contains", "WEST MIDLANDS RAIL", "West Midlands Railway", "Transport", "Rail", 100),
    ("contains", "UBER", "Uber", "Transport", "Rideshare", 100),
    ("contains", "PRET", "Pret A Manger", "Eating out", "Coffee/Snacks", 100),
    ("contains", "GREGGS", "Greggs", "Eating out", "Coffee/Snacks", 100),
    ("contains", "MCDONALDS", "McDonald's", "Eating out", "Takeaway", 100),
    ("contains", "TONKOTSU", "Tonkotsu", "Eating out", "Restaurants", 100),
    ("contains", "TAMATANGA", "Tamatanga", "Eating out", "Restaurants", 100),
    ("contains", "JUST EAT", "Just Eat", "Eating out", "Takeaway", 100),
    ("contains", "OPENAI", "OpenAI", "Software & Subscriptions", None, 100),
    ("contains", "CHATGPT", "OpenAI", "Software & Subscriptions", None, 100),
    ("contains", "GITHUB", "GitHub", "Software & Subscriptions", None, 100),
    ("contains", "TRINITY ESTATES", "Trinity Estates", "Housing", "Service charge", 100),
    ("contains", "HELP TO BUY", "Help to Buy", "Housing", "Help to Buy", 100),
    ("contains", "mortgage a/c", "Mortgage", "Housing", "Mortgage", 90),
    ("contains", "LV LIFE", "LV=", "Insurance", "Life", 100),
    ("contains", "ANIMAL HEALTH CARE", "Animal Health Care", "Pets", None, 100),
    ("contains", "CARDMARKET", "Cardmarket", "Cards", "Card purchases", 100),
    ("contains", "FANFINITY", "Fanfinity", "Cards", "Card purchases", 100),
    ("contains", "DRAKKAR LUDIK", "Drakkar Ludik", "Cards", "Card purchases", 100),
    ("contains", "LORCAN", "Lorcan", "Cards", "Card purchases", 100),
    ("contains", "COINBASE", "Coinbase", "Crypto", None, 100),
    ("contains", "CB PAYMENTS", "Coinbase", "Crypto", None, 100),
    ("contains", "EBAY", "eBay", "Shopping", "Marketplace", 100),
    ("contains", "Interest added", "Interest", "Income", "Interest", 100),
]
