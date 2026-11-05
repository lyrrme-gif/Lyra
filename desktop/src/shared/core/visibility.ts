// ONE answer to "is this window really on screen?", shared by every loop in every window.
//
// WebView2 reports document.hidden === false for a window that was only moved far off screen ("parked",
// e.g. the Settings window waiting for the tray click), so rAF / setInterval loops in it never stopped.
// A window counts as NOT visible when the page is hidden OR the window sits at x/y <= PARK_LIMIT.
//
// Also sets <html data-parked> while not visible; a injected rule pauses every CSS animation then.

const PARK_LIMIT = -20000;

// ── Debug switch: pause ONE window at a time, to read each window's GPU/CPU cost on its own ─────────
// Off by default and inert unless localStorage["lyra-debug-pause"] holds this window's name.
// Windows share one origin, so the key is shared: any window can set it, all windows react (storage event).
//   Ctrl+Alt+Shift+P  in any focused window cycles: none -> island -> settings -> mascot -> caption -> wheel -> all -> none
//   DevTools:         __lyraPause("settings")   (also "none", "all", or "island,mascot")
// A paused window behaves exactly like a parked one: every loop built on isReallyVisible() stops and its
// CSS animations are frozen. Window, backend GPU line and the perf-window trace line show the effect.
const DEBUG_KEY = "lyra-debug-pause";
const DEBUG_CYCLE = ["", "island", "settings", "mascot", "caption", "wheel", "island,settings,mascot,caption,wheel"];

function windowName(): string {
  const p = (typeof location !== "undefined" ? location.pathname : "").toLowerCase();
  if (p.includes("settings")) return "settings";
  if (p.includes("wheel")) return "wheel";
  if (p.includes("caption")) return "caption";
  if (p.includes("mascot")) return "mascot";
  return "island";
}
export const WINDOW_NAME = windowName();

let debugPaused = false;
function readDebug(): boolean {
  try {
    const v = window.localStorage.getItem(DEBUG_KEY) ?? "";
    return v.split(",").map((x) => x.trim()).includes(WINDOW_NAME);
  } catch { return false; }
}
/** True while the debug switch holds THIS window paused (for the probe's log line). */
export function isDebugPaused(): boolean { return debugPaused; }

export function isParked(): boolean {
  const x = window.screenX ?? window.screenLeft ?? 0;
  const y = window.screenY ?? window.screenTop ?? 0;
  return x <= PARK_LIMIT || y <= PARK_LIMIT;
}

export function isReallyVisible(): boolean {
  return !document.hidden && !isParked() && !debugPaused;
}

type Listener = (visible: boolean) => void;
const listeners = new Set<Listener>();
let last = isReallyVisible();

function apply(v: boolean): void {
  const root = document.documentElement;
  if (v) root.removeAttribute("data-parked"); else root.setAttribute("data-parked", "1");
}

function recheck(): void {
  debugPaused = readDebug();
  const v = isReallyVisible();
  if (v === last) return;
  last = v;
  apply(v);
  for (const cb of [...listeners]) { try { cb(v); } catch { /* a listener must not break the others */ } }
}

/** Call `cb` whenever the window becomes (in)visible. Returns an unsubscribe function. */
export function onVisibilityChange(cb: Listener): () => void {
  listeners.add(cb);
  return () => { listeners.delete(cb); };
}

if (typeof document !== "undefined") {
  const style = document.createElement("style");
  style.textContent = "html[data-parked] *, html[data-parked] *::before, html[data-parked] *::after { animation-play-state: paused !important; }";
  (document.head ?? document.documentElement).appendChild(style);
  apply(last);
  document.addEventListener("visibilitychange", recheck);
  window.addEventListener("focus", recheck);
  window.addEventListener("resize", recheck);
  // Moving a window raises no event: a cheap property read once a second notices parking / un-parking.
  window.setInterval(recheck, 1000);

  window.addEventListener("storage", (e) => { if (e.key === DEBUG_KEY) recheck(); });
  const setPause = (v: string): void => {
    try { if (v) window.localStorage.setItem(DEBUG_KEY, v); else window.localStorage.removeItem(DEBUG_KEY); } catch { /* ignore */ }
    recheck();
  };
  (window as unknown as Record<string, unknown>).__lyraPause = (v = "none") => setPause(v === "none" ? "" : v === "all" ? DEBUG_CYCLE[DEBUG_CYCLE.length - 1] : v);
  window.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey && e.altKey && e.shiftKey && e.code === "KeyP")) return;
    let cur = ""; try { cur = window.localStorage.getItem(DEBUG_KEY) ?? ""; } catch { /* ignore */ }
    const i = DEBUG_CYCLE.indexOf(cur);
    setPause(DEBUG_CYCLE[(i + 1) % DEBUG_CYCLE.length]);
  }, { passive: true });
  debugPaused = readDebug();
  if (debugPaused) apply(false);
}
