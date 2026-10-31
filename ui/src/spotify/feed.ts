// Spotify feed for the island's Music tab.
//
// Rust (src-tauri/src/spotify) owns the Windows media session. It already sends
// `spotify-state` to the little Spotify pill; while the Music tab is open it sends
// the very same payload to the island window too. This module keeps the latest one
// (plus the cover, which only arrives when it changes) and lets the tab subscribe.
//
// No polling here and no Spotify API: everything the tab shows comes from Rust.

import { Bridge, IS_TAURI, onEvent } from "../core/bridge";
import type { SpotifyState } from "./model";

type Listener = () => void;

class Feed {
  /** Last payload from Rust, or null before the first one arrives. */
  state: SpotifyState | null = null;
  /** performance.now() when `state` arrived — the position is extrapolated from it. */
  receivedAt = 0;
  /** Current cover as a data URL; kept across payloads that don't carry one. */
  art: string | null = null;

  private listeners = new Set<Listener>();
  private starting: Promise<void> | null = null;

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  /** Attaches the event listener once. Resolves when it is safe to ask Rust for state. */
  private start(): Promise<void> {
    if (!this.starting) {
      this.starting = onEvent<SpotifyState>("spotify-state", (s) => this.onState(s)).then(() => {});
    }
    return this.starting;
  }

  private onState(next: SpotifyState) {
    if (!next.running) this.art = null;
    else if (next.artChanged) this.art = next.art;
    this.state = next;
    this.receivedAt = performance.now();
    for (const fn of this.listeners) fn();
  }

  /**
   * The Music tab became visible (true) or hidden (false). Rust only reads Spotify
   * for the island while this is on, and answers with the full current state.
   */
  async setActive(on: boolean): Promise<void> {
    if (!IS_TAURI) return;
    if (on) await this.start(); // listener first, so the answer cannot be missed
    await Bridge.musicView(on);
  }

  control(action: "toggle" | "next" | "previous" | "shuffle") {
    void Bridge.spotifyControl(action);
  }

  seek(positionMs: number) {
    const ms = Math.max(0, Math.round(positionMs));
    void Bridge.spotifyControl(`seek:${ms}`);
  }

  /** Starts the Spotify desktop app through its URI handler. */
  launch() {
    void Bridge.openUrl("spotify:");
  }
}

export const SpotifyFeed = new Feed();
