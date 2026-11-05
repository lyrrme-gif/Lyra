// Lyra mascot character: the v1.0 Mochi (Canvas 2D, see mochiBot.ts).
// Implements the same `Character` interface as before, so engine.ts, overlay.ts, native.ts,
// the mascot settings (size, monitor, follow, ...) and the Rust side are untouched.

import { BotEngine } from "./mochiBot";
import type { BotEmoteName, BotStateName } from "../core/layout";

export type EmoteName =
  "idle"|"happy"|"think"|"talk"|"listen"|"surprised"|"sad"|"sleep"|"wave"|"point"|"celebrate"|"error"|"dizzy"
  | "love"|"wink"|"tired"|"focus"|"hot"|"thirsty"|"ring"|"lowbat"|"munch"|"shy"|"grumpy"|"sneeze"|"dance"|"gaming";

export interface Character {
  el: HTMLElement;                       // root, about 120x120 px, anchor = bottom centre
  setEmote(name: EmoteName): void;
  look(dx: number, dy: number): void;    // -1..1, where the eyes point
  setMouth(level: number): void;         // 0..1 speech loudness
  setFacing(dir: -1 | 1): void;
  setMotion(speed: number): void;        // px/s, drives squash/stretch and walk bob
  setActive?(on: boolean): void;         // false = stop drawing (docked / hidden)
  setShape?(name: string): void;        // "cloud" (default), "fox" or "cat"
  setVibing?(on: boolean): void;         // true while music plays: grooving to the beat
  provoke?(level: number): void;         // the Knight: poke number `level` in a row (1..6): angrier each time, nail out, swings
  destroy(): void;
}

export interface CharacterOptions {
  /** Size the element to the body only (no empty margin), for the island notch and the subtitles.
   *  The host then sets the width; the height follows the aspect ratio. */
  tight?: boolean;
  /** Extra render resolution (1 = device pixels). The settings previews draw the Mochi larger than it is on screen. */
  sharp?: number;
}

/* ── Mascot emote → Mochi state / emote ────────────────────────────────────────────── */
interface Look { state: BotStateName; emote?: BotEmoteName }
const LOOKS: Record<EmoteName, Look> = {
  idle:      { state: "idle" },
  happy:     { state: "idle", emote: "happy" },
  think:     { state: "thinking" },
  talk:      { state: "idle" },                       // speech level pulses the body (see draw)
  listen:    { state: "question" },
  surprised: { state: "idle", emote: "surprised" },
  sad:       { state: "ratelimit" },                  // tired eyes + sweat drop
  sleep:     { state: "sleeping" },
  wave:      { state: "idle" },                       // Hello: each character's own hello() (cloud / fox / cat), repeated while waving
  point:     { state: "approval" },
  celebrate: { state: "finished", emote: "proud" },   // each character celebrates its own way, repeated while celebrating
  error:     { state: "error" },
  dizzy:     { state: "dizzy" },
  love:      { state: "idle", emote: "love" },        // content eyes; purr / wag / sunrise glow, depending on the character
  wink:      { state: "idle", emote: "wink" },
  tired:     { state: "idle", emote: "yawn" },        // droopy eyes, a yawn every few seconds
  focus:     { state: "focus" },                      // determined line eyes
  hot:       { state: "hot" },                        // red, trembling, sweating
  thirsty:   { state: "thirsty" },                    // cyan, droopy, water drops
  ring:      { state: "ringing" },                    // buzzing, green "!" badge
  lowbat:    { state: "lowbat" },                     // slumped and grey
  munch:     { state: "idle", emote: "happy" },       // squashes in time with chewing
  shy:       { state: "idle", emote: "happy" },       // blushing, head tilted (see pose)
  grumpy:    { state: "idle", emote: "annoyed" },     // puffed up, line eyes
  sneeze:    { state: "idle" },                       // scripted: wind-up, achoo, shake
  dance:     { state: "idle" },                       // groove + notes
  gaming:    { state: "focus" },                      // determined, holding a controller
};

/* ── Layout: the character box is 120x120, the canvas bleeds past it so hearts, stars, ──
   hands and the badge are never clipped. ───────────────────────────────────────────── */
const BOX = 120;
const PAD_X = 60;          // extra room left / right: the angry tools (needle, nail, flame, bamboo) reach about 3 R sideways and swing to either side
const OVERHANG = 60;       // extra room above (particles fly up)
const UNDER = 40;          // extra room below the body: the angry tools (Grimm's flame, the nail, the bamboo) swing down there
// Where the Mochi body sits inside the 120x120 box (logical units), used by `tight` mode.
const TIGHT = { x: 18, y: 29, w: 84, h: 66 };

