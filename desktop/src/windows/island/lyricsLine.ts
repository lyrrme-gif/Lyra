// Current lyric line for the compact notch.
//
// Uses the same backend route as the Music tab (GET /lyrics → LRCLIB, already cached
// there), so no new endpoint, no new URL for the CSP. Only time-synced lyrics are used:
// plain (unsynced) lyrics can't follow playback, so the notch keeps showing the artist.
//
// This file has no DOM: the island asks `at(trackKey, positionMs)` for the line to show.

import { isSpotifySource, type SpotifyState } from "../../shared/spotify/model";

const BASE = "http://127.0.0.1:8765";

export interface LyricLine {
  t: number; // ms from the start of the song
  text: string;
}

export interface CurrentLine {
  idx: number;
  text: string;
  /** How long this line stays up (until the next one), ms. */
  dur: number;
}

/** Show a line a hair early, like the Music tab does. */
export const LEAD_MS = 150;
const DEFAULT_LINE_MS = 4000;
const RETRY_MS = 30_000;

/** Index of the line being sung at `pos`, or -1 before the first line (binary search). */
export function lineIndexAt(lines: LyricLine[], pos: number, lead = LEAD_MS): number {
  let lo = 0;
  let hi = lines.length - 1;
  let idx = -1;
  const p = pos + lead;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lines[mid].t <= p) {
      idx = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return idx;
}

function clean(raw: unknown): LyricLine[] {
  if (!Array.isArray(raw)) return [];
  const out: LyricLine[] = [];
  for (const l of raw) {
    const t = Number((l as LyricLine)?.t);
    if (Number.isFinite(t)) out.push({ t, text: String((l as LyricLine).text ?? "").trim() });
  }
  return out.sort((a, b) => a.t - b.t);
}

export class LyricsLine {
  private key = "";
  private lines: LyricLine[] | null = null;
  private loading = false;
  private retryAt = 0;
  /** Finished lookups, by trackKey. Only definitive answers are cached (not network errors). */
  private cache = new Map<string, LyricLine[] | null>();

  /**
   * Call whenever the media state changes. Starts a lookup the first time a track is
   * seen; does nothing for a track that is already loaded or loading.
   * `s` null (nothing playing) forgets the current track.
   */
  track(s: SpotifyState | null, onReady: () => void): void {
    if (!s || !s.running || s.status === "idle" || !s.title || !s.trackKey || !isSpotifySource(s)) {
      this.key = "";
      this.lines = null;
      this.loading = false;
      return;
    }
    if (s.trackKey === this.key) {
      // Same song: only retry a failed lookup, and not too often.
      if (this.lines || this.loading || performance.now() < this.retryAt) return;
    } else {
      this.key = s.trackKey;
      this.lines = null;
      this.loading = false;
      this.retryAt = 0;
    }
    if (this.cache.has(s.trackKey)) {
      this.lines = this.cache.get(s.trackKey) ?? null;
      return;
    }
    void this.load(s, onReady);
  }

  private async load(s: SpotifyState, onReady: () => void): Promise<void> {
    const key = s.trackKey;
    this.loading = true;
    const q = new URLSearchParams({
      title: s.title,
      artist: s.artist || "",
      album: s.album || "",
      duration: s.durationMs ? String(Math.round(s.durationMs / 1000)) : "",
    });
    let lines: LyricLine[] | null = null;
    let definitive = true;
    try {
      const r = await fetch(`${BASE}/lyrics?${q}`);
      if (!r.ok) throw new Error(String(r.status));
      const d = await r.json();
      if (d.error) definitive = false; // lyrics service unreachable: try again later
      else if (d.found && !d.instrumental) {
        const c = clean(d.synced);
        lines = c.length ? c : null;
      }
    } catch {
      definitive = false;
    }
    if (definitive) this.cache.set(key, lines);
    if (this.key !== key) return; // the song changed while we were fetching
    this.loading = false;
    this.lines = lines;
    if (!definitive) this.retryAt = performance.now() + RETRY_MS;
    if (lines) onReady();
  }

  /** The line to show at `posMs` for `trackKey`, or null (no lyrics / before the first line). */
  at(trackKey: string, posMs: number | null): CurrentLine | null {
    const lines = this.lines;
    if (!lines || trackKey !== this.key || posMs == null) return null;
    const idx = lineIndexAt(lines, posMs);
    if (idx < 0) return null;
    const next = lines[idx + 1];
    const dur = next ? Math.max(500, next.t - lines[idx].t) : DEFAULT_LINE_MS;
    return { idx, text: lines[idx].text || "\u266a", dur };
  }
}
