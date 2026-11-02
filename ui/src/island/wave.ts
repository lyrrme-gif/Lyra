// Voice waveform: a row of white bars at the top-centre of the island that move
// with Lyra's voice while she speaks (compact pill and expanded island alike).
//
// Data flow: the backend measures how loud each moment of her speech is and sends
// that once as a "wave" event (see backend/speech_wave.py). We play it back against
// the wall clock, so it stays in sync however late the event arrives. If there is
// no envelope (backend couldn't compute one) we fall back to a simulated,
// speech-like wave so the bars still move.

import { h } from "../views/dom";
import { isReallyVisible, onVisibilityChange } from "../core/visibility";

export const WAVE_BARS = 21;
const MIN_H = 4; // px — always-visible resting dots so the talking state reads immediately
const MAX_H = 24; // px — strong centre peak while still fitting the compact pill

/** Loudness envelope from the backend: one 0..1 value every 1/hz seconds. */
export interface WaveEnvelope {
  levels: number[];
  hz: number;
  /** Epoch ms at which playback of the speech started. */
  startMs: number;
}

/** Loudness (0..1) of `env` at wall-clock `nowMs`; 0 before it starts or after it ends. */
export function envelopeLevel(env: WaveEnvelope, nowMs: number): number {
  const pos = ((nowMs - env.startMs) / 1000) * env.hz;
  if (pos < 0 || pos > env.levels.length - 1) return 0;
  const i = Math.floor(pos);
  const f = pos - i;
  const a = env.levels[i] ?? 0;
  const b = env.levels[i + 1] ?? a;
  return a + (b - a) * f;
}

/** Speech-like loudness (0..1): syllable-rate bursts with pauses between phrases. */
export function simulatedLevel(tSec: number): number {
  const phrase = 0.5 + 0.5 * Math.sin(tSec * 1.3); // slow rise and fall
  const syllable = Math.pow(0.5 + 0.5 * Math.sin(tSec * 9.5 + Math.sin(tSec * 2.1) * 2), 1.5);
  const flutter = 0.75 + 0.25 * Math.sin(tSec * 17.3 + 1);
  const gate = phrase > 0.12 ? 1 : phrase / 0.12; // brief pause between phrases
  return Math.min(1, (0.15 + 0.85 * syllable * flutter) * (0.55 + 0.45 * phrase) * gate);
}

/** Bar heights in px for an overall `level` (0..1) at time `tSec`. Tallest in the
 *  middle, tapering to dots at the ends, each bar wobbling a little on its own. */
export function barHeights(level: number, tSec: number, n = WAVE_BARS): number[] {
  const c = (n - 1) / 2;
  const out: number[] = [];
  for (let i = 0; i < n; i++) {
    const x = (i - c) / c; // -1 .. 1
    const profile = 0.1 + 0.9 * Math.exp(-(x * 1.7) * (x * 1.7)); // ends shrink to dots
    const jitter = 0.78 + 0.22 * Math.sin(i * 12.9898 + 1.3); // fixed per bar
    const wobble = 0.5 + 0.5 * Math.sin(tSec * (5 + (i % 5) * 1.3) + i * 1.7);
    const a = level * profile * jitter * (0.4 + 0.6 * wobble);
    out.push(MIN_H + (MAX_H - MIN_H) * Math.min(1, a * 1.2));
  }
  return out;
}

export class VoiceWave {
  readonly el: HTMLElement;
  private bars: HTMLElement[] = [];

  private speaking = false;
  private speakingSince = 0;
  private env: WaveEnvelope | null = null;
  private envUnknown = false;

  private vis = 0; // fade 0..1
  private level = 0; // smoothed loudness 0..1
  private raf: number | null = null;
  private last = 0;

  constructor() {
    for (let i = 0; i < WAVE_BARS; i++) this.bars.push(h("span", { class: "vw-bar" }));
    this.el = h("div", { id: "voice-wave" }, ...this.bars);
    this.el.style.display = "none";
  }

  /** Smoothed loudness (0..1) the bars show right now; used to move the Mochi's mouth in step with them. */
  get currentLevel(): number {
    return this.level;
  }

  setSpeaking(on: boolean): void {
    // Keep this idempotent: state polling can deliver the same state more than
    // once. Repeated "speaking" events must never hide or restart the wave.
    if (on === this.speaking) {
      if (on) {
        this.ensureRunning();
      } else {
        this.el.style.opacity = "0";
        this.el.style.visibility = "hidden";
        this.el.style.display = "none";
      }
      return;
    }
    this.speaking = on;
    if (on) {
      this.speakingSince = Date.now();
      this.env = null;
      this.envUnknown = false;
      this.el.style.display = "flex";
      this.el.style.visibility = "visible";
      this.el.style.opacity = "1";
      this.ensureRunning();
      return;
    }

    // Speech ended: clear all animation state immediately.  Do not wait for
    // the fade-out loop, otherwise a stale RAF can leave the bars visibly
    // frozen when a later Spotify update arrives.
    this.env = null;
    this.envUnknown = false;
    this.level = 0;
    this.vis = 0;
    this.el.style.opacity = "0";
    this.el.style.visibility = "hidden";
    this.el.style.display = "none";
    if (this.raf != null) cancelAnimationFrame(this.raf);
    this.raf = null;
  }

  /** Called with the backend's "wave" event. Empty `levels` means "unknown". */
  feed(levels: number[], hz: number, startMs: number): void {
    if (!this.speaking) return;
    if (startMs < this.speakingSince - 3000) return; // stale, from an earlier sentence
    if (!levels.length || !(hz > 0)) {
      this.env = null;
      this.envUnknown = true;
    } else {
      this.env = { levels, hz, startMs };
      this.envUnknown = false;
    }
  }

  private ensureRunning(): void {
    if (this.raf != null) return;
    this.el.style.display = "flex";
    this.el.style.visibility = "visible";
    this.el.style.opacity = "1";
    this.last = performance.now();
    if (!this.offVis) this.offVis = onVisibilityChange((v) => { if (v && this.raf == null && (this.speaking || this.vis >= 0.01)) this.ensureRunning(); });
    this.raf = requestAnimationFrame((t) => this.tick(t));
  }

  private offVis: (() => void) | null = null;

  private tick(frameT: number): void {
    if (!isReallyVisible()) { this.raf = null; return; }   // parked / hidden: stop; resumed above
    const dt = Math.min(0.05, Math.max(0, (frameT - this.last) / 1000));
    this.last = frameT;
    const now = Date.now();
    const t = now / 1000;

    // Where does loudness come from right now?
    let target = 0;
    if (this.speaking) {
      if (this.env) target = envelopeLevel(this.env, now);
      else if (this.envUnknown || now - this.speakingSince > 900) target = simulatedLevel(t);
      else target = 0.52; // envelope may arrive late; keep a clearly visible talking pulse
    }

    // Fast attack, slower release, so syllables read as pulses.
    const rate = target > this.level ? 28 : 10;
    this.level += (target - this.level) * (1 - Math.exp(-dt * rate));
    this.vis += ((this.speaking ? 1 : 0) - this.vis) * (1 - Math.exp(-dt * (this.speaking ? 12 : 7)));

    const heights = barHeights(this.level, t);
    for (let i = 0; i < this.bars.length; i++) this.bars[i].style.height = `${heights[i].toFixed(1)}px`;
    this.el.style.opacity = this.vis.toFixed(3);

    if (!this.speaking && this.vis < 0.01 && this.level < 0.01) {
      this.el.style.display = "none";
      this.raf = null;
      return;
    }
    this.raf = requestAnimationFrame((ft) => this.tick(ft));
  }
}
