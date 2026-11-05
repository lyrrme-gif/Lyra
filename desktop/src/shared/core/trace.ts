// Live UI trace: what you clicked, what appeared, what is animating, what failed.
//
// Every line is sent to the Lyra backend (POST /trace) which prints it in the backend terminal
// and in ui.log as "[trace] ui:<window>:<tag>: ...", right next to the backend's own lines
// (keys, dictation, CPU...). So one log tells the whole story, in order.
//
// Privacy: typed text is NEVER logged. Ordinary character keys are logged as "key", text fields
// as "(text field, N chars)", chat / dictation / clipboard request bodies as key names only.
// Turn it all off in DevTools with   localStorage["lyra-trace"] = "0"   (default is ON).
//
// This file imports nothing from the island, so every window (island, settings, wheel, caption,
// mascot) can use it. The island-specific part (state diffs, snapshots) is in traceIsland.ts.

import { installPerfProbe } from "./perfProbe";

const BASE = "http://127.0.0.1:8765";

export type WinName = "island" | "settings" | "wheel" | "caption" | "mascot" | "other";

function detectWindow(): WinName {
  const p = (typeof location !== "undefined" ? location.pathname : "").toLowerCase();
  if (p.includes("settings")) return "settings";
  if (p.includes("wheel")) return "wheel";
  if (p.includes("caption")) return "caption";
  if (p.includes("mascot")) return "mascot";
  if (p === "/" || p.includes("index") || p === "") return "island";
  return "other";
}

export const WIN: WinName = detectWindow();

function enabled(): boolean {
  try { return window.localStorage.getItem("lyra-trace") !== "0"; } catch { return true; }
}
let ON = enabled();

// The backend's LYRA_TRACE=0 used to switch off only the backend half: every window kept its observers, wrapped
// timers and probe running and kept posting lines the backend then threw away. The backend now answers
// POST /trace with {"off": true} and the page shuts its tracer down (observers, wrappers, sampler, queue).
const offHooks: Array<() => void> = [];
export function traceIsOn(): boolean { return ON; }
export function onTraceOff(cb: () => void): void { offHooks.push(cb); }
function shutOff(): void {
  if (!ON) return;
  ON = false;
  queue = [];
  netHook = null;
  for (const cb of offHooks.splice(0)) { try { cb(); } catch { /* ignore */ } }
}

// ── Request hook (Settings log) ────────────────────────────────────────────────────────────
// settings/settingsLog.ts listens here to see every backend request: who started it, how long it took, how many
// were in flight together. The hook only records; it never changes a request or its answer.
export interface NetHook {
  /** A backend request is starting (called synchronously inside fetch(), so the owner stack is still valid). */
  start(method: string, path: string): number;
  /** It finished (status 0 = network error). */
  end(id: number, status: number, ms: number): void;
}
let netHook: NetHook | null = null;
export function setNetHook(h: NetHook | null): void { netHook = ON ? h : null; }

// ── Sending ────────────────────────────────────────────────────────────────────────────────

interface Line { t: number; tag: string; msg: string }
let queue: Line[] = [];
let flushTimer = 0;
let failures = 0;
let mutedUntil = 0;
const realFetch: typeof fetch = typeof window !== "undefined" && window.fetch ? window.fetch.bind(window) : (undefined as never);

function flush(): void {
  flushTimer = 0;
  if (!queue.length) return;
  if (Date.now() < mutedUntil) { queue = queue.slice(-200); return; }   // backend was offline: wait before retrying
  const batch = queue.splice(0, 80);
  realFetch(`${BASE}/trace`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ win: WIN, lines: batch }),
    keepalive: true,
  }).then((r) => r.json().catch(() => null)).then((j: { off?: boolean } | null) => {
    failures = 0;
    if (j && j.off === true) shutOff();
  }).catch(() => {
    failures++;
    if (failures >= 3) mutedUntil = Date.now() + Math.min(30_000, failures * 3_000);
  });
  if (queue.length) flushTimer = window.setTimeout(flush, 100);
}

/** Record one event. Never throws, never blocks. */
export function trace(tag: string, msg = ""): void {
  if (!ON) return;
  try {
    queue.push({ t: Date.now(), tag, msg: msg.length > 400 ? msg.slice(0, 400) + "…" : msg });
    if (queue.length > 400) queue.splice(0, queue.length - 400);
    if (!flushTimer) flushTimer = window.setTimeout(flush, 250);
  } catch { /* tracing must never break the UI */ }
}

