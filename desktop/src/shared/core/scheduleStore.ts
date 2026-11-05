// Scheduled actions, reminders and routines. The source of truth is the backend
// (scheduler.py, saved in schedule.json); this keeps a small cache for the Calendar view and the
// Home tile, and refreshes whenever the backend says something changed (including by voice).

const BASE = "http://127.0.0.1:8765";

export interface SchedItem { id: string; kind: "reminder" | "action"; text: string; due_ms: number; date: string; time: string }
export interface SchedRoutine {
  id: string;
  name: string;
  enabled: boolean;
  trigger: { type: "time" | "startup" | "phrase"; days?: number[]; time?: string; phrase?: string };
  actions: string[];
}
/** One line in the Calendar list. `id` is set for items that can be removed. */
export interface SchedLine { id: string | null; time: string; title: string; kind: "event" | "reminder"; tag: string }

let items: SchedItem[] = [];
let routines: SchedRoutine[] = [];
const listeners = new Set<() => void>();
const emit = () => { listeners.forEach((fn) => fn()); window.dispatchEvent(new CustomEvent("lyra-schedule-updated")); };

export const onSchedule = (fn: () => void): (() => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; };

async function call(path: string, body?: object): Promise<boolean> {
  try {
    const res = await fetch(`${BASE}${path}`, body
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
      : undefined);
    const data = await res.json();
    if (Array.isArray(data?.items)) items = data.items as SchedItem[];
    if (Array.isArray(data?.routines)) routines = data.routines as SchedRoutine[];
    if (Array.isArray(data?.items) || Array.isArray(data?.routines)) emit();
    return data?.ok !== false;
  } catch {
    return false;
  }
}

export const refreshSchedule = () => call("/schedule");

export function removeScheduled(id: string): Promise<boolean> {
  items = items.filter((i) => i.id !== id);   // gone immediately; the backend answer replaces this
  emit();
  return call("/schedule/remove", { id });
}

const dayKeyOf = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** Everything scheduled for `day` (YYYY-MM-DD): one-off items plus enabled time routines for that weekday. */
export function scheduleOn(day: string): SchedLine[] {
  const out: SchedLine[] = items
    .filter((i) => i.date === day)
    .map((i) => ({ id: i.id, time: i.time, title: i.text, kind: i.kind === "reminder" ? "reminder" as const : "event" as const, tag: i.kind === "action" ? "Scheduled" : "" }));
  const d = new Date(`${day}T12:00:00`);
  const wd = (d.getDay() + 6) % 7;   // Monday = 0, like the backend
  if (!Number.isNaN(wd) && dayKeyOf(d) >= dayKeyOf(new Date())) {
    for (const r of routines) {
      if (!r.enabled || r.trigger.type !== "time" || !r.trigger.time || !(r.trigger.days ?? []).includes(wd)) continue;
      out.push({ id: null, time: r.trigger.time, title: r.actions.join(", "), kind: "event", tag: "Routine" });
    }
  }
  return out.sort((a, b) => (a.time || "99:99").localeCompare(b.time || "99:99"));
}

if (typeof window !== "undefined") {
  window.addEventListener("lyra:schedule-changed", () => { void refreshSchedule(); });
  void refreshSchedule();
}
