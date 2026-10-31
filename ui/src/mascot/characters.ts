// The Mochi cast: Cloud, Fox and Cat. They share one engine (moods, blinking, looking around) but each
// has its own palette, silhouette, body language and way of thinking: the cloud drizzles and flashes an
// idea, the fox sniffs around with swivelling ears, the cat slow-blinks and swishes its tail.
//
// Everything here draws in "body space": origin at the middle of the body, R = body unit (the body is
// about 2.3 R wide and 1.8 R tall). Keep shapes inside roughly ±1.15 R / ±0.9 R so the eyes line up.

export type RGB3 = [number, number, number];

/** What a character needs to know about the Mochi's current mood. */
export interface Look {
  x: CanvasRenderingContext2D;
  R: number; rx: number; ry: number;
  t: number;               // seconds, for idle motion
  body: Path2D;            // the outline (for clipping)
  state: string;
  happy: boolean;          // grooving, finished, happy eyes
  sad: boolean;            // error / sad / thirsty / low battery
  hot: boolean;
  yaw: number;             // where the face points (radians), so nose and whiskers turn with the eyes
  pitch: number;
  // Smoothed 0..1 mood amounts the engine keeps up to date, so a character can ease into its own pose.
  love: number;            // affection (hover, "happy")
  cheer: number;           // celebrating a finished job
  think: number;           // working it out
  listen: number;          // hearing you
  // The Hello (the "wave" emote). Optional so callers that build a Look by hand keep working.
  hello?: number;          // 0..1: how far into the greeting pose it is (arm up, ears perked, tail up)
  helloT?: number;         // seconds since the Hello began: drives the swing so every wave starts the same way
  /** Where each visible eye is (body space), so patches, glows and masks can follow the eyes as they turn. */
  eyes?: EyeSlot[];
  // Rage (the Knight): how furious it is (0..1), and where the current nail swing is (0..1, -1 = no swing).
  rage?: number;
  slashP?: number;
  slashDir?: number;       // +1 / -1: which way the current swing goes, so the strokes alternate
}

export interface EyeSlot { sd: number; ex: number; ey: number; fx: number; fy: number }

// ── Personality ─────────────────────────────────────────────────────────────────
// Data the engine (mochiBot.ts) reads so a character can move and feel its own way without the engine
// knowing its name. Cloud, Fox and the tabby Cat keep their hand-written moves and have none of this.
export type EaseName = "out" | "inOut" | "back" | "lin";
/** A tween: [target, milliseconds, easing]. */
export type Key = [number, number, EaseName];
export interface Moves { sy?: Key[]; sx?: Key[]; oy?: Key[]; tilt?: Key[] }
/** A particle burst: [delay ms, type, count]. */
export type Fx = [number, "dust" | "glint" | "star" | "spark" | "heart", number];

export interface Persona {
  helloLen: number;
  hello: Moves; helloFx?: Fx[];
  /** The greeting starts with a slow blink (the cat way of saying hi). */
  helloSlowBlink?: boolean;
  /** Head movement during the Hello: [lean, speed, amount]. */
  helloTilt: [number, number, number];
  love?: Moves; loveSway?: [number, number]; purr?: boolean;
  /** Glint colour while fond / pleased: 0 gold, 1 peach, 2 ice, 3 violet, 4 pink, 5 mint; -1 none. */
  glint: number; loveGlint?: boolean;
  cheer: Moves; cheerFx?: Fx[]; cheerRoll?: number;
  /** Eyes while pleased with itself: [shape, seconds]. */
  cheerEye?: [string, number];
  error?: Moves;
  /** Where it looks while it works something out (yaw, pitch). */
  workGaze?: (t: number) => [number, number];
  work?: { sy?: number; sx?: number; sway?: [number, number]; bob?: [number, number]; slowBlink?: boolean };
  /** A small habit it does now and then while idle, and how often (seconds). */
  quirk?: "sideglance" | "twitch" | "yawn" | "shiver" | "headtilt";
  quirkEvery?: [number, number];
  /** Blink speed [closing ms, opening ms]. */
  blinkMs?: [number, number];
}

export interface CharDef {
  label: string;
  /** Short line for the picker. */
  blurb: string;
  top: RGB3;
  bottom: RGB3;
  /** Eye colour (default: near-black). */
  ink?: string;
  /** Kept for the engine's layout maths: how much lower the body sits than the blob. */
  lift?: number;
  /** Gentle floating, as a fraction of the body height. */
  float?: number;
  /** A thin light edge so dark characters stay readable on the black notch. */
  rim?: string;
  /** How much the state colour washes over the body (1 = fully; dark characters use less). */
  tintScale?: number;
  /** How strong the glossy highlight is (1 = default). */
  gloss?: number;
  /** Eye shape per state (an expression of its own), e.g. { thinking: "wide" }. */
  eyes?: Partial<Record<string, string>>;
  /** Eye shape per emote, e.g. { love: "happy" }. */
  emoteEyes?: Partial<Record<string, string>>;
  persona?: Persona;
  /** Own outline; without it the rounded blob is used. */
  path?(R: number, t: number): Path2D;
  /** Drawn behind the body: ears, tails, gills, leaves, feet. */
  behind?(l: Look): void;
  /** Drawn on the body, under the eyes: face patches, markings. */
  base?(l: Look): void;
  /** Drawn over the face, after the eyes: nose, whiskers, paws. */
  front?(l: Look): void;
}

const TAU = Math.PI * 2;
const css = (c: RGB3, a = 1) => `rgba(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)},${a})`;

function ell(p: Path2D, cx: number, cy: number, rx: number, ry: number): void {
  p.moveTo(cx + rx, cy);
  p.ellipse(cx, cy, rx, ry, 0, 0, TAU);
}

function ellipseFill(x: CanvasRenderingContext2D, cx: number, cy: number, rx: number, ry: number, fill: string, rot = 0): void {
  x.beginPath();
  x.ellipse(cx, cy, rx, ry, rot, 0, TAU);
  x.fillStyle = fill;
  x.fill();
}


const shiftX = (l: Look) => Math.sin(l.yaw) * l.rx * 0.8;
const lerp = (a: number, b: number, k: number) => a + (b - a) * k;

