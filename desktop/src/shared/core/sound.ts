import { getFeatures } from "./featuresCache";
import { ALWAYS_AUDIBLE, TAIL_S, playActivitySound, type ActivitySoundName, type ActivitySoundOpts } from "./activitySounds";
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

const CHARACTERS = ["cloud", "fox", "cat", "bunny", "panda", "knight", "hornet", "lantern", "grimm"];

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
      this.ensure();
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

  /** Creates the AudioContext and the master gain on demand (Settings previews never call preload). */
  private ensure(): { ctx: AudioContext; master: GainNode } | null {
    if (!this.ctx) {
      const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      if (!Ctor) return null;
      this.ctx = new Ctor();
    }
    if (!this.master) {
      this.master = this.ctx.createGain();
      this.master.gain.value = this.volume;
      this.master.connect(this.ctx.destination);
    }
    return { ctx: this.ctx, master: this.master };
  }

  private alertBus: GainNode | null = null;
  /** A second gain for alarms and invitations (calls, reminders, timers): never quieter than a sensible floor. */
  private alerts(): GainNode | null {
    const e = this.ensure();
    if (!e) return null;
    if (!this.alertBus) {
      this.alertBus = e.ctx.createGain();
      this.alertBus.connect(e.ctx.destination);
    }
    this.alertBus.gain.value = Math.max(this.volume, 0.1) * 4.4;
    return this.alertBus;
  }

  private uiBus: GainNode | null = null;
  /** Synthesized sounds are sparse (bells, air), so on average they are quieter than the sampled ones: lift them to match. */
  private ui(): GainNode | null {
    const e = this.ensure();
    if (!e) return null;
    if (!this.uiBus) {
      this.uiBus = e.ctx.createGain();
      this.uiBus.gain.value = 1.7;
      this.uiBus.connect(e.master);
    }
    return this.uiBus;
  }

  /**
   * The synthesized Live Activity sounds (core/activitySounds.ts): message, ring, reminder, timerDone,
   * volume, connect, downloadDone, found ... Alarms and invitations are audible even with UI sounds off;
   * everything else follows the island's sound switch. `preview` is the Settings speaker button: always audible.
   */
  activity(name: ActivitySoundName, opts: ActivitySoundOpts & { preview?: boolean } = {}) {
    const loud = ALWAYS_AUDIBLE.has(name) || !!opts.preview;
    if (!this.enabled && !loud) return;
    const e = this.ensure();
    if (!e) return;
    const dest = loud ? this.alerts() : this.ui();
    if (!dest) return;
    if (this.idleTimer != null) { window.clearTimeout(this.idleTimer); this.idleTimer = null; }
    void e.ctx.resume();
    try {
      const tail = playActivitySound(e.ctx, dest, name, opts);
      // let the reverb tail ring out, then let the audio thread sleep
      window.setTimeout(() => this.idle(), Math.ceil((tail || TAIL_S[name] || 1) * 1000) + 200);
    } catch { /* a sound must never break the island */ }
  }

  private ringTimer: number | null = null;
  /** An incoming call: ring now and every 2.6 s until `ringStop()` (answer, decline, the toast ending). */
  ringStart() {
    this.ringStop();
    let n = 0;
    const once = () => {
      this.activity("ring");
      if (++n >= 10) this.ringStop();
    };
    once();
    this.ringTimer = window.setInterval(once, 2600);
  }
  ringStop() {
    if (this.ringTimer != null) { window.clearInterval(this.ringTimer); this.ringTimer = null; }
  }

  /** Reminder alarm: the reminder fanfare, `rounds` times. Always audible, independent of UI sounds. */
  alarm(rounds = 2) {
    for (let r = 0; r < rounds; r++) window.setTimeout(() => this.activity("reminder"), r * 3300);
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
