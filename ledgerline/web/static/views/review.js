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
  if (!CATS) CATS = await api("/categories");
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
      el("span", { class: "spacer" }), aiBtn, aiStatus),
    el("div", { class: "muted", html: worst.length
      ? "Worst periods: " + worst.map((p) => `${p.period} (${p.to_review}, ${money(p.review_pennies)})`).join(" · ")
      : "Nothing left to review 🎉" })));

  const list = el("div");
  body.append(list);
  for (const g of groups) list.append(groupCard(g, list));
}

function groupCard(g, list) {
  const sel = catSelect(g.proposal);
  const merchant = el("input", { type: "text", value: g.descriptor, style: "min-width:200px" });
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
        is_transfer: asTransfer ? true : undefined,
        create_rule: !asTransfer && ruleBox.checked,
        retro_apply: retroBox.checked,
      }});
      card.remove();
      window.dispatchEvent(new CustomEvent("data-changed"));
      if (!list.querySelector(".group-card")) list.append(el("p", { class: "muted" }, "Backlog cleared for this batch."));
    } catch (e) { status.textContent = e.message; }
  };

  const card = el("div", { class: "card group-card" },
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
      el("label", { class: "muted" }, ruleBox, " rule"),
      el("label", { class: "muted" }, retroBox, " all history"),
      el("button", { onclick: () => apply(false) }, "Apply"),
      el("button", { class: "ghost", onclick: () => apply(true) }, "Mark transfer"),
      status,
    ));
  return card;
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
      tb.append(el("tr", {},
        el("td", {}, r.posting_date),
        el("td", {}, r.account),
        el("td", { title: r.description_raw }, r.description_raw.slice(0, 40)),
        el("td", {}, r.merchant || ""),
        el("td", { class: "num " + (r.amount_pennies < 0 ? "out" : "in") }, money(r.amount_pennies)),
        el("td", {}, r.is_transfer ? el("span", { class: "pill" }, "transfer") : sel),
        el("td", {}, r.counterparty || ""),
        el("td", {}, r.needs_review ? el("span", { class: "pill review" }, "review") : "")));
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
