"""WP4: LLM auto-classifier (spec §6.3 / Appendix A).

Reached only for descriptors that miss transfer/manual/directory/rule. Deduped by
normalised descriptor (one model call per distinct merchant). The model calls
`submit_classification` exactly once with a category_path constrained to the current
taxonomy enum, so it can never invent a category. Deterministic-first + directory
caching keep call volume — and cost — low.

Live runs need ANTHROPIC_API_KEY and the `anthropic` package (pip install .[llm]).
Everything except the network call is unit-tested; inject `classify_fn` to test offline.
"""
from __future__ import annotations

import json
from typing import Callable

from ..normalise import normalise_descriptor

SENTINEL = "Unknown ▸ Needs review"

_SYSTEM = """You categorise a single UK bank or credit-card transaction into a fixed taxonomy.
You are given one merchant descriptor (already de-duplicated) plus contextual signals.
Return exactly one classification by calling submit_classification once.

Descriptors are UK-centric and often mangled: payment-facilitator prefixes (PAYPAL*,
SUMUP*, ZETTLE*, SP*, EB*), trailing location/country codes (FRA, ENG, LND, DE, CA), and
reference numbers. descriptor_normalised has most of this stripped. `direction` tells you
whether money left (outflow = spending) or arrived (inflow) — trust it, never re-derive the
sign. An `fx` block means a foreign transaction — a strong hint the spend is travel/location
related. `occurrences` describes cadence — regular, stable amounts suggest subscriptions/bills.

How to decide:
1. Classify from your own knowledge and the signals first. Use web_search ONLY if the
   merchant is genuinely unfamiliar AND the signals don't already make the category clear.
   At most 2 searches. Never search person names or apparent internal transfers.
2. Choose exactly one category_path from the provided enum. Never invent categories. If you
   truly cannot place it, choose "%s" and set needs_human = true.
3. Set confidence honestly: 0.90-1.00 unmistakable; 0.70-0.89 confident on category, minor
   subcategory doubt; 0.40-0.69 reasonable guess / search only suggestive; 0.00-0.39 weak.
   Reserve >=0.90 for the unmistakable; if a search is only suggestive, cap ~0.6.
4. If the line looks like money moved between the person's own accounts (round-number
   payments to a name, credit-card payments, savings-pot moves), set looks_like_transfer =
   true and return promptly — do not treat it as spend.

Keep rationale to one sentence. If you searched, put the single best source in evidence_url
and <=20 words in evidence_summary. Call submit_classification exactly once.

Taxonomy (choose category_path from these leaves only):
%s""" % (SENTINEL, "%s")


def build_enum(conn) -> list[str]:
    """Flattened 'Category' + 'Category ▸ Subcategory' leaves + the sentinel."""
    rows = conn.execute("SELECT name, parent FROM categories ORDER BY parent IS NOT NULL, name").fetchall()
    tops = [r["name"] for r in rows if r["parent"] is None]
    subs: dict[str, list[str]] = {}
    for r in rows:
        if r["parent"]:
            subs.setdefault(r["parent"], []).append(r["name"])
    paths = []
    for t in tops:
        paths.append(t)
        paths += [f"{t} ▸ {s}" for s in subs.get(t, [])]
    paths.append(SENTINEL)
    return paths


def _tool(enum: list[str]) -> dict:
    return {
        "name": "submit_classification",
        "description": "Return exactly one classification for the given transaction descriptor.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "merchant": {"type": "string"},
                "category_path": {"type": "string", "enum": enum},
                "confidence": {"type": "number"},
                "rationale": {"type": "string"},
                "searched": {"type": "boolean"},
                "evidence_url": {"type": ["string", "null"]},
                "evidence_summary": {"type": ["string", "null"]},
                "looks_like_transfer": {"type": "boolean"},
                "needs_human": {"type": "boolean"},
            },
            "required": ["merchant", "category_path", "confidence", "rationale",
                         "searched", "looks_like_transfer", "needs_human"],
        },
    }


