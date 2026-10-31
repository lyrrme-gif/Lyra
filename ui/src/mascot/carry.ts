// Dragging the Mochi between the island and the screen.
//
//   island → screen:  grab the Mochi on the island's Mochi tab and pull it out. The mascot overlay (a separate
//                     window that covers the monitor) takes it over, flies it out of the tab to the cursor and
//                     lets it go where the button is released.
//   screen → island:  drag the Mochi on the screen back onto the island. It flies into the Mochi tab (if that is
//                     open) or into the notch, and the island shows its own again.
//
// The island window and the overlay are separate webviews, so they talk through these small events (the same
// way dock.ts does). Coordinates sent by the island are island-window coordinates; the island window is
// `winW` wide and sits centred at the top of the monitor, the overlay covers the whole monitor, so the overlay
// turns them into its own with  x + (screenWidth - winW) / 2.

import { emit, listen } from "@tauri-apps/api/event";

const IS_TAURI = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/** Where the Mochi sits in the Mochi tab, in island-window coordinates (`body` = its width in px). */
export interface NestSpot { cx: number; cy: number; body: number }

/** The island's current shape, so the overlay knows what counts as "on the island" and where the Mochi lands. */
export interface NestInfo {
  /** Width of the island window (the overlay centres it on the monitor). */
  winW: number;
  /** The island's shape in island-window coordinates (0-size while hidden). */
  rect: { x: number; y: number; w: number; h: number };
  /** The Mochi tab is open and showing its Mochi: this is where it lands. Null otherwise (it lands in the notch). */
  spot: NestSpot | null;
}

/** Island → overlay: the Mochi was picked up from the Mochi tab. */
export interface CarryStart {
  /** Centre of the Mochi in the tab, island-window coordinates. */
  fx: number; fy: number;
  /** Where the cursor holds it, relative to that centre. */
  gx: number; gy: number;
  /** Width of the Mochi's body in the tab, px. */
  body: number;
  winW: number;
}

/** Overlay → island: `over` = the Mochi is held above the island (it will land there); `end` = it was let go. */
export interface CarryState { over?: boolean; end?: "screen" | "island" }

// The island's own flag (same webview as the Mochi tab and the FSM): while true the island does not close.
export const carry = {
  active: false,
  /** The Mochi tab, while it is open, says where its Mochi sits (null otherwise). */
  spot: (): NestSpot | null => null,
};

const START = "mochi-carry";
const STATE = "mochi-carry-state";
const QUERY = "mochi-nest-query";
const NEST = "mochi-nest";

function send(name: string, payload?: unknown): void {
  if (!IS_TAURI) return;
  void emit(name, payload).catch(() => undefined);
}
async function on<T>(name: string, handler: (p: T) => void): Promise<() => void> {
  if (!IS_TAURI) return () => {};
  return listen<T>(name, (e) => handler(e.payload));
}

export const emitCarryStart = (c: CarryStart) => send(START, c);
export const onCarryStart = (h: (c: CarryStart) => void) => on<CarryStart>(START, h);

export const emitCarryState = (s: CarryState) => send(STATE, s);
export const onCarryState = (h: (s: CarryState) => void) => on<CarryState>(STATE, h);

export const emitNestQuery = () => send(QUERY, null);
export const onNestQuery = (h: () => void) => on<null>(QUERY, () => h());

export const emitNest = (n: NestInfo) => send(NEST, n);
export const onNest = (h: (n: NestInfo) => void) => on<NestInfo>(NEST, h);