/** Same line at most once per `everyMs` (per key); the lines skipped in between are counted. */
const lastSeen = new Map<string, { at: number; skipped: number }>();
export function traceLimited(key: string, everyMs: number, tag: string, msg: string): void {
  if (!ON) return;
  const now = performance.now();
  const s = lastSeen.get(key);
  if (s && now - s.at < everyMs) { s.skipped++; return; }
  const extra = s && s.skipped ? `  (+${s.skipped} more like this since last line)` : "";
  lastSeen.set(key, { at: now, skipped: 0 });
  if (lastSeen.size > 600) lastSeen.clear();
  trace(tag, msg + extra);
}

// ── Describing elements ───────────────────────────────────────────────────────────────────

const INTERACTIVE = "button, a, input, select, textarea, label, summary, [role=button], [role=tab], [role=switch], [data-view], [data-act], [data-action], [tabindex]";

function isTextField(el: Element): boolean {
  return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || (el as HTMLElement).isContentEditable === true;
}

function one(el: Element): string {
  let s = el.tagName.toLowerCase();
  if (el.id) s += "#" + el.id;
  const cls = Array.from(el.classList).filter((c) => !/^(on|off|active|hover|focus|show|hide|open|visible)$/i.test(c)).slice(0, 3);
  if (cls.length) s += "." + cls.join(".");
  const h = el as HTMLElement;
  for (const a of ["view", "act", "action", "kind", "tab", "id"]) {
    const v = h.dataset?.[a];
    if (v && a !== "id") s += `[${a}=${v.slice(0, 20)}]`;
  }
  return s;
}

/** A short label a human can recognise: aria-label / title / alt / the element's own text. */
function labelOf(el: Element): string {
  if (isTextField(el)) {
    const v = (el as HTMLInputElement).value ?? el.textContent ?? "";
    const ph = (el as HTMLInputElement).placeholder;
    return `"(text field${ph ? ` '${ph.slice(0, 24)}'` : ""}, ${String(v).length} chars)"`;
  }
  const h = el as HTMLElement;
  const raw = h.getAttribute("aria-label") || h.getAttribute("title") || h.getAttribute("alt") || (h.innerText || h.textContent || "");
  const t = raw.replace(/\s+/g, " ").trim();
  return t ? `"${t.length > 28 ? t.slice(0, 28) + "…" : t}"` : "";
}

/** `button.pk-shrink "Expand" < div.view.pk-is-peek < div#island` */
export function describe(el: Element | null): string {
  if (!el) return "(nothing)";
  const target = el.closest(INTERACTIVE) ?? el;
  const parts = [one(target) + (labelOf(target) ? " " + labelOf(target) : "")];
  let cur: Element | null = target.parentElement;
  let n = 0;
  while (cur && cur !== document.body && cur.id !== "root" && n < 3) {
    parts.push(one(cur));
    if (cur.id) break;
    cur = cur.parentElement;
    n++;
  }
  return parts.join(" < ");
}

function rectStr(el: Element): string {
  const r = el.getBoundingClientRect();
  return `${Math.round(r.width)}x${Math.round(r.height)}@${Math.round(r.left)},${Math.round(r.top)}`;
}

// ── Click → what happened afterwards ──────────────────────────────────────────────────────

let snapshotFn: (() => string) | null = null;
/** The island registers a function that returns a one-line picture of itself (mode, view, size...). */
export function setSnapshotProvider(fn: () => string): void { snapshotFn = fn; }

/** What the last user action was, so a later state change can say "because of this". */
let lastCause = { what: "", at: 0 };
export function noteCause(what: string): void { lastCause = { what, at: performance.now() }; }
export function recentCause(withinMs = 1500): string {
  const age = performance.now() - lastCause.at;
  return lastCause.what && age <= withinMs ? `${lastCause.what} ${Math.round(age)}ms ago` : "";
}

function followUp(label: string): void {
  if (!snapshotFn) return;
  const before = safe(snapshotFn);
  for (const ms of [250, 900]) {
    window.setTimeout(() => {
      const now = safe(snapshotFn!);
      trace("after", now === before
        ? `${label} +${ms}ms: NO visible change on the island (${now})`
        : `${label} +${ms}ms: ${now}   [before: ${before}]`);
    }, ms);
  }
}
function safe(fn: () => string): string { try { return fn(); } catch (e) { return `(snapshot failed: ${e})`; } }

// ── The installer ─────────────────────────────────────────────────────────────────────────

