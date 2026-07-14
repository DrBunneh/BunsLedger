// WP2: backlog review (touch each merchant once) + all-transactions table.
import { api, el, money } from "../api.js";

let CATS = null;
const catSelect = (value) => {
  const s = el("select", {});
  s.append(el("option", { value: "" }, "— category —"));
  for (const p of CATS.paths) s.append(el("option", { value: p, ...(p === value ? { selected: "" } : {}) }, p));
  return s;
};
const splitPath = (p) => p.includes(" ▸ ") ? p.split(" ▸ ") : [p, null];

export async function render(app) {
  CATS = await api("/categories");          // always fresh — you may have edited the taxonomy
  const body = el("div");
  const toolbar = el("div", { class: "toolbar" },
    el("button", { class: "mode", onclick: () => show("backlog") }, "Backlog by merchant"),
    el("button", { class: "mode ghost", onclick: () => show("all") }, "All transactions"),
    el("span", { class: "spacer" }),
  );
  app.append(el("div", { class: "card" }, el("h2", {}, "Review"), toolbar), body);

  function show(mode) {
    toolbar.querySelectorAll(".mode").forEach((b, i) =>
      b.className = "mode" + ((mode === "backlog") === (i === 0) ? "" : " ghost"));
    body.innerHTML = "";
    (mode === "backlog" ? renderBacklog : renderAll)(body);
  }
  show("backlog");
}

// ------------------------------------------------------------------ backlog mode
async function renderBacklog(body) {
  const [{ groups, total_groups }, { periods }] = await Promise.all([
    api("/review/merchants?limit=150"), api("/periods"),
  ]);
  const worst = periods.filter((p) => p.to_review > 0).slice(0, 6);
  const aiStatus = el("span", { class: "muted" });
  const reapplyBtn = el("button", { class: "ghost", title: "re-flow rules over all history (manual decisions preserved)",
    onclick: async () => {
      aiStatus.textContent = "re-applying rules…";
      const r = await api("/categorise", { method: "POST" });
      aiStatus.textContent = `rules ${r.rules}, transfers ${r.transfers}, map ${r.source_map}, ${r.needs_review} still to review`;
      window.dispatchEvent(new CustomEvent("data-changed"));
      renderBacklog(body);
    } }, "Re-apply rules");
  const aiBtn = el("button", { class: "ghost", onclick: runClassifier }, "Suggest categories (AI)");
  async function runClassifier() {
    const st = await api("/classify/status");
    if (!st.configured) { aiStatus.textContent = "set ANTHROPIC_API_KEY to enable"; return; }
    aiBtn.disabled = true; aiStatus.textContent = `classifying with ${st.model}…`;
    try {
      const r = await api("/classify/run", { method: "POST", body: { budget: 100 } });
      aiStatus.textContent = r.error
        ? r.error
        : `auto-applied ${r.auto_applied}, held ${r.held_for_review} for review (of ${r.descriptors} merchants)`;
      window.dispatchEvent(new CustomEvent("data-changed"));
      renderBacklog(body);   // refresh with proposals attached
    } catch (e) { aiStatus.textContent = e.message; } finally { aiBtn.disabled = false; }
  }
  body.append(el("div", { class: "card" },
    el("div", { class: "toolbar" },
      el("h3", { style: "margin:0" }, `Backlog — ${total_groups} merchants to decide`),
      el("span", { class: "spacer" }), reapplyBtn, aiBtn, aiStatus),
    el("div", { class: "muted", html: worst.length
      ? "Worst periods: " + worst.map((p) => `${p.period} (${p.to_review}, ${money(p.review_pennies)})`).join(" · ")
      : "Nothing left to review 🎉" })));

  const list = el("div");
  body.append(list);
  for (const g of groups) list.append(groupCard(g, list));
}

async function addCategoryInline(sel) {
  const name = prompt("New category name (e.g. Crypto, or a subtype like Travel):");
  if (!name || !name.trim()) return;
  const parent = prompt("Parent category for a subtype (leave blank for a top-level category):") || null;
  try {
    await api("/categories", { method: "POST", body: { name: name.trim(), parent: parent && parent.trim() || null } });
    CATS = await api("/categories");
    const path = parent && parent.trim() ? `${parent.trim()} ▸ ${name.trim()}` : name.trim();
    sel.append(el("option", { value: path, selected: "" }, path));  // add + select on this card
    window.dispatchEvent(new CustomEvent("cats-changed"));
  } catch (e) { alert(e.message); }
}