/* ── Shared animation loop (one rAF for all characters) ───────────────────────────── */
import { isReallyVisible, onVisibilityChange } from "../core/visibility";
import { FRAME_MS } from "../core/fps";
import { isEco } from "../core/eco";
import { EyeWander } from "./eyeWander";

interface Impl {
  step(dt: number): void;
  isActive(): boolean;
  /** Minimum ms between redraws: 0 = every display frame, larger = capped (idle / tiny Mochis). */
  interval(): number;
  acc: number;
}
const live = new Set<Impl>();
let raf = 0, tmr = 0, lastTs = 0, tmrDue = 0, inFrame = false;
function frame(ts: number) {
  raf = 0; tmr = 0; inFrame = true;
  const raw = Math.min(0.25, (ts - lastTs) / 1000 || 0.016);
  lastTs = ts;
  let any = false, wait = Infinity;
  for (const c of live) {
    if (!c.isActive()) continue;
    any = true;
    c.acc += raw;
    const iv = c.interval() / 1000;
    if (c.acc >= iv - 0.004) { c.step(Math.min(c.acc, 0.12)); c.acc = 0; }   // idle redraws get the whole elapsed time
    wait = Math.min(wait, Math.max(0, iv - c.acc) * 1000);
  }
  inFrame = false;
  // Park the loop when nothing is drawing (docked / hidden); setActive(true) kicks it again.
  if (!any || !isReallyVisible()) return;
  // Everyone is capped: sleep on a timer until the next redraw is due instead of waking at 60 Hz.
  if (wait > 24) { tmrDue = performance.now() + wait - 2; tmr = window.setTimeout(() => frame(performance.now()), wait - 2); }
  else raf = requestAnimationFrame(frame);
}
/** ms until the soonest character wants its next redraw. */
function nextWait(): number {
  let w = Infinity;
  for (const c of live) if (c.isActive()) w = Math.min(w, Math.max(0, c.interval() - c.acc * 1000));
  return w;
}
function kick() {
  if (inFrame || raf || !isReallyVisible()) return;   // inFrame: frame() re-arms itself at its end; a second loop here doubles the animation speed
  if (tmr) {
    // A timer is already pending. Pulling it forward to a rAF on every call (look()/setMouth() fire on every
    // cursor event) turned the capped idle loop into a display-rate one: keep it unless it is too late.
    if (tmrDue - performance.now() <= nextWait() + 6) return;
    window.clearTimeout(tmr); tmr = 0;
  } else lastTs = performance.now();
  raf = requestAnimationFrame(frame);
}

onVisibilityChange((v) => { if (v) kick(); });

const mq = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;
let reducedMq = !!mq?.matches;
mq?.addEventListener?.("change", (e) => { reducedMq = e.matches; });
/** Reduced motion (Windows setting) only. Eco mode does NOT slow the Mochis: it only stops the eye follow (see look()). */
const isReduced = () => reducedMq;

const CSS = `
.lyra-char{position:relative;display:block;width:120px;height:120px;user-select:none;-webkit-user-select:none;contain:layout style}
.lyra-char canvas{position:absolute;display:block;pointer-events:none}
`;
function injectCss() {
  if (document.getElementById("lyra-char-css")) return;
  const s = document.createElement("style");
  s.id = "lyra-char-css";
  s.textContent = CSS;
  document.head.appendChild(s);
}

const clamp = (v: number, lo: number, hi: number) => (v < lo ? lo : v > hi ? hi : v);

/** Eye direction (-1..1, +y = up) from a character's on-screen centre toward a point given in the
 *  same coordinate space as getBoundingClientRect(). Shared by the notch and the subtitles so both
 *  use the same sign and range (positive Mochi pitch moves the eyes UP, so y is flipped). */
export function gazeToward(el: HTMLElement, x: number, y: number): [number, number] {
  const r = el.getBoundingClientRect();
  const dx = (x - (r.left + r.width / 2)) / 150;
  const dy = ((r.top + r.height / 2) - y) / 110;
  return [Math.tanh(dx * 1.5), Math.tanh(dy * 1.5)];
}

