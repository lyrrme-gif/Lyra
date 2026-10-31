// Spotify island — pure helpers (no DOM, no Tauri) so they can be tested alone.

export type Status = "idle" | "playing" | "paused";

/** Payload of the `spotify-state` event (see model.rs `Payload`). */
export interface SpotifyState {
  running: boolean;
  status: Status;
  title: string;
  artist: string;
  album: string;
  positionMs: number | null;
  durationMs: number | null;
  canPrev: boolean;
  canNext: boolean;
  canToggle: boolean;
  /** Shuffle as the player reports it; null/undefined when it reports nothing. */
  shuffle?: boolean | null;
  /** True when the player says it accepts shuffle requests. */
  canShuffle?: boolean;
  trackKey: string;
  /** App that owns the media session: "Spotify", "Chrome", "VLC"... (empty when idle). */
  source?: string;
  /** Artwork is only attached when it changed; the page keeps the last one. */
  artChanged: boolean;
  art: string | null;
}

/** Lyrics are only looked up for Spotify itself: a browser tab, VLC... reports a "title" too (a match page, a
 *  video), and searching LRCLIB for it is a wasted request on every title change. */
export function isSpotifySource(s: Pick<SpotifyState, "source"> | null | undefined): boolean {
  return /spotify/i.test(s?.source || "");
}

/** m:ss, or h:mm:ss past an hour. */
export function formatTime(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/**
 * Where the song is *now*, given the position reported at `receivedAt`.
 * Spotify only reports on change, so while playing the page advances it itself.
 */
export function currentPosition(
  s: Pick<SpotifyState, "status" | "positionMs" | "durationMs">,
  receivedAt: number,
  now: number,
): number | null {
  if (s.positionMs == null || s.durationMs == null || s.durationMs <= 0) return null;
  const advance = s.status === "playing" ? Math.max(0, now - receivedAt) : 0;
  return Math.min(s.durationMs, s.positionMs + advance);
}

/** 0..1 for the progress bar. */
export function progressFraction(position: number | null, duration: number | null): number {
  if (position == null || duration == null || duration <= 0) return 0;
  return Math.min(1, Math.max(0, position / duration));
}

/** What the pill shows for the title / artist lines. Never invents a track. */
export function labels(s: SpotifyState): { title: string; artist: string } {
  const app = s.source || "Spotify";
  if (s.status === "idle") return { title: app, artist: "Nothing playing" };
  return { title: s.title || app, artist: s.artist || s.album || "" };
}
