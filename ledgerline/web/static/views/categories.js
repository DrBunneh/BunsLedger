// Manage your taxonomy: view the tree, add categories/subcategories, delete unused ones.
// Adding here immediately extends the classifier's enum, so the AI can use new categories too.
import { api, el } from "../api.js";

export async function render(app) {
  const tree = el("div", { class: "card" });
  const form = el("div", { class: "card" });
  app.append(el("div", { class: "card" }, el("h2", {}, "Categories"),
    el("p", { class: "muted" }, "Your taxonomy is yours to shape. Adding a category makes it available in review and to the AI classifier. Subcategory names are unique across the whole taxonomy.")),
    form, tree);

  async function load() {
    const cats = await api("/categories");
    // add form
    form.innerHTML = "";
    const name = el("input", { type: "text", placeholder: "new category or subcategory" });
    const parent = el("select", {}, el("option", { value: "" }, "— top-level —"),
      ...cats.top.map((t) => el("option", { value: t }, `under ${t}`)));
    const kind = el("select", {}, ...["spend", "income", "transfer"].map((k) => el("option", { value: k }, k)));
    const msg = el("span", { class: "muted" });
    form.append(el("h3", {}, "Add category"),
      el("div", { class: "toolbar" }, name, parent, kind,
        el("button", { onclick: async () => {
          if (!name.value.trim()) { msg.textContent = "enter a name"; return; }
          try {
            await api("/categories", { method: "POST", body: {
              name: name.value.trim(), parent: parent.value || null, kind: kind.value } });
            name.value = ""; msg.textContent = "";
            window.dispatchEvent(new CustomEvent("cats-changed"));
            load();
          } catch (e) { msg.textContent = e.message; }
        }}, "Add"), msg),
      el("p", { class: "muted", style: "margin-top:4px" }, "Tip: pick a parent to make it a subcategory (it inherits the parent's kind)."));

    // tree
    tree.innerHTML = "";
    tree.append(el("h3", {}, "Your taxonomy"));
    for (const t of cats.top) {
      tree.append(el("div", { class: "toolbar", style: "margin:2px 0" },
        el("b", {}, t), el("span", { class: "pill" }, cats.kinds[t]),
        delBtn(t, null, load)));
      for (const s of (cats.subcategories[t] || [])) {
        tree.append(el("div", { class: "toolbar", style: "margin:2px 0 2px 24px" },
          el("span", { class: "muted" }, "▸ " + s), delBtn(s, t, load)));
      }
    }
  }

  function delBtn(catName, parent, reload) {
    const q = parent ? `?parent=${encodeURIComponent(parent)}` : "";
    return el("button", { class: "ghost", style: "padding:2px 8px;font-size:12px",
      onclick: async () => {
        try { await api(`/categories/${encodeURIComponent(catName)}${q}`, { method: "DELETE" });
          window.dispatchEvent(new CustomEvent("cats-changed")); reload();
        } catch (e) { alert(e.message); }
      } }, "delete");
  }

  load();
}