// ── Cloud ───────────────────────────────────────────────────────────────────────
// Thinks like weather: it drifts, drizzles while it works things out (the engine adds the rain and the
// flash of an idea). In love it warms up like a sunrise; when a job is done the rainbow comes out.
const cloud: CharDef = {
  label: "Cloud", blurb: "Puffy, drifts, makes weather",
  top: [0.929, 0.929, 0.937], bottom: [0.769, 0.773, 0.792],
  lift: 0.12, float: 0.022,
  path(R) {
    const p = new Path2D();
    ell(p, 0, 0.3 * R, 1.12 * R, 0.58 * R);          // base
    ell(p, -0.62 * R, -0.12 * R, 0.52 * R, 0.52 * R); // left lobe
    ell(p, 0, -0.26 * R, 0.66 * R, 0.66 * R);         // big middle lobe
    ell(p, 0.66 * R, -0.1 * R, 0.5 * R, 0.5 * R);     // right lobe
    return p;
  },
  behind(l) {
    const { x, R, t, love, cheer } = l;
    const hello = l.hello ?? 0, helloT = l.helloT ?? 0;
    if (hello > 0.01) {
      // Hello: the sun peeks out. A soft gold glow behind the cloud and a puff of an arm that grows out of
      // its right side and waves (three puffs, the last one the biggest, like a little hand).
      const SR = R * 1.38;                                          // same reason as the glow below: it must fade out inside the canvas
      const sun = x.createRadialGradient(0, 0.05 * R, R * 0.35, 0, 0.05 * R, SR);
      const k = Math.min(1, hello);
      sun.addColorStop(0, `rgba(255,226,150,${0.46 * k * (0.9 + 0.1 * Math.sin(t * 5))})`);
      sun.addColorStop(1, "rgba(255,226,150,0)");
      x.fillStyle = sun;
      x.beginPath(); x.arc(0, 0.05 * R, SR, 0, TAU); x.fill();

      const a = -1.0 + Math.sin(helloT * 10) * 0.34 * k;           // 0 = pointing right, negative = up
      const reach = 0.66 * R * hello;
      x.save();
      x.translate(0.96 * R, 0.2 * R);
      x.fillStyle = css([0.945, 0.945, 0.953]);
      x.strokeStyle = "rgba(110,118,150,.22)"; x.lineWidth = R * 0.03;
      for (const [d, r] of [[0.3, 0.2], [0.62, 0.22], [1, 0.27]] as const) {
        const px = Math.cos(a) * reach * d, py = Math.sin(a) * reach * d;
        x.beginPath(); x.arc(px, py, R * r * Math.min(1, 0.4 + hello * 0.6), 0, TAU); x.fill(); x.stroke();
      }
      x.restore();
    }
    const glow = Math.max(love, cheer * 0.8);
    if (glow < 0.02) return;
    // A soft sunrise glow behind the cloud (peach for affection, gold for a win), breathing slowly.
    const pulse = 0.85 + 0.15 * Math.sin(t * 2.2);
    // The glow must fade to nothing BEFORE the canvas edge: the canvas ends 1.5 R under the cloud's centre, and a
    // larger glow was cut there into a hard peach line that showed under the Mochi (it stayed after the animation).
    const GR = R * 1.38;
    const g = x.createRadialGradient(0, 0.1 * R, R * 0.35, 0, 0.1 * R, GR);
    const warm = cheer > love ? "255,214,120" : "255,176,150";
    g.addColorStop(0, `rgba(${warm},${0.5 * glow * pulse})`);
    g.addColorStop(1, `rgba(${warm},0)`);
    x.fillStyle = g;
    x.beginPath(); x.arc(0, 0.1 * R, GR, 0, TAU); x.fill();
  },
  base(l) {
    const { x, R, body, love, think, t } = l;
    x.save();
    x.clip(body);
    if (love > 0.02) {                                   // the cloud turns rosy-peach, like a sky at sunrise
      const g = x.createLinearGradient(0, -R * 0.8, 0, R * 0.9);
      g.addColorStop(0, `rgba(255,214,190,${0.5 * love})`);
      g.addColorStop(1, `rgba(255,150,150,${0.45 * love})`);
      x.fillStyle = g; x.fillRect(-R * 1.4, -R, R * 2.8, R * 2);
    }
    if (think > 0.02) {                                  // the underside darkens a little, like a rain cloud
      const g = x.createLinearGradient(0, -R * 0.1, 0, R * 0.95);
      g.addColorStop(0, "rgba(120,130,160,0)");
      g.addColorStop(1, `rgba(110,122,160,${0.38 * think * (0.8 + 0.2 * Math.sin(t * 3))})`);
      x.fillStyle = g; x.fillRect(-R * 1.4, -R * 0.1, R * 2.8, R * 1.2);
    }
    x.restore();
  },
};

