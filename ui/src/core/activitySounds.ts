// Activity sounds: a tiny sound-design studio built on WebAudio. No samples, no downloads.
// Every Live Activity gets its own signature, synthesized on the spot with FM bells, detuned
// pads, filtered-noise whooshes, a shared hall reverb and a soft limiter, so they sound like a
// product and not like a beep. Used by core/sound.ts (Sound.activity) and previewed in
// Settings → Live Activities (the speaker button on each tile).

export type ActivitySoundName =
  | "message" | "ring" | "answer" | "decline" | "reminder" | "timerDone" | "timerTick"
  | "volume" | "brightness" | "connect" | "disconnect"
  | "downloadStart" | "downloadDone" | "downloadFail"
  | "listenOn" | "listenOff" | "found" | "notFound" | "copy" | "music";

export interface ActivitySoundOpts {
  /** message: discord | whatsapp | telegram | anything else. */
  app?: string;
  /** volume / brightness: 0‥1. */
  level?: number;
  /** volume: muted. */
  muted?: boolean;
}

/** Sounds that are alarms or invitations to act: audible even when UI sounds are switched off. */
export const ALWAYS_AUDIBLE: ReadonlySet<ActivitySoundName> = new Set(["ring", "reminder", "timerDone"]);

interface Bus { ctx: AudioContext; dry: GainNode; wet: GainNode }

// ── chain: voices → (dry | reverb) → limiter → destination ────────────────────────────────────
const chains = new WeakMap<AudioNode, { input: GainNode; wetIn: GainNode }>();

function impulse(ctx: AudioContext, seconds = 2.1, decay = 3.2): AudioBuffer {
  const rate = ctx.sampleRate;
  const len = Math.floor(rate * seconds);
  const buf = ctx.createBuffer(2, len, rate);
  for (let c = 0; c < 2; c++) {
    const d = buf.getChannelData(c);
    let lp = 0;
    for (let i = 0; i < len; i++) {
      const env = Math.pow(1 - i / len, decay);
      // a little low-pass on the noise: a warm hall, not a hiss
      lp += ((Math.random() * 2 - 1) - lp) * (0.35 + 0.65 * (1 - i / len));
      d[i] = lp * env;
    }
  }
  return buf;
}

function chain(ctx: AudioContext, dest: AudioNode): { input: GainNode; wetIn: GainNode } {
  const hit = chains.get(dest);
  if (hit) return hit;
  const comp = ctx.createDynamicsCompressor();
  comp.threshold.value = -14;
  comp.knee.value = 18;
  comp.ratio.value = 6;
  comp.attack.value = 0.004;
  comp.release.value = 0.22;
  comp.connect(dest);
  const input = ctx.createGain();
  input.connect(comp);
  const conv = ctx.createConvolver();
  conv.buffer = impulse(ctx);
  const wetIn = ctx.createGain();
  const wetOut = ctx.createGain();
  wetOut.gain.value = 0.9;
  wetIn.connect(conv);
  conv.connect(wetOut);
  wetOut.connect(comp);
  const c = { input, wetIn };
  chains.set(dest, c);
  return c;
}

function makeBus(ctx: AudioContext, dest: AudioNode): Bus {
  const c = chain(ctx, dest);
  return { ctx, dry: c.input, wet: c.wetIn };
}

// ── building blocks ───────────────────────────────────────────────────────────────────────────
interface ToneOpts {
  f: number; t: number; dur: number;
  type?: OscillatorType; vol?: number; attack?: number;
  glideTo?: number; glideT?: number;
  detune?: number; pan?: number; wet?: number; curve?: number;
}

/** A plucked / sustained voice: sine-ish oscillator with an exponential envelope. */
function tone(b: Bus, o: ToneOpts) {
  const { ctx } = b;
  const osc = ctx.createOscillator();
  osc.type = o.type ?? "sine";
  osc.frequency.setValueAtTime(o.f, o.t);
  if (o.glideTo) osc.frequency.exponentialRampToValueAtTime(o.glideTo, o.t + (o.glideT ?? o.dur * 0.6));
  if (o.detune) osc.detune.value = o.detune;
  const g = ctx.createGain();
  const a = o.attack ?? 0.008;
  const v = o.vol ?? 0.5;
  g.gain.setValueAtTime(0.0001, o.t);
  g.gain.exponentialRampToValueAtTime(v, o.t + a);
  g.gain.exponentialRampToValueAtTime(0.0001, o.t + o.dur);
  osc.connect(g);
  let out: AudioNode = g;
  if (o.pan && ctx.createStereoPanner) {
    const p = ctx.createStereoPanner();
    p.pan.value = o.pan;
    g.connect(p);
    out = p;
  }
  out.connect(b.dry);
  const w = o.wet ?? 0.25;
  if (w > 0) {
    const s = ctx.createGain();
    s.gain.value = w;
    out.connect(s);
    s.connect(b.wet);
  }
  osc.start(o.t);
  osc.stop(o.t + o.dur + 0.05);
}