const NOISY_PATHS = new Set(["/trace", "/events", "/status", "/record/status", "/diagnostics"]);
const SECRET_BODY = /(chat|dictation|clipboard|memory|secret|key|token|password|translate|search|notify|google|smarthome|snippets|dictionary)/i;

let installed = false;

export function installTracer(): void {
  if (!ON || installed || typeof window === "undefined") return;
  installed = true;
  const t0 = performance.now();

  trace("window", `${WIN} window started: ${innerWidth}x${innerHeight} @${devicePixelRatio}x, ${location.pathname}, ` +
    `UI ${(document.documentElement.dataset.dTheme || "?")} theme, tauri=${"__TAURI_INTERNALS__" in window}`);

  // ----- mouse -----
  const BTN = ["left", "middle", "right"];
  window.addEventListener("pointerdown", (e) => {
    const el = e.target instanceof Element ? e.target : null;
    const btn = BTN[e.button] ?? `button${e.button}`;
    const hit = el ? describe(el) : "(nothing)";
    const empty = el && (el === document.body || el === document.documentElement || el.id === "root");
    const isl = document.getElementById("island");
    let where = "";
    if (isl) {
      const r = isl.getBoundingClientRect();
      const inside = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
      where = inside ? ` | inside island (+${Math.round(e.clientX - r.left)},${Math.round(e.clientY - r.top)}) island=${rectStr(isl)}` : ` | OUTSIDE island (island=${rectStr(isl)})`;
    }
    const what = `${btn} press on ${hit}${empty ? " [empty page area]" : ""} at (${Math.round(e.clientX)},${Math.round(e.clientY)})${where}`;
    trace("click", what);
    noteCause(`${btn} click on ${el ? one(el.closest(INTERACTIVE) ?? el) : "?"}`);
    followUp(`after ${btn} click on ${el ? one(el.closest(INTERACTIVE) ?? el) : "?"}`);
  }, true);

  window.addEventListener("click", (e) => {
    const el = e.target instanceof Element ? e.target : null;
    const t = el?.closest(INTERACTIVE);
    if (t) trace("click", `activated ${describe(t)}${(t as HTMLButtonElement).disabled ? "  [DISABLED]" : ""}`);
  }, true);

  window.addEventListener("dblclick", (e) => trace("click", `double-click on ${describe(e.target as Element)}`), true);
  window.addEventListener("contextmenu", (e) => {
    trace("click", `right-click menu on ${describe(e.target as Element)}${e.defaultPrevented ? " (page handled it)" : ""}`);
    noteCause("right click");
  }, true);

  let wheelAcc = 0;
  window.addEventListener("wheel", (e) => {
    wheelAcc += e.deltaY;
    traceLimited("wheel", 800, "scroll", `wheel over ${describe(e.target as Element)} (dy=${Math.round(wheelAcc)})`);
    wheelAcc = 0;
  }, { capture: true, passive: true });

  // ----- keyboard: special keys by name, letters never -----
  window.addEventListener("keydown", (e) => {
    if (e.repeat) return;
    const mods = [e.ctrlKey && "Ctrl", e.altKey && "Alt", e.metaKey && "Win", e.shiftKey && e.key.length > 1 && "Shift"].filter(Boolean) as string[];
    const named = e.key.length > 1 || e.key === " ";
    const combo = mods.length > 0;
    const shown = named ? (e.key === " " ? "Space" : e.key) : combo ? e.key.toUpperCase() : "key";
    const target = e.target instanceof Element ? e.target : null;
    const inField = target && isTextField(target) ? ` in text field${(target as HTMLElement).className ? " ." + String((target as HTMLElement).className).split(" ")[0] : ""}` : "";
    if (!named && !combo && !inField) { traceLimited("key-plain", 3000, "key", "a character key was pressed (not logged)"); return; }
    trace("key", `${[...mods, shown].join("+")} pressed${inField}`);
    noteCause(`key ${[...mods, shown].join("+")}`);
  }, true);

  // ----- drag & drop -----
  for (const ev of ["dragenter", "drop", "dragleave"] as const) {
    window.addEventListener(ev, (e) => {
      const dt = (e as DragEvent).dataTransfer;
      const kinds = dt ? Array.from(dt.types).join(",") : "?";
      const n = dt?.files?.length ?? 0;
      if (ev === "dragleave") { traceLimited("dragleave", 1000, "drag", "drag left the window"); return; }
      trace("drag", `${ev} over ${describe(e.target as Element)} (types: ${kinds}${n ? `, ${n} file(s)` : ""})`);
    }, true);
  }

  // ----- window / page life -----
  window.addEventListener("focus", () => trace("window", "window got focus"));
  window.addEventListener("blur", () => trace("window", "window lost focus"));
  document.addEventListener("visibilitychange", () => trace("window", `page is now ${document.visibilityState}`));
  window.addEventListener("online", () => trace("window", "network: online"));
  window.addEventListener("offline", () => trace("window", "network: OFFLINE"));
  let rz = 0;
  window.addEventListener("resize", () => {
    window.clearTimeout(rz);
    rz = window.setTimeout(() => trace("window", `window resized to ${innerWidth}x${innerHeight}`), 300);
  });
  window.addEventListener("pagehide", () => { trace("window", "window closing"); flush(); });

  // ----- errors -----
  window.addEventListener("error", (e) => {
    trace("ERROR", `${e.message} at ${(e.filename || "").split("/").pop()}:${e.lineno}:${e.colno}`);
  });
  window.addEventListener("unhandledrejection", (e) => {
    const r = e.reason;
    trace("ERROR", `unhandled promise rejection: ${r && (r.stack || r.message) ? String(r.stack || r.message).split("\n").slice(0, 3).join(" | ") : String(r)}`);
  });
  for (const level of ["error", "warn"] as const) {
    const orig = console[level].bind(console);
    console[level] = (...args: unknown[]) => {
      orig(...args);
      traceLimited(`console-${level}-${String(args[0]).slice(0, 40)}`, 1500, level === "error" ? "ERROR" : "warn",
        `console.${level}: ${args.map((a) => (a instanceof Error ? a.message : typeof a === "object" ? safeJson(a) : String(a))).join(" ").slice(0, 300)}`);
    };
  }

  // ----- backend requests: failures, slow ones, and user-triggered ones -----
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = (init?.method || (typeof input === "object" && "method" in input ? input.method : "GET") || "GET").toUpperCase();
    const isBackend = url.startsWith(BASE);
    const path = isBackend ? url.slice(BASE.length).split("?")[0] : "";
    if (path === "/trace" || path === "/settings-log") return realFetch(input as RequestInfo, init);
    const hook = isBackend ? netHook : null;
    const hid = hook ? hook.start(method, path) : 0;
    const t = performance.now();
    try {
      const res = await realFetch(input as RequestInfo, init);
      if (hid) hook!.end(hid, res.status, Math.round(performance.now() - t));
      if (isBackend) {
        const ms = Math.round(performance.now() - t);
        const noisy = NOISY_PATHS.has(path);
        if (!res.ok) trace("http", `FAILED ${method} ${path} -> ${res.status} (${ms}ms)`);
        else if (ms > 1500) trace("http", `SLOW ${method} ${path} -> ${res.status} took ${ms}ms`);
        else if (method !== "GET" && !noisy) trace("http", `${method} ${path} -> ${res.status} (${ms}ms)${bodyKeys(path, init?.body)}`);
        else if (!noisy) traceLimited(`get-${path}`, 5000, "http", `GET ${path} -> ${res.status} (${ms}ms)`);
      }
      return res;
    } catch (err) {
      if (hid) hook!.end(hid, 0, Math.round(performance.now() - t));
      if (isBackend) trace("http", `NETWORK ERROR ${method} ${path}: ${(err as Error)?.message ?? err} (is the backend running?)`);
      throw err;
    }
  };

  // ----- animations & transitions -----
  const animKey = (e: Event, name: string) => `${name}|${one(e.target as Element)}`;
  const running = new Map<string, number>();
  for (const ev of ["animationstart", "transitionrun"] as const) {
    document.addEventListener(ev, (e) => {
      const name = (e as AnimationEvent).animationName || (e as TransitionEvent).propertyName;
      if (!(e.target instanceof Element)) return;
      const k = animKey(e, name);
      running.set(k, performance.now());
      traceLimited(`anim-${k}`, 1200, "anim", `${ev === "animationstart" ? "animation" : "transition"} '${name}' started on ${one(e.target)}`);
    }, true);
  }
  for (const ev of ["animationend", "transitionend", "animationcancel", "transitioncancel"] as const) {
    document.addEventListener(ev, (e) => {
      const name = (e as AnimationEvent).animationName || (e as TransitionEvent).propertyName;
      if (!(e.target instanceof Element)) return;
      const k = animKey(e, name);
      const started = running.get(k);
      running.delete(k);
      const took = started != null ? Math.round(performance.now() - started) : -1;
      const cancelled = ev.endsWith("cancel");
      if (cancelled) traceLimited(`anim-c-${k}`, 1200, "anim", `'${name}' on ${one(e.target)} was INTERRUPTED${took >= 0 ? ` after ${took}ms` : ""}`);
      else if (took > 1200) trace("anim", `'${name}' on ${one(e.target)} finished after ${took}ms (slow)`);
    }, true);
  }
  // Animations that never end are a classic cause of "the island is stuck" and of idle CPU use.
  let lastRunning = -1;
  window.setInterval(() => {
    try {
      const all = document.getAnimations();
      let infinite = 0;
      const stuck: string[] = [];
      for (const a of all) {
        if (a.playState !== "running") continue;
        const timing = (a.effect as AnimationEffect | null)?.getComputedTiming?.();
        if (timing && timing.iterations === Infinity) { infinite++; continue; }
        const ct = Number(a.currentTime ?? 0);
        if (ct > 4000) {
          const tgt = (a.effect as KeyframeEffect | null)?.target;
          stuck.push(`${(a as CSSAnimation).animationName || (a as CSSTransition).transitionProperty || "anim"} on ${tgt ? one(tgt) : "?"} (${Math.round(ct / 1000)}s)`);
        }
      }
      if (stuck.length) traceLimited("stuck-anim", 10_000, "anim", `STILL RUNNING after 4s+: ${stuck.slice(0, 4).join(", ")}`);
      const run = all.filter((a) => a.playState === "running").length;
      if (Math.abs(run - lastRunning) >= 3) {
        lastRunning = run;
        trace("anim", `${run} animations running now (${infinite} endless loops)`);
      }
    } catch { /* getAnimations not supported */ }
  }, 3000);

  // ----- what appeared / disappeared on the page -----
  if (typeof MutationObserver !== "undefined") {
    const obs = new MutationObserver((records) => {
      if (!ON) return;
      const added: string[] = [];
      const removed: string[] = [];
      for (const r of records) {
        if (r.type === "childList") {
          r.addedNodes.forEach((n) => { if (n instanceof Element && interesting(n)) added.push(one(n) + sizeOf(n)); });
          r.removedNodes.forEach((n) => { if (n instanceof Element && interesting(n)) removed.push(one(n)); });
        } else if (r.type === "attributes" && r.target instanceof Element && r.attributeName === "class") {
          const el = r.target;
          if (!el.id && !el.classList.contains("view")) continue;
          const was = new Set((r.oldValue || "").split(/\s+/).filter(Boolean));
          const now = new Set(Array.from(el.classList));
          const plus = [...now].filter((c) => !was.has(c) && !IGNORE_CLASS.test(c));
          const minus = [...was].filter((c) => !now.has(c) && !IGNORE_CLASS.test(c));
          if (plus.length || minus.length) {
            traceLimited(`cls-${one(el)}-${plus}-${minus}`, 600, "dom", `${one(el)} classes: ${plus.length ? "+" + plus.join(" +") : ""}${minus.length ? " -" + minus.join(" -") : ""}`);
          }
        } else if (r.type === "attributes" && r.target instanceof Element && (r.attributeName === "hidden" || r.attributeName?.startsWith("data-"))) {
          const el = r.target;
          if (!el.id && !el.classList.contains("view")) continue;
          const v = el.getAttribute(r.attributeName!);
          if (v === r.oldValue) continue;
          traceLimited(`attr-${one(el)}-${r.attributeName}-${v}`, 600, "dom", `${one(el)} ${r.attributeName}: ${r.oldValue ?? "(none)"} -> ${v ?? "(removed)"}`);
        }
      }
      for (const s of dedupe(added).slice(0, 8)) traceLimited(`add-${s}`, 800, "appeared", s);
      for (const s of dedupe(removed).slice(0, 8)) traceLimited(`rem-${s}`, 800, "gone", s);
    });
    const start = () => obs.observe(document.body, {
      childList: true, subtree: true, attributes: true, attributeOldValue: true,
      attributeFilter: ["class", "hidden", "data-view", "data-mode", "data-state", "data-peek", "data-theme"],
    });
    if (document.body) start(); else document.addEventListener("DOMContentLoaded", start, { once: true });
    onTraceOff(() => obs.disconnect());
  }

  // ----- the UI thread was blocked (this is what "lag" looks like) -----
  try {
    const longTasks = new PerformanceObserver((list) => {
      for (const e of list.getEntries()) {
        if (e.duration >= 120) trace("LAG", `the UI thread was frozen for ${Math.round(e.duration)}ms (starting ${Math.round(e.startTime - t0)}ms after the window opened)`);
      }
    });
    longTasks.observe({ entryTypes: ["longtask"] });
    onTraceOff(() => longTasks.disconnect());
  } catch { /* longtask not supported */ }

  // ----- what exactly is burning CPU/GPU in this window (animations, timers, scripts, blur) -----
  installPerfProbe(trace, describe);
}

