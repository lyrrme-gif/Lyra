// One /settings/features request per window, shared by everything in it.
//
// The caption, island (notch Mochi + sounds + home) and others each asked for the same JSON on their own
// timers, so the backend answered the same question many times a minute while nothing had changed.
// Callers now share one cached answer and one in-flight request. The answer is refreshed only when:
//   - the window gets focus or becomes visible again (a change made in Settings shows up straight away),
//   - this window saves something through postFeatures(),
//   - the cached answer is older than MIN_AGE_MS (30 s) and somebody asks for it.
// There is no timer in here. A hidden window does not ask at all.

import { invoke } from "@tauri-apps/api/core";
import { emit, listen } from "@tauri-apps/api/event";

const BASE = "http://127.0.0.1:8765";
type Features = Record<string, unknown>;

/** No caller can make the cache younger than this, whatever maxAgeMs it passes. */
const MIN_AGE_MS = 30_000;

let cache: { at: number; data: Features } | null = null;
let inflight: Promise<Features | null> | null = null;
const listeners = new Set<(f: Features) => void>();

export function invalidateFeatures(): void {
  cache = null;
}

export async function getFeatures(maxAgeMs = MIN_AGE_MS, force = false): Promise<Features | null> {
  const age = Math.max(maxAgeMs, MIN_AGE_MS);
  if (cache && Date.now() - cache.at < age) return cache.data;
  if (document.hidden && !force) return cache?.data ?? null;
  if (inflight) return inflight;
  inflight = (async () => {
    try {
      const res = await fetch(`${BASE}/settings/features`);
      if (!res.ok) return cache?.data ?? null;
      const data = (await res.json()) as Features;
      cache = { at: Date.now(), data };
      for (const fn of listeners) { try { fn(data); } catch { /* a listener must not break the others */ } }
      return data;
    } catch {
      return cache?.data ?? null; // backend not up yet: keep the last value
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

/** Called with every fresh answer (after focus / visible / a save). Returns an unsubscribe function. */
export function onFeatures(fn: (f: Features) => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

/** POST a patch to /settings/features, then refresh the shared answer once. Resolves true when saved. */
export async function postFeatures(patch: Record<string, unknown>): Promise<boolean> {
  try {
    const r = await fetch(`${BASE}/settings/features`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
    });
    if (!r.ok) return false;
    invalidateFeatures();
    void getFeatures();                 // the one refresh after a save; listeners hear about it
    // The Rust mascot window no longer polls every 2 s; tell it to re-read now.
    if ("__TAURI_INTERNALS__" in window) void invoke("mascot_refresh_prefs").catch(() => { /* not fatal */ });
    return true;
  } catch {
    return false;                       // backend not up: the caller decides what to show
  }
}

/** Settings saved a feature switch: every window (the island too) re-reads at once instead of waiting for a focus event. */
const CHANGED_EVENT = "lyra-features-changed";
export function announceFeaturesChanged(): void {
  if (!("__TAURI_INTERNALS__" in window)) return;
  void emit(CHANGED_EVENT).catch(() => undefined);
}
if ("__TAURI_INTERNALS__" in window) {
  void listen(CHANGED_EVENT, () => { invalidateFeatures(); void getFeatures(MIN_AGE_MS, true); }).catch(() => undefined);
}

/** Focus / visible: drop the cache, and when anything is listening fetch once so it shows at once. */
const refreshOnReturn = (): void => {
  invalidateFeatures();
  if (listeners.size > 0 && !document.hidden) void getFeatures();
};
window.addEventListener("focus", refreshOnReturn);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshOnReturn(); });
