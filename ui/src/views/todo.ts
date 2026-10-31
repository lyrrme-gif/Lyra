// To-do: today's tasks on the island. Tick to complete, × to delete, type to add.
// Same list Lyra edits by voice ("add buy milk to my to-do list").

import { h, svg } from "./dom";
import { ICONS } from "./icons";
import type { ViewHost } from "./views";
import { addTodo, getTodos, onTodos, refreshTodos, removeTodo, toggleTodo, todoCounts } from "../core/todoStore";
import { fxAdded } from "./addedFx";

export function buildTodo(): ViewHost {
  const count = h("span", { class: "todo-count" });
  const list = h("div", { class: "todo-list" });
  const input = h("input", { class: "todo-in", type: "text", placeholder: "Add a to-do…", maxlength: "120" }) as HTMLInputElement;
  const addBtn = h("button", { class: "todo-add", type: "button", "aria-label": "Add", title: "Add" }, svg(ICONS.plus, 14)) as HTMLButtonElement;
  const el = h("div", { class: "view todo-view" },
    h("div", { class: "todo-head" }, h("span", { class: "todo-title", text: "To-do" }), count),
    list,
    h("div", { class: "todo-form" }, input, addBtn),
  );

  // Rows that were not there at the last paint slide in with a pulse (voice, typed, any source).
  // The first list that arrives after start-up is the baseline, not "new".
  let seen = new Set(getTodos().map((i) => i.text));
  let primed = seen.size > 0;
  const paint = () => {
    const items = getTodos();
    const fresh = new Set<string>();
    if (primed) for (const it of items) if (!it.done && !seen.has(it.text)) fresh.add(it.text);
    primed = true;
    seen = new Set(items.map((i) => i.text));
    if (fresh.size) fxAdded(el, "Added", "todo");
    const { open, total } = todoCounts();
    count.textContent = total ? `${total - open}/${total}` : "";
    if (!items.length) {
      list.replaceChildren(h("span", { class: "todo-none", text: "Nothing to do. Add something below." }));
      return;
    }
    list.replaceChildren(...items.map((it) => {
      const tick = h("button", {
        class: "todo-check", type: "button", "aria-label": it.done ? "Mark as not done" : "Mark as done",
        onclick: (e: Event) => { e.stopPropagation(); void toggleTodo(it.text, !it.done); },
      }, it.done ? svg(ICONS.check, 12, { stroke: 3 }) : null);
      const del = h("button", {
        class: "todo-del", type: "button", "aria-label": "Delete", title: "Delete", text: "×",
        onclick: (e: Event) => { e.stopPropagation(); void removeTodo(it.text); },
      });
      return h("div", { class: `todo-item${it.done ? " done" : ""}${fresh.has(it.text) ? " fx-new" : ""}` }, tick, h("span", { class: "todo-text", text: it.text }), del);
    }));
  };

  const submit = () => {
    const t = input.value.trim();
    if (!t) return;
    input.value = "";
    void addTodo(t);
  };
  addBtn.addEventListener("click", (e) => { e.stopPropagation(); submit(); });
  input.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") submit(); });
  input.addEventListener("click", (e) => e.stopPropagation());

  onTodos(paint);
  paint();

  return {
    el,
    sync() { paint(); },
    focus() { input.focus(); },
    setActive(on) { if (on) void refreshTodos(); },
  };
}
