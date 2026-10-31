// To-do list shared by the To-do view and the Home tile. The source of truth is the
// backend (actions/notes.py, the "to-do" list); this keeps a small cache and tells
// listeners when it changes, including changes made by voice ("add milk to my to-do").

const BASE = "http://127.0.0.1:8765";

export interface Todo { text: string; done: boolean }

let items: Todo[] = [];
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((fn) => fn());

export const getTodos = (): Todo[] => items;
export const todoCounts = () => ({ open: items.filter((t) => !t.done).length, total: items.length });

/** Subscribe to changes; returns an unsubscribe function. */
export function onTodos(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

async function call(path: string, body?: object): Promise<boolean> {
  try {
    const res = await fetch(`${BASE}${path}`, body
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
      : undefined);
    const data = await res.json();
    if (Array.isArray(data?.items)) { items = data.items as Todo[]; emit(); }
    return data?.ok !== false;
  } catch {
    return false;
  }
}

export const refreshTodos = () => call("/todos");

export async function addTodo(text: string): Promise<boolean> {
  const t = text.trim();
  if (!t) return false;
  if (!items.some((i) => !i.done && i.text.toLowerCase() === t.toLowerCase())) {
    items = [...items.filter((i) => !i.done), { text: t, done: false }, ...items.filter((i) => i.done)];
    emit();   // show it immediately; the backend answer replaces this
  }
  return call("/todos/add", { text: t });
}

export function toggleTodo(text: string, done: boolean): Promise<boolean> {
  const moved = items.map((i) => (i.text === text ? { ...i, done } : i));
  items = [...moved.filter((i) => !i.done), ...moved.filter((i) => i.done)];   // open first, done last
  emit();
  return call("/todos/toggle", { text, done });
}

export function removeTodo(text: string): Promise<boolean> {
  items = items.filter((i) => i.text !== text);
  emit();
  return call("/todos/remove", { text });
}

// The backend pushes {type:"todos"} when the list changes by voice; the island relays it here.
if (typeof window !== "undefined") {
  window.addEventListener("lyra:todos-changed", () => { void refreshTodos(); });
}