// After filing a merchant, surface uncategorised look-alikes to file in one click.
async function suggestSimilar(descriptor, category, subcategory, list) {
  let sim;
  try { sim = await api(`/review/similar?descriptor=${encodeURIComponent(descriptor)}`); }
  catch { return; }
  if (!sim.groups.length) return;
  const label = subcategory ? `${category} ▸ ${subcategory}` : category;
  const panel = el("div", { class: "card", style: "border-color:var(--accent)" });
  const header = el("div", { class: "toolbar" },
    el("b", {}, `Also look like “${descriptor}” — file as ${label}?`), el("span", { class: "spacer" }),
    el("button", { class: "ghost", onclick: () => panel.remove() }, "dismiss"));
  panel.append(header);
  for (const s of sim.groups) {
    const row = el("div", { class: "toolbar", style: "margin:2px 0" },
      el("b", {}, s.descriptor), el("span", { class: "pill" }, `${s.count}×`),
      el("span", { class: s.total_pennies < 0 ? "out" : "in" }, money(s.total_pennies)),
      el("span", { class: "muted" }, `~${Math.round(s.score * 100)}% match`),
      el("span", { class: "muted", style: "opacity:.7" }, s.samples[0] || ""),
      el("span", { class: "spacer" }),
      el("button", { onclick: async () => {
        await api("/review/apply", { method: "POST", body: {
          descriptor: s.descriptor, category, subcategory, merchant: s.descriptor, retro_apply: true }});
        row.remove();
        window.dispatchEvent(new CustomEvent("data-changed"));
        list.querySelectorAll(".group-card").forEach((c) => {
          if (c.getAttribute("data-descriptor") === s.descriptor) c.remove();
        });
        if (!panel.querySelector(".sim-row")) panel.remove();
      } }, `File as ${category}`),
      el("button", { class: "ghost", onclick: () => { row.remove(); if (!panel.querySelector(".sim-row")) panel.remove(); } }, "skip"));
    row.classList.add("sim-row");
    panel.append(row);
  }
  list.prepend(panel);
}

function groupCard(g, list) {
  const sel = catSelect(g.proposal);
  const merchant = el("input", { type: "text", value: g.descriptor, style: "min-width:180px" });
  const note = el("input", { type: "text", placeholder: "what is this? (remembered, feeds the AI)", style: "min-width:260px" });
  const ruleBox = el("input", { type: "checkbox", checked: "" });
  const retroBox = el("input", { type: "checkbox", checked: "" });
  const status = el("span", { class: "muted" });

  const apply = async (asTransfer) => {
    const [category, subcategory] = asTransfer ? ["Transfers", null] : splitPath(sel.value);
    if (!asTransfer && !category) { status.textContent = "pick a category"; return; }
    status.textContent = "applying…";
    try {
      const res = await api("/review/apply", { method: "POST", body: {
        descriptor: g.descriptor, category, subcategory,
        merchant: asTransfer ? null : merchant.value,
        note: note.value || undefined,
        is_transfer: asTransfer ? true : undefined,
        create_rule: !asTransfer && ruleBox.checked,
        retro_apply: retroBox.checked,
      }});
      card.remove();
      window.dispatchEvent(new CustomEvent("data-changed"));
      if (!asTransfer && category) suggestSimilar(g.descriptor, category, subcategory, list);
      if (!list.querySelector(".group-card")) list.append(el("p", { class: "muted" }, "Backlog cleared for this batch."));
    } catch (e) { status.textContent = e.message; }
  };

  const card = el("div", { class: "card group-card", "data-descriptor": g.descriptor },
    el("div", { class: "toolbar" },
      el("b", {}, g.descriptor),
      el("span", { class: "pill" }, `${g.count}×`),
      el("span", { class: g.total_pennies < 0 ? "out" : "in" }, money(g.total_pennies)),
      el("span", { class: "pill" }, g.accounts.join(", ")),
      el("span", { class: "spacer" }),
      g.proposal ? el("span", { class: "pill review", title: g.rationale || "" }, `guess: ${g.proposal}`) : null,
    ),
    el("div", { class: "muted", style: "margin:-4px 0 10px" }, g.samples.join("  ·  ")),
    el("div", { class: "toolbar" },
      el("label", { class: "muted" }, "Merchant "), merchant,
      el("label", { class: "muted" }, "Category "), sel,
      el("button", { class: "ghost", style: "padding:6px 10px",
        title: "create a category that doesn't exist yet",
        onclick: () => addCategoryInline(sel) }, "+ new"),
      el("label", { class: "muted" }, ruleBox, " rule"),
      el("label", { class: "muted" }, retroBox, " all history")),
    el("div", { class: "toolbar" }, el("label", { class: "muted" }, "Note "), note,
      el("button", { onclick: () => apply(false) }, "Apply"),
      el("button", { class: "ghost", onclick: () => apply(true) }, "Mark transfer"),
      status,
    ));
  return card;
}