// ── Fox ─────────────────────────────────────────────────────────────────────────
// Curious and quick: ears swivel one at a time while it sniffs out an answer, they snap forward when
// it hears you, the tail wags hard when it is fond of you and sweeps when it is pleased with itself.
const fox: CharDef = {
  label: "Fox", blurb: "Curious, ears that swivel, a tail that talks",
  top: [1, 0.74, 0.38], bottom: [0.92, 0.46, 0.18],
  behind(l) {
    const { x, R, rx, ry, t, happy, sad, love, cheer, think, listen } = l;
    const hello = l.hello ?? 0, helloT = l.helloT ?? 0;
    const orange = css(fox.bottom), cream = "#fff1dc", dark = "#5b2d17";
    // tail: lazy sweep → fast wag (love) → big pleased sweep (cheer) → hard, high wag (hello); still and low when sad
    const speed = lerp(lerp(2.4, 4.2, think), hello > 0.01 ? 15 : 11, Math.max(love, cheer * 0.7, hello * 0.9));
    const amp = sad ? 0.03 : lerp(lerp(0.13, 0.2, think), 0.34, Math.max(love, cheer, hello));
    const lift = sad ? 0.5 : lerp(-0.55, -0.85, Math.max(listen * 0.6, cheer, hello * 0.8));
    const wag = Math.sin(t * speed) * amp;
    x.save();
    x.translate(0.95 * R, 0.4 * R);
    x.rotate(lift + wag);
    ellipseFill(x, 0.42 * R, 0, 0.55 * R, 0.27 * R, orange);
    ellipseFill(x, 0.78 * R, 0, 0.2 * R, 0.22 * R, cream);
    x.restore();
    // ears
    x.save();
    x.lineJoin = "round";
    const sneak = Math.floor(t * 0.9) % 2 === 0 ? -1 : 1;     // which ear is swivelling right now
    for (const sd of [-1, 1]) {
      let rot = 0, grow = 1;
      if (sad) { rot = sd * 0.5; grow = 0.88; }                                   // droop outwards
      else {
        rot += sd === sneak ? Math.sin(t * 3.4) * 0.22 * think : 0;                // swivel while thinking
        rot += (sd === -sneak ? Math.sin(t * 2.1 + 1) * 0.07 : 0) * think;
        rot += -sd * 0.12 * listen;                                               // tilt toward the sound
        grow += 0.1 * listen + 0.06 * cheer + 0.14 * hello;                       // ears up and tall
        rot += sd * 0.38 * love;                                                  // relax back and out
        rot += (Math.sin(helloT * 16 + (sd > 0 ? 0 : Math.PI)) * 0.09 - sd * 0.05) * hello;   // hello: ears perk and flick
        if (happy && love < 0.1) rot += Math.sin(t * 12) * 0.04 * sd;
      }
      x.save();
      x.translate(sd * rx * 0.58, -ry * 0.68);
      x.rotate(rot);
      x.scale(1, grow);
      x.translate(-sd * rx * 0.58, ry * 0.68);
      x.beginPath();
      x.moveTo(sd * rx * 0.2, -ry * 0.72);
      x.lineTo(sd * rx * 0.66, -ry * 1.34);
      x.lineTo(sd * rx * 0.98, -ry * 0.45);
      x.closePath();
      x.fillStyle = orange; x.strokeStyle = orange; x.lineWidth = R * 0.12;
      x.fill(); x.stroke();
      x.beginPath();
      x.moveTo(sd * rx * 0.4, -ry * 0.78);
      x.lineTo(sd * rx * 0.67, -ry * 1.14);
      x.lineTo(sd * rx * 0.82, -ry * 0.58);
      x.closePath();
      x.fillStyle = cream; x.fill();
      x.beginPath();                                                              // dark ear tips
      x.moveTo(sd * rx * 0.57, -ry * 1.2);
      x.lineTo(sd * rx * 0.66, -ry * 1.36);
      x.lineTo(sd * rx * 0.76, -ry * 1.16);
      x.closePath();
      x.fillStyle = dark; x.fill();
      x.restore();
    }
    x.restore();
  },
  base(l) {
    const { x, R, body } = l;
    x.save();
    x.clip(body);
    ellipseFill(x, 0, 0.62 * R, 1.0 * R, 0.4 * R, "#fff1dc");           // cream cheeks and chin
    ellipseFill(x, -0.62 * R, 0.3 * R, 0.32 * R, 0.2 * R, "#fff1dc", 0.5);
    ellipseFill(x, 0.62 * R, 0.3 * R, 0.32 * R, 0.2 * R, "#fff1dc", -0.5);
    x.restore();
  },
  front(l) {
    const { x, R, rx, ry, t, think } = l;
    const dx = shiftX(l);
    const sniff = think > 0.05 ? Math.sin(t * 14) * R * 0.012 * think : 0;      // the nose twitches when it sniffs
    x.save();
    x.translate(dx * 0.9, R * 0.3 + sniff);
    x.fillStyle = "#4a2314";
    x.beginPath(); x.ellipse(0, 0, R * 0.1, R * 0.07, 0, 0, TAU); x.fill();
    x.restore();
    const hello = l.hello ?? 0;
    if (hello > 0.01) {                                  // hello: a quick wave with a dark-socked paw, on the side away from the tail
      const k = Math.min(1, hello);
      const a = -0.95 + Math.sin((l.helloT ?? 0) * 18) * 0.4 * k;
      const len = 0.74 * R * hello;
      const sx = rx * 0.88, sy = ry * 0.38;
      const ex = sx + Math.cos(a) * len, ey = sy + Math.sin(a) * len;
      const mx = sx + Math.cos(a) * len * 0.62, my = sy + Math.sin(a) * len * 0.62;
      x.save();
      x.globalAlpha = Math.min(1, hello * 3.5);          // fades as it lowers, so no stub is left on the body
      x.scale(-1, 1);                                    // drawn for the right-hand side, mirrored to the left
      x.lineCap = "round";
      x.strokeStyle = css(fox.bottom); x.lineWidth = R * 0.27;
      x.beginPath(); x.moveTo(sx, sy); x.lineTo(mx, my); x.stroke();
      x.strokeStyle = "#5b2d17";                         // the sock
      x.beginPath(); x.moveTo(mx, my); x.lineTo(ex, ey); x.stroke();
      x.restore();
    }
  },
};

// ── Shared bits for the new cast ────────────────────────────────────────────────

/** Pointed cat ears that tilt with the mood. `rot(sd)` is the extra rotation of the ear on side `sd`. */
function catEars(l: Look, o: { col: string; inner: string; rim?: string; rot: (sd: number) => number; tall?: number }): void {
  const { x, R, rx, ry } = l;
  const g = o.tall ?? 1;
  x.save();
  x.lineJoin = "round";
  for (const sd of [-1, 1]) {
    x.save();
    x.translate(sd * rx * 0.58, -ry * 0.62);
    x.rotate(o.rot(sd));
    x.translate(-sd * rx * 0.58, ry * 0.62);
    x.beginPath();
    x.moveTo(sd * rx * 0.16, -ry * 0.78);
    x.lineTo(sd * rx * 0.62, -ry * (0.78 + 0.52 * g));
    x.lineTo(sd * rx * 0.97, -ry * 0.5);
    x.closePath();
    x.fillStyle = o.col; x.strokeStyle = o.rim ?? o.col; x.lineWidth = o.rim ? R * 0.05 : R * 0.12;
    x.fill(); x.stroke();
    if (o.rim) { x.strokeStyle = o.col; x.lineWidth = R * 0.1; x.stroke(); }
    x.beginPath();
    x.moveTo(sd * rx * 0.38, -ry * 0.82);
    x.lineTo(sd * rx * 0.63, -ry * (0.82 + 0.3 * g));
    x.lineTo(sd * rx * 0.82, -ry * 0.6);
    x.closePath();
    x.fillStyle = o.inner; x.fill();
    x.restore();
  }
  x.restore();
}

/** A raised paw / flipper / arm on the left (away from the tail), used by the Hello. */
function helloArm(l: Look, o: { sx: number; sy: number; a0: number; speed: number; swing: number; len: number; w: number; col: string; sock?: string; rim?: string }): void {
  const hello = l.hello ?? 0;
  if (hello <= 0.01) return;
  const { x, R } = l;
  const k = Math.min(1, hello);
  const a = o.a0 + Math.sin((l.helloT ?? 0) * o.speed) * o.swing * k;
  const len = o.len * R * hello;
  const ex = o.sx + Math.cos(a) * len, ey = o.sy + Math.sin(a) * len;
  x.save();
  x.globalAlpha = Math.min(1, hello * 3.5);
  x.scale(-1, 1);
  x.lineCap = "round";
  if (o.rim) { x.strokeStyle = o.rim; x.lineWidth = R * (o.w + 0.05); x.beginPath(); x.moveTo(o.sx, o.sy); x.lineTo(ex, ey); x.stroke(); }
  x.strokeStyle = o.col; x.lineWidth = R * o.w;
  x.beginPath(); x.moveTo(o.sx, o.sy); x.lineTo(ex, ey); x.stroke();
  if (o.sock) {
    const mx = o.sx + Math.cos(a) * len * 0.62, my = o.sy + Math.sin(a) * len * 0.62;
    x.strokeStyle = o.sock; x.beginPath(); x.moveTo(mx, my); x.lineTo(ex, ey); x.stroke();
  }
  x.restore();
}

