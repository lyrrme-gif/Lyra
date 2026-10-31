// Google Calendar events for the Calendar tab. Read on demand from the backend (GET /gcal/events),
// which only talks to Google when the user has connected it. Nothing here writes anywhere.

const BASE = "http://127.0.0.1:8765";

export interface GoogleLine { time: string; title: string; kind: "event"; tag: string }

let rows: Array<GoogleLine & { date: string }> = [];
let loadedAt = 0;

const key = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** Fetch the visible range (14 days back, 75 ahead, like the day strip). Cached for 60 s unless `force`. */
export async function refreshGoogle(force = false): Promise<void> {
  if (!force && Date.now() - loadedAt < 60_000) return;
  loadedAt = Date.now();
  try {
    const from = new Date(); from.setDate(from.getDate() - 14);
    const to = new Date(); to.setDate(to.getDate() + 75);
    const res = await fetch(`${BASE}/gcal/events?from=${key(from)}&to=${key(to)}`);
    const data = await res.json();
    rows = data?.ok && Array.isArray(data.events)
      ? data.events.map((e: { date: string; time: string; title: string }) => ({ date: e.date, time: e.time || "", title: e.title, kind: "event" as const, tag: "Google" }))
      : [];
    window.dispatchEvent(new CustomEvent("lyra-google-updated"));
  } catch { /* backend not up: keep what we have */ }
}

export const googleOn = (day: string): GoogleLine[] => rows.filter((r) => r.date === day);