// ── helpers ───────────────────────────────────────────────────────────────────────────────

const IGNORE_CLASS = /^(hover|focus|pressed|dragging|tick|beat|pulse|blink)$/i;

function interesting(el: Element): boolean {
  const tag = el.tagName;
  if (tag === "SCRIPT" || tag === "STYLE" || tag === "LINK" || tag === "BR" || tag === "SPAN" && !el.id && !el.classList.length) return false;
  if (tag === "CANVAS" || tag === "I" || tag === "SVG" || tag === "PATH" || tag === "CIRCLE" || tag === "RECT" || tag === "IMG" && !el.id) return false;
  return !!(el.id || el.classList.length);
}

function sizeOf(el: Element): string {
  const r = el.getBoundingClientRect();
  return r.width || r.height ? ` (${Math.round(r.width)}x${Math.round(r.height)})` : "";
}

function dedupe(a: string[]): string[] { return Array.from(new Set(a)); }

function safeJson(o: unknown): string {
  try { return JSON.stringify(o).slice(0, 200); } catch { return String(o); }
}

function bodyKeys(path: string, body: BodyInit | null | undefined): string {
  if (typeof body !== "string") return body ? " [binary body]" : "";
  try {
    const obj = JSON.parse(body);
    if (obj && typeof obj === "object") {
      const keys = Object.keys(obj);
      if (SECRET_BODY.test(path)) return `  body keys: ${keys.join(",")} (values hidden)`;
      const small = keys.slice(0, 6).map((k) => {
        const v = (obj as Record<string, unknown>)[k];
        return `${k}=${typeof v === "object" ? "{…}" : String(v).slice(0, 24)}`;
      });
      return `  body: ${small.join(", ")}`;
    }
  } catch { /* not JSON */ }
  return `  body: ${body.length} chars`;
}

