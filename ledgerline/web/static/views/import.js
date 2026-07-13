// WP1 fills this in.
import { el } from "../api.js";
export async function render(app) {
  app.append(el("div", { class: "card" }, el("h2", {}, "Import"), el("p", { class: "muted" }, "Upload coming in WP1.")));
}
