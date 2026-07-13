// WP2 fills this in.
import { el } from "../api.js";
export async function render(app) {
  app.append(el("div", { class: "card" }, el("h2", {}, "Review"), el("p", { class: "muted" }, "Backlog review table coming in WP2.")));
}