def assemble_unknowns(conn) -> list[dict]:
    """Dedupe the needs-review backlog by normalised descriptor and gather sibling signals."""
    rows = conn.execute(
        "SELECT description_raw, amount_pennies, account, currency, fx_amount, fx_currency, "
        "fx_rate, posting_date FROM transactions WHERE needs_review=1 AND is_transfer=0 "
        "AND COALESCE(categorised_by,'none')<>'manual'"
    ).fetchall()
    groups: dict[str, dict] = {}
    for r in rows:
        key = normalise_descriptor(r["description_raw"]) or r["description_raw"]
        g = groups.setdefault(key, {"descriptor_normalised": key,
                                    "descriptor_raw": r["description_raw"],
                                    "account": r["account"], "amounts": [], "dates": [],
                                    "currency": r["currency"], "fx": None})
        g["amounts"].append(r["amount_pennies"])
        g["dates"].append(r["posting_date"])
        if r["fx_amount"] and not g["fx"]:
            g["fx"] = {"amount": r["fx_amount"], "currency": r["fx_currency"], "rate": r["fx_rate"]}
    payloads = []
    for g in groups.values():
        amts = g.pop("amounts"); dates = g.pop("dates")
        payloads.append({
            "descriptor_raw": g["descriptor_raw"],
            "descriptor_normalised": g["descriptor_normalised"],
            "account": g["account"],
            "direction": "outflow" if (sum(amts) / len(amts)) < 0 else "inflow",
            "amount_gbp": round(abs(min(amts)) / 100, 2),
            "currency": g["currency"],
            "fx": g["fx"],
            "occurrences": {"count": len(amts),
                            "amount_range_gbp": [round(min(amts) / 100, 2), round(max(amts) / 100, 2)],
                            "date_range": [min(dates), max(dates)]},
        })
    return payloads


def _web_search_tool(model: str) -> dict:
    # Dynamic-filtering variant on Opus 4.8/4.7/4.6 and Sonnet 5/4.6; basic elsewhere.
    if any(m in model for m in ("opus-4-8", "opus-4-7", "opus-4-6", "sonnet-5", "sonnet-4-6")):
        return {"type": "web_search_20260209", "name": "web_search", "max_uses": 2}
    return {"type": "web_search_20250305", "name": "web_search", "max_uses": 2}


