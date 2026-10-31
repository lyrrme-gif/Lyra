// Thin wrapper over the Tauri commands/events. Every call is a no-op when the
// page is opened in a plain browser, so the island can be iterated on with
// `npm run dev` alone.

import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import type { AutoHideContext } from "./autohide";
import type { Settings } from "./state";
import { trace, traceLimited } from "./trace";

export const IS_TAURI =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

async function call<T>(cmd: string, args?: Record<string, unknown>): Promise<T | null> {
  if (!IS_TAURI) return null;
  if (!QUIET_CMDS.has(cmd)) traceLimited(`cmd-${cmd}`, 400, "native", `UI -> window/OS command '${cmd}'${briefArgs(args)}`);
  try {
    return await invoke<T>(cmd, args);
  } catch (err) {
    trace("ERROR", `native command '${cmd}' FAILED: ${String((err as Error)?.message ?? err).slice(0, 200)}`);
    console.error(`[lyra] ${cmd} failed`, err);
    return null;
  }
}

/** Calls too frequent or too boring for the trace (the trace's own log line included). */
const QUIET_CMDS = new Set(["log_line", "set_hit_shapes", "context"]);

/** Small, harmless arguments only (numbers, booleans, short words); never text the user typed. */
function briefArgs(args?: Record<string, unknown>): string {
  if (!args) return "";
  const out: string[] = [];
  for (const [k, v] of Object.entries(args)) {
    if (typeof v === "boolean" || typeof v === "number") out.push(`${k}=${v}`);
    else if (typeof v === "string" && v.length <= 20 && /^[\w .:-]*$/.test(v)) out.push(`${k}=${v}`);
  }
  return out.length ? ` (${out.join(", ")})` : "";
}

export interface BootInfo {
  settings: Settings;
  /** Logical screen rect of the monitor the island lives on. */
  screen: { x: number; y: number; width: number; height: number; scale: number };
  version: string;
  hookPath: string;
  /** False where the OS has no global cursor (Wayland): see Island.followPageCursor. */
  cursorPoll: boolean;
}

export const Bridge = {
  boot: () => call<BootInfo>("boot"),

  saveSettings: (settings: Settings) => call<void>("save_settings", { settings }),

  /** Desktop / app / screen-share state for the auto-hide options (then the "context" event on every change). */
  context: () => call<AutoHideContext>("context"),

  /** Shrink the window down to the invisible wake strip (hidden) or back to full. */
  setCollapsed: (collapsed: boolean) => call<void>("set_collapsed", { collapsed }),

  /** Chat uses a tall native panel so its message area and composer can use the screen. */
  setChatPanel: (tall: boolean) => call<void>("set_chat_panel", { tall }),

  /** The island's Music tab is open / closed: Rust keeps reading Spotify while it is. */
  musicView: (active: boolean) => call<void>("spotify_music_view", { active }),

  /** Media playback control used by the Music tab. */
  spotifyControl: (action: string) => call<void>("spotify_control", { action }),

  /**
   * Pushes the island shape in window coordinates. Rust flips click-through from
   * its own cursor poll, so the flag is never a frame behind a click.
   */
  setIslandRect: (
    x: number, y: number, width: number, height: number,
    extra: { x: number; y: number; w: number; h: number }[] = [],
  ) => call<void>("set_island_rect", { x, y, width, height, extra }),

  /** Give the window keyboard focus (chat field) and take it away again. */
  focusWindow: (focused: boolean) => call<void>("focus_window", { focused }),

  reposition: () => call<void>("reposition"),

  openUrl: (url: string) => call<void>("open_url", { url }),

  quit: () => call<void>("quit_app"),

  openSettingsWindow: () => call<void>("open_settings_window"),

  /** Writes to %LOCALAPPDATA%\Coucou\coucou.log, next to the Rust lines. */
  log: (message: string) => call<void>("log_line", { message }),

  approvalDecision: (requestId: string, decision: "allow" | "deny") =>
    call<void>("approval_decision", { requestId, decision }),

  // ── Chat, files ───────────────────────────────────────────────────────────
  chatReset: () => call<void>("chat_reset"),
  /** Copies a dropped file into the inbox. */
  ingestFile: (path: string) => callOrThrow<DroppedFile>("ingest_file", { path }),

  /** Tray → Pause. Pauses the island. */
  setPaused: (paused: boolean) => call<void>("set_paused", { paused }),

  /** Arm/disarm the native Windows OLE drop target for the Clipboard tab. */
  setDropTarget: (enabled: boolean) => call<void>("set_drop_target", { enabled }),

  // ── Tab wheel (its own window: src/wheel, src-tauri/src/wheel.rs) ──────────
  /** Show the wheel at the cursor, or in the middle of the screen. */
  wheelOpen: (atCursor: boolean, payload: WheelPayload) => call<void>("wheel_open", { atCursor, payload }),
  /** Hide the wheel without choosing anything. */
  wheelClose: () => call<void>("wheel_close"),
  /** The wheel window chose a tab: Rust hides it and tells the island. */
  wheelPick: (view: string) => call<void>("wheel_pick", { view }),
  /** Island → wheel window: "stay" (the shortcut was only tapped) or "commit" (it was held and let go). */
  wheelCmd: (cmd: "stay" | "commit") => call<void>("wheel_cmd", { cmd }),
};

/** What the island tells the wheel window when it opens it. */
export interface WheelPayload {
  /** The tabs that are switched on, as island view names. */
  views: string[];
  /** The tab on screen now. */
  current: string;
  /** Opened by the held shortcut: letting go opens the tab you point at. */
  hold: boolean;
  theme: string;
}

/** Same as `call`, but surfaces the error so the UI can show what went wrong. */
async function callOrThrow<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  if (!IS_TAURI) throw new Error("not running inside Lyra");
  return invoke<T>(cmd, args);
}

export interface DroppedFile {
  name: string;
  path: string;
  size: number;
}

export type BridgeEvent =
  | { name: "cursor"; payload: { x: number; y: number } }
  | { name: "tray"; payload: string }
  | { name: "screen-changed"; payload: null };

export interface DragDropPayload {
  type: "enter" | "over" | "drop" | "leave";
  paths?: string[];
}

/** Files dragged onto the island. Only reaches us when the window takes the mouse. */
export async function onDragDrop(handler: (e: DragDropPayload) => void) {
  if (!IS_TAURI) return () => {};
  return getCurrentWebview().onDragDropEvent((event) => {
    handler(event.payload as DragDropPayload);
  });
}

export async function onEvent<T>(name: string, handler: (payload: T) => void) {
  if (!IS_TAURI) return () => {};
  return listen<T>(name, (e) => {
    if (name !== "cursor" && name !== "spotify-state") traceLimited(`evt-${name}`, 300, "native", `OS/window -> UI event '${name}'`);
    handler(e.payload);
  });
}
