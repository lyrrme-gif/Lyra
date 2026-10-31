// Signals for the interactive tour (Settings → Replay tour). The island, dock and wheel report what
// the user actually does ("opened the wheel", "picked a tab" …) and the tour window waits for them,
// so a step's Next button only unlocks when the real action happened. A Tauri event reaches every
// window; in a plain browser both functions are no-ops.

import { emit, listen } from "@tauri-apps/api/event";
import { IS_TAURI } from "./bridge";

export type TourSignalKind =
  | "wheel-open"   // detail: "shortcut" | "rightclick" | "voice"
  | "wheel-pick"   // detail: the tab view
  | "dock-pick"    // detail: the tab view
  | "open-view"    // Lyra opened a tab on request (voice / backend), detail: the tab view
  | "said";        // the user spoke or typed a message, detail: the text

export interface TourSignal { kind: TourSignalKind; detail?: string }

const EVENT = "lyra-tour";

export function tourSignal(kind: TourSignalKind, detail?: string): void {
  if (!IS_TAURI) return;
  void emit(EVENT, { kind, detail } satisfies TourSignal).catch(() => undefined);
}

export function onTourSignal(handler: (s: TourSignal) => void): Promise<() => void> {
  if (!IS_TAURI) return Promise.resolve(() => undefined);
  return listen<TourSignal>(EVENT, (e) => handler(e.payload));
}
