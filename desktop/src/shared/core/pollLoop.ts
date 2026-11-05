// Polling the backend's /events feed, but only as often as it pays off.
//
// Five windows (island, caption, mascot, notch mascot, dictation pill) each asked the backend for news
// every 150-250 ms, all day, even when nothing was happening. Every request costs the Python backend a
// thread, a lock and a JSON dump. Now a window polls at its old "fast" rate while events are arriving
// (and for `holdMs` after the last one), and at a calmer "idle" rate otherwise. A window that is hidden
// skips the request, as before. Nothing is drawn here: no animation, layout or frame rate changes.

import { isReallyVisible } from "./visibility";

let lastNews = 0;

/** Call with the number of events a poll just received (0 is fine). */
export function noteEvents(n: number): void {
  if (n > 0) lastNews = performance.now();
}

/**
 * Runs `tick` in a loop: every `fastMs` while there is news, every `idleMs` when it has been quiet for `holdMs`.
 * `skipWhenHidden` (default true) skips the request while the window is hidden; the caption and notch-mascot
 * windows never did that, so they pass false and keep polling exactly as before.
 */
export function adaptivePoll(tick: () => Promise<void>, fastMs: number, idleMs: number, skipWhenHidden = true, holdMs = 4000): void {
  const loop = async (): Promise<void> => {
    if (!skipWhenHidden || isReallyVisible()) {
      try { await tick(); } catch { /* every poller handles its own errors */ }
    }
    const quiet = performance.now() - lastNews > holdMs;
    window.setTimeout(() => void loop(), quiet ? idleMs : fastMs);
  };
  void loop();
}
