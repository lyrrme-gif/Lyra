// Calendar events and reminders. Stored locally (shared by the island and Settings,
// which live on the same origin). A reminder can repeat daily; every item has a name.

export interface CalItem {
  id: string;
  title: string;
  /** YYYY-MM-DD (the first day, for repeating items). */
  date: string;
  /** HH:MM, or "" for an all-day event. */
  time: string;
  kind: "event" | "reminder";
  repeat: "none" | "daily";
}

const KEY = "lyra-calendar";
const FIRED = "lyra-calendar-fired";

export const dayKey = (d = new Date()) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export function loadItems(): CalItem[] {
  try {
    const raw = JSON.parse(window.localStorage.getItem(KEY) || "[]");
    return Array.isArray(raw) ? raw.filter((x) => x && typeof x.title === "string") : [];
  } catch { return []; }
}

function save(items: CalItem[]) {
  try { window.localStorage.setItem(KEY, JSON.stringify(items)); } catch { /* storage unavailable */ }
  window.dispatchEvent(new CustomEvent("lyra-calendar-changed"));
  syncToBackend();
}

// The events live in the browser, so Lyra's backend could not read them out ("what's on my calendar
// today"). Send a copy to it whenever they change, and once at start-up. Failures are ignored.
let syncTimer: number | undefined;
function syncToBackend() {
  if (typeof window === "undefined") return;
  window.clearTimeout(syncTimer);
  syncTimer = window.setTimeout(() => {
    try {
      void fetch("http://127.0.0.1:8765/calendar/sync", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items: loadItems() }),
      }).catch(() => { /* backend not up yet */ });
    } catch { /* ignore */ }
  }, 400);
}
if (typeof window !== "undefined") {
  window.setTimeout(syncToBackend, 3000);
  window.setTimeout(syncToBackend, 15000);   // the backend may still be starting at 3 s
}

export function addItem(it: Omit<CalItem, "id">): CalItem {
  const item = { ...it, id: `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}` };
  save([...loadItems(), item]);
  return item;
}

export function removeItem(id: string) {
  save(loadItems().filter((x) => x.id !== id));
}

/** Items that happen on `day`: that date, plus daily repeats started on or before it. */
export function itemsOn(day: string): CalItem[] {
  return loadItems()
    .filter((x) => x.date === day || (x.repeat === "daily" && x.date <= day))
    .sort((a, b) => (a.time || "99:99").localeCompare(b.time || "99:99"));
}

/** Reminders (and timed events) whose time is now and that haven't fired today. */
export function dueNow(now = new Date()): CalItem[] {
  const day = dayKey(now);
  const hhmm = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  let fired: Record<string, string> = {};
  try { fired = JSON.parse(window.localStorage.getItem(FIRED) || "{}"); } catch { /* ignore */ }
  const due = itemsOn(day).filter((x) => x.time && x.time <= hhmm && fired[x.id] !== day && x.time >= minutesAgo(now, 5));
  if (due.length) {
    for (const x of due) fired[x.id] = day;
    try { window.localStorage.setItem(FIRED, JSON.stringify(fired)); } catch { /* ignore */ }
  }
  return due;
}

function minutesAgo(now: Date, m: number): string {
  const t = new Date(now.getTime() - m * 60_000);
  return `${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`;
}