/** FM bell: a carrier whose modulator index decays, so the strike is bright and the tail is pure glass. */
function bell(b: Bus, f: number, t: number, o: { dur?: number; vol?: number; ratio?: number; index?: number; pan?: number; wet?: number } = {}) {
  const { ctx } = b;
  const dur = o.dur ?? 1.3;
  const vol = o.vol ?? 0.45;
  const car = ctx.createOscillator();
  const mod = ctx.createOscillator();
  const modG = ctx.createGain();
  car.frequency.value = f;
  mod.frequency.value = f * (o.ratio ?? 3.51);
  const idx = f * (o.index ?? 2.2);
  modG.gain.setValueAtTime(idx, t);
  modG.gain.exponentialRampToValueAtTime(Math.max(1, idx * 0.02), t + dur * 0.7);
  mod.connect(modG);
  modG.connect(car.frequency);
  const g = ctx.createGain();
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(vol, t + 0.004);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  car.connect(g);
  let out: AudioNode = g;
  if (o.pan && ctx.createStereoPanner) {
    const p = ctx.createStereoPanner();
    p.pan.value = o.pan;
    g.connect(p);
    out = p;
  }
  out.connect(b.dry);
  const s = ctx.createGain();
  s.gain.value = o.wet ?? 0.55;
  out.connect(s);
  s.connect(b.wet);
  car.start(t); mod.start(t);
  car.stop(t + dur + 0.05); mod.stop(t + dur + 0.05);
}

