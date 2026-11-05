// Auto-hide for the island (Settings → General → Auto-hide).
//
// Rust (src-tauri/src/autohide.rs) reports what the user is looking at and the island applies its mode
// to it. Keep `autoHidden` in step with `hidden()` in autohide.rs: the floating Mochi window is hidden
// from the Rust side.

import { Bridge, onEvent } from "./bridge";

export type AutoHideMode = "always" | "desktop";

export interface AutoHideContext {
  /** No app window is focused (desktop, taskbar, tray). */
  desktop: boolean;
  /** The focused app is maximized or full-screen. */
  covering: boolean;
  /** A screen share is running. */
  sharing: boolean;
}

export const AUTOHIDE_MODES: Array<{ id: AutoHideMode; label: string }> = [
  { id: "always", label: "Always show" },
  { id: "desktop", label: "Only on desktop" },
];

export const AUTOHIDE_HINT =
  "Always show: never hides. Only on desktop: shown only while no app window is focused. " +
  "Either way it hides automatically while you share your screen.";

export function normalizeAutoHide(v: unknown): AutoHideMode {
  return v === "desktop" ? v : "always";   // an old "apps" value reads as "always"
}

/** What the page assumes before Rust has answered (or in a plain browser): nothing hides. */
export const DEFAULT_CONTEXT: AutoHideContext = { desktop: true, covering: false, sharing: false };

export function autoHidden(mode: AutoHideMode, ctx: AutoHideContext): boolean {
  if (ctx.sharing) return true;
  if (mode === "desktop") return !ctx.desktop;
  return false;
}

/** Current context now, then every change. Returns the unsubscribe function (a no-op in a plain browser). */
export async function watchContext(handler: (ctx: AutoHideContext) => void): Promise<() => void> {
  const first = await Bridge.context();
  if (first) handler(first);
  return onEvent<AutoHideContext>("context", handler);
}
