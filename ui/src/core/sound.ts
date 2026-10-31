import { getFeatures } from "./featuresCache";
// SoundEngine. Every Mochi character has its own set of the 28 sounds below, served at
// /sounds/<character>/<name>.wav (cat, fox, cloud). The engine plays the set of the character picked in
// Settings → Mochi and switches by itself when that pick changes. Default volume 0.12, slider range 0–0.2,
// and several sounds may overlap.

export const SOUND_NAMES = [
  "peek", "open", "close", "hover", "blip", "slap", "annoyed", "dizzy", "greet",
  "work", "finish", "error", "approval", "question", "approve", "gulp", "tick",
  "send", "love", "pop", "proud", "wink", "yawn", "attach", "think", "search",
  "rate", "sleep",
] as const;

export type SoundName = (typeof SOUND_NAMES)[number];

const CHARACTERS = ["cloud", "fox", "cat", "bunny", "panda", "knight"];
const BASE = "http://127.0.0.1:8765";

class SoundEngine {
  enabled = true;
  volume = 0.12;
  character = "cloud";
  private checkedAt = 0;

  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private buffers = new Map<string, AudioBuffer>();
  private loading: Promise<void> | null = null;
  private idleTimer: number | null = null;

  /** Creates the context and decodes every WAV. Safe to call more than once. */
  preload(): Promise<void> {
    if (this.loading) return this.loading;
    this.loading = (async () => {
      const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      if (!Ctor) return;
      const ctx = new Ctor();
      this.ctx = ctx;
      const master = ctx.createGain();
      master.gain.value = this.volume;
      master.connect(ctx.destination);
      this.master = master;
      await this.refreshCharacter(true);
    })();
    return this.loading;
  }

  /** Decodes the 28 sounds of `id` (once). A missing file must never break the island. */
  private async loadSet(id: string): Promise<void> {
    const ctx = this.ctx;
    if (!ctx) return;
    await Promise.all(
      SOUND_NAMES.map(async (name) => {
        const key = `${id}/${name}`;
        if (this.buffers.has(key)) return;
        try {
          const res = await fetch(`/sounds/${key}.wav`);
          if (!res.ok) return;
          this.buffers.set(key, await ctx.decodeAudioData(await res.arrayBuffer()));
        } catch { /* ignore */ }
      }),
    );
  }

  /** Switch to the sounds of `id` (cloud / fox / cat). Anything else falls back to the cloud. */
  setCharacter(id: string): void {
    const next = CHARACTERS.includes(id) ? id : "cloud";
    if (next === this.character && this.buffers.has(`${next}/finish`)) return;
    this.character = next;
    void this.loadSet(next);
  }

  /** Reads the character picked in Settings → Mochi (at most every few seconds; every window has its own engine). */
  private async refreshCharacter(force = false): Promise<void> {
    if (!force && Date.now() - this.checkedAt < 4000) return;
    this.checkedAt = Date.now();
    try {
      const f = (await getFeatures()) as { mascot_shape?: string } | null;
      if (f) {
        const id = String(f.mascot_shape ?? "cloud");
        this.character = CHARACTERS.includes(id) ? id : "cloud";
      }
    } catch { /* backend not up yet: keep the current set */ }
    await this.loadSet(this.character);
  }

  /** WebView2 can hand us a suspended context; call after any user input. */
  resume() {
    if (this.idleTimer != null) {
      window.clearTimeout(this.idleTimer);
      this.idleTimer = null;
    }
    void this.ctx?.resume();
  }

  /**
   * Called when the island goes quiet. A running AudioContext keeps an audio
   * thread and its render quantum alive even with nothing playing, which shows
   * up as a steady trickle of CPU on a machine that is supposed to be idle.
   *
   * The delay covers the tail of whatever just played — suspending mid-sound
   * would clip it — and `play()` resumes the context on its own.
   */
  idle() {
    if (!this.ctx || this.ctx.state !== "running" || this.idleTimer != null) return;
    this.idleTimer = window.setTimeout(() => {
      this.idleTimer = null;
      void this.ctx?.suspend();
    }, 1500);
  }

  setVolume(v: number) {
    this.volume = Math.max(0, Math.min(0.2, v));
    if (this.master) this.master.gain.value = this.volume;
  }

  setEnabled(on: boolean) {
    this.enabled = on;
  }

  /** Reminder alarm: a clear two-note chime, three rounds. Always audible, independent of UI sounds. */
  alarm(rounds = 3) {
    const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return;
    const ctx = this.ctx ?? (this.ctx = new Ctor());
    void ctx.resume();
    const out = ctx.createGain();
    out.gain.value = 0.32;
    out.connect(ctx.destination);
    const bell = (freq: number, at: number) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = "sine";
      o.frequency.value = freq;
      g.gain.setValueAtTime(0.0001, at);
      g.gain.exponentialRampToValueAtTime(1, at + 0.015);
      g.gain.exponentialRampToValueAtTime(0.0001, at + 0.7);
      o.connect(g); g.connect(out);
      o.start(at); o.stop(at + 0.75);
    };
    const t0 = ctx.currentTime + 0.05;
    for (let r = 0; r < rounds; r++) {
      const t = t0 + r * 1.25;
      [880, 1318.5, 880, 1318.5].forEach((f, k) => bell(f, t + k * 0.2));
    }
    this.idle();
  }

  /** `force`: play even when the island's UI sounds are off (a Mochi sound stands in for speech). */
  play(name: SoundName | string, force = false) {
    if (!this.enabled && !force) return;
    const ctx = this.ctx;
    const master = this.master;
    void this.refreshCharacter();
    const buf = this.buffers.get(`${this.character}/${name}`);
    if (!ctx || !master || !buf) return;
    if (this.idleTimer != null) {
      window.clearTimeout(this.idleTimer);
      this.idleTimer = null;
    }
    if (ctx.state === "suspended") void ctx.resume();
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(master);
    src.start();
  }
}

export const Sound = new SoundEngine();
