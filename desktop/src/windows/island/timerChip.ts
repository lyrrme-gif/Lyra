// Timer chip: the live countdown / stopwatch shown at the right of the compact notch.
// Works next to the music notch: with a song playing it replaces the little equalizer
// and shows only the time; without music it also shows a short label ("Focus", "Pasta").
//
// The backend (timers.py) sends absolute times, so everything here counts by itself —
// no traffic per second, no animation frames. Pure helpers are exported for testing.

import { h } from "../../shared/views/dom";
import { formatTime } from "../../shared/spotify/model";

export interface TimerItem {
  id: number;
  kind: "timer" | "pomodoro" | "stopwatch";
  label: string;
  phase: "" | "focus" | "break" | "long";
  round: number;
  state: "running" | "paused";
  total_ms: number | null;
  end_ms: number | null;
  remaining_ms: number | null;
  start_ms: number | null;
  elapsed_ms: number | null;
}

export interface TimersPayload {
  items: TimerItem[];
  /** Backend clock (ms) when this was sent. Same machine, same clock: informational only. */
  now: number;
}

/** Milliseconds left (countdown) or elapsed (stopwatch) for `it` at wall-clock `now`. */
export function valueMs(it: TimerItem, now: number): number {
  if (it.kind === "stopwatch") {
    if (it.state === "running" && it.start_ms != null) return Math.max(0, now - it.start_ms);
    return Math.max(0, it.elapsed_ms ?? 0);
  }
  if (it.state === "running" && it.end_ms != null) return Math.max(0, it.end_ms - now);
  return Math.max(0, it.remaining_ms ?? 0);
}

/** What the chip shows: the countdown ending first, else a stopwatch, else anything paused. */
export function pickPrimary(items: TimerItem[], now: number): TimerItem | null {
  if (!items.length) return null;
  const running = items.filter((i) => i.state === "running");
  const counts = running.filter((i) => i.kind !== "stopwatch");
  if (counts.length) return counts.reduce((a, b) => (valueMs(b, now) < valueMs(a, now) ? b : a));
  if (running.length) return running[0];
  return items[0];
}

/** "4:07" / "1:02:03". Countdowns round up (so 0:01 shows until it really ends). */
export function clockText(it: TimerItem, now: number): string {
  const ms = valueMs(it, now);
  return formatTime(it.kind === "stopwatch" ? ms : Math.ceil(ms / 1000) * 1000);
}

export function labelText(it: TimerItem): string {
  if (it.kind === "stopwatch") return "Stopwatch";
  if (it.kind === "pomodoro") return it.phase === "break" || it.phase === "long" ? "Break" : "Focus";
  return it.label ? it.label.charAt(0).toUpperCase() + it.label.slice(1) : "Timer";
}

// ── Apple-style ring ──────────────────────────────────────────────────────────
// A thin orange ring that drains as the countdown runs (a stopwatch sweeps once a
// minute). Orange is the Clock-app colour; a pomodoro break turns green.
export const TIMER_ORANGE = "#ff9f0a";
export const TIMER_GREEN = "#30d158";

export function colorFor(it: TimerItem): string {
  return it.kind === "pomodoro" && (it.phase === "break" || it.phase === "long") ? TIMER_GREEN : TIMER_ORANGE;
}

/** 0‥1: how much of the ring is lit. */
export function ringFraction(it: TimerItem, now: number): number {
  const ms = valueMs(it, now);
  if (it.kind === "stopwatch") return ms === 0 ? 1 : (ms % 60_000) / 60_000 || 1;
  if (!it.total_ms || it.total_ms <= 0) return 1;
  return Math.max(0, Math.min(1, ms / it.total_ms));
}

const RING_R = 8.6;
const RING_C = 2 * Math.PI * RING_R;
const NS = "http://www.w3.org/2000/svg";

