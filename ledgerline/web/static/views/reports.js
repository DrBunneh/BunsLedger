// WP3 fills this in.
import { el } from "../api.js";
export async function render(app) {
  app.append(el("div", { class: "card" }, el("h2", {}, "Reports"), el("p", { class: "muted" }, "Reporting dashboard coming in WP3.")));
}