// ── Cat ─────────────────────────────────────────────────────────────────────────
// The white cat: soft, plump and shy, with big white ears that are pink inside, whiskers, a pink nose
// and little paws, and no tail. It tilts its head at whatever you say, bounces twice when it says hello,
// waves a paw, and blushes, purrs and kneads the air when it is fond of you. Like the cloud it floats a
// touch and glows warm when it is happy.
const CAT_FUR = "#fbfbff", CAT_EDGE = "rgba(140,150,200,.4)", CAT_PINK = "#f4a9b8";
const cat: CharDef = {
  label: "White cat", blurb: "Soft and white, big ears, shy",
  top: [1, 1, 1], bottom: [0.86, 0.89, 0.97],
  ink: "#2b3566", float: 0.012,
  eyes: { thinking: "wide", question: "wide", searching: "wide", working: "wide" },
  persona: {
    helloLen: 1.5, helloTilt: [0.1, 6, 0.04],
    hello: { oy: [[-0.1, 160, "out"], [0, 170, "back"], [-0.1, 150, "out"], [0, 220, "back"]], sy: [[0.94, 100, "out"], [1.06, 200, "out"], [1, 300, "inOut"]], sx: [[1.05, 100, "out"], [0.96, 200, "out"], [1, 300, "inOut"]] },
    helloFx: [[300, "glint", 2], [800, "glint", 2]],
    love: { sy: [[0.94, 380, "inOut"]] }, loveSway: [2.4, 0.06], purr: true,
    glint: 4, loveGlint: true,
    cheer: { oy: [[-0.2, 150, "out"], [0.02, 200, "back"], [-0.14, 140, "out"], [0, 220, "back"]], sy: [[0.9, 120, "out"], [1.08, 150, "out"], [0.95, 160, "inOut"], [1, 220, "back"]], sx: [[1.08, 120, "out"], [0.95, 150, "out"], [1.03, 160, "inOut"], [1, 220, "back"]] },
    cheerFx: [[300, "glint", 3], [800, "glint", 3]],
    workGaze: (t) => [Math.sin(t * 1.1) * 0.25, -0.12],
    work: { sway: [0.07, 1.3] },
    quirk: "headtilt", quirkEvery: [3, 7], blinkMs: [90, 170],
  },
  behind(l) {
    const { x, R, rx, ry, t, sad, love, cheer, listen, state } = l;
    const hello = l.hello ?? 0, sleeping = state === "sleeping";
    const glow = Math.max(love, cheer * 0.8);
    if (glow > 0.02) {                                       // a soft warm glow when it is happy (fades out inside the canvas)
      const GR = R * 1.38, warm = cheer > love ? "255,214,120" : "255,176,190";
      const g = x.createRadialGradient(0, 0.1 * R, R * 0.35, 0, 0.1 * R, GR);
      g.addColorStop(0, `rgba(${warm},${0.4 * glow * (0.85 + 0.15 * Math.sin(t * 2.2))})`);
      g.addColorStop(1, `rgba(${warm},0)`);
      x.fillStyle = g; x.beginPath(); x.arc(0, 0.1 * R, GR, 0, TAU); x.fill();
    }
    x.save();                                                // fluffy cheek tufts
    x.fillStyle = CAT_FUR; x.strokeStyle = CAT_EDGE; x.lineWidth = R * 0.03;
    for (const sd of [-1, 1]) for (const [oy, r] of [[0.18, 0.17], [0.38, 0.15]] as const) {
      x.beginPath(); x.ellipse(sd * rx * 0.97, ry * oy, R * r, R * r * 0.8, sd * 0.5, 0, TAU); x.fill(); x.stroke();
    }
    x.restore();
    catEars(l, {                                             // big white ears, pink inside
      col: CAT_FUR, inner: CAT_PINK, rim: CAT_EDGE, tall: 1.75,
      rot: (sd) => sad || sleeping ? sd * 0.28 : -sd * 0.14 * listen + sd * 0.22 * love - sd * 0.06 * cheer - sd * 0.12 * hello + (sd === 1 ? Math.sin(t * 38) * 0.2 * Math.max(0, Math.sin(t * 1.7)) ** 12 * 0.5 : 0) + (sd === -1 ? Math.sin(t * 1.9) * 0.025 : 0),
    });
  },
  base(l) {
    const { x, R, body } = l;
    const dx = shiftX(l);
    x.save();
    x.clip(body);
    ellipseFill(x, dx * 0.9, 0.55 * R, 0.62 * R, 0.4 * R, "#ffffff");               // a bright muzzle
    x.fillStyle = "rgba(255,170,190,.24)";                                          // always a little pink in the cheeks
    for (const sd of [-1, 1]) { x.beginPath(); x.ellipse(dx + sd * R * 0.8, R * 0.42, R * 0.2, R * 0.12, 0, 0, TAU); x.fill(); }
    x.restore();
  },
  front(l) {
    const { x, R, rx, ry, t, love, cheer, think, listen, state } = l;
    const hello = l.hello ?? 0;
    const dx = shiftX(l);
    x.save();
    x.translate(dx * 0.9, 0);
    x.fillStyle = "#ee8ea4";                                                         // nose
    x.beginPath(); x.moveTo(-R * 0.09, R * 0.27); x.lineTo(R * 0.09, R * 0.27); x.lineTo(0, R * 0.37); x.closePath(); x.fill();
    x.strokeStyle = "rgba(110,118,160,.7)"; x.lineWidth = R * 0.028; x.lineCap = "round";   // whiskers (they lift when it listens or thinks)
    const lift = (think + listen + hello * 0.6) * R * 0.05;
    for (const sd of [-1, 1]) for (const k of [-1, 0, 1]) {
      x.beginPath(); x.moveTo(sd * R * 0.34, R * 0.38 + k * R * 0.04); x.lineTo(sd * R * 0.8, R * 0.38 + k * R * 0.12 - lift); x.stroke();
    }
    x.restore();
    if (hello > 0.01) {                                      // hello: the paw comes up and beckons, palm toward you
      const k = Math.min(1, hello);
      const a = -1.12 + Math.sin((l.helloT ?? 0) * 11) * 0.3 * k;
      const len = 0.8 * R * hello;
      const sx = rx * 0.88, sy = ry * 0.55;
      const ex = sx + Math.cos(a) * len, ey = sy + Math.sin(a) * len;
      x.save();
      x.globalAlpha = Math.min(1, hello * 3.5);
      x.scale(-1, 1);
      x.lineCap = "round";
      x.strokeStyle = CAT_EDGE; x.lineWidth = R * 0.32;
      x.beginPath(); x.moveTo(sx, sy); x.lineTo(ex, ey); x.stroke();
      x.strokeStyle = CAT_FUR; x.lineWidth = R * 0.27;
      x.beginPath(); x.moveTo(sx, sy); x.lineTo(ex, ey); x.stroke();
      const pr = Math.min(1, 0.5 + hello * 0.5);
      x.translate(ex, ey); x.rotate(a + Math.PI / 2);
      x.fillStyle = "#ffffff"; x.strokeStyle = CAT_EDGE; x.lineWidth = R * 0.035;
      x.beginPath(); x.ellipse(0, 0, R * 0.23 * pr, R * 0.26 * pr, 0, 0, TAU); x.fill(); x.stroke();
      x.fillStyle = CAT_PINK;                                // pad and three toe beans
      x.beginPath(); x.ellipse(0, R * 0.06 * pr, R * 0.095 * pr, R * 0.075 * pr, 0, 0, TAU); x.fill();
      for (const o of [-0.1, 0, 0.1]) { x.beginPath(); x.arc(o * R * pr, -R * 0.09 * pr + Math.abs(o) * R * 0.08 * pr, R * 0.035 * pr, 0, TAU); x.fill(); }
      x.restore();
    }
    if (love > 0.05 || (cheer > 0.3 && state === "finished")) {   // paws: kneading the air when it purrs, a little paw up when it has finished something
      for (const sd of [-1, 1]) {
        const phase = Math.sin(t * 6 + (sd > 0 ? Math.PI : 0));
        const rise = love > 0.05 ? Math.max(0, phase) * R * 0.14 : (sd > 0 ? R * 0.2 : 0);
        x.fillStyle = "#ffffff"; x.strokeStyle = CAT_EDGE; x.lineWidth = R * 0.03;
        x.beginPath(); x.ellipse(sd * R * 0.42, R * 0.8 - rise, R * 0.19, R * 0.14, 0, 0, TAU); x.fill(); x.stroke();
        x.fillStyle = CAT_PINK;
        x.beginPath(); x.ellipse(sd * R * 0.42, R * 0.82 - rise, R * 0.06, R * 0.045, 0, 0, TAU); x.fill();
      }
    }
  },
};

