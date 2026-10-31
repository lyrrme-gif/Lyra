// The mascot lives in the island's notch (src/island/notchMochi.ts) and only leaves it to
// explain something on the screen. The mascot overlay window and the island window are
// separate webviews, so this tiny channel tells the island "the Mochi is out" / "it is back".
// While out, the overlay repeats the message as a heartbeat, so a crashed overlay can never
// leave the notch empty for good (the island side times out, see AWAY_TIMEOUT_MS).

import { emit, listen } from "@tauri-apps/api/event";

const EVENT = "mascot-away";
const IS_TAURI = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/** The island treats the Mochi as back home when no heartbeat arrived for this long. */
export const AWAY_TIMEOUT_MS = 10_000;

/** Overlay → island: true while the Mochi is out of the notch. */
export function emitAway(away: boolean): void {
  if (!IS_TAURI) return;
  void emit(EVENT, away).catch(() => undefined);
}

/** Island side: called with true on every heartbeat while out, false when it is back. */
export async function onAway(handler: (away: boolean) => void): Promise<() => void> {
  if (!IS_TAURI) return () => {};
  return listen<boolean>(EVENT, (e) => handler(!!e.payload));
}

// ── Subtitles ───────────────────────────────────────────────────────────────────────────
// The caption window tells the overlay where the subtitle pill is (size only: the caption window
// is always bottom-centre of the island's monitor), so the Mochi can walk down and stand beside it.
const CAPTION_EVENT = "caption-pill";
/** `lift`: how far the pill sits above its usual spot (the chat bar is open under it). */
export interface CaptionPill { visible: boolean; w: number; h: number; lift?: number }

/** Caption → overlay: a subtitle pill is on screen (with its size) / gone. */
export function emitCaptionPill(p: CaptionPill): void {
  if (!IS_TAURI) return;
  void emit(CAPTION_EVENT, p).catch(() => undefined);
}

export async function onCaptionPill(handler: (p: CaptionPill) => void): Promise<() => void> {
  if (!IS_TAURI) return () => {};
  return listen<CaptionPill>(CAPTION_EVENT, (e) => handler(e.payload));
}

// ── Music ───────────────────────────────────────────────────────────────────────────────
// The island window owns the media session (Rust sends "spotify-state" to it only). Every window that
// draws a Mochi (notch, floating mascot, subtitles) is a separate webview, so the island relays one
// boolean: "music is playing". While it is true the island repeats it as a heartbeat, so a window
// that opens later (or a restarted overlay) catches up, and a crashed island can never leave the
// Mochi grooving for good (the listener times out, see MUSIC_TIMEOUT_MS).
const MUSIC_EVENT = "music-playing";
const MUSIC_BEAT_MS = 3000;
const MUSIC_TIMEOUT_MS = 9000;
let musicBeat = 0;

/** Island → everyone: true while a track is actually playing (not paused / stopped). */
export function emitMusic(playing: boolean): void {
  if (!IS_TAURI) return;
  window.clearInterval(musicBeat);
  const send = () => void emit(MUSIC_EVENT, playing).catch(() => undefined);
  send();
  if (playing) musicBeat = window.setInterval(send, MUSIC_BEAT_MS);
}

/** Called with true on every heartbeat while music plays, false when it stops (or the island went quiet). */
export async function onMusic(handler: (playing: boolean) => void): Promise<() => void> {
  if (!IS_TAURI) return () => {};
  let timer = 0;
  const off = await listen<boolean>(MUSIC_EVENT, (e) => {
    window.clearTimeout(timer);
    const on = !!e.payload;
    if (on) timer = window.setTimeout(() => handler(false), MUSIC_TIMEOUT_MS);
    handler(on);
  });
  return () => { window.clearTimeout(timer); off(); };
}

// ── Mochi sounds ────────────────────────────────────────────────────────────────────────
// Only the island window has the sounds loaded. When the floating Mochi makes one by itself (it gets
// dizzy when it is clicked again and again), it asks the island to play it.
const MOCHI_SOUND_EVENT = "mochi-sound";

/** Overlay → island: play the Mochi sound of this kind (see core/mochiSound.ts). */
export function emitMochiSound(kind: string): void {
  if (!IS_TAURI) return;
  void emit(MOCHI_SOUND_EVENT, kind).catch(() => undefined);
}

export async function onMochiSound(handler: (kind: string) => void): Promise<() => void> {
  if (!IS_TAURI) return () => {};
  return listen<string>(MOCHI_SOUND_EVENT, (e) => handler(String(e.payload)));
}