// ── What the backend told the UI ──────────────────────────────────────────────────────────

const QUIET_EVENTS = new Set(["wave", "levels", "heartbeat"]);
const PRIVATE_EVENTS = new Set(["chat", "reply", "notify", "translate", "web_search", "mochi_sound"]);

/** One line per event the backend pushed (what the UI asked for / was told to show). Text is never logged. */
export function traceBackendEvent(msg: Record<string, unknown>, replaying: boolean): void {
  if (!ON) return;
  const type = String(msg.type ?? "?");
  if (QUIET_EVENTS.has(type)) return;
  const f: string[] = [];
  for (const k of ["state", "view", "action", "kind", "phase", "open", "on", "app", "source", "provider", "percent", "level", "muted", "mic", "recording"]) {
    if (msg[k] !== undefined && msg[k] !== "" && !(type === "notify" && k === "sender")) f.push(`${k}=${String(msg[k]).slice(0, 30)}`);
  }
  if (PRIVATE_EVENTS.has(type)) {
    const text = typeof msg.text === "string" ? msg.text : typeof msg.answer === "string" ? msg.answer : "";
    if (text) f.push(`text=${text.length} chars (hidden)`);
    if (Array.isArray(msg.results)) f.push(`results=${msg.results.length}`);
  } else {
    if (typeof msg.title === "string") f.push(`title="${msg.title.slice(0, 30)}"`);
    if (Array.isArray(msg.items)) f.push(`items=${msg.items.length}`);
  }
  const line = `backend -> UI: ${type}${f.length ? " " + f.join(" ") : ""}${replaying ? "  [old event replayed after (re)start, ignored by the UI]" : ""}`;
  const frequent = type === "stats" || type === "timers" || type === "controls" || type === "status" || type === "appvolume";
  if (frequent) traceLimited(`ev-${type}-${msg.kind ?? ""}`, 4000, "event", line);
  else trace("event", line);
  if (!replaying) noteCause(`backend event ${type}${msg.view ? " " + msg.view : ""}${msg.state ? " " + msg.state : ""}`);
}
