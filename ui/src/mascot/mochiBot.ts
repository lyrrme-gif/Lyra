// Lyra v1.0 Mochi: Canvas 2D engine, copied unchanged from v1.0 (ui/src/mochi/engine.ts)
// except that sounds are muted (see the Sound stub below). Used by character.ts.

import { Ease, lerp, type EaseFn } from "../core/anim";
import type { BotEmoteName, BotStateName } from "../core/layout";

// The mascot window is silent: the engine plays island sounds (slap / greet / annoyed), so they are stubbed out.
const Sound = { play(_name: string): void {} };

// ── Types ─────────────────────────────────────────────────────────────────────

export type EyeShape =
  | "pill" | "wide" | "dot" | "line" | "flat" | "happy" | "closed"
  | "spiral" | "cross" | "heart" | "star" | "tired" | "wink" | "cup" | "squint" | "content";

export type BadgeKind = "dots" | "bang" | "question" | "dot";

export interface Badge {
  kind: BadgeKind;
  color: RGB;
}

export type RGB = readonly [number, number, number]; // components 0…1

export type TweenKey = readonly [target: number, durationMs: number, ease: EaseFn];

interface Tween {
  prop: PropKey;
  keys: TweenKey[];
  index: number;
  from: number;
  startMs: number;
  onComplete?: () => void;
}

type PropKey =
  | "yaw" | "pitch" | "roll" | "tilt" | "open" | "sx" | "sy"
  | "oy" | "ox" | "tint" | "morph" | "hands" | "blush" | "es" | "badgeS";

interface BotStateCfg {
  color: RGB;
  tint: number;
  eye: EyeShape;
  badge: Badge | null;
  bounces: boolean;
  scans: boolean;
  breathes: boolean;
  zz: boolean;
  sweat: boolean;
  look: readonly [number, number] | null;
  tilt: number;
}

interface Particle {
  type: "heart" | "star" | "spark" | "sweat" | "z" | "note" | "rain" | "bolt" | "rainbow" | "thought" | "wave" | "orbit" | "glint" | "dust";
  x: number; y: number; vx: number; vy: number;
  age: number; life: number; rot: number; size: number;
  tone?: number;   // glint colour: 0 gold, 1 peach, 2 sky, 3 violet, 4 pink, 5 mint
}

// ── Constants (MochiConst / PISTES.mochi) ─────────────────────────────────────

const EYE_W = 0.50; // v1.0 was 0.25: larger, more expressive eyes
const EYE_H = 0.54; // v1.0 was 0.27
const EYE_SP = 0.40; // v1.0 was 0.37: a little wider apart for the bigger eyes
const EYE_P = -0.12;
const BASE_TOP: RGB = [0.929, 0.929, 0.937]; // #EDEDEF
const BASE_BOTTOM: RGB = [0.769, 0.773, 0.792]; // #C4C5CA
const INK = "rgb(26,20,18)"; // #1A1412
const MINI_INK = "rgb(16,19,26)"; // #10131A

const C = {
  idle: [0.902, 0.914, 0.933] as RGB,
  working: [0.231, 0.62, 1] as RGB,
  thinking: [0.545, 0.361, 0.965] as RGB,
  searching: [0.388, 0.396, 0.949] as RGB,
  approval: [0.961, 0.647, 0.141] as RGB,
  question: [0.133, 0.827, 0.933] as RGB,
  error: [0.957, 0.314, 0.369] as RGB,
  finished: [0.204, 0.831, 0.6] as RGB,
  ratelimit: [0.984, 0.573, 0.235] as RGB,
  sleeping: [0.58, 0.635, 0.722] as RGB,
  dizzy: [0.957, 0.447, 0.714] as RGB,
  focus: [0.29, 0.52, 0.92] as RGB,
  hot: [1, 0.36, 0.2] as RGB,
  thirsty: [0.22, 0.74, 0.95] as RGB,
  ringing: [0.2, 0.83, 0.5] as RGB,
  lowbat: [0.62, 0.64, 0.7] as RGB,
};

const base = {
  bounces: false, scans: false, breathes: false, zz: false, sweat: false,
  look: null, tilt: 0,
};

export const BOT_STATES: Record<BotStateName, BotStateCfg> = {
  idle: { ...base, color: C.idle, tint: 0, eye: "pill", badge: null },
  working: { ...base, color: C.working, tint: 0.72, eye: "pill", badge: { kind: "dots", color: C.working } },
  thinking: { ...base, color: C.thinking, tint: 0.72, eye: "pill", badge: { kind: "dots", color: C.thinking }, look: [0.55, 0.55] },
  searching: { ...base, color: C.searching, tint: 0.72, eye: "pill", badge: { kind: "dots", color: C.searching }, scans: true },
  approval: { ...base, color: C.approval, tint: 0.78, eye: "wide", badge: { kind: "bang", color: C.approval }, bounces: true },
  question: { ...base, color: C.question, tint: 0.75, eye: "pill", badge: { kind: "question", color: C.question }, tilt: 0.17 },
  error: { ...base, color: C.error, tint: 0.78, eye: "flat", badge: { kind: "dot", color: C.error } },
  finished: { ...base, color: C.finished, tint: 0.35, eye: "happy", badge: { kind: "dot", color: C.finished } },
  ratelimit: { ...base, color: C.ratelimit, tint: 0.72, eye: "tired", badge: { kind: "dot", color: C.ratelimit }, sweat: true },
  sleeping: { ...base, color: C.sleeping, tint: 0.32, eye: "closed", badge: null, breathes: true, zz: true },
  dizzy: { ...base, color: C.dizzy, tint: 0.7, eye: "spiral", badge: null },
  focus: { ...base, color: C.focus, tint: 0.3, eye: "line", badge: null, breathes: true },
  hot: { ...base, color: C.hot, tint: 0.82, eye: "wide", badge: null, sweat: true, bounces: true },
  thirsty: { ...base, color: C.thirsty, tint: 0.55, eye: "tired", badge: null, sweat: true, tilt: -0.08 },
  ringing: { ...base, color: C.ringing, tint: 0.8, eye: "wide", badge: { kind: "bang", color: C.ringing }, bounces: true },
  lowbat: { ...base, color: C.lowbat, tint: 0.5, eye: "tired", badge: { kind: "dot", color: C.error }, breathes: true },
};

/** State → sound, as in BotStateCfg.sound. */
export const STATE_SOUND: Partial<Record<BotStateName, string>> = {
  working: "work", thinking: "think", searching: "search", approval: "approval",
  question: "question", error: "error", finished: "finish", ratelimit: "rate",
  sleeping: "sleep", dizzy: "dizzy",
};

const EMOTE_EYE: Record<BotEmoteName, EyeShape> = {
  love: "content", surprised: "dot", proud: "happy", wink: "wink",
  yawn: "tired", happy: "happy", annoyed: "line",
};

// ── Small helpers ─────────────────────────────────────────────────────────────

const now = () => performance.now() / 1000;

