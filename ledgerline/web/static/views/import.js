// WP1: upload (drag-drop / picker) + sources status panel.
import { api, el } from "../api.js";

export async function render(app) {
  const results = el("div", { class: "card hidden" });
  const statusCard = el("div", { class: "card" }, el("h3", {}, "Sources"),
    el("div", {}, el("span", { class: "muted" }, "loading…")));

  const input = el("input", { type: "file", multiple: true, accept: ".csv,.pdf", style: "display:none" });
  const zone = el("div", { class: "dropzone" },
    el("div", {}, "Drop Monzo CSV / Aqua / Nationwide PDF here"),
    el("div", { class: "muted", style: "margin-top:6px" }, "or click to choose files"));

  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", () => upload([...input.files]));
  ["dragover", "dragenter"].forEach((e) => zone.addEventListener(e, (ev) => {
    ev.preventDefault(); zone.classList.add("drag");
  }));
  ["dragleave", "drop"].forEach((e) => zone.addEventListener(e, () => zone.classList.remove("drag")));
  zone.addEventListener("drop", (ev) => { ev.preventDefault(); upload([...ev.dataTransfer.files]); });

  async function upload(files) {
    if (!files.length) return;
    zone.querySelector("div").textContent = `Uploading ${files.length} file(s)…`;
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    try {
      const res = await api("/ingest", { method: "POST", form });
      renderResults(res);
      window.dispatchEvent(new CustomEvent("data-changed"));
      await loadStatus();
    } catch (e) {
      renderResults({ results: [{ file: "(batch)", ok: false, error: e.message }] });
    } finally {
      zone.querySelector("div").textContent = "Drop Monzo CSV / Aqua / Nationwide PDF here";
    }
  }

  function renderResults(res) {
    results.classList.remove("hidden");
    results.innerHTML = "";
    results.append(el("h3", {}, "Import results"));
    const t = el("table", {}, el("thead", {}, el("tr", {},
      ...["File", "Source", "New", "Duplicate", "Status"].map((h) => el("th", {}, h)))));
    const tb = el("tbody");
    for (const r of res.results) {
      tb.append(el("tr", {},
        el("td", {}, r.file || ""),
        el("td", {}, r.source || "—"),
        el("td", { class: "num" }, r.ok ? r.new : ""),
        el("td", { class: "num" }, r.ok ? r.dup : ""),
        el("td", { class: r.ok ? "good" : "error" }, r.ok ? "imported" : r.error)));
    }
    t.append(tb); results.append(t);
    if (res.categorise) {
      const c = res.categorise;
      results.append(el("p", { class: "muted" },
        `Categorised: ${c.rules} by rule, ${c.source_map} by map, ${c.transfers} transfers, ${c.needs_review} need review.`));
    }
  }

  async function loadStatus() {
    const { sources } = await api("/sources/status");
    statusCard.innerHTML = "";
    statusCard.append(el("h3", {}, "Sources"));
    const t = el("table", {}, el("thead", {}, el("tr", {},
      ...["Account", "Rows", "Latest txn", "Last import", ""].map((h) => el("th", {}, h)))));
    const tb = el("tbody");
    for (const s of sources) {
      const nudge = s.manual_upload && (s.stale || !s.rows)
        ? el("span", { class: "pill review" }, s.rows ? "upload latest statement" : "no data — upload")
        : (s.manual_upload ? el("span", { class: "pill" }, "up to date")
                           : el("span", { class: "pill" }, "API-syncable"));
      tb.append(el("tr", {},
        el("td", {}, s.account),
        el("td", { class: "num" }, s.rows),
        el("td", {}, s.last_txn || "—"),
        el("td", {}, s.last_import ? s.last_import.slice(0, 10) : "—"),
        el("td", {}, nudge)));
    }
    t.append(tb); statusCard.append(t);
  }

  app.append(
    el("div", { class: "card" }, el("h2", {}, "Import"), zone, input),
    results, statusCard,
  );
  loadStatus();
}