// ── Bunny ───────────────────────────────────────────────────────────────────────
// Bright and bouncy. Long ears say everything: straight up when it listens, one flopped over while it
// thinks, folded back when it is fond of you, drooped when it is sad. The nose never stops twitching,
// it hops (twice, for a finished job) instead of walking, and it blinks fast.
const bunny: CharDef = {
  label: "Bunny", blurb: "Long ears say it all, hops when happy",
  top: [1, 0.96, 0.97], bottom: [0.96, 0.83, 0.9],
  ink: "#4a2f3d", tintScale: 0.6,
  eyes: { question: "wide", approval: "wide", searching: "wide" },
  persona: {
    helloLen: 1.4, helloTilt: [-0.04, 10, 0.04],
    hello: {
      oy: [[0.02, 120, "out"], [-0.28, 150, "out"], [0.02, 150, "inOut"], [-0.2, 140, "out"], [0, 160, "back"]],
      sy: [[0.88, 110, "out"], [1.1, 140, "out"], [0.95, 120, "inOut"], [1, 180, "back"]],
      sx: [[1.1, 110, "out"], [0.93, 140, "out"], [1.04, 120, "inOut"], [1, 180, "back"]],
    },
    helloFx: [[300, "dust", 3], [650, "dust", 2], [520, "glint", 2]],
    love: { oy: [[-0.07, 150, "out"], [0, 260, "back"]] }, loveSway: [3, 0.05],
    glint: 4, loveGlint: true,
    cheer: {
      oy: [[0.03, 120, "out"], [-0.42, 170, "out"], [0.03, 160, "inOut"], [-0.3, 150, "out"], [0.02, 150, "inOut"], [0, 150, "back"]],
      sy: [[0.84, 120, "out"], [1.14, 160, "out"], [0.86, 150, "inOut"], [1.08, 140, "out"], [0.92, 140, "inOut"], [1, 180, "back"]],
      sx: [[1.12, 120, "out"], [0.9, 160, "out"], [1.1, 150, "inOut"], [0.94, 140, "out"], [1.05, 140, "inOut"], [1, 180, "back"]],
    },
    cheerFx: [[480, "dust", 3], [900, "dust", 3], [600, "glint", 3]],
    workGaze: (t) => [Math.sin(t * 2.3) * 0.28, -0.1 + Math.sin(t * 3.1) * 0.05],
    work: { bob: [0.012, 16] },
    quirk: "twitch", quirkEvery: [2, 4.5], blinkMs: [55, 100],
  },
  behind(l) {
    const { x, R, rx, ry, t, sad, love, cheer, think, listen, state } = l;
    const hello = l.hello ?? 0, helloT = l.helloT ?? 0;
    const fur = css(bunny.bottom), inner = "#f7a3bd", sleeping = state === "sleeping";
    // cotton tail
    x.fillStyle = "#fffafc"; x.strokeStyle = "rgba(200,150,170,.35)"; x.lineWidth = R * 0.03;
    x.beginPath(); x.arc(rx * 0.92, ry * 0.5 + Math.sin(t * 9) * R * 0.012 * Math.max(love, cheer), R * 0.22, 0, TAU); x.fill(); x.stroke();
    x.save();
    for (const sd of [-1, 1]) {
      let rot = 0;
      if (sad || sleeping) rot = sd * 0.62;                                   // flop out to the sides
      else {
        rot += sd === -1 ? 0.55 * think + Math.sin(t * 2.4) * 0.05 * think : -Math.sin(t * 3) * 0.03 * think;   // one ear flops while it thinks
        rot += -sd * 0.07 * listen;                                           // both straight and attentive
        rot += sd * 0.5 * love;                                               // folded back, relaxed
        rot += sd * 0.12 * cheer * Math.sin(t * 8);
        rot += (sd === 1 ? Math.sin(helloT * 15) * 0.14 : -0.04) * hello;     // hello: one ear waves
        if (Math.floor(t * 0.5) % 2 === 0 && sd === 1) rot += Math.max(0, Math.sin(t * 20)) * 0.06 * (1 - listen);   // the odd ear flick
      }
      const grow = 1 + 0.1 * listen + 0.07 * hello + 0.05 * cheer;
      x.save();
      x.translate(sd * rx * 0.44, -ry * 0.6);
      x.rotate(rot);
      x.scale(1, grow);
      x.beginPath(); x.ellipse(0, -ry * 0.5, R * 0.25, ry * 0.56, sd * 0.08, 0, TAU);
      x.fillStyle = css(bunny.top); x.strokeStyle = fur; x.lineWidth = R * 0.035; x.fill(); x.stroke();
      x.beginPath(); x.ellipse(0, -ry * 0.48, R * 0.12, ry * 0.42, sd * 0.08, 0, TAU);
      x.fillStyle = inner; x.fill();
      x.restore();
    }
    x.restore();
  },
  base(l) {
    const { x, R, body } = l;
    const dx = shiftX(l);
    x.save();
    x.clip(body);
    x.fillStyle = "rgba(255,150,180,.3)";
    for (const sd of [-1, 1]) { x.beginPath(); x.ellipse(dx + sd * R * 0.78, R * 0.4, R * 0.2, R * 0.12, 0, 0, TAU); x.fill(); }
    ellipseFill(x, dx * 0.9, R * 0.5, R * 0.34, R * 0.26, "#fff7f9");        // muzzle
    x.restore();
  },
  front(l) {
    const { x, R, rx, ry, t, think, listen, happy, love, cheer } = l;
    const dx = shiftX(l);
    const twitch = Math.sin(t * (think > 0.05 ? 22 : 9)) * R * (0.008 + 0.012 * think);
    x.save();
    x.translate(dx * 0.9, R * 0.34 + twitch);
    x.fillStyle = "#ef7d9f";                                                  // a heart-shaped nose
    x.beginPath(); x.ellipse(0, 0, R * 0.08, R * 0.055, 0, 0, TAU); x.fill();
    x.strokeStyle = "#b86b83"; x.lineWidth = R * 0.03; x.lineCap = "round";   // the little Y of the mouth
    x.beginPath(); x.moveTo(0, R * 0.05); x.lineTo(0, R * 0.12); x.moveTo(-R * 0.1, R * 0.19); x.quadraticCurveTo(-R * 0.02, R * 0.14, 0, R * 0.12); x.quadraticCurveTo(R * 0.02, R * 0.14, R * 0.1, R * 0.19); x.stroke();
    if (happy || love > 0.3 || cheer > 0.3) {                                 // two front teeth
      x.fillStyle = "#fff"; x.strokeStyle = "rgba(150,100,120,.4)"; x.lineWidth = R * 0.02;
      for (const sd of [-1, 1]) { x.beginPath(); x.rect(sd > 0 ? R * 0.005 : -R * 0.075, R * 0.17, R * 0.07, R * 0.11); x.fill(); x.stroke(); }
    }
    x.restore();
    const hello = l.hello ?? 0;
    if (hello > 0.01) helloArm(l, { sx: rx * 0.86, sy: ry * 0.45, a0: -1.05, speed: 14, swing: 0.36, len: 0.7, w: 0.25, col: css(bunny.bottom) });
    void listen;
  },
};