export function createCharacter(host: HTMLElement, opts: CharacterOptions = {}): Character {
  injectCss();
  const el = document.createElement("div");
  el.className = "lyra-char";
  // The box the element occupies, in the 120-unit logical space the Mochi is drawn in.
  const box = opts.tight ? TIGHT : { x: 0, y: 0, w: BOX, h: BOX };
  if (opts.tight) { el.style.width = "100%"; el.style.height = "auto"; el.style.aspectRatio = `${box.w} / ${box.h}`; }

  // Render at the device resolution so the canvas is not needlessly resampled by WebView2.
  // The backing store is sized to what is actually shown (layout px x devicePixelRatio), never larger:
  // a 58 px wide notch Mochi no longer carries a 200 px canvas. `sharp` only matters until the first measure.
  const cw = BOX + 2 * PAD_X, chDraw = BOX + OVERHANG, ch = chDraw + UNDER;   // chDraw: the height the Mochi is centred in (unchanged); ch: the canvas, with UNDER more room below
  let k = Math.max(1, window.devicePixelRatio || 1) * Math.max(1, opts.sharp ?? 1);
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(cw * k);
  canvas.height = Math.round(ch * k);
  const pct = (v: number) => `${(v * 100).toFixed(3)}%`;
  canvas.style.left = pct((-PAD_X - box.x) / box.w);
  canvas.style.top = pct((-OVERHANG - box.y) / box.h);
  canvas.style.width = pct(cw / box.w);
  canvas.style.height = pct(ch / box.h);
  el.appendChild(canvas);
  host.appendChild(el);
  const ctx = canvas.getContext("2d")!;
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";

  /** Re-size the backing store to the displayed size. Skipped while hidden (offsetWidth 0) or when the change is tiny. */
  function fit() {
    const shown = canvas.offsetWidth;                         // layout px, unaffected by animated ancestor transforms
    if (!shown) return;
    const nk = Math.max(0.5, Math.round((window.devicePixelRatio || 1) * (shown / cw) * 8) / 8);
    if (Math.abs(nk - k) < 0.01) return;
    k = nk;
    canvas.width = Math.round(cw * k);                        // resizing clears the canvas; the next frame redraws
    canvas.height = Math.round(ch * k);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    if (active) kick();
  }
  let fitTimer = 0;
  const fitSoon = () => { window.clearTimeout(fitTimer); fitTimer = window.setTimeout(fit, 120); };
  const ro = typeof ResizeObserver === "function" ? new ResizeObserver(fitSoon) : null;
  ro?.observe(canvas);
  window.addEventListener("resize", fitSoon);               // devicePixelRatio changes (moving between monitors)

  const bot = new BotEngine();
  bot.particleOverhang = OVERHANG;

  // State
  let emote: EmoteName = "idle";
  let lvT = 0, lvE = 0;          // speech level (target / eased)
  let facing: 1 | -1 = 1;
  let motionT = 0, motionE = 0;  // 0..1
  let t = 0, walkPh = 0;
  let nextBurst = 0;
  const wander = new EyeWander();   // a resting Mochi whose eye target has not changed for a few seconds glances around by itself
  let active = true;
  let busyUntil = 0;     // full rate until this time (one-shot animations and particles need to finish)
  const poke = (sec = 2.5) => { busyUntil = Math.max(busyUntil, t + sec); kick(); };

  function sneeze() {
    bot.triggerEmote("yawn", 1.4);                       // wind-up: head back, eyes squeeze shut
    window.setTimeout(() => {
      if (emote !== "sneeze") return;
      bot.squash();
      bot.emit("spark", 7);
      bot.emit("sweat", 1);
    }, 650);
  }

  function apply(name: EmoteName) {
    const look = LOOKS[name];
    bot.resetEyes();                                     // a leftover "happy" from a wave must not hide e.g. the dizzy spirals
    bot.setPermanentEmote(look.emote ?? null);
    bot.setState(look.state, true);
    bot.pose = name === "shy" ? "shy" : name === "grumpy" ? "grumpy" : "";
    bot.setDance(name === "dance");
    bot.setController(name === "gaming");
    if (look.emote) bot.triggerEmote(look.emote, 1.8);
    nextBurst = t + 1.8;
    if (name === "wave") bot.hello();
    if (name === "sneeze") sneeze();
    if (name === "munch") nextBurst = t + 0.3;
  }
  apply("idle");

  function step(dt: number) {
    if (!active) return;
    t += dt;
    const mo = isReduced() ? 0 : 1;

    // Idle wandering: only a plain resting Mochi (not talking, waving, eating, sleeping ...) and never with reduced motion.
    const glance = wander.tick(t, emote === "idle" && !!mo, bot.lookX, bot.lookY);
    // No poke()/kick() here: step() already runs inside the frame loop, and kick() from in there started an extra loop on every glance.
    if (glance) { bot.lookX = glance[0]; bot.lookY = glance[1]; busyUntil = Math.max(busyUntil, t + 1.2); }

    // repeat the one-shot animations while the emote lasts
    if (emote === "wave" && mo && t >= nextBurst) { bot.hello(); nextBurst = t + 2.2; }
    if (mo && t >= nextBurst) {
      switch (emote) {
        case "wink": bot.triggerEmote("wink", 1.2); nextBurst = t + 2.6; break;
        case "tired": bot.triggerEmote("yawn", 1.8); nextBurst = t + 4.5; break;
        case "love": bot.triggerEmote("love", 1.8); nextBurst = t + 2.4; break;
        case "sneeze": sneeze(); nextBurst = t + 4.6; break;
        case "munch": bot.squash(); bot.emit("spark", 1); nextBurst = t + 0.45; break;
        default: break;
      }
    }
    if (emote === "celebrate" && mo && t >= nextBurst) {
      bot.celebrate();
      nextBurst = t + 3.4;
    }

    // Music: the groove pauses while the body is busy talking / waving / being dragged.
    bot.calm = isReduced();
    bot.grooveHold = emote === "talk" || emote === "wave" || emote === "celebrate" || emote === "surprised";

    lvE += (lvT - lvE) * (1 - Math.exp(-30 * dt));
    motionE += (motionT - motionE) * (1 - Math.exp(-8 * dt));
    if (motionE > 0.02) walkPh += dt * (6 + 10 * motionE);

    // Large steps (capped / idle redraws) are integrated in small slices so the springs stay stable.
    const n = Math.max(1, Math.ceil(dt / 0.034));
    for (let i = 0; i < n; i++) bot.update(dt / n);

    // ── draw ──
    ctx.setTransform(k, 0, 0, k, 0, 0);
    ctx.clearRect(0, 0, cw, ch);
    ctx.save();
    ctx.translate(PAD_X, 0);

    // pivot = bottom centre of the body, so speech pulse / walk squash plant on the "ground"
    const px = BOX / 2, py = OVERHANG + BOX * 0.78;
    const w = Math.abs(Math.sin(walkPh));
    const hop = w * 4 * motionE * (isReduced() ? 0.5 : 1);
    const speech = lvE * mo;
    const sy = (1 + 0.07 * speech) * (1 + 0.1 * motionE * (w - 0.5));
    const sx = (1 - 0.04 * speech) * Math.pow(1 + 0.1 * motionE * (w - 0.5), -0.6);
    ctx.translate(px, py - hop);
    if (motionE > 0.05) ctx.rotate(facing * 0.07 * motionE * mo);
    ctx.scale(sx, sy);
    ctx.translate(-px, -py);

    bot.draw(ctx, BOX, chDraw);
    ctx.restore();
  }

  /** Every visible Mochi redraws at ~60 fps (FRAME_MS); only reduced-motion slows it down. */
  function interval(): number {
    if (isReduced()) return 100;
    return FRAME_MS;
  }
  // A character in a tab that is not showing (Settings builds them all), docked, or scrolled away kept drawing at 60 Hz
  // because only its owner could switch it off. It now also checks for itself that it is on screen. Fails open:
  // until the observer has reported once, the character counts as visible.
  let onScreen = true;
  const io = typeof IntersectionObserver !== "undefined"
    ? new IntersectionObserver((es) => {
        const on = es[es.length - 1].isIntersecting;
        if (on !== onScreen) { onScreen = on; if (on) poke(); }
      })
    : null;
  io?.observe(el);
  const impl: Impl = { step, isActive: () => active && onScreen, interval, acc: 0 };
  live.add(impl);
  step(0);
  kick();

  return {
    el,
    setEmote(name) {
      if (name === emote || !(name in LOOKS)) return;
      emote = name;
      apply(name);
      poke();
    },
    look(dx, dy) {
      if (isEco()) { if (bot.lookX || bot.lookY) { bot.lookX = 0; bot.lookY = 0; poke(0.7); } return; }   // Eco mode: no eye follow
      const x = clamp(dx, -1, 1), y = clamp(dy, -1, 1);
      if (!wander.see(x, y, t)) return;       // the same cursor spot again while wandering: keep glancing around
      if (Math.abs(x - bot.lookX) + Math.abs(y - bot.lookY) > 0.04) poke(0.7);   // eyes ease toward the target
      bot.lookX = x; bot.lookY = y;
    },
    setMouth(level) { lvT = clamp(level, 0, 1); if (lvT > 0) kick(); },
    setFacing(dir) { if (dir !== facing) poke(1); facing = dir; },
    setMotion(speed) { motionT = clamp(speed / 420, 0, 1); if (motionT > 0) kick(); },
    setActive(on) { active = on; if (on) poke(); },
    setVibing(on) { bot.setVibing(on); poke(); },
    setShape(name) { bot.setShape(name); poke(); },
    provoke(level) { bot.provoke(level); poke(4); window.setTimeout(() => poke(0.8), 1300); window.setTimeout(() => poke(0.8), 4300); },
    destroy() { live.delete(impl); io?.disconnect(); ro?.disconnect(); window.removeEventListener("resize", fitSoon); window.clearTimeout(fitTimer); el.remove(); },
  };
}