// Expand a row to show ±2 days of surrounding activity (all accounts, in time order).
async function toggleContext(r, tr) {
  const next = tr.nextElementSibling;
  if (next && next.classList.contains("ctx-row")) { next.remove(); return; }
  const ctx = await api(`/transactions/${r.txn_id}/context?days=2`);
  const inner = el("table", { style: "width:100%;background:var(--panel-2);border-radius:8px" });
  for (const w of ctx.window) {
    const when = w.meal || (w.datetime ? w.datetime.slice(11, 16) : "");
    inner.append(el("tr", { style: w.txn_id === r.txn_id ? "font-weight:700" : "" },
      el("td", { style: "width:90px" }, w.date),
      el("td", { class: "muted", style: "width:80px" }, when),
      el("td", { class: "num " + (w.amount_pennies < 0 ? "out" : "in"), style: "width:90px" }, money(w.amount_pennies)),
      el("td", {}, (w.description_raw || "").slice(0, 44)),
      el("td", { style: "width:90px" }, w.away ? el("span", { class: "pill", title: w.place || "" }, w.place || "away") : ""),
      el("td", { class: "muted", style: "width:120px" }, w.category || "")));
  }
  const td = el("td", { colspan: "8", style: "padding:8px 16px" },
    el("div", { class: "muted", style: "margin-bottom:6px" }, `Around this (±2 days, home: ${ctx.home || "?"})`), inner);
  tr.after(el("tr", { class: "ctx-row" }, td));
}

// -------------------------------------------------------------------- table mode
async function renderAll(body) {
  const state = { q: "", account: "", needs_review: "", page: 1, page_size: 50 };
  const controls = el("div", { class: "toolbar" });
  const search = el("input", { type: "text", placeholder: "search descriptor / merchant / counterparty" });
  const acct = el("select", {}, ...["", "monzo", "nationwide", "aqua"].map((a) => el("option", { value: a }, a || "all accounts")));
  const rev = el("select", {}, el("option", { value: "" }, "all"), el("option", { value: "true" }, "needs review"), el("option", { value: "false" }, "categorised"));
  const tableWrap = el("div", { style: "overflow-x:auto" });
  const pager = el("div", { class: "toolbar" });

  const load = async () => {
    const params = new URLSearchParams({ page: state.page, page_size: state.page_size });
    if (state.q) params.set("q", state.q);
    if (state.account) params.set("account", state.account);
    if (state.needs_review) params.set("needs_review", state.needs_review);
    const data = await api("/transactions?" + params);
    renderTable(data);
  };
  const debounce = (fn, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
  search.addEventListener("input", debounce(() => { state.q = search.value; state.page = 1; load(); }));
  acct.addEventListener("change", () => { state.account = acct.value; state.page = 1; load(); });
  rev.addEventListener("change", () => { state.needs_review = rev.value; state.page = 1; load(); });

  function renderTable(data) {
    const t = el("table", {}, el("thead", {}, el("tr", {},
      ...["Date", "Account", "Description", "Merchant", "Amount", "Category", "Counterparty", ""].map((h) => el("th", {}, h)))));
    const tb = el("tbody");
    for (const r of data.rows) {
      const sel = catSelect(r.subcategory ? `${r.category} ▸ ${r.subcategory}` : r.category);
      sel.addEventListener("change", async () => {
        const [category, subcategory] = splitPath(sel.value);
        await api(`/transactions/${r.txn_id}`, { method: "POST", body: { category, subcategory } });
        window.dispatchEvent(new CustomEvent("data-changed"));
      });
      const tr = el("tr", {},
        el("td", {}, r.posting_date),
        el("td", {}, r.account),
        el("td", { title: r.description_raw }, r.description_raw.slice(0, 40)),
        el("td", {}, r.merchant || ""),
        el("td", { class: "num " + (r.amount_pennies < 0 ? "out" : "in") }, money(r.amount_pennies)),
        el("td", {}, r.is_transfer ? el("span", { class: "pill" }, "transfer") : sel),
        el("td", {}, r.counterparty || ""),
        el("td", {}, el("button", { class: "ghost", style: "padding:2px 8px;font-size:12px",
          title: "what else was happening around this?", onclick: () => toggleContext(r, tr) }, "context"),
          r.needs_review ? el("span", { class: "pill review", style: "margin-left:6px" }, "review") : ""));
      tb.append(tr);
    }
    t.append(tb);
    tableWrap.innerHTML = ""; tableWrap.append(t);
    const pages = Math.max(1, Math.ceil(data.total / data.page_size));
    pager.innerHTML = "";
    pager.append(
      el("button", { class: "ghost", ...(state.page <= 1 ? { disabled: "" } : {}), onclick: () => { state.page--; load(); } }, "‹ Prev"),
      el("span", { class: "muted" }, `page ${data.page} / ${pages} · ${data.total} rows`),
      el("button", { class: "ghost", ...(state.page >= pages ? { disabled: "" } : {}), onclick: () => { state.page++; load(); } }, "Next ›"));
  }

  controls.append(search, acct, rev);
  body.append(el("div", { class: "card" }, controls, tableWrap, pager));
  load();
}
