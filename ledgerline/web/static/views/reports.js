// WP3: reporting dashboard. Charts are hand-rolled inline SVG (no external libs -
// privacy + no build step). All figures exclude transfers and note coverage.
import { api, el, money } from "../api.js";

const px = (n) => `${n}`;

// Horizontal bars for a ranked list (category / merchant spend).
function barsH(items, { max }) {
  const rows = items.map((it) => {
    const w = max ? Math.max(1, Math.round((it.value / max) * 100)) : 0;
    return `<div class="hbar">
      <div class="hbar-label">${it.label}</div>
      <div class="hbar-track"><div class="hbar-fill" style="width:${w}%"></div></div>
      <div class="hbar-val">${money(-it.value)}</div></div>`;
  }).join("");
  return el("div", { class: "hbars", html: rows });
}

// Grouped monthly income vs spend bars + zero baseline.
function cashflowChart(months) {
  const W = Math.max(320, months.length * 62), H = 180, pad = 24, base = H - pad;
  const max = Math.max(1, ...months.flatMap((m) => [m.income_pennies, m.spend_pennies]));
  const scale = (v) => (v / max) * (base - pad);
  const bw = 12;
  let bars = "";
  months.forEach((m, i) => {
    const x = pad + i * ((W - pad * 2) / Math.max(1, months.length - 0.001)) + 6;
    const ih = scale(m.income_pennies), sh = scale(m.spend_pennies);
    bars += `<rect x="${px(x)}" y="${px(base - ih)}" width="${bw}" height="${px(ih)}" class="c-in"/>`;
    bars += `<rect x="${px(x + bw + 2)}" y="${px(base - sh)}" width="${bw}" height="${px(sh)}" class="c-out"/>`;
    bars += `<text x="${px(x + bw)}" y="${px(H - 6)}" class="c-axis" text-anchor="middle">${m.period.slice(2)}</text>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" class="chart" preserveAspectRatio="xMidYMid meet">
    <line x1="${pad}" y1="${base}" x2="${W - pad}" y2="${base}" class="c-base"/>${bars}</svg>`;
}

export async function render(app) {
  const range = el("select", {},
    el("option", { value: "12" }, "last 12 months"),
    el("option", { value: "24" }, "last 24 months"),
    el("option", { value: "120" }, "all time"));
  const head = el("div", { class: "card" }, el("h2", {}, "Reports"),
    el("div", { class: "toolbar" }, el("span", { class: "muted" }, "Window "), range));
  const tiles = el("div", { class: "card" });
  const cashCard = el("div", { class: "card" });
  const catCard = el("div", { class: "card" });
  const recCard = el("div", { class: "card" });
  app.append(head, tiles, cashCard, catCard, recCard);

  async function load() {
    const months = parseInt(range.value, 10);
    const [sum, cf, cats, rec] = await Promise.all([
      api("/reports/summary"), api(`/reports/cashflow?months=${months}`),
      api("/reports/spend-by-category"), api("/reports/recurring"),
    ]);

    tiles.innerHTML = "";
    tiles.append(
      stat("Income", money(sum.income_pennies), "in"),
      stat("Spend", money(sum.spend_pennies), "out"),
      stat("Net", money(sum.net_pennies), sum.net_pennies < 0 ? "out" : "in"),
      stat("Transactions", String(sum.transactions)),
      stat("Committed / mo", money(-rec.monthly_committed_pennies), "out"));
    if (sum.full_coverage_from) {
      tiles.append(el("p", { class: "muted", style: "margin-top:10px" },
        `Coverage: full cross-account data only from ${sum.full_coverage_from}; earlier periods under-count.`));
    }

    cashCard.innerHTML = "";
    cashCard.append(el("h3", {}, "Cashflow — income vs spend"),
      el("div", { html: cashflowChart(cf.months) }),
      el("div", { class: "muted", html: `<span class="dot in"></span> income &nbsp; <span class="dot out"></span> spend (transfers excluded)` }));

    const spendCats = cats.categories.filter((c) => c.spend_pennies > 0);
    const cmax = Math.max(1, ...spendCats.map((c) => c.spend_pennies));
    catCard.innerHTML = "";
    catCard.append(el("h3", {}, "Spend by category"),
      barsH(spendCats.slice(0, 12).map((c) => ({ label: c.category, value: c.spend_pennies })), { max: cmax }));

    recCard.innerHTML = "";
    const t = el("table", {}, el("thead", {}, el("tr", {},
      ...["Merchant", "Typical / mo", "Months", "Last seen", "Status"].map((h) => el("th", {}, h)))));
    const tb = el("tbody");
    for (const r of rec.recurring.slice(0, 20)) {
      tb.append(el("tr", {},
        el("td", {}, r.merchant),
        el("td", { class: "num" }, money(-r.typical_pennies)),
        el("td", { class: "num" }, r.months),
        el("td", {}, r.last_seen),
        el("td", {}, el("span", { class: "pill " + (r.status === "active" ? "" : "review") }, r.status))));
    }
    t.append(tb);
    recCard.append(el("h3", {}, `Recurring / subscriptions (${rec.recurring.length})`), t);
  }
  range.addEventListener("change", load);
  load();
}

function stat(label, value, cls = "") {
  return el("div", { class: "stat" }, el("span", { class: "muted" }, label), el("b", { class: cls }, value));
}
