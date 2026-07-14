// App shell: auth gate, hash router, summary header. Views are lazy-loaded modules
// (one per work package) so each stays self-contained.
import { api } from "./api.js";

const views = {
  import: () => import("./views/import.js"),
  review: () => import("./views/review.js"),
  reports: () => import("./views/reports.js"),
  categories: () => import("./views/categories.js"),
  trips: () => import("./views/trips.js"),
};

const $ = (id) => document.getElementById(id);

async function refreshSummary() {
  try {
    const s = await api("/summary");
    $("summary").textContent = s.transactions
      ? `${s.transactions} txns · ${s.needs_review} to review · ${s.date_from ?? ""}–${s.date_to ?? ""}`
      : "no data yet — start in Import";
  } catch { /* not logged in yet */ }
}

async function route() {
  const name = (location.hash.replace(/^#\//, "") || "import").split("/")[0];
  document.querySelectorAll("#nav a").forEach((a) =>
    a.classList.toggle("active", a.dataset.view === name));
  const app = $("app");
  const loader = views[name] || views.import;
  app.innerHTML = "";
  try {
    const mod = await loader();
    await mod.render(app);
  } catch (e) {
    app.innerHTML = `<div class="card error">Failed to load view: ${e.message}</div>`;
  }
  refreshSummary();
}

function showShell() {
  $("login").classList.add("hidden");
  $("shell").classList.remove("hidden");
  if (!location.hash) location.hash = "#/import";
  route();
}

function showLogin(message) {
  $("shell").classList.add("hidden");
  $("login").classList.remove("hidden");
  if (message) { const e = $("login-error"); e.textContent = message; e.classList.remove("hidden"); }
}

async function boot() {
  window.addEventListener("hashchange", route);
  window.addEventListener("needs-login", () => showLogin());
  window.addEventListener("data-changed", refreshSummary);

  $("login-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await api("/auth/login", { method: "POST", body: { passphrase: $("passphrase").value } });
      showShell();
    } catch (e) { showLogin(e.message); }
  });

  const health = await api("/health");
  if (!health.auth_enabled) return showShell();  // dev mode: unlocked
  try { await api("/me"); showShell(); } catch { showLogin(); }
}

boot();
