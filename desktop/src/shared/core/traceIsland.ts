// Island-specific tracing: a one-line picture of the island, and a log line whenever any part of it
// changes (mode, tab, small/full card, pin, ...), saying what caused it. See core/trace.ts.

import type { Island } from "../../windows/island/island";
import { PEEK_SIZES } from "./layout";
import { State } from "./state";
import { noteCause, recentCause, setSnapshotProvider, trace, traceLimited } from "./trace";

export function installIslandTrace(island: Island): void {
  setSnapshotProvider(() => island.traceSnapshot());

  // Anything that changes on the island is reported with its cause.
  const watched = (): Record<string, string> => ({
    mode: State.mode,
    view: State.view,
    peek: PEEK_SIZES[State.view] && State.mode === "expanded" ? (State.peek ? "SMALL" : "FULL") : "-",
    fsm: island.fsm.state,
    pinned: String(State.isPinned),
    lyrics: String(State.lyricsOpen),
    listening: String(State.recognitionBusy),
    paused: String(State.paused),
    bot: String(State.stateOverride ?? "-"),
    search: String(!!State.searchResult),
  });
  let prev = watched();
  State.subscribe(() => {
    const now = watched();
    const diff: string[] = [];
    for (const k of Object.keys(now)) if (now[k] !== prev[k]) diff.push(`${k}: ${prev[k]} -> ${now[k]}`);
    if (!diff.length) return;
    const was = prev;
    prev = now;
    const cause = recentCause();
    const modeOrView = was.mode !== now.mode || was.view !== now.view || was.peek !== now.peek;
    trace("state", `${diff.join(" | ")}   <= ${cause ? "caused by " + cause : "no click or key in the last 1.5s (voice, timer, backend event, auto-close...)"}` +
      (modeOrView ? `\n      island now: ${island.traceSnapshot()}` : ""));
  });

  // Pointer entering / leaving the island: the auto-close timers depend on this.
  const el = document.getElementById("island");
  if (el) {
    el.addEventListener("pointerenter", () => traceLimited("p-in", 300, "pointer", `pointer entered the island (mode=${State.mode})`));
    el.addEventListener("pointerleave", () => traceLimited("p-out", 300, "pointer", `pointer left the island (mode=${State.mode}, view=${State.view})`));
  }

  // Calm-time picture every 30 s: what was on screen when something later went wrong.
  window.setInterval(() => {
    if (State.mode !== "hidden") trace("snapshot", island.traceSnapshot());
  }, 30_000);
}

export { noteCause };
