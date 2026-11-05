// One helper for every "ask the backend again every so often" loop in the UI.
//
// Rules it enforces, so each caller does not have to remember them:
//   1. Nothing runs while the window is hidden (document.hidden). The timer is CLEARED, not just skipped,
//      so a hidden window has no pending timer at all. It is re-armed on visibilitychange.
//   2. With `target`, nothing runs while that element is not on screen (a Settings panel that is not the
//      open tab, a window that was closed and removed). Watched with an IntersectionObserver, with an
//      offsetParent check as the fallback. The timer is cleared the same way.
//   3. `floorMs` is a hard minimum between two runs. It also covers the "run at once when shown again"
//      step: flipping focus back and forth can never beat the floor.
//   4. Calls never overlap: the next timer starts only after the previous tick has finished.
//
// Nothing is drawn here and no backend code is involved. Logging / trace calls stay in the callers.

import { isReallyVisible, onVisibilityChange } from "./visibility";
import { elLabel, withOwner } from "./loadOwner";

export interface VisiblePollOptions {
  /** Delay before the next run, in ms. A function lets a caller go faster while something is busy. */
  ms: number | (() => number);
  /** Hard minimum between two runs, in ms (default 0). Callers that must stay slow pass 30_000. */
  floorMs?: number;
  /** Run only while this element is on screen and attached. */
  target?: Element;
  /** Run once as soon as the poll becomes active (default true). Still respects floorMs. */
  runOnShow?: boolean;
  /** Only for the Settings log: names this poll in "who started this request?". Defaults to the target's class. */
  name?: string;
}

export interface VisiblePoll {
  /** Stop for good: clears the timer and removes every listener. */
  stop(): void;
  /** Run now if the poll is active and the floor allows it (e.g. after a user action). */
  poke(): void;
  /** Re-arm the timer with the current delay (after a one-off refresh made by hand, so it is not doubled). */
  reschedule(): void;
  /** True while a timer is pending (useful in tests and in the final audit). */
  readonly armed: boolean;
}

export function visiblePoll(tick: () => Promise<unknown> | unknown, opts: VisiblePollOptions): VisiblePoll {
  const floor = Math.max(0, opts.floorMs ?? 0);
  const runOnShow = opts.runOnShow !== false;
  const target = opts.target;
  const owner = "poll:" + (opts.name ?? elLabel(target));
  const hasIO = typeof IntersectionObserver !== "undefined";

  let timer = 0;
  let stopped = false;
  let busy = false;
  let lastRun = 0;
  let inView = !target || !hasIO;          // with an observer we wait for its first report
  let io: IntersectionObserver | null = null;

  const onScreen = (): boolean => {
    if (!target) return true;
    if (!target.isConnected) return false;
    if (hasIO) return inView;
    return (target as HTMLElement).offsetParent !== null;
  };
  const active = (): boolean => !stopped && isReallyVisible() && onScreen();
  const delay = (): number => Math.max(floor, typeof opts.ms === "function" ? opts.ms() : opts.ms);

  const clearTimer = (): void => {
    if (timer) { window.clearTimeout(timer); timer = 0; }
  };
  const arm = (): void => {
    clearTimer();
    if (!active()) return;                 // hidden / off screen: no timer at all
    timer = window.setTimeout(() => void run(), delay());
  };
  const run = async (): Promise<void> => {
    timer = 0;
    if (!active()) return;
    if (busy) return;
    busy = true;
    lastRun = Date.now();
    try { await withOwner(owner, tick); } catch { /* every caller handles its own errors */ } finally {
      busy = false;
      arm();
    }
  };
  /** The window or element just changed state: clear, or run once (if the floor allows) and re-arm. */
  const reevaluate = (): void => {
    if (!active()) { clearTimer(); return; }
    if (runOnShow && !busy && Date.now() - lastRun >= floor) void run();
    else arm();
  };

  const onVisibility = (): void => reevaluate();
  document.addEventListener("visibilitychange", onVisibility);
  const offVis = onVisibilityChange(onVisibility);   // also fires when the window is parked / un-parked
  window.addEventListener("pagehide", stop);

  if (target && hasIO) {
    io = new IntersectionObserver((entries) => {
      const last = entries[entries.length - 1];
      if (last) inView = last.isIntersecting;
      reevaluate();
    });
    io.observe(target);
  } else {
    reevaluate();
  }

  function stop(): void {
    stopped = true;
    clearTimer();
    io?.disconnect();
    io = null;
    document.removeEventListener("visibilitychange", onVisibility);
    offVis();
    window.removeEventListener("pagehide", stop);
  }

  return {
    stop,
    poke(): void {
      if (!active() || busy) return;
      if (Date.now() - lastRun < floor) { arm(); return; }
      void run();
    },
    reschedule(): void { if (!busy) arm(); },
    get armed(): boolean { return timer !== 0; },
  };
}
