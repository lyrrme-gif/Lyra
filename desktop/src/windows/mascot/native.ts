// Thin wrapper over the Tauri side of the mascot window (src-tauri/src/mascot.rs).
// Every call is a no-op in a plain browser, so `npm run dev` + /mascot.html works.

import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { postFeatures } from "../../shared/core/featuresCache";

export const IS_TAURI = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export interface Rect { x: number; y: number; w: number; h: number }
export type MonitorMode = "cursor" | "island" | "hold";

export interface MascotPrefs {
  feature_mascot?: boolean;
  mascot_size?: string;
  feature_mascot_stay?: boolean;
  /** Settings → Mochi → Character ("cloud" | "fox" | "cat"). Missing = cloud. */
  mascot_shape?: string;
}
export interface ScreenInfo { x: number; y: number; width: number; height: number; scale: number }
export interface BootInfo { screen: ScreenInfo; prefs: MascotPrefs; suspended: boolean }

async function call<T>(cmd: string, args?: Record<string, unknown>): Promise<T | null> {
  if (!IS_TAURI) return null;
  try { return await invoke<T>(cmd, args); } catch (err) { console.error(`[mascot] ${cmd} failed`, err); return null; }
}

export const native = {
  boot: () => call<BootInfo>("mascot_boot"),
  /** Window-logical rectangles that take the mouse (character, open menu). Empty = all click-through. */
  setRects: (rects: Rect[]) => call<void>("mascot_set_rects", { rects }),
  /** True while the character is moving: Rust then streams the cursor at ~60 Hz. */
  setActive: (active: boolean) => call<void>("mascot_set_active", { active }),
  /** Which monitor the window covers: the cursor's, the island's, or stay where it is. */
  monitor: (mode: MonitorMode) => call<void>("mascot_monitor", { mode }),
  /** Click on the character: open the island chat. */
  openChat: () => call<void>("mascot_open_chat"),
  /** Draw this window above the island (the Mochi is dragged to or from it). */
  raise: () => call<void>("mascot_raise"),
  /** A Mochi dragged out of the island's Mochi tab is being held: Rust reports where the button is let go ("mascot-drop"). */
  carry: (on: boolean) => call<void>("mascot_carry", { on }),
};

export async function onNative<T>(name: string, handler: (payload: T) => void): Promise<() => void> {
  if (!IS_TAURI) return () => {};
  return listen<T>(name, (e) => handler(e.payload));
}

/** Persist a mascot preference in the backend (settings group "features"). */
export function savePref(values: Record<string, unknown>): void {
  void postFeatures(values);
}
