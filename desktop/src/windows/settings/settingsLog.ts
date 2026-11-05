// The Settings log, UI half. Answers, while the Settings window is on screen:
//   · which tab is shown, is the window focused, what element has focus
//   · what is loading NOW (tab being built, requests in flight, who started each) and
//     what waits in the BACKGROUND (tabs not built yet, cards waiting for their tab)
//   · what the page itself costs: animation-frame rate, main-thread busy %, running animations by screen area
//     (hero / sidebar / the tab's own panel), canvases (drawn size vs shown size, on screen or not)
// and sends a small snapshot to the backend (POST /settings-log) every ~3 s and whenever something changes. The
// backend (settings_log.py) adds the machine side (CPU / GPU of each WebView2 process), charges every sample to the
// tab that was shown, and writes the table. See that file's header for the output.
//
// Cost: nothing runs while the window is hidden or parked (the timer is not armed). While it is on screen:
// one timer every 3 s, a few counters, one POST of a few KB. Off with LYRA_TRACE=0 / localStorage lyra-trace=0.

import { onTraceOff, setNetHook, traceIsOn, WIN } from "../../shared/core/trace";
import { isReallyVisible, onVisibilityChange } from "../../shared/core/visibility";
import { onPerfSample, type PerfSample } from "../../shared/core/perfProbe";
import { currentOwner, elLabel, lazyCounts, ownerTab, setLoadListener, tabOf } from "../../shared/core/loadOwner";

const BASE = "http://127.0.0.1:8765";
const EVERY_MS = 3000;
const MIN_GAP_MS = 400;                 // changes are sent at once, but never more often than this

interface Req { m: string; p: string; o: string; tab: string; kind: string; bg: boolean; t0: number }
interface Ev { k: string; m: string; tab?: string; ms?: number; why?: string }

const nativeFetch: typeof fetch | null = typeof window !== "undefined" && window.fetch ? window.fetch.bind(window) : null;

let started = false;
let stopped = false;
let active = "general";
let searching = false;
let building = "";
let pending: string[] = [];
const built = new Set<string>();

const inflight = new Map<number, Req>();
let done: Array<Record<string, unknown>> = [];
let maxIn = 0;
let nextId = 1;
let events: Ev[] = [];

let perf: Record<string, unknown> | null = null;
const tabsSincePerf = new Set<string>();
let visWholePerf = true;

let timer = 0;
let gapTimer = 0;
let lastSent = 0;
let sending = false;
let queuedWhy = "";                    // a snapshot that had to wait for the one in flight (e.g. the last "hidden" one)
let failures = 0;
let mutedUntil = 0;

const NOISE = new Set(["/events", "/pidprobe"]);

function ev(k: string, m: string, extra: Partial<Ev> = {}): void {
  if (stopped) return;
  events.push({ k, m, ...extra });
  if (events.length > 60) events.splice(0, events.length - 60);
}

// ── where on screen does something live? ───────────────────────────────────────────────────────
function region(el: Element | null | undefined): string {
  if (!el || !el.closest) return "other";
  const t = tabOf(el);
  if (t) return t;
  if (el.closest(".hero")) return "hero";
  if (el.closest(".tabs, .brand")) return "sidebar";
  if (el.closest(".topbar")) return "topbar";
  return "other";
}

function animationsByArea(): { where: Record<string, number>; running: number; endless: number } {
  const where: Record<string, number> = {};
  let running = 0, endless = 0;
  try {
    for (const a of document.getAnimations?.() ?? []) {
      if (a.playState !== "running") continue;
      running++;
      const eff = a.effect as KeyframeEffect | null;
      if (eff?.getComputedTiming?.().iterations === Infinity) endless++;
      const r = region(eff?.target as Element | null);
      where[r] = (where[r] ?? 0) + 1;
    }
  } catch { /* getAnimations not supported */ }
  return { where, running, endless };
}

function canvases(): string[] {
  const out: string[] = [];
  try {
    for (const c of Array.from(document.querySelectorAll("canvas")).slice(0, 5)) {
      const r = c.getBoundingClientRect();
      const on = r.width > 1 && r.height > 1 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
      out.push(`${region(c)} drawn ${c.width}x${c.height} shown ${Math.round(r.width)}x${Math.round(r.height)} ${on ? "ON screen" : "off screen"}`);
    }
  } catch { /* ignore */ }
  return out;
}

function focusLabel(): string {
  const a = document.activeElement;
  if (!a || a === document.body) return "";
  const field = a instanceof HTMLInputElement || a instanceof HTMLTextAreaElement;
  return field ? `${elLabel(a)} (text field, ${(a as HTMLInputElement).value.length} chars)` : elLabel(a);
}

// ── the snapshot ──────────────────────────────────────────────────────────────────────────────
function snapshot(why: string): Record<string, unknown> {
  const now = performance.now();
  const body = {
    v: 1, t: Date.now(), why,
    tab: active, search: searching, vis: isReallyVisible(), focus: document.hasFocus(), doc: document.visibilityState,
    focusel: focusLabel(),
    building,
    pending: pending.slice(0, 24),
    built: [...built],
    lazy: lazyCounts(),
    inflight: [...inflight.values()].slice(0, 8).map((r) => ({ m: r.m, p: r.p, o: r.o, ms: Math.round(now - r.t0) })),
    done, maxin: Math.max(maxIn, inflight.size),
    ev: events,
    perf,
    canvas: canvases(),
  };
  done = []; events = []; maxIn = inflight.size;
  return body;
}