def user_context(conn, limit: int = 30) -> str:
    """How the user categorises things, in their words — your rules and explained
    merchants. Injected so the model mimics your taxonomy/style instead of guessing blind."""
    lines: list[str] = []
    for r in conn.execute(
        "SELECT pattern, category, subcategory FROM merchant_rules WHERE origin='user' "
        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall():
        path = f"{r['category']} ▸ {r['subcategory']}" if r["subcategory"] else r["category"]
        lines.append(f"- \"{r['pattern']}\" -> {path}")
    for r in conn.execute(
        "SELECT raw_pattern, category, subcategory, notes FROM merchant_directory "
        "WHERE source='manual' AND category IS NOT NULL ORDER BY resolved_at DESC LIMIT ?",
        (limit,)).fetchall():
        path = f"{r['category']} ▸ {r['subcategory']}" if r["subcategory"] else r["category"]
        note = f"  ({r['notes']})" if r["notes"] else ""
        lines.append(f"- \"{r['raw_pattern']}\" -> {path}{note}")
    if not lines:
        return ""
    return ("\n\nThe user has already categorised these merchants — follow the SAME taxonomy and "
            "style, and prefer a category consistent with these when a line is similar:\n"
            + "\n".join(lines[:limit]))


def anthropic_classify(payload: dict, enum: list[str], *, model: str, api_key: str,
                       allow_search: bool = True, context: str = "") -> dict:
    """One model call for one descriptor. Returns the validated submit_classification input."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    tools = [_tool(enum)]
    if allow_search:
        tools.append(_web_search_tool(model))
        tool_choice = {"type": "auto"}          # can't force a tool AND allow search
    else:
        tool_choice = {"type": "tool", "name": "submit_classification"}

    messages = [{"role": "user", "content": json.dumps(payload)}]
    for _ in range(6):                          # bounded loop for server-tool pause_turn
        resp = client.messages.create(
            model=model, max_tokens=1024,
            output_config={"effort": "low"},
            system=(_SYSTEM % "\n".join(enum)) + context,
            tools=tools, tool_choice=tool_choice, messages=messages,
        )
        for block in resp.content:
            if block.type == "tool_use" and block.name == "submit_classification":
                return dict(block.input)
        if resp.stop_reason == "pause_turn":    # server tool mid-run; resume
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break
    raise RuntimeError("model did not call submit_classification")


def run(conn, *, api_key: str | None, model: str = "claude-opus-4-8",
        threshold: float = 0.85, allow_search: bool = True, budget: int = 200,
        classify_fn: Callable | None = None) -> dict:
    """Classify the deduped backlog, cache to the directory, apply to siblings.

    `classify_fn(payload, enum)` overrides the network call (for tests / offline). Live
    mode requires api_key. Returns a summary dict.
    """
    enum = build_enum(conn)
    valid = set(enum)
    payloads = assemble_unknowns(conn)[:budget]
    if classify_fn is None:
        if not api_key:
            return {"error": "ANTHROPIC_API_KEY not set", "descriptors": len(payloads),
                    "applied": 0, "auto_applied": 0, "held_for_review": 0}
        ctx = user_context(conn)          # your rules + explanations, fed to the model
        classify_fn = lambda p, e: anthropic_classify(p, e, model=model, api_key=api_key,
                                                       allow_search=allow_search, context=ctx)

    applied = auto = held = failed = 0
    for p in payloads:
        try:
            out = classify_fn(p, enum)
        except Exception:
            failed += 1
            continue
        path = out.get("category_path")
        if path not in valid:
            failed += 1
            continue
        conf = max(0.0, min(1.0, float(out.get("confidence", 0))))
        if path == SENTINEL:
            cat, sub = None, None            # the sentinel is "unknown" — leave category unset
        else:
            cat, sub = (path.split(" ▸ ", 1) + [None])[:2] if " ▸ " in path else (path, None)
        merchant = out.get("merchant")
        is_transfer = bool(out.get("looks_like_transfer"))
        auto_apply = conf >= threshold and not out.get("needs_human") and path != SENTINEL

        # directory memory + enrichment audit
        conn.execute(
            "INSERT OR REPLACE INTO merchant_directory "
            "(raw_pattern, merchant, category, subcategory, status, source, confidence, "
            " rationale, evidence_url, resolved_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,datetime('now'))",
            (p["descriptor_normalised"], merchant, cat, sub,
             "auto" if auto_apply else "proposed",
             "web" if out.get("searched") else "inference",
             conf, out.get("rationale"), out.get("evidence_url")),
        )
        if out.get("searched"):
            conn.execute(
                "INSERT INTO enrichment_log (raw_pattern, query, result_summary, evidence_url, searched_at) "
                "VALUES (?,?,?,?,datetime('now'))",
                (p["descriptor_normalised"], p["descriptor_normalised"],
                 out.get("evidence_summary"), out.get("evidence_url")),
            )

        # apply to sibling transactions of this descriptor, respecting sticky manual
        ids = [r["txn_id"] for r in conn.execute(
            "SELECT txn_id, description_raw FROM transactions "
            "WHERE needs_review=1 AND COALESCE(categorised_by,'none')<>'manual'").fetchall()
            if (normalise_descriptor(r["description_raw"]) or r["description_raw"]) == p["descriptor_normalised"]]
        if not ids:
            continue
        qmarks = ",".join("?" * len(ids))
        if is_transfer:
            conn.execute(
                f"UPDATE transactions SET is_transfer=1, counts_as_spend=0, category='Transfers', "
                f"categorised_by='auto', needs_review=0, updated_at=datetime('now') "
                f"WHERE txn_id IN ({qmarks})", ids)
            applied += len(ids)
        elif auto_apply:
            conn.execute(
                f"UPDATE transactions SET merchant=?, category=?, subcategory=?, categorised_by='auto', "
                f"confidence=?, rationale=?, evidence_url=?, needs_review=0, updated_at=datetime('now') "
                f"WHERE txn_id IN ({qmarks})",
                [merchant, cat, sub, conf, out.get("rationale"), out.get("evidence_url")] + ids)
            applied += len(ids); auto += len(ids)
        else:
            # hold for human review, but attach the proposal so review shows the guess
            conn.execute(
                f"UPDATE transactions SET merchant=?, category=?, subcategory=?, confidence=?, "
                f"rationale=?, evidence_url=?, updated_at=datetime('now') WHERE txn_id IN ({qmarks})",
                [merchant, cat, sub, conf, out.get("rationale"), out.get("evidence_url")] + ids)
            held += len(ids)
    conn.commit()
    return {"descriptors": len(payloads), "applied": applied, "auto_applied": auto,
            "held_for_review": held, "failed": failed, "model": model}
