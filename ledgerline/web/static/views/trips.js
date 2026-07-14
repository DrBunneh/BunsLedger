// Trips: away-from-home spend clustered into episodes. See the whole story in time
// order, then file the trip — a card weekend becomes Cards ▸ Travel/Accom/Food/Cards.
import { api, el, money } from "../api.js";

let CATS = null;
const splitPath = (p) => (p && p.includes(" ▸ ")) ? p.split(" ▸ ") : [p || null, null];
function catSelect(value, onchange) {
  const s = el("select", { onchange });
  s.append(el("option", { value: "" }, "— category —"));
  for (const p of CATS.paths) s.append(el("option", { value: p, ...(p === value ? { selected: "" } : {}) }, p));
  return s;
}

export async function render(app) {
  const refresh = () => { app.innerHTML = ""; render(app); };
  CATS = await api("/categories");
  const { home, episodes } = await api("/trips");
  app.append(el("div", { class: "card" }, el("h2", {}, "Trips"),
    el("p", { class: "muted" }, `Away-from-home spend grouped into episodes (home: ${home || "?"}). Each trip is the story around it — file the whole thing, or line by line. Card weekends live here.`)));
  if (!episodes.length) { app.append(el("div", { class: "card muted" }, "No away-from-home episodes detected.")); return; }
  for (const e of episodes) app.append(episodeCard(e, refresh));
}

function episodeCard(e, refresh) {
  const span = e.date_from === e.date_to ? e.date_from : `${e.date_from} → ${e.date_to}`;
  const places = (e.places || []).join(", ") || "—";
  const body = el("div", { class: "hidden" });

  // bulk-file controls
  const bulkSel = catSelect(e.abroad ? "Cards ▸ Travel" : null);
  const bulkMsg = el("span", { class: "muted" });
  const bulkBtn = el("button", { onclick: async () => {
    const [category, subcategory] = splitPath(bulkSel.value);
    if (!category) { bulkMsg.textContent = "pick a category"; return; }
    const ids = e.transactions.filter((t) => t.category === null && t.amount_pennies < 0).map((t) => t.txn_id);
    if (!ids.length) { bulkMsg.textContent = "nothing uncategorised here"; return; }
    await api("/review/apply", { method: "POST", body: { txn_ids: ids, category, subcategory } });
    window.dispatchEvent(new CustomEvent("data-changed"));
    refresh();
  } }, "File uncategorised");

  function fill() {
    body.innerHTML = "";
    const t = el("table", {}, el("thead", {}, el("tr", {},
      ...["Date", "When", "Amount", "Description", "", "Category"].map((h) => el("th", {}, h)))));
    const tb = el("tbody");
    for (const r of e.transactions) {
      const sel = catSelect(r.category, async (ev) => {
        const [category, subcategory] = splitPath(ev.target.value);
        await api(`/transactions/${r.txn_id}`, { method: "POST", body: { category, subcategory } });
        window.dispatchEvent(new CustomEvent("data-changed"));
      });
      tb.append(el("tr", {},
        el("td", {}, r.date),
        el("td", { class: "muted" }, r.meal || (r.datetime ? r.datetime.slice(11, 16) : "")),
        el("td", { class: "num " + (r.amount_pennies < 0 ? "out" : "in") }, money(r.amount_pennies)),
        el("td", { title: r.description_raw }, (r.description_raw || "").slice(0, 40)),
        el("td", {}, r.away ? el("span", { class: "pill", title: r.place || "" }, r.place || "away") : ""),
        el("td", {}, r.is_transfer ? el("span", { class: "pill" }, "transfer") : sel)));
    }
    t.append(tb);
    // label the trip: name + purpose -> tag every member (enables trip-aware reporting)
    const defName = `${(e.places[0] || "trip")} ${e.date_from}`;
    const nameInp = el("input", { type: "text", value: defName, style: "min-width:220px" });
    const purpose = el("select", {}, ...["", "card", "work", "holiday", "personal"].map((p) =>
      el("option", { value: p, ...(p === (e.purpose_guess || "") ? { selected: "" } : {}) }, p || "— purpose —")));
    const saveMsg = el("span", { class: "muted" });
    const saveBtn = el("button", { class: "ghost", onclick: async () => {
      const ids = e.transactions.map((t) => t.txn_id);
      const r = await api("/trips/tag", { method: "POST", body: {
        txn_ids: ids, name: nameInp.value.trim() || defName, purpose: purpose.value || null } });
      saveMsg.textContent = `saved “${r.trip}” (${r.tagged} txns)`;
      window.dispatchEvent(new CustomEvent("data-changed"));
    } }, "Save trip");

    body.append(
      el("div", { class: "toolbar" }, el("span", { class: "muted" }, "Trip "), nameInp, purpose, saveBtn, saveMsg),
      el("div", { class: "toolbar" }, el("span", { class: "muted" }, "Bulk-file the uncategorised: "),
        bulkSel, bulkBtn, bulkMsg),
      el("div", { style: "overflow-x:auto" }, t));
  }

  const header = el("div", { class: "toolbar", style: "cursor:pointer" },
    el("b", {}, span),
    e.abroad ? el("span", { class: "pill review" }, "✈ " + places) : el("span", { class: "pill" }, places),
    el("span", { class: "muted" }, `${e.count} txns`),
    el("span", { class: "out" }, money(-e.spend_pennies)),
    e.uncategorised ? el("span", { class: "pill review" }, `${e.uncategorised} to file`) : el("span", { class: "pill good" }, "filed"),
    el("span", { class: "spacer" }), el("span", { class: "muted" }, "▾"));
  header.addEventListener("click", () => {
    if (body.classList.contains("hidden")) { fill(); body.classList.remove("hidden"); }
    else body.classList.add("hidden");
  });
  return el("div", { class: "card" }, header, body);
}