let noiseBuf: AudioBuffer | null = null;
let noiseCtx: BaseAudioContext | null = null;
function noise(ctx: AudioContext): AudioBuffer {
  if (noiseBuf && noiseCtx === ctx) return noiseBuf;
  const len = ctx.sampleRate;
  const buf = ctx.createBuffer(1, len, ctx.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
  noiseBuf = buf; noiseCtx = ctx;
  return buf;
}

/** Filtered noise with a sweeping centre frequency: whooshes, air, paper, sparkle dust. */
function sweep(b: Bus, o: { t: number; dur: number; from: number; to: number; q?: number; vol?: number; type?: BiquadFilterType; pan?: number; wet?: number; attack?: number }) {
  const { ctx } = b;
  const src = ctx.createBufferSource();
  src.buffer = noise(ctx);
  src.loop = true;
  const f = ctx.createBiquadFilter();
  f.type = o.type ?? "bandpass";
  f.Q.value = o.q ?? 3;
  f.frequency.setValueAtTime(o.from, o.t);
  f.frequency.exponentialRampToValueAtTime(o.to, o.t + o.dur);
  const g = ctx.createGain();
  const at = o.attack ?? o.dur * 0.35;
  g.gain.setValueAtTime(0.0001, o.t);
  g.gain.exponentialRampToValueAtTime(o.vol ?? 0.25, o.t + at);
  g.gain.exponentialRampToValueAtTime(0.0001, o.t + o.dur);
  src.connect(f); f.connect(g);
  let out: AudioNode = g;
  if (o.pan && ctx.createStereoPanner) {
    const p = ctx.createStereoPanner();
    p.pan.value = o.pan;
    g.connect(p);
    out = p;
  }
  out.connect(b.dry);
  const s = ctx.createGain();
  s.gain.value = o.wet ?? 0.3;
  out.connect(s);
  s.connect(b.wet);
  src.start(o.t, Math.random() * 0.5);
  src.stop(o.t + o.dur + 0.05);
}

/** A soft sub thump: gives a sound weight. */
function thump(b: Bus, t: number, f = 90, vol = 0.55, dur = 0.28) {
  tone(b, { f: f * 2, glideTo: f * 0.62, glideT: dur * 0.8, t, dur, vol, attack: 0.003, wet: 0.05 });
}

/** A short click (noise through a high-pass): the "touch" of a UI sound. */
function click(b: Bus, t: number, vol = 0.18, hp = 3200) {
  sweep(b, { t, dur: 0.035, from: hp, to: hp * 1.3, q: 0.7, vol, type: "highpass", attack: 0.002, wet: 0.05 });
}

// Pentatonic ladder (C major pent): everything we sparkle with stays consonant, whatever the order.
const PENT = [523.25, 587.33, 659.25, 783.99, 880, 1046.5, 1174.66, 1318.51, 1567.98, 1760, 2093, 2349.3];

/** A shower of random tiny high bells: stardust. */
function sparkle(b: Bus, t: number, count: number, spread: number, vol = 0.12, lo = 5) {
  for (let i = 0; i < count; i++) {
    const f = PENT[Math.min(PENT.length - 1, lo + Math.floor(Math.random() * (PENT.length - lo)))] * (Math.random() < 0.3 ? 2 : 1);
    bell(b, f, t + Math.random() * spread, { dur: 0.5 + Math.random() * 0.4, vol: vol * (0.5 + Math.random() * 0.7), ratio: 4.1, index: 0.9, pan: Math.random() * 1.6 - 0.8, wet: 0.7 });
  }
}

/** A warm detuned pad chord that swells and fades: the "glow" under a celebration. */
function pad(b: Bus, t: number, freqs: number[], dur: number, vol = 0.1) {
  for (const f of freqs) {
    for (const d of [-9, 7]) tone(b, { f, t, dur, type: "triangle", vol, attack: dur * 0.3, detune: d, wet: 0.5, pan: d * 0.05 });
  }
}

// ── the sounds ────────────────────────────────────────────────────────────────────────────────
type Voice = (b: Bus, t: number, o: ActivitySoundOpts) => void;

const VOICES: Record<ActivitySoundName, Voice> = {
  // A message: a bubble that pops up, a glassy two-note answer and a whiff of stardust.
  // Every chat app keeps its own character, so you know who is talking with your eyes closed.
  message(b, t, o) {
    const app = (o.app || "").toLowerCase();
    if (app === "discord") {
      // bouncy: two quick marimba-glass notes and a "boop" bubble
      tone(b, { f: 330, glideTo: 740, glideT: 0.07, t, dur: 0.12, vol: 0.28, wet: 0.1 });
      bell(b, 784, t + 0.07, { dur: 0.9, vol: 0.4, ratio: 2.01, index: 1.3, pan: -0.25 });
      bell(b, 1174.66, t + 0.17, { dur: 1.1, vol: 0.36, ratio: 2.01, index: 1.3, pan: 0.25 });
      sparkle(b, t + 0.24, 3, 0.25, 0.09);
    } else if (app === "whatsapp") {
      // soft water drops: a glug up, a glug higher, round and friendly
      tone(b, { f: 520, glideTo: 1180, glideT: 0.06, t, dur: 0.16, vol: 0.34, wet: 0.35 });
      tone(b, { f: 700, glideTo: 1560, glideT: 0.06, t: t + 0.13, dur: 0.22, vol: 0.3, wet: 0.45, pan: 0.2 });
      bell(b, 1568, t + 0.16, { dur: 0.7, vol: 0.18, ratio: 3, index: 0.7, wet: 0.7 });
    } else if (app === "telegram") {
      // a paper plane: air whoosh rising into a three-note arpeggio
      sweep(b, { t, dur: 0.28, from: 500, to: 5200, q: 2.2, vol: 0.2, pan: -0.4 });
      [659.25, 830.61, 987.77].forEach((f, i) => bell(b, f * 1.0, t + 0.14 + i * 0.075, { dur: 1.0, vol: 0.3, pan: -0.2 + i * 0.25 }));
      sparkle(b, t + 0.3, 3, 0.2, 0.08);
    } else {
      bell(b, 880, t, { dur: 0.9, vol: 0.38, pan: -0.2 });
      bell(b, 1318.51, t + 0.11, { dur: 1.2, vol: 0.36, pan: 0.2 });
      sparkle(b, t + 0.2, 3, 0.25, 0.09);
    }
  },

  // A ring tone you would actually keep: two pulses of a shimmering major-seventh chord with a
  // tremolo "brrr" inside each. Played once per ring; Sound.ringStart() repeats it while a call is up.
  ring(b, t) {
    const chord = [523.25, 659.25, 783.99, 987.77];
    for (const pulse of [0, 0.42]) {
      const s = t + pulse;
      chord.forEach((f, i) => {
        const { ctx } = b;
        const o = ctx.createOscillator();
        o.type = "triangle";
        o.frequency.value = f;
        const g = ctx.createGain();
        const trem = ctx.createOscillator();
        const tg = ctx.createGain();
        trem.frequency.value = 22;
        tg.gain.value = 0.35;
        trem.connect(tg);
        const amp = ctx.createGain();
        amp.gain.value = 0.65;
        tg.connect(amp.gain);
        g.gain.setValueAtTime(0.0001, s);
        g.gain.exponentialRampToValueAtTime(0.22, s + 0.02);
        g.gain.setValueAtTime(0.22, s + 0.26);
        g.gain.exponentialRampToValueAtTime(0.0001, s + 0.36);
        o.connect(amp); amp.connect(g);
        const p = ctx.createStereoPanner?.();
        if (p) { p.pan.value = (i - 1.5) * 0.3; g.connect(p); p.connect(b.dry); } else g.connect(b.dry);
        const w = ctx.createGain(); w.gain.value = 0.35; g.connect(w); w.connect(b.wet);
        o.start(s); trem.start(s); o.stop(s + 0.4); trem.stop(s + 0.4);
      });
      bell(b, 2093, s, { dur: 0.7, vol: 0.14, wet: 0.7 });
    }
  },

  // Answering: a quick rising "connected" slide into a bright chord.
  answer(b, t) {
    tone(b, { f: 392, glideTo: 784, glideT: 0.12, t, dur: 0.2, vol: 0.3, wet: 0.2 });
    bell(b, 1046.5, t + 0.1, { dur: 0.8, vol: 0.4 });
    bell(b, 1568, t + 0.17, { dur: 1.0, vol: 0.3, pan: 0.25 });
    sparkle(b, t + 0.22, 4, 0.2, 0.08);
  },

  // Declining: a soft hang-up, two falling notes and a dull thud.
  decline(b, t) {
    tone(b, { f: 440, glideTo: 330, glideT: 0.14, t, dur: 0.2, vol: 0.34, type: "triangle", wet: 0.1 });
    tone(b, { f: 330, glideTo: 220, glideT: 0.2, t: t + 0.13, dur: 0.3, vol: 0.34, type: "triangle", wet: 0.1 });
    thump(b, t + 0.28, 70, 0.4, 0.22);
  },

  // A reminder should be unmissable and never rude: a rising bell arpeggio over a swelling pad,
  // landing on a long ringing top note. (Replaces the old two-note beep.)
  reminder(b, t) {
    const run = [523.25, 659.25, 783.99, 1046.5, 1318.51];
    run.forEach((f, i) => bell(b, f, t + i * 0.11, { dur: 1.6, vol: 0.36, pan: -0.5 + i * 0.25, index: 1.6 }));
    pad(b, t, [261.63, 329.63, 392], 1.9, 0.07);
    bell(b, 2093, t + 0.62, { dur: 2.2, vol: 0.22, wet: 0.85 });
    sparkle(b, t + 0.6, 5, 0.6, 0.08);
  },

  // Timer finished: a small fanfare. Rising arpeggio, a shimmer sweep, a sub thump and a warm chord that blooms.
  timerDone(b, t) {
    thump(b, t, 82, 0.6, 0.34);
    sweep(b, { t, dur: 0.55, from: 400, to: 7000, q: 1.8, vol: 0.2, attack: 0.4 });
    [523.25, 659.25, 783.99, 1046.5, 1318.51, 1567.98].forEach((f, i) =>
      bell(b, f, t + 0.06 + i * 0.075, { dur: 1.5, vol: 0.34, pan: Math.sin(i) * 0.5, index: 1.8 }));
    pad(b, t + 0.45, [523.25, 659.25, 783.99, 1046.5], 1.8, 0.08);
    sparkle(b, t + 0.55, 8, 0.8, 0.09);
  },

  // One heartbeat of a tick for the last seconds of a countdown.
  timerTick(b, t) {
    click(b, t, 0.22, 4200);
    tone(b, { f: 1500, t, dur: 0.05, vol: 0.18, wet: 0.05 });
  },

  // The volume detent: a tiny glass tick whose pitch follows the level, so you can hear where you are.
  volume(b, t, o) {
    const lvl = Math.max(0, Math.min(1, o.level ?? 0.5));
    if (o.muted || lvl <= 0.001) {
      tone(b, { f: 300, glideTo: 90, glideT: 0.12, t, dur: 0.2, vol: 0.4, wet: 0.05 });
      click(b, t, 0.1, 1800);
      return;
    }
    const f = 500 * Math.pow(2, lvl * 2.2);
    click(b, t, 0.1, 5000);
    tone(b, { f, t, dur: 0.11, vol: 0.28, wet: 0.2 });
    tone(b, { f: f * 2.01, t, dur: 0.07, vol: 0.08, wet: 0.2 });
    if (lvl >= 0.999) bell(b, f * 2, t + 0.03, { dur: 0.6, vol: 0.22 });   // top of the scale: a little "ping"
  },

  // Brightness: air and light. A breath of high noise and a pair of detuned sines gliding up or down.
  brightness(b, t, o) {
    const lvl = Math.max(0, Math.min(1, o.level ?? 0.5));
    const base = 420 * Math.pow(2, lvl * 2);
    sweep(b, { t, dur: 0.26, from: 1800 + lvl * 3000, to: 4200 + lvl * 4000, q: 1.2, vol: 0.12, attack: 0.1 });
    tone(b, { f: base, glideTo: base * 1.12, glideT: 0.2, t, dur: 0.26, vol: 0.2, attack: 0.05, detune: -8, wet: 0.45 });
    tone(b, { f: base * 1.5, glideTo: base * 1.68, glideT: 0.2, t, dur: 0.26, vol: 0.12, attack: 0.05, detune: 8, wet: 0.45 });
  },

  // A device appears: two ascending notes with a "latch" click, a bright chord shimmer on top.
  connect(b, t) {
    click(b, t, 0.16);
    bell(b, 659.25, t + 0.02, { dur: 0.7, vol: 0.34, pan: -0.2 });
    bell(b, 987.77, t + 0.13, { dur: 0.9, vol: 0.34, pan: 0.2 });
    bell(b, 1318.51, t + 0.25, { dur: 1.2, vol: 0.28 });
    sparkle(b, t + 0.3, 3, 0.2, 0.07);
  },

  // ...and disappears: the same notes, falling, the last one swallowed by a low thud.
  disconnect(b, t) {
    bell(b, 1318.51, t, { dur: 0.6, vol: 0.28, pan: 0.2 });
    bell(b, 987.77, t + 0.11, { dur: 0.7, vol: 0.28, pan: -0.2 });
    bell(b, 659.25, t + 0.22, { dur: 0.9, vol: 0.28 });
    thump(b, t + 0.3, 75, 0.35, 0.22);
    click(b, t + 0.3, 0.1, 2000);
  },

  // Download starts: a whoosh pulled inwards and a low note, like something being drawn into the notch.
  downloadStart(b, t) {
    sweep(b, { t, dur: 0.32, from: 4800, to: 380, q: 2.4, vol: 0.22, attack: 0.05 });
    tone(b, { f: 220, glideTo: 330, glideT: 0.25, t: t + 0.05, dur: 0.3, vol: 0.28, type: "triangle", wet: 0.3 });
    bell(b, 880, t + 0.26, { dur: 0.5, vol: 0.2 });
  },

  // Download done: a coin-bright "ta-da": a little rising figure, then confetti.
  downloadDone(b, t) {
    thump(b, t, 100, 0.4, 0.2);
    bell(b, 784, t + 0.02, { dur: 0.8, vol: 0.34, pan: -0.25 });
    bell(b, 1046.5, t + 0.1, { dur: 0.9, vol: 0.34 });
    bell(b, 1567.98, t + 0.2, { dur: 1.5, vol: 0.4, pan: 0.25, index: 1.6 });
    sweep(b, { t: t + 0.18, dur: 0.5, from: 2500, to: 9000, q: 1.5, vol: 0.1, attack: 0.3 });
    sparkle(b, t + 0.25, 10, 0.6, 0.1);
  },

  // Download failed: two flat, detuned notes dropping, with a low rough edge. Clear, but not alarming.
  downloadFail(b, t) {
    tone(b, { f: 233, t, dur: 0.3, type: "sawtooth", vol: 0.12, detune: -12, wet: 0.1 });
    tone(b, { f: 233, glideTo: 196, glideT: 0.25, t, dur: 0.3, type: "triangle", vol: 0.3, wet: 0.1 });
    tone(b, { f: 175, glideTo: 117, glideT: 0.3, t: t + 0.18, dur: 0.42, type: "triangle", vol: 0.32, wet: 0.2 });
    tone(b, { f: 117, t: t + 0.18, dur: 0.42, type: "sawtooth", vol: 0.08, detune: 14, wet: 0.1 });
  },

  // The microphone wakes up: an upward "bloop" with a halo, like something turning to look at you.
  listenOn(b, t) {
    tone(b, { f: 330, glideTo: 990, glideT: 0.14, t, dur: 0.22, vol: 0.32, wet: 0.35 });
    bell(b, 1318.51, t + 0.1, { dur: 0.7, vol: 0.3 });
    sweep(b, { t, dur: 0.22, from: 1200, to: 6000, q: 2, vol: 0.08, attack: 0.1 });
  },

  // ...and goes back to sleep.
  listenOff(b, t) {
    tone(b, { f: 880, glideTo: 330, glideT: 0.16, t, dur: 0.24, vol: 0.28, wet: 0.35 });
    bell(b, 659.25, t + 0.06, { dur: 0.5, vol: 0.2 });
  },

  // A song was recognised: a harp glissando up the scale into a held bell and a shower of light.
  found(b, t) {
    [0, 2, 3, 4, 5, 6, 7, 8].forEach((n, i) =>
      bell(b, PENT[n], t + i * 0.055, { dur: 1.1, vol: 0.26, ratio: 2, index: 0.8, pan: -0.6 + i * 0.17, wet: 0.7 }));
    bell(b, 2093, t + 0.48, { dur: 2.0, vol: 0.28, wet: 0.9, index: 1.4 });
    pad(b, t + 0.35, [392, 523.25, 659.25], 1.6, 0.06);
    sparkle(b, t + 0.5, 8, 0.7, 0.09);
  },

  // Nothing found: a soft questioning two-note shrug.
  notFound(b, t) {
    tone(b, { f: 392, t, dur: 0.22, vol: 0.3, type: "triangle", wet: 0.3 });
    tone(b, { f: 330, glideTo: 311, glideT: 0.3, t: t + 0.15, dur: 0.36, vol: 0.3, type: "triangle", wet: 0.35 });
  },

  // A copy: a crisp paper snap with a small bright "pip".
  copy(b, t) {
    click(b, t, 0.3, 2600);
    sweep(b, { t, dur: 0.07, from: 6000, to: 1500, q: 1, vol: 0.12, type: "highpass", attack: 0.004, wet: 0.05 });
    tone(b, { f: 1760, glideTo: 1320, glideT: 0.06, t: t + 0.015, dur: 0.12, vol: 0.2, wet: 0.2 });
  },

  // Music wakes the notch: a warm swell with a glass top, like a record needle dropping onto something good.
  music(b, t) {
    pad(b, t, [220, 277.18, 329.63], 1.3, 0.08);
    bell(b, 659.25, t + 0.15, { dur: 1.1, vol: 0.26, pan: -0.3 });
    bell(b, 987.77, t + 0.3, { dur: 1.3, vol: 0.24, pan: 0.3 });
    sweep(b, { t, dur: 0.5, from: 300, to: 3200, q: 1.4, vol: 0.08, attack: 0.35 });
  },
};

/** Total length in seconds of the longest tail, for callers that want to idle the AudioContext afterwards. */
export const TAIL_S: Record<ActivitySoundName, number> = {
  message: 1.6, ring: 1.2, answer: 1.4, decline: 0.7, reminder: 3.4, timerDone: 3.0, timerTick: 0.2,
  volume: 0.8, brightness: 0.5, connect: 1.6, disconnect: 1.3, downloadStart: 0.9, downloadDone: 2.4,
  downloadFail: 0.9, listenOn: 1.0, listenOff: 0.8, found: 3.2, notFound: 0.8, copy: 0.3, music: 2.2,
};

/** Plays `name` into `dest` (the engine's master gain or its alarm bus). */
export function playActivitySound(ctx: AudioContext, dest: AudioNode, name: ActivitySoundName, opts: ActivitySoundOpts = {}, delay = 0.02): number {
  const voice = VOICES[name];
  if (!voice) return 0;
  const bus = makeBus(ctx, dest);
  voice(bus, ctx.currentTime + delay, opts);
  return TAIL_S[name] ?? 1;
}