// ── Panda ───────────────────────────────────────────────────────────────────────
// Chill and a bit sleepy. White eyes on dark patches, round ears, slow moves. It chews on a bamboo
// shoot while it thinks, hugs itself (rocking) when it is fond of you, rolls over to celebrate and
// yawns on its own every so often. Its hello is a big, slow wave.
const panda: CharDef = {
  label: "Panda", blurb: "Sleepy, chews bamboo, rolls when done",
  top: [1, 1, 1], bottom: [0.9, 0.9, 0.94],
  ink: "#ffffff", tintScale: 0.6,
  eyes: { working: "pill", thinking: "pill" },
  persona: {
    helloLen: 1.8, helloTilt: [0.04, 4, 0.03],
    hello: { sy: [[1.05, 500, "inOut"], [1.05, 800, "lin"], [1, 500, "inOut"]], sx: [[0.96, 500, "inOut"], [0.96, 800, "lin"], [1, 500, "inOut"]] },
    helloFx: [[1100, "glint", 2]],
    love: { sy: [[0.95, 450, "inOut"]] }, loveSway: [1, 0.06],
    glint: 5, loveGlint: true,
    cheer: { sy: [[0.84, 160, "out"], [1.06, 200, "out"], [1, 300, "back"]], sx: [[1.12, 160, "out"], [0.97, 200, "out"], [1, 300, "back"]] },
    cheerRoll: 1, cheerFx: [[300, "dust", 3], [900, "glint", 3]],
    workGaze: (t) => [Math.sin(t * 0.5) * 0.12, 0.28],
    work: { sy: 0.97, bob: [0.01, 5] },
    quirk: "yawn", quirkEvery: [9, 18], blinkMs: [120, 200],
  },
  behind(l) {
    const { x, R, rx, ry, t, sad, listen, love, hello: h0, state } = l;
    const hello = h0 ?? 0, sleeping = state === "sleeping";
    x.save();
    for (const sd of [-1, 1]) {
      const rot = sad || sleeping ? sd * 0.25 : sd * 0.1 * love - sd * 0.1 * listen + (sd === 1 ? Math.sin((l.helloT ?? 0) * 12) * 0.1 * hello : 0) + (sd === -1 ? Math.sin(t * 1.4) * 0.02 : 0);
      x.save();
      x.translate(sd * rx * 0.72, -ry * 0.7);
      x.rotate(rot);
      x.beginPath(); x.arc(0, -R * 0.08, R * 0.32, 0, TAU);
      x.fillStyle = "#23232b"; x.strokeStyle = "rgba(190,190,230,.55)"; x.lineWidth = R * 0.04;
      x.fill(); x.stroke();
      x.restore();
    }
    x.restore();
  },
  base(l) {
    const { x, R, body } = l;
    x.save();
    x.clip(body);
    const eyes = l.eyes ?? [];
    for (const e of eyes) {                                                   // dark patches that follow the eyes
      x.save();
      x.translate(e.ex, e.ey + R * 0.04);
      x.rotate(e.sd * 0.38);
      x.beginPath(); x.ellipse(0, 0, R * 0.36 * Math.max(0.5, e.fx), R * 0.46 * Math.max(0.5, e.fy), 0, 0, TAU);
      x.fillStyle = "#23232b"; x.fill();
      x.restore();
    }
    x.restore();
  },
  front(l) {
    const { x, R, rx, ry, t, think, love, cheer, state } = l;
    const dx = shiftX(l);
    const chew = think > 0.05 ? Math.abs(Math.sin(t * 7)) * think : 0;
    x.save();
    x.translate(dx * 0.9, 0);
    x.fillStyle = "#23232b";
    x.beginPath(); x.ellipse(0, R * 0.34, R * 0.1, R * 0.07, 0, 0, TAU); x.fill();
    x.strokeStyle = "#23232b"; x.lineWidth = R * 0.03; x.lineCap = "round";
    x.beginPath(); x.moveTo(0, R * 0.4); x.lineTo(0, R * 0.46);
    if (chew > 0.05) { x.ellipse(0, R * 0.5, R * 0.09, R * 0.04 + R * 0.05 * chew, 0, 0, TAU); }
    else { x.moveTo(-R * 0.1, R * 0.5); x.quadraticCurveTo(-R * 0.05, R * 0.54, 0, R * 0.46); x.quadraticCurveTo(R * 0.05, R * 0.54, R * 0.1, R * 0.5); }
    x.stroke();
    x.restore();
    // bamboo, held in the right paw while it works things out
    if (think > 0.05) {
      x.save();
      x.globalAlpha = Math.min(1, think * 1.5);
      x.translate(R * 0.62, R * 0.74 + Math.sin(t * 7) * R * 0.012);
      x.rotate(-0.25);
      x.lineCap = "round";
      x.strokeStyle = "#7fbf5a"; x.lineWidth = R * 0.14;
      x.beginPath(); x.moveTo(0, 0); x.lineTo(R * 0.12, -R * 0.62); x.stroke();
      x.strokeStyle = "#4f9a3a"; x.lineWidth = R * 0.035;
      for (const k of [0.25, 0.55]) { x.beginPath(); x.moveTo(-R * 0.08 + R * 0.12 * k, -R * 0.62 * k); x.lineTo(R * 0.08 + R * 0.12 * k, -R * 0.62 * k); x.stroke(); }
      x.fillStyle = "#6fb04c";
      x.beginPath(); x.ellipse(R * 0.26, -R * 0.55, R * 0.18, R * 0.07, -0.5, 0, TAU); x.fill();
      x.restore();
    }
    // paws: down at its sides, up around its cheeks when it is fond of you
    const hug = Math.max(love, cheer * 0.5 * (state === "finished" ? 1 : 0));
    for (const sd of [-1, 1]) {
      const px = sd * lerp(rx * 0.8, R * 0.5, hug), py = lerp(ry * 0.62, R * 0.32, hug) + (hug > 0.05 ? Math.sin(t * 3 + sd) * R * 0.015 * hug : 0);
      x.fillStyle = "#23232b"; x.strokeStyle = "rgba(190,190,230,.45)"; x.lineWidth = R * 0.03;
      x.beginPath(); x.ellipse(px, py, R * 0.2, R * 0.27, sd * lerp(0.45, -0.5, hug), 0, TAU); x.fill(); x.stroke();
    }
    if ((l.hello ?? 0) > 0.01) helloArm(l, { sx: rx * 0.84, sy: ry * 0.4, a0: -1.0, speed: 7, swing: 0.3, len: 0.84, w: 0.3, col: "#23232b", rim: "rgba(190,190,230,.45)" });
  },
};