function buildRing(): { el: SVGSVGElement; set(frac: number): void } {
  const el = document.createElementNS(NS, "svg");
  el.setAttribute("viewBox", "0 0 24 24");
  el.setAttribute("width", "20");
  el.setAttribute("height", "20");
  el.setAttribute("aria-hidden", "true");
  const mk = (cls: string) => {
    const c = document.createElementNS(NS, "circle");
    c.setAttribute("cx", "12");
    c.setAttribute("cy", "12");
    c.setAttribute("r", String(RING_R));
    c.setAttribute("fill", "none");
    c.setAttribute("stroke", "currentColor");
    c.setAttribute("stroke-width", "2.7");
    c.setAttribute("class", cls);
    return c;
  };
  const track = mk("ring-track");
  let lastOff = "";
  const arc = mk("ring-arc");
  arc.setAttribute("stroke-linecap", "round");
  arc.setAttribute("stroke-dasharray", `${RING_C.toFixed(2)}`);
  arc.setAttribute("transform", "rotate(-90 12 12)");
  el.append(track, arc);
  return {
    el,
    set(frac) {
      const off = ((1 - Math.max(0.001, frac)) * RING_C).toFixed(2);
      if (off === lastOff) return;   // unchanged value must not restart the CSS transition
      lastOff = off;
      arc.setAttribute("stroke-dashoffset", off);
    },
  };
}

// Latest backend state, shared with the Timer tab so both read the same data.
let latest: TimersPayload = { items: [], now: 0 };
const listeners = new Set<(p: TimersPayload) => void>();
export const getTimers = (): TimersPayload => latest;

export interface TimerChip {
  el: HTMLElement;
  /** New state from the backend. */
  setItems(p: TimersPayload): void;
  /** Re-reads the clock and repaints the text; cheap, call it ~4×/s while visible. */
  tick(): void;
  hasItems(): boolean;
  /** True while something is running or paused (the island keeps the notch awake for it). */
  active(): boolean;
}

export function buildTimerChip(): TimerChip {
  const ring = buildRing();
  const icon = h("span", { id: "timer-chip-icon" }, ring.el);
  const label = h("span", { id: "timer-chip-label" });
  const time = h("span", { id: "timer-chip-time" });
  const more = h("span", { id: "timer-chip-more" });
  const el = h("div", { id: "timer-chip" }, icon, label, time, more);

  let items: TimerItem[] = [];
  let lastText = "";
  let lastKey = "";

  const now = () => Date.now();

  function paint() {
    const primary = pickPrimary(items, now());
    if (!primary) {
      el.classList.remove("on", "urgent");
      lastKey = lastText = "";
      return;
    }
    el.classList.add("on");
    el.classList.toggle("paused", primary.state === "paused");
    const remaining = valueMs(primary, now());
    const urgent = primary.kind !== "stopwatch" && primary.state === "running" && remaining > 0 && remaining <= 10_000;
    el.classList.toggle("urgent", urgent);
    const key = `${primary.id}:${primary.kind}:${primary.phase}:${primary.label}:${items.length}`;
    if (key !== lastKey) {
      lastKey = key;
      const accent = colorFor(primary);
      el.style.setProperty("--timer-accent", accent);
      el.title = labelText(primary);
      el.setAttribute("aria-label", `${labelText(primary)} ${clockText(primary, now())}`);
      label.textContent = labelText(primary);
      more.textContent = items.length > 1 ? `+${items.length - 1}` : "";
    }
    ring.set(ringFraction(primary, now()));
    const text = clockText(primary, now());
    if (text !== lastText) {
      lastText = text;
      time.textContent = text;
    }
  }

  return {
    el,
    setItems(p) {
      items = Array.isArray(p.items) ? p.items : [];
      latest = { items, now: p.now };
      listeners.forEach((fn) => fn(latest));
      lastKey = ""; // new data: repaint everything once
      paint();
    },
    tick: paint,
    hasItems: () => items.length > 0,
    active: () => items.length > 0,
  };
}