function send(why: string): void {
  if (stopped || !nativeFetch) return;
  const now = Date.now();
  if (now < mutedUntil) return;
  if (sending) { queuedWhy = why; return; }
  lastSent = now;
  sending = true;
  nativeFetch(`${BASE}/settings-log`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(snapshot(why)),
    keepalive: true,
  }).then((r) => r.json().catch(() => null)).then((j: { off?: boolean } | null) => {
    failures = 0;
    if (j && j.off === true) stop();
  }).catch(() => {
    failures++;
    if (failures >= 3) mutedUntil = Date.now() + Math.min(30_000, failures * 3_000);
  }).finally(() => {
    sending = false;
    if (queuedWhy) { const w = queuedWhy; queuedWhy = ""; send(w); }
  });
}

/** Something changed: tell the backend now (but not more than every MIN_GAP_MS). */
function soon(why: string): void {
  if (stopped) return;
  const wait = Math.max(0, MIN_GAP_MS - (Date.now() - lastSent));
  window.clearTimeout(gapTimer);
  gapTimer = window.setTimeout(() => send(why), wait);
}

function arm(): void {
  window.clearTimeout(timer);
  timer = 0;
  if (stopped || !isReallyVisible()) return;       // hidden / parked: no timer at all
  timer = window.setTimeout(() => { send("tick"); arm(); }, EVERY_MS);
}

function stop(): void {
  if (stopped) return;
  stopped = true;
  window.clearTimeout(timer);
  window.clearTimeout(gapTimer);
  setNetHook(null);
  setLoadListener(null);
  inflight.clear(); done = []; events = [];
}

// ── public API (called from settings/main.ts) ─────────────────────────────────────────────────
export const slog = {
  /** The shown tab changed. `why` = "tab click", "back/forward", "search" ... */
  tab(id: string, why: string): void {
    if (stopped || !started) return;
    if (id === active && !searching) return;
    ev("tab", `${active} -> ${id} (${why})`, { tab: id });
    active = id;
    tabsSincePerf.add(id);
    soon("tab");
  },
  search(q: string): void {
    if (stopped || !started) return;
    const on = q.trim().length > 0;
    if (on === searching) return;
    searching = on;
    ev("search", on ? "search started (every tab has to be built so it can be searched)" : "search cleared");
    soon("search");
  },
  /** Tabs that are still empty shells (the idle fill has not reached them). */
  pending(ids: string[]): void { pending = ids.slice(); },
  buildStart(id: string, why: string): void {
    if (stopped || !started) return;
    building = id;
    ev("build", `building tab ${id} (${why})`, { tab: id, why });
  },
  buildEnd(id: string, ms: number, why: string): void {
    if (stopped || !started) return;
    building = "";
    built.add(id);
    ev("built", `tab ${id} built in ${ms.toFixed(0)} ms (${why})`, { tab: id, ms, why });
    soon("built");
  },
  /** Call once, after the panels exist. */
  start(firstTab: string): void {
    if (started || stopped || WIN !== "settings" || !traceIsOn()) return;
    started = true;
    active = firstTab;
    built.add(firstTab);
    tabsSincePerf.add(firstTab);
    ev("open", `Settings window ready, first tab ${firstTab}`);

    setNetHook({
      start(method, path) {
        try {
          if (NOISE.has(path)) return 0;
          const o = currentOwner();
          const ot = ownerTab(o);
          const kind = o.startsWith("build:") ? "build" : o.startsWith("lazy:") ? "lazy" : o.startsWith("poll:") ? "poll" : "user";
          const id = nextId++;
          inflight.set(id, { m: method, p: path, o, tab: ot || active, kind, bg: ot !== "" && ot !== active, t0: performance.now() });
          if (inflight.size > maxIn) maxIn = inflight.size;
          return id;
        } catch { return 0; }
      },
      end(id, status, ms) {
        try {
          const r = inflight.get(id);
          if (!r) return;
          inflight.delete(id);
          done.push({ m: r.m, p: r.p, o: r.o, tab: r.tab, kind: r.kind, bg: r.bg, ms, st: status });
          if (done.length > 80) done.splice(0, done.length - 80);
        } catch { /* never break a request */ }
      },
    });

    setLoadListener((k, m) => ev(k, m));

    onPerfSample((s: PerfSample) => {
      const whole = visWholePerf && isReallyVisible();
      const single = tabsSincePerf.size <= 1;
      const a = animationsByArea();
      perf = {
        ok: whole && single, tab: active, mixed: !single,
        raf: s.rafRate, main: s.busy, script: s.scriptPct, render: s.renderPct, layout: s.layoutPct,
        slow: s.loafCount, worst: s.worstWho, rafFrom: s.rafSites,
        anims: a.running, endless: a.endless, heavy: s.heavy, where: a.where,
      };
      tabsSincePerf.clear();
      tabsSincePerf.add(active);
      visWholePerf = isReallyVisible();
    });

    window.addEventListener("focus", () => { ev("focus", "window got focus"); soon("focus"); });
    window.addEventListener("blur", () => { ev("focus", "window lost focus"); soon("focus"); });
    document.addEventListener("focusin", () => soon("focusin"));
    onVisibilityChange((v) => {
      ev("vis", v ? "window is on screen" : "window left the screen");
      visWholePerf = false;
      if (v) tabsSincePerf.add(active);
      arm();
      send(v ? "shown" : "hidden");               // one last snapshot says "not visible"
    });
    onTraceOff(stop);
    send("start");
    arm();
  },
};