export function hexToRGB(hex: string): RGB {
  const h = hex.replace("#", "");
  const v = parseInt(h, 16);
  return [((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255];
}

const rgba = (c: RGB, a = 1) =>
  `rgba(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)},${a})`;

const mix3 = (a: RGB, b: RGB, t: number): RGB => [
  lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t),
];

function roundRectPath(x: CanvasRenderingContext2D, X: number, Y: number, W: number, H: number, R: number) {
  const r = Math.max(0, Math.min(R, W / 2, H / 2));
  x.beginPath();
  x.moveTo(X + r, Y);
  x.arcTo(X + W, Y, X + W, Y + H, r);
  x.arcTo(X + W, Y + H, X, Y + H, r);
  x.arcTo(X, Y + H, X, Y, r);
  x.arcTo(X, Y, X + W, Y, r);
  x.closePath();
}

function heartPath(x: CanvasRenderingContext2D, s: number) {
  x.beginPath();
  x.moveTo(0, s * 0.38);
  x.bezierCurveTo(-s * 1.05, -s * 0.15, -s * 0.5, -s * 0.95, 0, -s * 0.38);
  x.bezierCurveTo(s * 0.5, -s * 0.95, s * 1.05, -s * 0.15, 0, s * 0.38);
  x.closePath();
}

function starPath(x: CanvasRenderingContext2D, ro: number, ri: number) {
  x.beginPath();
  for (let i = 0; i < 10; i++) {
    const r = i % 2 ? ri : ro;
    const a = -Math.PI / 2 + (i * Math.PI) / 5;
    x.lineTo(Math.cos(a) * r, Math.sin(a) * r);
  }
  x.closePath();
}

/** Shapes whose anger throws sparks. */
const EMBER_SHAPES = new Set(["knight", "fox", "hornet", "lantern", "grimm"]);
import { CHAR_DEFS, angerBase, angerFront, type CharDef, type Look, type Persona, type Moves, type Fx, type EaseName } from "./characters";

// ── Skins (hats, glasses, masks) ───────────────────────────────────────────────

// ── Vibing (groove + notes while music plays) ───────────────────────────────────────────────
const BEAT_HZ = 1.9;                                   // hops per second (~114 bpm)
const NOTE_COLORS = ["#8f7bff", "#ff7ac8", "#5fd0ff", "#ffcf5a"];

/** A music note (♪, or ♫ when `double`) drawn from paths so it never depends on a font. */
function notePath(x: CanvasRenderingContext2D, s: number, double: boolean) {
  x.lineWidth = Math.max(1.2, s * 0.26);
  x.lineCap = "round";
  const head = (cx: number, cy: number) => {
    x.beginPath();
    x.ellipse(cx, cy, s * 0.5, s * 0.36, -0.45, 0, Math.PI * 2);
    x.fill();
  };
  const line = (x0: number, y0: number, x1: number, y1: number) => {
    x.beginPath(); x.moveTo(x0, y0); x.lineTo(x1, y1); x.stroke();
  };
  if (!double) {
    head(0, s * 0.7);
    line(s * 0.42, s * 0.6, s * 0.42, -s * 0.95);
    x.beginPath();
    x.moveTo(s * 0.42, -s * 0.95);
    x.quadraticCurveTo(s * 1.1, -s * 0.6, s * 0.85, -s * 0.05);
    x.stroke();
  } else {
    head(-s * 0.55, s * 0.7);
    head(s * 0.55, s * 0.45);
    line(-s * 0.1, s * 0.6, -s * 0.1, -s * 0.9);
    line(s * 1.0, s * 0.35, s * 1.0, -s * 1.15);
    x.lineWidth = s * 0.4;
    line(-s * 0.1, -s * 0.9, s * 1.0, -s * 1.15);
  }
}

const FONT = `system-ui, "Segoe UI Variable Text", "Segoe UI", sans-serif`;

// ── Engine ────────────────────────────────────────────────────────────────────

export class BotEngine {
  isMini = false;
  /** Solid body colour for mini bots / integration pills (null = Mochi gradient). */
  bodyColor: RGB | null = null;

  // Animated state (BotEngine `s`)
  yaw = 0; pitch = 0; roll = 0; tilt = 0; open = 1;
  sx = 1; sy = 1; oy = 0; ox = 0;
  tint = 0; morph = 0; hands = 0; blush = 0; es = 1; badgeS = 0;

  // Targets
  tgYaw = 0; tgPitch = 0; tgTilt = 0; tgSy = 1; tgSx = 1; tgEs = 1;

  /** Extra canvas height above the body so hearts can fly out without clipping. */
  particleOverhang = 0;

  // Mouth spring (fraction of R)
  slotH = 0; slotHTarget = 0; slotHVel = 0; isChewing = false;

  col: RGB = C.idle;
  colT: RGB = C.idle;

  state: BotStateName = "idle";
  cfg: BotStateCfg = BOT_STATES.idle;

  eyeOverride: EyeShape | null = null;
  eyeOverrideUntil = 0;
  permanentEye: EyeShape | null = null;
  permanentEmote: BotEmoteName | null = null;
  miniNextBehavior = 0;

  badge: Badge | null = null;
  private badgeKey = "none";
  private badgeToken = 0;

  private tweens = new Map<PropKey, Tween>();
  private locks = new Set<PropKey>();
  private particles: Particle[] = [];

  lookX = 0;
  lookY = 0;

  /** Which character this is: a key of CHAR_DEFS (cloud, fox, cat, bunny or panda). */
  shape = "cloud";
  setShape(name: string) {
    this.shape = CHAR_DEFS[name] ? name : "cloud";
    this.rage = 0; this.rageT = 0;
  }
  /** The cloud specifically: it makes weather (rain, lightning, rainbows). */
  private get cloud(): boolean { return this.shape === "cloud" && !this.isMini; }
  /** Any of the new characters (everything except the original blob and the mini bots). */
  private get own(): boolean { return this.shape !== "mochi" && !this.isMini; }
  private get def(): CharDef | null { return this.own ? CHAR_DEFS[this.shape] ?? CHAR_DEFS.cloud : null; }

  /** Holding a game controller (mashing buttons): the "gaming" mood. */
  controller = false;
  setController(on: boolean) { this.controller = on; }
  /** Drips: rain falling from a cloud, sweat on the blob. */
  private drip(n: number) { if (this.cloud) this.emit("rain", n + 1); else this.emit("sweat", n); }

  /** Extra body language that is not a full state: "shy" (blushing, head tilted) or "grumpy" (puffed up). */
  pose: "" | "shy" | "grumpy" = "";
  /** Dancing (groove + floating notes). */
  dance = false;
  setDance(on: boolean) { this.dance = on; if (on) this.lastNote = 0; }

  /** Drop any short-lived eye expression (happy from a wave, a blink …) so the new look's own eyes show. */
  resetEyes() {
    this.interruptGreet();                       // also lowers a waving hand
    this.greetToken++;
    this.eyeOverride = this.permanentEye;
    this.eyeOverrideUntil = this.permanentEye ? Number.POSITIVE_INFINITY : 0;
  }

  // ── Vibing: bobbing to the music (see setVibing) ──
  /** True while music plays: notes float up and the body grooves. */
  vibing = false;
  /** Set by the host while something else owns the body (talking, waving): the groove pauses. */
  grooveHold = false;
  /** "Reduce motion": no bobbing and no notes. */
  calm = false;
  private groove = 0;      // how much the body is bobbing, 0..1
  private beat = 0;        // beat phase (radians, wraps every 4π)
  private lastNote = 0;
  private lastFx = 0;

  lastTime = now();
  private t0 = now() - Math.random() * 5;
  private nextBlink = now() + 1.5 + Math.random() * 2;
  waveUntil = 0;
  waveStart = 0;
  private greetToken = 0;
  // The character Hello (the "wave" emote): its own timeline, so the old peek-wave above stays as it was for the mini bots.
  private helloToken = 0;
  private helloStart = 0;
  private helloEnd = 0;
  private helloCut = 0;
  private lastAmbient = 0;
  private slapTimes: number[] = [];
  private miniLookTarget = { x: 0, y: 0 };
  private miniLookNextTime = 0;

  /** Fired when three slaps land inside 1.7 s (→ dizzy + confused view). */
  onDizzy: (() => void) | null = null;

  // ── Public API ──────────────────────────────────────────────────────────────

  setState(next: BotStateName, force = false) {
    if (this.state === next && !force) return;
    const prev = this.state;
    this.state = next;
    this.cfg = BOT_STATES[next];
    this.colT = this.cfg.color;
    if (!this.locks.has("tint")) this.tint = this.cfg.tint;
    if (!this.locks.has("tilt")) this.tgTilt = this.cfg.tilt;
    // The cloud has its own look for thinking / listening / dizzy (puffs, sound waves, orbiting stars), so it skips the badges.
    this.setBadge(this.own && (next === "thinking" || next === "question") ? null : this.cfg.badge);
    if (this.own && next === "question") this.tgTilt = 0;
    if (next === "dizzy" && prev !== "dizzy") this.dizzyClock = 0;
    if (this.own && next === "dizzy" && !this.def?.dizzyFront) for (let i = 0; i < 3; i++) this.emit("orbit", 1);   // the characters with their own dizzy have their own things whirling

    switch (next) {
      case "finished":
        this.celebrate();
        break;
      case "error":
        if (this.cloud) this.emit("bolt", 1);
        if (this.def?.persona?.error) this.play(this.def.persona.error);
        this.anim("ox", [
          [0.08, 50, Ease.out], [-0.08, 70, Ease.inOut],
          [0.05, 70, Ease.inOut], [0, 90, Ease.out],
        ]);
        break;
      case "approval":
        this.anim("oy", [[-0.2, 150, Ease.out], [0, 300, Ease.back]]);
        break;
      case "dizzy":
        this.doRoll(1300, 2);
        break;
      case "question":
        this.blink();
        break;
      case "ratelimit":
        this.drip(1);
        break;
      default:
        if (prev !== "idle" || next !== "idle") this.blink();
    }
  }

  setBadge(b: Badge | null) {
    const key = b ? `${b.kind}-${b.color.join(",")}` : "none";
    if (key === this.badgeKey) return;
    this.badgeKey = key;
    const tok = ++this.badgeToken;
    this.anim("badgeS", [[0, 90, Ease.inOut]]);
    setTimeout(() => {
      if (tok !== this.badgeToken) return;
      this.badge = b;
      if (b) this.anim("badgeS", [[1, 280, Ease.back]]);
    }, 100);
  }

  blink() {
    if (this.locks.has("open")) return;
    const [shut, open] = this.def?.persona?.blinkMs ?? [70, 130];
    this.anim("open", [[0.06, shut, Ease.inOut], [1, open, Ease.out]]);
  }

  // ── Personality helpers (used by characters that have a `persona` in characters.ts) ──
  private static readonly EASES: Record<EaseName, EaseFn> = { out: Ease.out, inOut: Ease.inOut, back: Ease.back, lin: Ease.lin };

  /** Plays a set of body tweens. */
  private play(m?: Moves) {
    if (!m) return;
    for (const k of ["sy", "sx", "oy", "tilt"] as const) {
      const keys = m[k];
      if (keys) this.anim(k, keys.map(([v, ms, e]) => [v, ms, BotEngine.EASES[e]] as [number, number, EaseFn]));
    }
  }

  /** Schedules particle bursts; `alive` cancels them if something else took over. */
  private fx(list: Fx[] | undefined, alive?: () => boolean) {
    for (const [ms, type, n] of list ?? []) setTimeout(() => { if (!alive || alive()) this.emit(type, n); }, ms);
  }

  /** The glint colour for this character's own sparkles. */
  private glintTone(): number | undefined {
    const g = this.def?.persona?.glint;
    return g === undefined || g < 0 ? undefined : g;
  }

  /** A little idle habit (a side glance, a nose twitch, a yawn...). */
  private doQuirk(kind: NonNullable<Persona["quirk"]>) {
    if (this.calm) return;
    switch (kind) {
      case "sideglance": {
        const s = Math.random() < 0.5 ? -1 : 1;
        this.anim("yaw", [[s * 0.65, 200, Ease.out], [s * 0.65, 650, Ease.lin], [0, 300, Ease.inOut]]);
        this.anim("pitch", [[-0.04, 200, Ease.out], [-0.04, 650, Ease.lin], [0, 300, Ease.inOut]]);
        break;
      }
      case "twitch":
        this.anim("sy", [[1.04, 60, Ease.out], [0.98, 70, Ease.inOut], [1.02, 60, Ease.inOut], [1, 90, Ease.out]]);
        setTimeout(() => this.blink(), 140);
        break;
      case "yawn":
        this.triggerEmote("yawn", 1.7);
        break;
      case "shiver":
        this.anim("tilt", [[0.05, 55, Ease.out], [-0.05, 75, Ease.inOut], [0.04, 75, Ease.inOut], [-0.04, 75, Ease.inOut], [0.02, 75, Ease.inOut], [0, 90, Ease.out]]);
        this.anim("sx", [[1.03, 55, Ease.out], [0.98, 150, Ease.inOut], [1, 150, Ease.out]]);
        break;
      case "headtilt":
        this.anim("tilt", [[0.14, 260, Ease.out], [0.14, 900, Ease.lin], [0, 320, Ease.inOut]]);
        break;
    }
  }

  squash() {
    this.anim("sy", [[0.78, 70, Ease.out], [1.1, 130, Ease.out], [1, 170, Ease.inOut]]);
    this.anim("sx", [[1.16, 70, Ease.out], [0.95, 130, Ease.out], [1, 170, Ease.inOut]]);
  }

  /** Mailbox swallow — opens the slot, chews, then closes. */
  gulp() {
    this.slotHTarget = 0.42;
    setTimeout(() => {
      this.slotHTarget = 0;
      this.isChewing = true;
      setTimeout(() => { this.isChewing = false; }, 800);
    }, 460);
    this.anim("sy", [[0.78, 80, Ease.out], [1.18, 130, Ease.out], [1, 220, Ease.back]]);
    this.anim("sx", [[1.28, 80, Ease.out], [0.92, 130, Ease.out], [1, 220, Ease.back]]);
    this.blink();
  }

  slap() {
    this.interruptGreet();
    if (this.state === "dizzy") return;
    const t = now();
    this.slapTimes = this.slapTimes.filter((s) => t - s < 1.7);
    this.slapTimes.push(t);
    Sound.play("slap");
    this.squash();
    if (this.slapTimes.length >= 3) {
      this.slapTimes = [];
      this.onDizzy?.();
    } else {
      this.eyeOverride = "line";
      this.eyeOverrideUntil = t + 0.8;
      setTimeout(() => Sound.play("annoyed"), 60);
    }
  }

  // ── Rage (the Knight) ─────────────────────────────────────────────────────────────
  // Every poke (provoke) raises the target; the shown value eases toward it, then cools down when left alone.
  rageT = 0; rage = 0;
  // Dizzy (shaken too fast): eased 0..1 amount, and seconds since it started. Each character reels its own way (CharDef.dizzy).
  private dizzyAmt = 0; private dizzyClock = 0;
  private rageAt = -99;
  private slashAt = -99;
  private slashLen = 0.4;
  private slashDir = 1;
  /** How many pokes in a row (1 = first). Sets how angry it is, how fast it swings and whether it swings twice. */
  provoke(level: number) {
    if (!this.own) return;
    const n = now();
    const lv = Math.max(1, Math.min(6, Math.round(level)));
    this.rageT = Math.min(1, lv / 6);
    this.rageAt = n;
    this.slashLen = Math.max(0.24, 0.46 - lv * 0.04);
    this.slashDir = lv % 2 ? 1 : -1;
    this.slashAt = n;
    this.squash();
    const fx = this.shape === "cloud" ? "bolt" : EMBER_SHAPES.has(this.shape) ? "spark" : "dust";
    this.emit(fx, lv >= 5 ? 3 : 1);
    if (this.shape === "cloud") this.emit("rain", lv * 2);
    if (lv >= 4) setTimeout(() => { this.slashDir = -this.slashDir; this.slashAt = now(); this.emit(fx, 2); }, this.slashLen * 1000 * 0.62);   // a second swing right behind the first
    this.interruptGreet();
  }

  doRoll(durationMs: number, turns: number) {
    this.roll = 0;
    this.anim("roll", [[Math.PI * 2 * turns, durationMs, Ease.inOut]], () => { this.roll = 0; });
  }

  /** Peek wave — the "coucou". Timings from BotEngine.greet(). */
  greet() {
    const t = now();
    const tok = ++this.greetToken;
    this.waveStart = t + 0.45;
    this.waveUntil = t + 1.55;

    this.eyeOverride = "happy";
    this.eyeOverrideUntil = t + 2.0;
    this.anim("oy", [[-0.06, 220, Ease.out], [0.0, 220, Ease.back]]);

    setTimeout(() => {
      if (this.greetToken !== tok) return;
      this.anim("hands", [[1, 280, Ease.out]]);
      this.anim("sy", [[0.95, 100, Ease.out], [1.0, 260, Ease.back]]);
      this.anim("sx", [[1.04, 100, Ease.out], [1.0, 260, Ease.back]]);
      Sound.play("greet");
    }, 250);

    setTimeout(() => { if (this.greetToken === tok) this.blink(); }, 550);
    setTimeout(() => { if (this.greetToken === tok) this.blink(); }, 1500);
    setTimeout(() => {
      if (this.greetToken !== tok) return;
      this.waveUntil = 0;
      this.anim("hands", [[0, 200, Ease.inOut]]);
    }, 1550);
    setTimeout(() => {
      if (this.greetToken !== tok) return;
      this.eyeOverride = "happy";
      this.eyeOverrideUntil = now() + 0.3;
    }, 1750);
  }

  interruptGreet() {
    this.cutHello();
    if (this.hands <= 0.01 && now() >= this.waveUntil) return;
    this.greetToken++;
    this.waveUntil = 0;
    this.waveStart = 0;
    this.anim("hands", [[0, 150, Ease.inOut]]);
  }

  // ── Hello: every character greets in its own way ─────────────────────────────
  // Cloud: a sunny peek, a puff of an arm waves and a warm glow comes up behind it.
  // Fox: a quick hop, ears perk, the tail wags hard and a socked paw waves fast.
  // The pose itself (arm, ears, tail, glow) is drawn in characters.ts from Look.hello / Look.helloT.
  private static readonly HELLO_LEN: Record<string, number> = { cloud: 1.5, fox: 1.35 };

  /** The Hello for the "wave" emote. The mini bots and the old blob keep the original peek-wave. */
  hello() {
    if (!this.own) { this.greet(); return; }
    const t = now();
    const tok = ++this.helloToken;
    this.interruptGreet();                 // a leftover hand wave (or a hello that is still playing) goes down first
    this.helloToken = tok;
    const len = this.def?.persona?.helloLen ?? BotEngine.HELLO_LEN[this.shape] ?? 1.5;
    this.helloStart = t;
    this.helloEnd = t + len;
    this.helloCut = 0;
    const alive = () => this.helloToken === tok;
    const happyFor = (sec: number) => { this.eyeOverride = "happy"; this.eyeOverrideUntil = now() + sec; };
    setTimeout(() => { if (alive()) Sound.play("greet"); }, 250);
    const settle = () => { if (alive()) happyFor(0.25); };

    const P = this.def?.persona;
    if (P) {
      // data-driven hello: the character says how it moves, the engine just plays it
      if (P.helloSlowBlink) {
        if (this.calm) { happyFor(len + 0.25); return; }
        this.play(P.hello);
        setTimeout(() => { if (alive()) { this.eyeOverride = "closed"; this.eyeOverrideUntil = now() + 0.42; } }, 380);
        setTimeout(() => { if (alive()) happyFor(len - 0.8 + 0.25); }, 820);
      } else {
        happyFor(len + 0.25);
        if (this.calm) return;
        this.play(P.hello);
      }
      this.fx(P.helloFx, alive);
    } else if (this.shape === "fox") {
      happyFor(len + 0.25);
      if (this.calm) return;
      this.anim("sy", [[0.84, 120, Ease.out], [1.12, 140, Ease.out], [0.9, 110, Ease.inOut], [1, 200, Ease.back]]);
      this.anim("sx", [[1.12, 120, Ease.out], [0.92, 140, Ease.out], [1.07, 110, Ease.inOut], [1, 200, Ease.back]]);
      this.anim("oy", [[0.02, 120, Ease.out], [-0.3, 160, Ease.out], [0.02, 150, Ease.inOut], [0, 170, Ease.back]]);
      setTimeout(() => { if (alive()) this.emit("dust", 3); }, 440);
      setTimeout(() => { if (alive()) this.emit("glint", 2); }, 520);
      setTimeout(() => { if (alive()) this.blink(); }, 1050);
    } else {
      happyFor(len + 0.25);
      if (this.calm) return;
      this.anim("oy", [[0.025, 130, Ease.inOut], [-0.13, 380, Ease.out], [-0.1, 500, Ease.inOut], [0, 480, Ease.inOut]]);
      this.anim("sy", [[0.96, 130, Ease.inOut], [1.07, 380, Ease.out], [1.03, 500, Ease.inOut], [1, 480, Ease.inOut]]);
      this.anim("sx", [[1.03, 130, Ease.inOut], [0.97, 380, Ease.out], [0.99, 500, Ease.inOut], [1, 480, Ease.inOut]]);
      setTimeout(() => { if (alive()) this.emit("glint", 2); }, 420);
      setTimeout(() => { if (alive()) this.emit("glint", 2); }, 1000);
    }
    setTimeout(settle, len * 1000);
  }

  /** Something else took over (another emote, a slap): the Hello lowers quickly instead of vanishing. */
  private cutHello() {
    if (this.helloEnd <= 0 || this.helloCut > 0) return;
    if (now() >= this.helloEnd) return;
    this.helloToken++;                     // cancels its pending timers
    this.helloCut = now();
  }

  /** 0..1 (a touch over 1 on the way up): how far into the Hello pose the arm / ears / tail are. */
  private helloEnv(): number {
    if (this.helloEnd <= 0) return 0;
    const n = now();
    if (n >= this.helloEnd) return 0;
    const up = Math.min(1, Math.max(0, (n - this.helloStart) / 0.26));
    const down = Math.min(1, Math.max(0, (this.helloEnd - n) / 0.32));
    let e = Ease.back(up) * Ease.inOut(down);
    if (this.helloCut > 0) e *= 1 - Math.min(1, Math.max(0, (n - this.helloCut) / 0.16));
    return e < 0.001 ? 0 : e;
  }

  /** Seconds since the Hello began (it drives the swing, so every wave starts the same way); 0 with reduced motion. */
  private helloClock(): number {
    return this.calm || this.helloEnd <= 0 ? 0 : now() - this.helloStart;
  }

  /** Music started / stopped: the Mochi starts / stops grooving. */
  setVibing(on: boolean) {
    if (on === this.vibing) return;
    this.vibing = on;
    if (on) {
      this.lastNote = now();
      if (!this.calm) this.emit("note", 2);
    }
  }

  setPermanentEmote(emote: BotEmoteName | null) {
    this.permanentEmote = emote;
    if (emote === "wink") {
      this.miniNextBehavior = now() + 0.8 + Math.random() * 1.7;
      return;
    }
    this.permanentEye = emote ? this.emoteEye(emote) : null;
    if (this.permanentEye) {
      this.eyeOverride = this.permanentEye;
      this.eyeOverrideUntil = Number.POSITIVE_INFINITY;
    } else if (this.eyeOverrideUntil === Number.POSITIVE_INFINITY) {
      this.eyeOverride = null;
      this.eyeOverrideUntil = 0;
    }
    this.miniNextBehavior = now() + 0.8 + Math.random() * 1.7;
  }

  /** The eye shape for an emote: the character's own take on it, or the default. */
  private emoteEye(e: BotEmoteName): EyeShape {
    return (this.def?.emoteEyes?.[e] as EyeShape | undefined) ?? EMOTE_EYE[e];
  }

  triggerEmote(emote: BotEmoteName, duration = 1.8) {
    const t = now();
    this.eyeOverride = this.emoteEye(emote);
    this.eyeOverrideUntil = t + duration;

    switch (emote) {
      case "love":
        this.anim("blush", [
          [1, 300, Ease.out], [1, (duration - 0.6) * 1000, Ease.lin], [0, 300, Ease.inOut],
        ]);
        if (this.own) this.affection(duration, 1);
        else { this.emit("heart", 4); this.anim("oy", [[-0.1, 160, Ease.out], [0, 300, Ease.back]]); }
        break;
      case "surprised":
        this.anim("oy", [[-0.3, 140, Ease.out], [0, 380, Ease.back]]);
        this.anim("es", [[1.25, 120, Ease.out], [1, 500, Ease.inOut]]);
        break;
      case "proud":
        if (this.own) this.celebrate(); else this.emit("star", 5);
        this.anim("tilt", [
          [-0.14, 220, Ease.out], [-0.14, (duration - 0.5) * 1000, Ease.lin], [0, 280, Ease.inOut],
        ]);
        this.anim("blush", [
          [0.7, 250, Ease.out], [0.7, (duration - 0.5) * 1000, Ease.lin], [0, 300, Ease.inOut],
        ]);
        break;
      case "wink":
        this.anim("tilt", [
          [0.12, 160, Ease.out], [0.12, (duration - 0.4) * 1000, Ease.lin], [0, 240, Ease.inOut],
        ]);
        break;
      case "yawn":
        this.anim("sy", [[1.12, 500, Ease.inOut], [1, 500, Ease.inOut]]);
        this.anim("sx", [[0.94, 500, Ease.inOut], [1, 500, Ease.inOut]]);
        setTimeout(() => { this.eyeOverride = "closed"; this.emit("z", 2); }, 700);
        break;
      case "happy":
        this.anim("blush", [[0.6, 200, Ease.out], [0, 600, Ease.inOut]]);
        if (this.own) this.affection(duration, 0.45);
        break;
      case "annoyed":
        this.eyeOverride = "line";
        this.eyeOverrideUntil = t + 0.8;
        setTimeout(() => Sound.play("annoyed"), 60);
        break;
    }
  }

  // ── Per-character moods ─────────────────────────────────────────────────────
  /** How strongly (0..1) and until when the Mochi is feeling affectionate / pleased with itself. */
  private loveUntil = 0; private loveAmt = 1; private cheerUntil = 0;
  private mLove = 0; private mCheer = 0; private mThink = 0; private mListen = 0;
  private nextSlowBlink = 0;
  private nextQuirk = now() + 4 + Math.random() * 4;

  /** Affection, in each character's own way (no hearts): the cloud warms up like a sunrise, the fox
   *  wags and relaxes its ears, the cat purrs and kneads. The look itself is drawn in characters.ts. */
  private affection(duration: number, amount: number) {
    this.loveUntil = now() + duration + 0.4;
    this.loveAmt = amount;
    if (this.calm) return;
    const P = this.def?.persona;
    if (P) {
      this.play(P.love);
      if (P.love?.sy) setTimeout(() => this.anim("sy", [[1, 500, Ease.inOut]]), Math.max(300, duration * 1000 - 300));
      if (P.loveGlint) this.emit("glint", 1);
    } else if (this.shape === "fox") {
      this.anim("oy", [[-0.07, 120, Ease.out], [0, 220, Ease.back]]);
      this.emit("glint", 1);
    } else {
      this.anim("oy", [[-0.08, 400, Ease.inOut], [0, 600, Ease.inOut]]); // drifts up a little, like warm air
      this.emit("glint", 2);
    }
  }

  /** A finished job, in each character's own way instead of a spin and stars. */
  celebrate() {
    const n = now();
    this.cheerUntil = n + 2.4;
    if (!this.own) {                                                    // mini bots keep the old roll
      this.doRoll(950, 1);
      setTimeout(() => this.emit("spark", 5), 500);
      return;
    }
    if (this.calm) return;
    const P = this.def?.persona;
    if (P) {
      this.play(P.cheer);
      this.fx(P.cheerFx);
      if (P.cheerRoll) this.doRoll(950, P.cheerRoll);
      if (P.cheerEye) { this.eyeOverride = P.cheerEye[0] as EyeShape; this.eyeOverrideUntil = now() + P.cheerEye[1]; }
    } else if (this.shape === "fox") {                                  // a pounce: crouch, leap, land in a puff
      this.anim("sy", [[0.82, 140, Ease.out], [1.14, 160, Ease.out], [0.8, 150, Ease.inOut], [1, 220, Ease.back]]);
      this.anim("sx", [[1.14, 140, Ease.out], [0.9, 160, Ease.out], [1.12, 150, Ease.inOut], [1, 220, Ease.back]]);
      this.anim("oy", [[0.02, 140, Ease.out], [-0.5, 190, Ease.out], [0.03, 170, Ease.inOut], [0, 160, Ease.back]]);
      setTimeout(() => { this.emit("dust", 4); this.emit("glint", 3); }, 520);
    } else {                                                            // cloud: the sun comes out
      this.anim("oy", [[-0.14, 500, Ease.inOut], [0, 700, Ease.inOut]]);
      this.anim("sy", [[1.07, 500, Ease.inOut], [1, 600, Ease.inOut]]);
      setTimeout(() => this.emit("rainbow", 1), 250);
      setTimeout(() => this.emit("glint", 6), 500);
    }
  }

  emit(type: Particle["type"], count: number) {
    for (let i = 0; i < count; i++) {
      if (type === "note") {                       // floats off one of the ear cups
        const side = Math.random() < 0.5 ? -1 : 1;
        this.particles.push({
          type,
          x: side * (0.92 + Math.random() * 0.1),
          y: -0.3 - Math.random() * 0.25,
          vx: side * (0.1 + Math.random() * 0.16),
          vy: -(0.32 + Math.random() * 0.22),
          age: -i * 0.25,
          life: 1.9 + Math.random() * 0.6,
          rot: Math.random() * Math.PI * 2,
          size: 0.17 + Math.random() * 0.06,
        });
        continue;
      }
      if (type === "thought") {                    // a chain of puffs drifting up and away, growing as they go
        this.particles.push({
          type, x: 0.5 + i * 0.06, y: -0.72 - i * 0.02, vx: 0.2, vy: -0.24, age: -i * 0.28, life: 2.1, rot: 0, size: 0.12 + i * 0.075,
        });
        continue;
      }
      if (type === "wave") {                       // sound arriving from both sides
        for (const side of [-1, 1]) {
          this.particles.push({ type, x: side * 0.8, y: -0.05, vx: 0, vy: 0, age: -i * 0.3, life: 1.0, rot: side, size: 1 });
        }
        continue;
      }
      if (type === "orbit") {                      // seeing stars
        this.particles.push({ type, x: 0, y: -0.75, vx: 0, vy: 0, age: -i * 0.1, life: 2.8, rot: (i / 3) * Math.PI * 2, size: 0.12 });
        continue;
      }
      if (type === "rain") {                       // falls from underneath the cloud
        this.particles.push({
          type, x: (Math.random() - 0.5) * 1.1, y: 0.5 + Math.random() * 0.1, vx: 0, vy: 0.5 + Math.random() * 0.25,
          age: -i * 0.12, life: 0.9, rot: 0, size: 0.07 + Math.random() * 0.03,
        });
        continue;
      }
      if (type === "glint") {                      // a small four-point sparkle that drifts up and fades
        this.particles.push({
          type, x: (Math.random() - 0.5) * 1.7, y: -0.55 + Math.random() * 0.8, vx: (Math.random() - 0.5) * 0.12, vy: -0.1 - Math.random() * 0.1,
          age: -i * 0.13, life: 1.1 + Math.random() * 0.5, rot: 0, size: 0.1 + Math.random() * 0.07,
          tone: this.glintTone() ?? (this.cloud && this.state !== "finished" ? 1 : this.cloud ? 0 : this.shape === "fox" ? 0 : 1),
        });
        continue;
      }
      if (type === "dust") {                       // a puff kicked up when the fox lands
        this.particles.push({
          type, x: (Math.random() < 0.5 ? -1 : 1) * (0.55 + Math.random() * 0.35), y: 0.7, vx: (Math.random() - 0.5) * 0.7, vy: -0.08 - Math.random() * 0.08,
          age: -i * 0.03, life: 0.65, rot: 0, size: 0.13 + Math.random() * 0.06,
        });
        continue;
      }
      if (type === "bolt") {
        this.particles.push({ type, x: (Math.random() - 0.5) * 0.5, y: 0.52, vx: 0, vy: 0.04, age: 0, life: 0.5, rot: 0, size: 0.2 });
        continue;
      }
      if (type === "rainbow") {
        this.particles.push({ type, x: 0, y: 0.04, vx: 0, vy: 0, age: 0, life: 1.9, rot: 0, size: 1 });
        continue;
      }
      const isZ = type === "z";
      this.particles.push({
        type,
        x: (Math.random() - 0.5) * 0.9 + (isZ ? 0.55 : 0),
        y: -0.7 - Math.random() * 0.2,
        vx: (Math.random() - 0.5) * 0.35 + (isZ ? 0.18 : 0),
        vy: -(0.45 + Math.random() * 0.35),
        age: -i * 0.14,
        life: 1.3 + Math.random() * 0.5,
        rot: Math.random() * Math.PI * 2,
        size: 0.15 + Math.random() * 0.08,
      });
    }
  }

  animateMorph(target: number, durationMs?: number) {
    const dur = durationMs ?? (target > 0.5 ? 550 : 650);
    this.anim("morph", [[target, dur, Ease.inOut]]);
  }

  resetMorph() {
    this.tweens.delete("morph");
    this.locks.delete("morph");
    this.morph = 0;
  }

  /** True while anything is still moving — lets the island stop its RAF loop. */
  get busy(): boolean {
    return (
      this.tweens.size > 0 ||
      this.particles.length > 0 ||
      this.cfg.bounces || this.cfg.scans || this.cfg.breathes || this.cfg.zz || this.cfg.sweat ||
      this.isMini ||
      Math.abs(this.tgYaw - this.yaw) > 0.002 ||
      Math.abs(this.tgPitch - this.pitch) > 0.002 ||
      Math.abs(this.tgTilt - this.tilt) > 0.002 ||
      Math.abs(this.tgSy - this.sy) > 0.002 ||
      Math.abs(this.tgSx - this.sx) > 0.002 ||
      Math.abs(this.tgEs - this.es) > 0.002 ||
      this.slotH > 0.001 || Math.abs(this.slotHVel) > 0.001 ||
      Math.abs(this.col[0] - this.colT[0]) > 0.003 ||
      Math.abs(this.col[1] - this.colT[1]) > 0.003 ||
      Math.abs(this.col[2] - this.colT[2]) > 0.003
    );
  }

  // ── Tweens ──────────────────────────────────────────────────────────────────

  anim(prop: PropKey, keys: TweenKey[], onComplete?: () => void) {
    this.tweens.set(prop, {
      prop, keys, index: 0, from: this[prop], startMs: performance.now(), onComplete,
    });
    this.locks.add(prop);
  }

  // ── Update ──────────────────────────────────────────────────────────────────

  update(dt: number) {
    const n = now();
    const nowMs = performance.now();

    for (const tw of [...this.tweens.values()]) {
      const k = tw.keys[tw.index];
      const p = Math.min(1, Math.max(0, (nowMs - tw.startMs) / k[1]));
      this[tw.prop] = tw.from + (k[0] - tw.from) * k[2](p);
      if (p >= 1) {
        tw.from = k[0];
        tw.index += 1;
        tw.startMs = nowMs;
        if (tw.index >= tw.keys.length) {
          this.tweens.delete(tw.prop);
          this.locks.delete(tw.prop);
          tw.onComplete?.();
        }
      }
    }

    const t = n - this.t0;
    let ty = this.lookX * 0.62;
    let tp = this.lookY * 0.5;

    if (this.cfg.look) {
      ty = ty * 0.7 + this.cfg.look[0] * 0.3;
      tp = tp * 0.7 + this.cfg.look[1] * 0.3;
    }
    if (this.cfg.scans) {
      ty = Math.sin(t * 2.6) * 0.6;
      tp = -0.06;
    }
    if (this.state === "sleeping") { ty = 0; tp = -0.14; }
    if (this.state === "dizzy") {
      const dg = this.own ? this.def?.dizzy?.gaze : undefined;                      // its own way of seeing double
      if (dg) [ty, tp] = dg(this.dizzyClock); else ty = Math.sin(t * 9) * 0.25;
    }
    const working = this.own && !this.calm && (this.state === "thinking" || this.state === "working" || this.state === "searching");
    if (working && this.def?.persona?.workGaze) [ty, tp] = this.def.persona.workGaze(t);                          // its own way of looking while it works
    else if (working && this.shape === "fox") { ty = Math.sin(t * 1.7) * 0.55; tp = 0.12 + Math.sin(t * 2.9) * 0.05; }   // sniffing around: scans, nose down

    // Mini bots never follow the mouse — they wander.
    if (this.isMini && !this.cfg.look && !this.cfg.scans && this.state !== "sleeping" && this.state !== "dizzy") {
      if (n > this.miniLookNextTime) {
        this.miniLookTarget = {
          x: -0.88 + Math.random() * 1.76,
          y: -0.55 + Math.random() * 1.0,
        };
        this.miniLookNextTime = n + 0.5 + Math.random() * 1.5;
      }
      ty = this.miniLookTarget.x * 0.62;
      tp = this.miniLookTarget.y * 0.5;
    }

    this.tgYaw = ty;
    this.tgPitch = tp;
    this.tgTilt = this.own && this.state === "question" ? 0 : this.cfg.tilt;

    if (n > this.waveStart && n < this.waveUntil) {
      const wt = n - this.waveStart;
      this.tgTilt = -0.06 + Math.sin(2 * Math.PI * 1.2 * wt) * 0.07;
    }
    const he = this.own ? this.helloEnv() : 0;                            // the character Hello: a head movement that goes with the paw
    if (he > 0.001) {
      const ht = this.helloClock();
      const HP = this.def?.persona?.helloTilt;
      if (HP) this.tgTilt += (HP[0] + Math.sin(ht * HP[1]) * HP[2]) * he;
      else if (this.shape === "fox") this.tgTilt += (-0.05 + Math.sin(ht * 9) * 0.04) * he;        // quick happy bobs
      else this.tgTilt += (-0.04 + Math.sin(ht * 5) * 0.035) * he;                                // the cloud sways
    }
    this.tgTilt += Math.sin(this.beat / 2) * 0.11 * this.groove;          // head nods left / right every other beat

    // Vibing: the groove (hop + head sway + squash) follows the beat.
    const grooving = (this.vibing || this.dance) && !this.calm && !this.grooveHold && this.state === "idle" && this.morph < 0.3;
    this.groove += ((grooving ? 1 : 0) - this.groove) * (1 - Math.pow(0.004, dt));
    if (this.groove < 0.003) this.groove = 0;
    this.beat = (this.beat + dt * Math.PI * 2 * BEAT_HZ) % (Math.PI * 4);
    const hop = this.groove * Math.abs(Math.sin(this.beat / 2)) * 0.055;   // |sin| = a bounce that lands on every beat

    const fl = this.def?.float ?? 0;
    const float = fl ? Math.sin(t * 1.5) * fl : 0;                             // clouds, ghosts and moons drift gently
    const bounce = (this.cfg.bounces ? -Math.abs(Math.sin(t * 5.2)) * 0.07 : 0) - hop - float;
    const kGen = 1 - Math.pow(0.0008, dt);
    if (!this.locks.has("oy")) this.oy += (bounce - this.oy) * kGen;

    if (this.cfg.breathes) {
      const amp = this.isMini ? 0.07 : 0.035;
      this.tgSy = 1 + Math.sin(t * 1.8) * amp;
      this.tgSx = 1 - Math.sin(t * 1.8) * amp * 0.57;
    } else if (this.isMini) {
      this.tgSy = 1 + Math.sin(t * 2.2) * 0.04;
      this.tgSx = 1 - Math.sin(t * 2.2) * 0.02;
    } else {
      this.tgSy = 1;
      this.tgSx = 1;
    }

    if (this.own && this.shape === "jelly") { const w = Math.sin(t * 5) * 0.035; this.tgSx += w; this.tgSy -= w; }
    if (this.controller) { this.tgTilt += Math.sin(t * 13) * 0.045; this.tgSy += Math.sin(t * 26) * 0.012; }

    // Mood body language
    if (this.state === "ringing") this.tgTilt += Math.sin(t * 22) * 0.1;             // phone buzzing
    if (this.state === "lowbat") { this.tgSy -= 0.07; this.tgSx += 0.03; this.tgTilt += 0.05; }   // slumped
    if (this.state === "hot") this.tgTilt += Math.sin(t * 30) * 0.03;                // trembling
    if (this.pose === "shy") { this.blush = Math.max(this.blush, 0.85); this.tgTilt += 0.12; this.tgSx -= 0.04; this.tgSy -= 0.03; }
    if (this.pose === "grumpy") { this.tgSx += 0.06; this.tgSy -= 0.05; this.blush = Math.max(this.blush, 0.35); }

    if (this.groove > 0.01) {                       // stretched at the top of the hop, squashed on landing
      const p = Math.abs(Math.sin(this.beat / 2));
      this.tgSy += (p - 0.6) * 0.09 * this.groove;
      this.tgSx -= (p - 0.6) * 0.05 * this.groove;
    }

    if (this.isMini && n > this.miniNextBehavior) this.doMiniBehaviorLoop();

    // Mood amounts the characters read to ease into their own poses (see Look in characters.ts).
    const kMood = 1 - Math.pow(0.04, dt);
    const busy = this.state === "thinking" || this.state === "working" || this.state === "searching";
    this.mLove += ((n < this.loveUntil ? this.loveAmt : 0) - this.mLove) * kMood;
    this.mCheer += ((n < this.cheerUntil || this.state === "finished" ? 1 : 0) - this.mCheer) * kMood;
    this.mThink += ((busy && this.own ? 1 : 0) - this.mThink) * kMood;
    this.mListen += ((this.state === "question" && this.own ? 1 : 0) - this.mListen) * kMood;

    // Each character thinks differently.
    const PW = this.def?.persona;
    if (working) {
      if (PW) {
        const w = PW.work;
        if (w?.sy) this.tgSy += w.sy - 1;
        if (w?.sx) this.tgSx += w.sx - 1;
        if (w?.sway) this.tgTilt += Math.sin(t * w.sway[1]) * w.sway[0];
        if (w?.bob) this.tgSy += Math.sin(t * w.bob[1]) * w.bob[0];
        if (w?.slowBlink && n > this.nextSlowBlink) {
          this.nextSlowBlink = n + 2.8 + Math.random() * 1.6;
          this.eyeOverride = "closed"; this.eyeOverrideUntil = n + 0.5;
        }
      } else if (this.shape === "fox") {                                // little sniffing bobs
        this.tgSy += Math.sin(t * 14) * 0.012 * Math.max(0, Math.sin(t * 1.7 * 2));
        this.tgTilt += Math.sin(t * 1.7) * 0.05;
      } else {                                                          // cloud: drifts slowly side to side
        this.tgTilt += Math.sin(t * 0.9) * 0.04;
      }
    }
    if (this.mLove > 0.05 && this.own) {
      if (PW) {
        if (PW.purr) { this.tgSx += Math.sin(t * 52) * 0.007 * this.mLove; this.tgSy += Math.sin(t * 47) * 0.005 * this.mLove; }
        if (PW.loveSway) this.tgTilt += Math.sin(t * PW.loveSway[0]) * PW.loveSway[1] * this.mLove;
      } else if (this.shape === "fox") this.tgTilt += Math.sin(t * 2.6) * 0.07 * this.mLove;                                                    // nuzzling head tilt
      else this.tgTilt += Math.sin(t * 1.5) * 0.05 * this.mLove;
      if (!this.calm && n - this.lastFx > 1.1) { this.lastFx = n; if (PW ? PW.loveGlint : true) this.emit("glint", 1); }
    }

    if (PW?.quirk && this.state === "idle" && !this.calm && this.morph < 0.3 && this.groove < 0.2 && he < 0.001 && n > this.nextQuirk) {
      const [qa, qb] = PW.quirkEvery ?? [5, 10];
      this.nextQuirk = n + qa + Math.random() * (qb - qa);
      this.doQuirk(PW.quirk);                                          // a small habit of its own
    }

    if (this.own) {                                                     // it cools down when nobody pokes it
      if (n - this.rageAt > 3.2) this.rageT = Math.max(0, this.rageT - dt * 0.3);
      this.rage += (this.rageT - this.rage) * (1 - Math.pow(0.0006, dt));
      if (this.rage < 0.004 && this.rageT === 0) this.rage = 0;
      const r = this.rage;
      if (r > 0.2 && this.own) {
        this.tgTilt += Math.sin(t * (30 + 40 * r)) * 0.03 * (r - 0.15);          // trembling with anger
        this.tgSx += 0.05 * r; this.tgSy += 0.04 * r;                            // puffs up
        if (!this.calm && r > 0.45 && Math.random() < 0.07) this.emit(this.shape === "cloud" ? "rain" : EMBER_SHAPES.has(this.shape) ? "spark" : "sweat", 1);
      }
    }
    {                                                                     // dizzy: the body reels its own way
      const on = this.own && this.state === "dizzy";
      this.dizzyAmt += ((on ? 1 : 0) - this.dizzyAmt) * (1 - Math.pow(0.004, dt));
      if (on) this.dizzyClock += dt;
      else if (this.dizzyAmt < 0.01) { this.dizzyAmt = 0; this.dizzyClock = 0; }
      const DZ = this.def?.dizzy;
      if (DZ && this.dizzyAmt > 0.01) {
        this.tgTilt += Math.sin(this.dizzyClock * DZ.sway[1]) * DZ.sway[0] * this.dizzyAmt;
        if (DZ.bob) { const b = Math.sin(this.dizzyClock * DZ.bob[1]) * DZ.bob[0] * this.dizzyAmt; this.tgSy += b; this.tgSx -= b * 0.6; }
      }
    }
    const kLook = 1 - Math.pow(0.0025, dt);
    if (!this.locks.has("yaw")) this.yaw += (this.tgYaw - this.yaw) * kLook;
    if (!this.locks.has("pitch")) this.pitch += (this.tgPitch - this.pitch) * kLook;
    if (!this.locks.has("tilt")) this.tilt += (this.tgTilt - this.tilt) * kGen;
    if (!this.locks.has("sy")) this.sy += (this.tgSy - this.sy) * kGen;
    if (!this.locks.has("sx")) this.sx += (this.tgSx - this.sx) * kGen;
    if (!this.locks.has("es")) this.es += (this.tgEs - this.es) * kGen;

    this.col = mix3(this.col, this.colT, 1 - Math.pow(0.002, dt));

    if (n > this.nextBlink) {
      if (this.state !== "sleeping" && this.state !== "dizzy") {
        this.blink();
        if (Math.random() < 0.22) setTimeout(() => this.blink(), 230);
      }
      this.nextBlink = n + 2.2 + Math.random() * 3.2;
    }

    if (this.eyeOverride && n > this.eyeOverrideUntil) {
      this.eyeOverride = this.permanentEye;
      if (this.permanentEye) this.eyeOverrideUntil = Number.POSITIVE_INFINITY;
    }

    if (n - this.lastAmbient > 1.3) {
      this.lastAmbient = n;
      if (this.cfg.zz) this.emit("z", 1);
      if (!this.isMini && this.cfg.sweat && Math.random() < 0.5) this.drip(1);
      if (this.cloud && this.state === "hot" && Math.random() < 0.45) this.emit("bolt", 1);
    }

    if (this.shape === "moon" && this.own && this.state !== "sleeping" && Math.random() < 0.012) this.emit("spark", 1);
    if (this.own && !this.calm) {
      if (this.cloud && this.state === "thinking" && n - this.lastFx > 1.9) { this.lastFx = n; this.emit("thought", 3); }
      if (this.cloud && (this.state === "thinking" || this.state === "working") && Math.random() < 0.05) this.emit("rain", 1);   // a drizzle while it works it out
      if (this.cloud && this.state === "question" && n - this.lastFx > 0.85) { this.lastFx = n; this.emit("wave", 2); }
      if (this.state === "dizzy" && !this.def?.dizzyFront && n - this.lastFx > 2.6) { this.lastFx = n; this.emit("orbit", 3); }
    }

    if (!this.calm && (this.vibing || this.dance) && n - this.lastNote > 0.75) {
      this.lastNote = n;
      this.emit("note", 1);
    }

    for (const p of this.particles) p.age += dt;
    this.particles = this.particles.filter((p) => p.age < p.life);

    // Mouth slot spring — ω₀ = 2π/0.25, ζ = 0.6
    const omega = (2 * Math.PI) / 0.25;
    const zeta = 0.6;
    const acc = omega * omega * (this.slotHTarget - this.slotH) - 2 * zeta * omega * this.slotHVel;
    this.slotHVel += acc * dt;
    this.slotH = Math.max(0, this.slotH + this.slotHVel * dt);

    this.lastTime = n;
  }

  private doMiniBehaviorLoop() {
    const n = now();
    switch (this.permanentEmote) {
      case "happy":
        if (this.locks.has("oy")) { this.miniNextBehavior = n + 0.4; return; }
        this.anim("oy", [[-0.3, 120, Ease.out], [0.03, 200, Ease.inOut], [0, 160, Ease.back]]);
        this.anim("sy", [[0.82, 80, Ease.out], [1.18, 130, Ease.out], [0.88, 160, Ease.inOut], [1, 200, Ease.back]]);
        this.anim("sx", [[1.15, 80, Ease.out], [0.88, 130, Ease.out], [1.06, 160, Ease.inOut], [1, 200, Ease.back]]);
        this.miniNextBehavior = n + 2.2 + Math.random() * 1.2;
        break;
      case "annoyed":
        if (this.locks.has("yaw")) { this.miniNextBehavior = n + 0.5; return; }
        this.anim("yaw", [
          [-0.65, 50, Ease.out], [0.65, 90, Ease.inOut], [-0.5, 80, Ease.inOut],
          [0.4, 75, Ease.inOut], [-0.2, 70, Ease.inOut], [0, 140, Ease.out],
        ]);
        this.miniNextBehavior = n + 3.0 + Math.random() * 2.5;
        break;
      case "wink":
        this.eyeOverride = "wink";
        this.eyeOverrideUntil = n + 0.55;
        this.anim("tilt", [[0.13, 100, Ease.out], [0.13, 320, Ease.lin], [0, 200, Ease.inOut]]);
        this.miniNextBehavior = n + 2.2 + Math.random() * 2.0;
        break;
      case "love":
        this.emit("heart", 2);
        this.anim("tilt", [[-0.1, 180, Ease.out], [0.1, 340, Ease.inOut], [0, 220, Ease.inOut]]);
        this.miniNextBehavior = n + 2.6 + Math.random() * 1.5;
        break;
      default:
        this.miniNextBehavior = n + 3.0 + Math.random() * 2.0;
    }
  }

  // ── Draw ────────────────────────────────────────────────────────────────────

  /**
   * Draws hands, body, blush, eyes, mouth, badge and particles into a canvas of
   * `w`×`h` CSS pixels (the caller has already applied the DPR transform).
   */
  draw(x: CanvasRenderingContext2D, W: number, H: number) {
    const R = W * 0.3;
    const rx = R * 1.14;
    const ry = R * 0.88;
    const cx = W / 2 + this.ox * R;
    const cy = H / 2 + this.particleOverhang / 2 + this.oy * R + R * 0.06;

    this.drawHandsBehind(x, R, rx, ry, cx, cy);

    x.save();
    x.translate(cx, cy);
    if (this.tilt !== 0) x.rotate(this.tilt);
    x.scale(this.sx, this.sy);

    const body = this.bodyPath(rx, ry, R);
    const cdef = this.morph < 0.3 ? this.def : null;
    const look = cdef ? this.lookCtx(x, R, rx, ry, body) : null;
    if (cdef && look && look.rage) cdef.furyBehind?.(look);
    if (cdef && look && look.dizzy) cdef.dizzyBehind?.(look);
    if (cdef && look) cdef.behind?.(look);
    this.drawBody(x, body, R, rx, ry);
    if (cdef && look) cdef.base?.(look);
    if (cdef && look && look.rage && !cdef.ownFury) angerBase(look, cdef.furyTint);

    const blushVal = Math.max(this.blush, this.tint * 0.5) * (1 - this.morph);
    if (blushVal > 0.01) {
      x.save();
      x.clip(body);
      const yOffset = Math.sin(this.yaw) * rx * 0.8;
      x.fillStyle = `rgba(255,120,150,${0.5 * blushVal})`;
      for (const sd of [-1, 1]) {
        x.beginPath();
        x.ellipse(sd * rx * 0.55 + yOffset, ry * 0.2, R * 0.17, R * 0.1, 0, 0, Math.PI * 2);
        x.fill();
      }
      x.restore();
    }

    this.drawEyes(x, body, R, rx, ry);
    if (cdef && look) cdef.front?.(look);
    if (cdef && look && look.dizzy) cdef.dizzyFront?.(look);
    if (cdef && look && look.rage) {
      if (!cdef.ownFury) angerFront(look, cdef.furyInk);
      cdef.fury?.(look);
    }
    if (this.morph > 0.05) this.drawMouth(x, body, R);
    if (this.controller && this.morph < 0.3 && !this.isMini) this.drawController(x, R, rx, ry);

    x.restore();

    if (this.badge && this.badgeS > 0.01 && this.morph < 0.25) {
      this.drawBadge(x, this.badge, R, cx, cy);
    }
    this.drawParticles(x, R, cx, cy);
  }

  /** Progress (0..1) of the current nail swing, or -1 when none is going on. */
  private slashP(): number {
    const p = (now() - this.slashAt) / this.slashLen;
    return p >= 0 && p <= 1 ? p : -1;
  }

  private lookCtx(x: CanvasRenderingContext2D, R: number, rx: number, ry: number, body: Path2D): Look {
    return {
      x, R, rx, ry, t: now(), body, state: this.state,
      happy: this.state === "finished" || this.eyeOverride === "happy" || this.permanentEye === "happy" || this.groove > 0.5,
      sad: this.state === "ratelimit" || this.state === "error" || this.state === "thirsty" || this.state === "lowbat",
      hot: this.state === "hot",
      yaw: this.yaw, pitch: this.pitch,
      love: this.mLove, cheer: this.mCheer, think: this.mThink, listen: this.mListen,
      hello: this.helloEnv(), helloT: this.helloClock(),
      eyes: this.eyeSlots(rx, ry),
      rage: this.rage,
      slashP: this.slashP(),
      slashDir: this.slashDir,
      dizzy: this.dizzyAmt,
      dizzyT: this.dizzyClock,
    };
  }

  /** A game controller held at the bottom of the body, buttons being mashed. */
  private drawController(x: CanvasRenderingContext2D, R: number, rx: number, ry: number) {
    const t = now();
    const w = R * 0.8, h = R * 0.34;
    x.save();
    x.translate(0, ry * 0.64 + Math.sin(t * 26) * R * 0.012);
    x.lineJoin = "round";
    // grips + body
    x.fillStyle = "#2a2b36"; x.strokeStyle = "#101118"; x.lineWidth = R * 0.05;
    for (const sd of [-1, 1]) {
      x.beginPath(); x.ellipse(sd * w * 0.8, h * 0.45, w * 0.2, h * 0.62, sd * 0.25, 0, Math.PI * 2); x.fill(); x.stroke();
    }
    roundRectPath(x, -w, -h * 0.6, w * 2, h * 1.2, h * 0.5);
    x.fill(); x.stroke();
    // d-pad
    x.fillStyle = "#c9ccd8";
    x.fillRect(-w * 0.62, -h * 0.1, w * 0.3, h * 0.2);
    x.fillRect(-w * 0.5 - h * 0.1, -h * 0.28, h * 0.2, h * 0.56);
    // face buttons: one is "pressed" at a time
    const cols = ["#5ee08a", "#ff5d6c", "#4dc3ff", "#ffd84d"];
    const pos: Array<[number, number]> = [[0, 0.24], [0.24, 0], [0, -0.24], [-0.24, 0]];
    const down = Math.floor(t * 9) % 4;
    pos.forEach(([bx, by], i) => {
      x.beginPath();
      x.arc(w * 0.52 + bx * w * 0.5, by * h * 1.3, h * (i === down ? 0.15 : 0.2), 0, Math.PI * 2);
      x.fillStyle = i === down ? "#6b6e7e" : cols[i];
      x.fill();
    });
    // sticks
    x.fillStyle = "#4a4c5a";
    for (const sx of [-0.16, 0.16]) { x.beginPath(); x.arc(sx * w, h * 0.34, h * 0.2, 0, Math.PI * 2); x.fill(); }
    // little hands on the grips
    x.fillStyle = rgba(this.def?.top ?? BASE_TOP);
    for (const sd of [-1, 1]) { x.beginPath(); x.arc(sd * w * 0.92, h * 0.32 + (sd > 0 ? 1 : -1) * Math.sin(t * 18) * R * 0.01, R * 0.15, 0, Math.PI * 2); x.fill(); }
    x.restore();
  }

  private bodyPath(rx: number, ry: number, R: number): Path2D {
    if (this.own && this.morph < 0.005) {
      const d = CHAR_DEFS[this.shape] ?? CHAR_DEFS.cloud;
      if (d.path) return d.path(R, now());
    }
    const n = 72;
    const expN = 2.0 / 2.7;
    const tw = R * 1.0;
    const th = R * 0.94;
    const tr = R * 0.42;
    const p = new Path2D();
    const m = this.morph;
    for (let i = 0; i <= n; i++) {
      const a = (i / n) * Math.PI * 2;
      const ca = Math.cos(a);
      const sa = Math.sin(a);
      const px0 = rx * (ca >= 0 ? Math.pow(ca, expN) : -Math.pow(-ca, expN));
      const py0 = ry * (sa >= 0 ? Math.pow(sa, expN) : -Math.pow(-sa, expN));
      let px = px0;
      let py = py0;
      if (m >= 0.005) {
        const rr = rrPoint(ca, sa, tw, th, tr);
        px = lerp(px0, rr.x, m);
        py = lerp(py0, rr.y, m);
      }
      if (i === 0) p.moveTo(px, py);
      else p.lineTo(px, py);
    }
    p.closePath();
    return p;
  }

  private drawBody(x: CanvasRenderingContext2D, body: Path2D, R: number, rx: number, ry: number) {
    if (this.bodyColor) {
      // Mini bots: flat solid fill — no gradient, no reflection, no highlight
      x.fillStyle = rgba(this.bodyColor, 1);
      x.fill(body);
      return;
    }
    const g = x.createLinearGradient(rx * 0.7, -ry * 0.85, -rx * 0.8, ry * 0.9);
    g.addColorStop(0, rgba(this.def?.top ?? BASE_TOP));
    g.addColorStop(1, rgba(this.def?.bottom ?? BASE_BOTTOM));
    x.fillStyle = g;
    x.fill(body);

    const effectiveTint = this.tint * (1 - this.morph) * (this.def?.tintScale ?? 1);
    if (effectiveTint > 0.01) {
      const tg = x.createLinearGradient(0, ry, 0, -ry);
      tg.addColorStop(0, rgba(this.col, 0.72 * effectiveTint));
      tg.addColorStop(1, rgba(this.col, 0));
      x.fillStyle = tg;
      x.fill(body);
    }

    const sh = x.createRadialGradient(0, 0, R * 0.15, 0, 0, R * 1.25);
    sh.addColorStop(0, "rgba(0,0,0,0)");
    sh.addColorStop(0.6, "rgba(0,0,0,0)");
    sh.addColorStop(1, "rgba(0,0,0,0.2)");
    x.fillStyle = sh;
    x.fill(body);

    const hl = x.createRadialGradient(rx * 0.34, -ry * 0.46, 0, rx * 0.34, -ry * 0.46, R * 0.42);
    hl.addColorStop(0, `rgba(255,255,255,${0.55 * (this.def?.gloss ?? 1)})`);
    hl.addColorStop(1, "rgba(255,255,255,0)");
    x.fillStyle = hl;
    x.fill(body);

    if (this.def?.rim) {                                  // a thin light edge so dark characters read on the black notch
      x.strokeStyle = this.def.rim;
      x.lineWidth = R * 0.04;
      x.stroke(body);
    }
  }

  /** Where each visible eye is right now (body space). Skins use it so glasses follow the eyes. */
  private eyeSlots(rx: number, ry: number): Array<{ sd: number; ex: number; ey: number; fx: number; fy: number }> {
    const out: Array<{ sd: number; ex: number; ey: number; fx: number; fy: number }> = [];
    for (const sd of [-1, 1]) {
      const eyeYaw = sd * EYE_SP + this.yaw;
      let eyePitch = EYE_P + this.pitch + this.roll;
      eyePitch = (((eyePitch + Math.PI) % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2) - Math.PI;
      const cp = Math.cos(eyePitch);
      if (Math.cos(eyeYaw) * cp <= 0.04) continue;
      out.push({
        sd,
        ex: Math.sin(eyeYaw) * cp * rx,
        ey: -Math.sin(eyePitch) * ry + (this.morph > 0 ? ry * 0.14 * this.morph : 0),
        fx: lerp(Math.max(0.18, Math.cos(eyeYaw)), 1, this.morph * 0.7),
        fy: lerp(Math.max(0.18, cp), 1, this.morph * 0.7),
      });
    }
    return out;
  }

  private drawEyes(x: CanvasRenderingContext2D, body: Path2D, R: number, rx: number, ry: number) {
    let shape: EyeShape = this.eyeOverride ?? (this.groove > 0.5 ? "happy" : (this.def?.eyes?.[this.state] as EyeShape | undefined) ?? this.cfg.eye);
    const dzEyes = this.own && this.state === "dizzy" ? this.def?.dizzy?.eyes : undefined;     // its own dizzy eyes
    if (dzEyes) shape = dzEyes as EyeShape;
    else if (shape === "spiral" && this.own) shape = "squint";
    if (this.morph > 0.5) {
      if (this.isChewing) shape = "happy";
      else if (this.slotHTarget > 0.05 || this.slotH > 0.1) shape = "cup";
    }

    x.save();
    x.clip(body);
    const ink = this.isMini ? MINI_INK : (this.def?.ink ?? INK);
    x.fillStyle = ink;
    x.strokeStyle = ink;

    for (const e of this.eyeSlots(rx, ry)) {
      const eyeMult = this.isMini ? 1.9 : 1.0;
      const ew = R * EYE_W * this.es * eyeMult;
      const eh = R * EYE_H * this.es * eyeMult;

      x.save();
      x.translate(e.ex, e.ey);
      x.scale(e.fx, e.fy);
      this.drawEyeShape(x, shape, ew, eh, e.sd, ink);
      x.restore();
    }
    x.restore();
  }

  private drawEyeShape(
    x: CanvasRenderingContext2D, shape: EyeShape,
    w: number, h: number, sd: number, ink: string,
  ) {
    const t = now();
    switch (shape) {
      case "wide":
        this.drawEyeShape(x, "pill", w * 1.16, h * 1.12, sd, ink);
        break;
      case "pill": {
        const hh = Math.max(h * this.open, w * 0.3);
        roundRectPath(x, -w / 2, -hh / 2, w, hh, Math.min(w / 2, hh / 2));
        x.fill();
        break;
      }
      case "dot":
        x.beginPath();
        x.arc(0, 0, w * 0.45, 0, Math.PI * 2);
        x.fill();
        break;
      case "line":
        x.rotate(-sd * 0.2);
        roundRectPath(x, -w * 0.78, -w * 0.21, w * 1.56, w * 0.42, w * 0.21);
        x.fill();
        break;
      case "flat":
        roundRectPath(x, -w * 0.72, -w * 0.2, w * 1.44, w * 0.4, w * 0.2);
        x.fill();
        break;
      case "happy":
        x.lineWidth = w * 0.5;
        x.lineCap = "round";
        x.beginPath();
        x.arc(0, h * 0.18, w * 0.82, Math.PI * 1.12, Math.PI * 1.88);
        x.stroke();
        break;
      case "content":                                  // soft, relaxed ‿ ‿ (purring / basking), thicker than "closed"
        x.lineWidth = w * 0.44;
        x.lineCap = "round";
        x.beginPath();
        x.arc(0, -h * 0.12, w * 0.8, Math.PI * 0.12, Math.PI * 0.88);
        x.stroke();
        break;
      case "closed":
        x.lineWidth = w * 0.36;
        x.lineCap = "round";
        x.beginPath();
        x.arc(0, -h * 0.08, w * 0.78, Math.PI * 0.15, Math.PI * 0.85);
        x.stroke();
        break;
      case "squint":                                   // > <  (eyes squeezed shut)
        x.lineWidth = w * 0.3;
        x.lineCap = "round";
        x.lineJoin = "round";
        x.beginPath();
        x.moveTo(sd * w * 0.62, -h * 0.34);          //  > on the left eye, < on the right eye
        x.lineTo(-sd * w * 0.5, 0);
        x.lineTo(sd * w * 0.62, h * 0.34);
        x.stroke();
        break;
      case "spiral": {
        x.lineWidth = w * 0.22;
        x.lineCap = "round";
        x.beginPath();
        for (let a = 0; a < 4.4 * Math.PI; a += 0.2) {
          const r = w * 0.06 + a * w * 0.058;
          const aa = a + t * 9 * sd;
          const px = Math.cos(aa) * r;
          const py = Math.sin(aa) * r;
          if (a === 0) x.moveTo(px, py);
          else x.lineTo(px, py);
        }
        x.stroke();
        break;
      }
      case "cross":                                   // x  x  (knocked silly)
        x.lineWidth = w * 0.26;
        x.lineCap = "round";
        x.beginPath();
        x.moveTo(-w * 0.5, -h * 0.42); x.lineTo(w * 0.5, h * 0.42);
        x.moveTo(w * 0.5, -h * 0.42); x.lineTo(-w * 0.5, h * 0.42);
        x.stroke();
        break;
      case "heart":
        x.fillStyle = "#FF4D6D";
        heartPath(x, w * 1.2);
        x.fill();
        x.fillStyle = ink;
        break;
      case "star":
        x.fillStyle = "#F7B32B";
        x.rotate(t * 1.5 * sd);
        starPath(x, w * 1.05, w * 0.46);
        x.fill();
        x.fillStyle = ink;
        break;
      case "tired":
        roundRectPath(x, -w / 2, -h * 0.02, w, h * 0.38, w / 2);
        x.fill();
        roundRectPath(x, -w * 0.62, -h * 0.1, w * 1.24, w * 0.22, w * 0.11);
        x.fill();
        break;
      case "wink":
        if (sd < 0) {
          const hh = Math.max(h * this.open, w * 0.3);
          roundRectPath(x, -w / 2, -hh / 2, w, hh, Math.min(w / 2, hh / 2));
          x.fill();
        } else {
          x.lineWidth = w * 0.5;
          x.lineCap = "round";
          x.beginPath();
          x.arc(0, h * 0.18, w * 0.82, Math.PI * 1.12, Math.PI * 1.88);
          x.stroke();
        }
        break;
      case "cup": {
        // Flat top, rounded bottom corners (U shape) — used while the box is open
        const hh = Math.max(h * this.open, w * 0.3);
        const cr = Math.min(w / 2, hh / 2);
        x.beginPath();
        x.moveTo(-w / 2, -hh / 2);
        x.lineTo(w / 2, -hh / 2);
        x.lineTo(w / 2, hh / 2 - cr);
        x.quadraticCurveTo(w / 2, hh / 2, w / 2 - cr, hh / 2);
        x.lineTo(-w / 2 + cr, hh / 2);
        x.quadraticCurveTo(-w / 2, hh / 2, -w / 2, hh / 2 - cr);
        x.closePath();
        x.fill();
        break;
      }
    }
  }

  /** Mailbox slot: dark pill cut into the box face, with rim and lip highlights. */
  private drawMouth(x: CanvasRenderingContext2D, body: Path2D, R: number) {
    const m = this.morph;
    const hW = R * 1.8 * m;
    const hH = this.slotH * R * m;
    const hX = -hW / 2;
    const boxTop = -R * (0.88 + 0.06 * m);
    const hY = boxTop + R * 0.08 * m;

    x.save();
    x.clip(body);

    x.strokeStyle = `rgba(255,255,255,${0.55 * m})`;
    x.lineWidth = 1;
    x.lineCap = "round";
    x.beginPath();
    x.moveTo(-R * 0.9 * m, boxTop + 1);
    x.lineTo(R * 0.9 * m, boxTop + 1);
    x.stroke();

    if (hH > 0.8) {
      const hR = Math.min(hW / 2, hH / 2);
      const g = x.createLinearGradient(0, hY, 0, hY + hH);
      g.addColorStop(0, "rgb(7,8,10)");
      g.addColorStop(1, "rgb(16,19,26)");
      roundRectPath(x, hX, hY, hW, hH, hR);
      x.fillStyle = g;
      x.fill();
      if (hH > 4) {
        const lipR = Math.min(hR, (hW - 2) / 2);
        x.strokeStyle = `rgba(255,255,255,${0.28 * m})`;
        x.beginPath();
        x.moveTo(hX + lipR, hY + hH - 0.5);
        x.lineTo(hX + hW - lipR, hY + hH - 0.5);
        x.stroke();
      }
    }
    x.restore();
  }

  /** Hands sit behind the body — drawn before it, in world coordinates. */
  private drawHandsBehind(
    x: CanvasRenderingContext2D,
    R: number, rx: number, ry: number, cx: number, cy: number,
  ) {
    if (this.hands <= 0.01 || this.isMini) return;
    if (R <= 14) return; // meaningless at compact/peek sizes

    const n = now();
    const bodyH = 2 * ry;
    const hew = 0.3 * ry * this.hands;
    const heh = 0.26 * ry * this.hands;
    const hwB = rx * this.sx;
    const hhB = ry * this.sy;
    const isWaving = n >= this.waveStart && this.waveStart > 0 && n < this.waveUntil;

    for (const sd of [-1, 1]) {
      let localX: number;
      let localY: number;
      let handRot = 0;

      if (sd > 0 && isWaving) {
        const wt = n - this.waveStart;
        const rise = Math.min(1, wt / 0.18);
        const riseEased = 1 - Math.pow(1 - rise, 3);
        const restX = hwB * 1.08;
        const restY = hhB * 0.7;
        const oscX = Math.cos(13 * wt) * 0.06 * bodyH;
        const oscY = -Math.sin(13 * wt) * 0.14 * bodyH;
        const waveX = hwB * 1.1 + oscX;
        const waveY = -hhB * 0.15 + oscY;
        localX = restX + (waveX - restX) * riseEased;
        localY = restY + (waveY - restY) * riseEased;
        handRot = (-0.5 + Math.sin(13 * wt) * 0.35) * riseEased;
      } else if (sd < 0 && isWaving) {
        const wt = n - this.waveStart;
        localX = -hwB * 1.08;
        localY = hhB * 0.7 + Math.sin(6 * wt) * 0.04 * bodyH;
      } else {
        localX = sd * hwB * 1.08;
        localY = hhB * 0.7;
      }

      const cosT = Math.cos(this.tilt);
      const sinT = Math.sin(this.tilt);
      const worldX = cx + cosT * localX - sinT * localY;
      const worldY = cy + sinT * localX + cosT * localY;

      x.save();
      x.translate(worldX, worldY);
      if (handRot !== 0) x.rotate(handRot);
      const g = x.createLinearGradient(hew * 0.7, -heh * 0.85, -hew * 0.8, heh * 0.9);
      if (this.bodyColor) {
        g.addColorStop(0, rgba(mix3(this.bodyColor, [1, 1, 1], 0.35)));
        g.addColorStop(1, rgba(this.bodyColor));
      } else {
        g.addColorStop(0, rgba(BASE_TOP));
        g.addColorStop(1, rgba(BASE_BOTTOM));
      }
      x.beginPath();
      x.ellipse(0, 0, hew, heh, 0, 0, Math.PI * 2);
      x.fillStyle = g;
      x.fill();
      x.strokeStyle = "rgba(0,0,0,0.08)";
      x.lineWidth = 1;
      x.stroke();
      x.restore();
    }
  }

  private drawBadge(x: CanvasRenderingContext2D, badge: Badge, R: number, cx: number, cy: number) {
    const bs = this.badgeS * (this.isMini ? 1.25 : 1);
    const bx = cx - R * 0.72 * this.sx;
    const by = cy - R * 0.72 * this.sy;
    const t = now();

    x.save();
    x.translate(bx, by);
    x.scale(bs, bs);
    const col = rgba(badge.color);

    if (badge.kind === "dots") {
      if (this.isMini) {
        const phase = (t * 2.4) % 1;
        const dotR = R * 0.22 * (1 + 0.25 * Math.sin(phase * Math.PI * 2));
        x.fillStyle = "#000";
        x.beginPath();
        x.arc(0, 0, R * 0.2, 0, Math.PI * 2);
        x.fill();
        x.fillStyle = col;
        x.beginPath();
        x.arc(0, 0, dotR, 0, Math.PI * 2);
        x.fill();
      } else {
        const pw = R * 0.72;
        const ph = R * 0.36;
        roundRectPath(x, -pw / 2, -ph / 2, pw, ph, ph / 2);
        x.fillStyle = col;
        x.fill();
        for (let i = 0; i < 3; i++) {
          const phase = (((t * 2.4 - i * 0.22) % 1) + 1) % 1;
          const dotR = R * 0.055 * (1 + 0.4 * Math.max(0, Math.sin(phase * Math.PI * 2)));
          x.fillStyle = "#fff";
          x.beginPath();
          x.arc((i - 1) * R * 0.18, 0, dotR, 0, Math.PI * 2);
          x.fill();
        }
      }
    } else if (badge.kind === "bang" || badge.kind === "question") {
      x.fillStyle = "#000";
      x.beginPath();
      x.arc(0, 0, R * 0.3, 0, Math.PI * 2);
      x.fill();
      x.fillStyle = col;
      x.beginPath();
      x.arc(0, 0, R * 0.23, 0, Math.PI * 2);
      x.fill();
      if (!this.isMini) {
        x.fillStyle = "#fff";
        x.font = `900 ${R * 0.32}px ${FONT}`;
        x.textAlign = "center";
        x.textBaseline = "middle";
        x.fillText(badge.kind === "bang" ? "!" : "?", 0, R * 0.02);
      }
    } else {
      x.fillStyle = "#000";
      x.beginPath();
      x.arc(0, 0, R * 0.2, 0, Math.PI * 2);
      x.fill();
      x.fillStyle = col;
      x.beginPath();
      x.arc(0, 0, R * 0.135, 0, Math.PI * 2);
      x.fill();
    }
    x.restore();
  }

  private drawParticles(x: CanvasRenderingContext2D, R: number, cx: number, cy: number) {
    for (const p of this.particles) {
      if (p.age <= 0) continue;
      const k = p.age / p.life;
      const a = k < 0.2 ? k / 0.2 : 1 - (k - 0.2) / 0.8;
      const px = cx + (p.x + p.vx * p.age) * R * 1.3;
      const py = cy + (p.y + p.vy * p.age) * R * 1.3;
      const sz = R * p.size * (1 + k * 0.4);

      x.save();
      x.translate(px, py);
      x.globalAlpha = Math.min(1, Math.max(0, a));
      switch (p.type) {
        case "heart":
          x.rotate(Math.sin(p.age * 6) * 0.3);
          x.fillStyle = "#FF4D6D";
          heartPath(x, sz);
          x.fill();
          break;
        case "star":
          x.rotate(p.rot + p.age * 2);
          x.fillStyle = "#F7B32B";
          starPath(x, sz, sz * 0.45);
          x.fill();
          break;
        case "glint": {
          const c = ["#fff1b8", "#ffd2c4", "#d4ebff", "#e6d9ff", "#ffd3e6", "#d6ffe2"][p.tone ?? 0];
          x.rotate(Math.sin(p.age * 3) * 0.2);
          x.fillStyle = c;
          x.beginPath();
          x.moveTo(0, -sz * 1.25); x.quadraticCurveTo(sz * 0.14, -sz * 0.14, sz * 1.25, 0);
          x.quadraticCurveTo(sz * 0.14, sz * 0.14, 0, sz * 1.25); x.quadraticCurveTo(-sz * 0.14, sz * 0.14, -sz * 1.25, 0);
          x.quadraticCurveTo(-sz * 0.14, -sz * 0.14, 0, -sz * 1.25);
          x.fill();
          break;
        }
        case "dust":
          x.globalAlpha = Math.min(1, Math.max(0, a)) * 0.55;
          x.fillStyle = "#f3e6d4";
          x.beginPath(); x.arc(0, 0, sz * (0.8 + k * 0.9), 0, Math.PI * 2); x.fill();
          break;
        case "spark":
          x.rotate(p.rot);
          x.fillStyle = "#fff";
          starPath(x, sz * 0.8, sz * 0.18);
          x.fill();
          break;
        case "sweat":
          x.fillStyle = "#7CC7FF";
          x.beginPath();
          x.moveTo(0, -sz);
          x.quadraticCurveTo(sz * 0.8, sz * 0.2, 0, sz * 0.6);
          x.quadraticCurveTo(-sz * 0.8, sz * 0.2, 0, -sz);
          x.fill();
          break;
        case "thought":
          x.fillStyle = "rgba(255,255,255,.95)";
          x.strokeStyle = "rgba(150,160,205,.75)";
          x.lineWidth = Math.max(1, sz * 0.16);
          x.beginPath(); x.arc(0, 0, sz * 1.15, 0, Math.PI * 2); x.fill(); x.stroke();
          break;
        case "wave": {
          const rad = R * (0.3 + p.age * 0.7);
          x.strokeStyle = "rgba(110,215,255,.95)";
          x.lineWidth = R * 0.055;
          x.lineCap = "round";
          for (let r = 0; r < 2; r++) {
            x.beginPath();
            const rr = rad + r * R * 0.2;
            if (p.rot > 0) x.arc(-R * 0.5, 0, rr, -0.75, 0.75);
            else x.arc(R * 0.5, 0, rr, Math.PI - 0.75, Math.PI + 0.75);
            x.stroke();
          }
          break;
        }
        case "orbit": {
          const ang = p.age * 4.2 + p.rot;
          x.translate(Math.cos(ang) * R * 1.0, Math.sin(ang) * R * 0.16);
          x.rotate(p.age * 3);
          x.fillStyle = "#ffd84d";
          starPath(x, sz * 1.1, sz * 0.5);
          x.fill();
          break;
        }
        case "rain":
          x.fillStyle = "#8fd0ff";
          x.beginPath();
          x.ellipse(0, 0, sz * 0.3, sz * 0.75, 0, 0, Math.PI * 2);
          x.fill();
          break;
        case "bolt":
          x.fillStyle = "#ffd84d";
          x.strokeStyle = "#e0a800";
          x.lineWidth = Math.max(1, sz * 0.12);
          x.lineJoin = "round";
          x.beginPath();
          x.moveTo(sz * 0.2, -sz); x.lineTo(-sz * 0.5, sz * 0.15); x.lineTo(-sz * 0.05, sz * 0.15);
          x.lineTo(-sz * 0.25, sz); x.lineTo(sz * 0.55, -sz * 0.2); x.lineTo(sz * 0.1, -sz * 0.2);
          x.closePath();
          x.fill(); x.stroke();
          break;
        case "rainbow": {
          x.globalAlpha = Math.min(1, Math.max(0, a)) * 0.9;
          x.lineCap = "butt";
          const cols = ["#ff5d6c", "#ffb64d", "#ffe66d", "#5ee08a", "#4dc3ff"];
          cols.forEach((c, i) => {
            x.beginPath();
            x.arc(0, 0, R * (1.46 - i * 0.065), Math.PI, Math.PI * 2);
            x.strokeStyle = c; x.lineWidth = R * 0.07; x.stroke();
          });
          break;
        }
        case "note":
          x.translate(Math.sin(p.age * 4 + p.rot) * R * 0.07, 0);     // drifts side to side as it rises
          x.rotate(Math.sin(p.age * 3 + p.rot) * 0.25);
          x.fillStyle = x.strokeStyle = NOTE_COLORS[Math.floor(p.rot * 10) % NOTE_COLORS.length];
          notePath(x, sz, p.rot > Math.PI);
          break;
        case "z":
          x.fillStyle = "rgb(209,219,235)";
          x.font = `700 ${sz * 1.9}px ${FONT}`;
          x.textAlign = "center";
          x.textBaseline = "middle";
          x.fillText("z", 0, 0);
          break;
      }
      x.restore();
    }
  }
}

/** Ray → rounded-rect boundary intersection, for the mailbox morph. */
function rrPoint(ca: number, sa: number, W: number, H: number, cr: number): { x: number; y: number } {
  const eps = 1e-6;
  const kx = ca >= 0 ? 1 : -1;
  const ky = sa >= 0 ? 1 : -1;
  const cx = kx * (W - cr);
  const cy = ky * (H - cr);

  const dot = ca * cx + sa * cy;
  const disc = dot * dot - (cx * cx + cy * cy - cr * cr);
  if (disc >= 0) {
    const t = dot + Math.sqrt(disc);
    if (t > eps) {
      const px = ca * t;
      const py = sa * t;
      if (Math.abs(px) >= W - cr - eps && Math.abs(py) >= H - cr - eps) return { x: px, y: py };
    }
  }
  if (Math.abs(sa) > eps) {
    const t = (ky * H) / sa;
    if (t > eps) {
      const px = ca * t;
      if (Math.abs(px) <= W - cr + eps) return { x: px, y: ky * H };
    }
  }
  if (Math.abs(ca) > eps) {
    const t = (kx * W) / ca;
    if (t > eps) {
      const py = sa * t;
      if (Math.abs(py) <= H - cr + eps) return { x: kx * W, y: py };
    }
  }
  return { x: kx * W, y: ky * H };
}
