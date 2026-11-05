/** Shared island layout constants. */
export const ATTACHED_ROOM = 10;
/** Tabs that get ATTACHED_ROOM extra height in the attached position (keep in sync with style.css). */
export const ATTACHED_ROOM_VIEWS = new Set<string>([
  "overview", "music", "recognition", "timer", "weather", "calendar", "todo",
  "stats", "controls", "wallpaper", "record", "volume", "clipboard",
]);

/** A slot of the secondary (side) pills next to the island. */
export interface SecSlot { el: HTMLElement; id: string | null; shown: boolean; w: number; key: string }

/** true = the red pill shows a live m:ss clock (h:mm:ss past an hour) instead of the static word "REC".
 *  The clock only touches one text node once a second. Set false to go back to the fully static pill if recording ever hurts fps. */
export const LIVE_REC_CLOCK = true;
export function fmtClock(secs: number): string {
  const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), sec = secs % 60;
  const ss = String(sec).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** How long the volume / brightness HUD stays up (ms). */
export const VOLUME_HUD_MS = 1600;

/** Toasts: how long one stays, when a queued one is too old to show, and the longest it may wait for the island to be free (ms). */
export const TOAST_MS = 4000;
export const TOAST_STALE_MS = 15_000;
export const TOAST_MAX_WAIT_MS = 60_000;
