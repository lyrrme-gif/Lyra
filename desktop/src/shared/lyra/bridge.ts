/**
 * Lyra ↔ UI bridge (stub)
 *
 * Lyra's Python backend already emits states via ui.set_state():
 *   idle | listening | thinking | speaking | executing | success | error
 *
 * Options to connect them:
 * 1. Named pipe / Unix socket (same pattern Lyra used for hooks)
 * 2. Local WebSocket server in the Tauri process
 * 3. Tauri command that Python calls via a small sidecar
 *
 * This module will receive those states and map them to Mochi expressions.
 */

export type LyraState =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "executing"
  | "success"
  | "error"
  | "waiting";

/** Map Lyra state → rough Mochi / island behaviour (to be wired into Island). */
export function lyraStateToHint(state: LyraState): string {
  switch (state) {
    case "idle":
      return "breathing";
    case "listening":
      return "attentive";
    case "thinking":
      return "thinking";
    case "speaking":
      return "talking";
    case "executing":
      return "working";
    case "success":
      return "happy-jump";
    case "error":
      return "confused";
    case "waiting":
      return "peeking";
    default:
      return "idle";
  }
}