// ── Hollow Knight ───────────────────────────────────────────────────────────────
// A pale horned mask, two big black eyes and a little black cloak. Calm and quiet at first. Poke it and it
// draws its nail (the sword): every poke makes it angrier: brows drop, the horns flare, the body shakes and
// glows ember-red, the swings get faster and double up. Left alone it slowly cools down and sheathes the nail.
// (The rage itself lives in mochiBot.ts: provoke(). This file only draws it.)
const KN_MASK = "#f3f2f8", KN_SHADE = "rgba(120,120,160,.28)", KN_VOID = "#0d0d14", KN_RIM = "rgba(150,150,195,.6)";
const easeOutQ = (k: number) => 1 - (1 - k) * (1 - k);
const easeInQ = (k: number) => k * k;
const easeInOutQ = (k: number) => (k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2);

/** The nail: a plain pale blade with a short crossguard, held at the right. `out` 0..1 = how far it is drawn. */
function knightNail(l: Look, out: number): void {
  const { x, R, rx, ry, t } = l;
  const rage = l.rage ?? 0, k = l.slashP ?? -1, dir = l.slashDir ?? 1;
  const px = rx * 0.9, py = ry * 0.42;                                       // the hand
  const rest = -1.2 + Math.sin(t * (8 + 34 * rage)) * 0.03 * rage;            // raised, trembling more the angrier it is
  let th = rest;
  if (k >= 0) {
    if (k < 0.22) th = lerp(rest, -1.8, easeOutQ(k / 0.22));                   // wind-up
    else if (k < 0.55) th = lerp(-1.8, 2.3, easeInQ((k - 0.22) / 0.33));      // the chop
    else th = lerp(2.3, rest, easeInOutQ((k - 0.55) / 0.45));                 // back to guard
  }
  const sc = Math.max(0.01, out);
  // swing trail (a crescent that fades)
  if (k > 0.22 && k < 0.9) {
    const fade = k < 0.55 ? 1 : 1 - (k - 0.55) / 0.35;
    const a1 = Math.max(-1.8, th - 1.5), a2 = th;
    x.save();
    x.beginPath(); x.rect(-R * 3, -R * 3, R * 6, R * 4.3); x.clip();          // never reaches the canvas edge (it would show as a flat red line)
    x.lineCap = "round";
    x.translate(px, py);
    x.scale(dir, 1);                                                          // (mirrored swing: pivot stays at the hand)
    x.strokeStyle = `rgba(255,${Math.round(190 - 110 * rage)},${Math.round(150 - 110 * rage)},${0.28 * fade})`;
    x.lineWidth = R * 0.26;
    x.beginPath(); x.arc(0, 0, R * 1.0 * sc, a1, a2); x.stroke();
    x.strokeStyle = `rgba(255,255,255,${0.85 * fade})`;
    x.lineWidth = R * 0.07;
    x.beginPath(); x.arc(0, 0, R * 1.02 * sc, a1, a2); x.stroke();
    x.restore();
  }
  x.save();
  x.translate(px, py);
  x.scale(dir, 1);
  x.rotate(th);
  x.lineJoin = "round"; x.lineCap = "round";
  // handle (dark, wrapped)
  x.strokeStyle = KN_VOID; x.lineWidth = R * 0.13;
  x.beginPath(); x.moveTo(-R * 0.34 * sc, 0); x.lineTo(R * 0.02, 0); x.stroke();
  x.strokeStyle = "rgba(190,190,230,.55)"; x.lineWidth = R * 0.02;
  for (const w of [-0.26, -0.18, -0.1]) { x.beginPath(); x.moveTo(R * w * sc, -R * 0.06); x.lineTo(R * w * sc + R * 0.03, R * 0.06); x.stroke(); }
  // guard
  x.fillStyle = KN_MASK; x.strokeStyle = KN_RIM; x.lineWidth = R * 0.025;
  x.beginPath(); x.rect(-R * 0.045, -R * 0.2, R * 0.09, R * 0.4); x.fill(); x.stroke();
  // blade
  const L = R * 1.4 * sc;
  x.beginPath();
  x.moveTo(R * 0.04, -R * 0.075); x.lineTo(L * 0.92, -R * 0.055); x.lineTo(L, 0); x.lineTo(L * 0.92, R * 0.055); x.lineTo(R * 0.04, R * 0.075); x.closePath();
  x.fillStyle = "#fbfbff"; x.fill(); x.stroke();
  if (rage > 0.15) {                                                          // the edge glows ember-red as it gets angry
    x.strokeStyle = `rgba(255,${Math.round(150 - 90 * rage)},60,${Math.min(1, rage * 1.1)})`;
    x.lineWidth = R * (0.03 + 0.04 * rage);
    x.beginPath(); x.moveTo(R * 0.1, 0); x.lineTo(L * 0.96, 0); x.stroke();
  }
  x.restore();
  // the hand over the handle
  x.beginPath(); x.arc(px, py, R * 0.15, 0, TAU);
  x.fillStyle = KN_VOID; x.fill();
  x.strokeStyle = KN_RIM; x.lineWidth = R * 0.025; x.stroke();
}

const knight: CharDef = {
  label: "Knight", blurb: "Pale horned mask. Poke him and he draws his nail",
  top: [0.965, 0.96, 0.98], bottom: [0.8, 0.8, 0.86],
  ink: KN_VOID, tintScale: 0.3, float: 0.012,
  eyes: { idle: "wide", thinking: "wide", working: "wide", searching: "wide" },
  persona: {
    helloLen: 1.5, helloTilt: [0.05, 3, 0.02],
    hello: { sy: [[0.96, 260, "out"], [1, 420, "back"]] },       // a small, formal bow
    glint: -1,
    cheer: { sy: [[0.9, 150, "out"], [1.04, 200, "out"], [1, 280, "back"]], sx: [[1.06, 150, "out"], [0.98, 200, "out"], [1, 280, "back"]] },
    cheerFx: [[350, "glint", 2]],
    workGaze: (t) => [Math.sin(t * 0.45) * 0.1, -0.02],
    work: { sy: 0.985, bob: [0.008, 4] },
    quirk: "sideglance", quirkEvery: [8, 16], blinkMs: [110, 170],
  },
  behind(l) {
    const { x, R, rx, ry, t, sad, listen, state } = l;
    const rage = l.rage ?? 0;
    const droop = sad || state === "sleeping" ? 0.3 : 0;
    // ember aura: it only shows when he is properly angry (kept inside the canvas like the cloud's glow)
    if (rage > 0.3) {
      const SR = R * 1.4;
      const g = x.createRadialGradient(0, 0, R * 0.5, 0, 0, SR);
      g.addColorStop(0, `rgba(255,90,40,${0.34 * (rage - 0.25) * (0.88 + 0.12 * Math.sin(t * 17))})`);
      g.addColorStop(1, "rgba(255,90,40,0)");
      x.fillStyle = g; x.beginPath(); x.arc(0, 0, SR, 0, TAU); x.fill();
    }
    // the cloak, peeking out under the mask
    x.save();
    x.fillStyle = KN_VOID; x.strokeStyle = KN_RIM; x.lineWidth = R * 0.03;
    const sw = Math.sin(t * 2.2) * R * 0.012;
    x.beginPath();
    x.moveTo(-rx * 0.66, ry * 0.5);
    x.quadraticCurveTo(-rx * 0.74, ry * 1.1 + sw, -rx * 0.4, ry * 1.1);
    x.lineTo(rx * 0.4, ry * 1.1 - sw);
    x.quadraticCurveTo(rx * 0.74, ry * 1.1 - sw, rx * 0.66, ry * 0.5);
    x.closePath(); x.fill(); x.stroke();
    x.restore();
    // the horns: long, curved outward. They lean back (flare) when he is angry and droop when he is sad.
    for (const sd of [-1, 1]) {
      x.save();
      x.translate(sd * rx * 0.4, -ry * 0.76);
      x.scale(sd, 1);
      x.rotate(0.2 + 0.2 * rage + 0.06 * listen - droop + Math.sin(t * 1.3 + sd) * 0.015 + Math.sin(t * 40) * 0.015 * rage);
      x.beginPath();
      x.moveTo(-R * 0.15, R * 0.06);
      x.bezierCurveTo(-R * 0.08, -R * 0.4, R * 0.02, -R * 0.8, R * 0.36, -R * 1.12);             // inner edge up to the tip
      x.bezierCurveTo(R * 0.62, -R * 0.72, R * 0.5, -R * 0.2, R * 0.2, R * 0.06);                 // outer edge bulges out and back down
      x.closePath();
      x.fillStyle = KN_MASK; x.fill();
      x.strokeStyle = "rgba(110,110,150,.85)"; x.lineWidth = R * 0.045; x.lineJoin = "round"; x.stroke();
      x.restore();
    }
  },
  base(l) {
    const { x, R, rx, ry, body } = l;
    const rage = l.rage ?? 0;
    x.save();
    x.clip(body);
    // soft shade under the cheeks and a hairline crack down the forehead
    const g = x.createLinearGradient(0, ry * 0.2, 0, ry);
    g.addColorStop(0, "rgba(120,120,160,0)"); g.addColorStop(1, KN_SHADE);
    x.fillStyle = g; x.fillRect(-rx, ry * 0.2, rx * 2, ry * 0.9);
    x.strokeStyle = "rgba(120,120,160,.5)"; x.lineWidth = R * 0.025; x.lineCap = "round";
    const dx = Math.sin(l.yaw) * l.rx * 0.8;
    x.beginPath(); x.moveTo(dx * 0.9, -ry * 0.98); x.lineTo(dx * 0.9 + R * 0.025, -ry * 0.62); x.lineTo(dx * 0.9 - R * 0.015, -ry * 0.42); x.stroke();
    if (rage > 0.05) {                                                        // ember flush on the mask
      x.fillStyle = `rgba(255,80,40,${0.3 * rage})`;
      x.fillRect(-rx, -ry, rx * 2, ry * 2);
    }
    x.restore();
  },
  front(l) {
    const { x, R, ry } = l;
    const rage = l.rage ?? 0;
    // angry brows: a mask-coloured lid slants down toward the nose over each eye, with a dark edge
    if (rage > 0.02) {
      const a = Math.min(1, rage * 3.2);
      x.save();
      x.clip(l.body);
      for (const e of l.eyes ?? []) {
        const W = R * 0.34 * Math.max(0.5, e.fx), H = R * 0.4 * Math.max(0.5, e.fy);
        const inner = -e.sd;                                                  // toward the middle of the face
        const ox = e.ex - inner * W, ix = e.ex + inner * W, top = e.ey - H;
        x.beginPath();
        x.moveTo(ox - inner * R * 0.1, top - R * 0.12); x.lineTo(ix + inner * R * 0.1, top - R * 0.12);
        x.lineTo(ix + inner * R * 0.1, top + a * 0.62 * R); x.lineTo(ox - inner * R * 0.1, top + a * 0.06 * R);
        x.closePath();
        x.fillStyle = KN_MASK; x.fill();
        x.strokeStyle = KN_VOID; x.lineWidth = R * (0.035 + 0.03 * rage); x.lineCap = "round";
        x.beginPath(); x.moveTo(ox - inner * R * 0.1, top + a * 0.06 * R); x.lineTo(ix + inner * R * 0.1, top + a * 0.62 * R); x.stroke();
        if (rage > 0.55) {                                                    // a glowing sliver in the eye when he is furious
          x.fillStyle = `rgba(255,${Math.round(150 - 80 * rage)},50,${(rage - 0.5) * 1.6})`;
          x.beginPath(); x.ellipse(e.ex, e.ey + H * 0.1, R * 0.07 * e.fx, R * 0.045 * e.fy, 0, 0, TAU); x.fill();
        }
      }
      x.restore();
      x.save(); x.strokeStyle = `rgba(255,90,40,${0.55 * rage})`; x.lineWidth = R * 0.06; x.stroke(l.body); x.restore();
    }
    const out = Math.min(1, rage * 6);                                        // the first poke already draws it
    if (out > 0.02) knightNail(l, out);
    if ((l.hello ?? 0) > 0.01) helloArm(l, { sx: l.rx * 0.84, sy: ry * 0.4, a0: -1.0, speed: 8, swing: 0.28, len: 0.8, w: 0.26, col: KN_VOID, rim: KN_RIM });
  },
};

export const CHAR_DEFS: Record<string, CharDef> = { cloud, fox, cat, bunny, panda, knight };

/** The characters you can pick. "mochi" (the old blob) is only kept for the little agent bots, never as a pick. */
export const CHARACTER_IDS = ["cloud", "fox", "cat", "bunny", "panda", "knight"] as const;
