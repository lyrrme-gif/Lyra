// Phase 6: the start-up sequence (about 1.5 s), played once when Lyra launches.
//
//   0 ms     the island opens out of the notch      (the island's own spring: fsm.reveal())
//   0-450    Mochi sleeps, eyes shut                (notchMochi emote "sleep")
//   250-900  a soft glow fades in behind the pill, a thin light sweeps along its lower edge
//   450      Mochi wakes: a small ripple leaves it  (emote "surprised", then "happy" at 750)
//   900-1500 the glow fades out; the pill is left as the ordinary compact pill
//   1500     everything this file added is removed; the island goes on exactly as without it
//
// It never moves or resizes the island. Everything it draws is a separate element behind the
// island (opacity / transform only), so there is nothing to undo if it is skipped.
//
// Skip: Escape or a click anywhere. Reduced motion (OS setting, or Lyra's own Motion setting): not played at all.
// There is no switch for it: it always plays unless the system asks for reduced motion.
//
// frameAt() is a pure function of time: the live animation and the docs/p6_preview frames use
// the same code, so what is approved is what plays.

import { COMPACT_W, NOTCH_H } from "../core/layout";
import type { EmoteName } from "../mascot/character";

export const DURATION_MS = 1500;
const SKIP_FADE_MS = 140;

// ── The sequence as data ────────────────────────────────────────────────────────────────────

export interface Frame {
  /** glow behind the pill, 0..1 */
  glow: number;
  /** glow size, 1 = natural */
  glowScale: number;
  /** thin light along the lower edge: opacity and horizontal position (-1 left .. 1 right of the pill) */
  rim: number;
  rimX: number;
  /** ripple that leaves Mochi when it wakes: opacity and scale */
  ring: number;
  ringScale: number;
  /** what Mochi does */
  emote: EmoteName;
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
const easeOut = (p: number) => 1 - Math.pow(1 - clamp01(p), 3);
const easeInOut = (p: number) => {
  const q = clamp01(p);
  return q < 0.5 ? 4 * q * q * q : 1 - Math.pow(-2 * q + 2, 3) / 2;
};
const span = (t: number, a: number, b: number) => clamp01((t - a) / (b - a));

/** Everything that is on screen `t` milliseconds into the sequence. */
export function frameAt(t: number): Frame {
  const up = easeOut(span(t, 250, 900));
  const down = easeInOut(span(t, 900, DURATION_MS));
  const sweep = span(t, 200, 1100);
  const ring = span(t, 450, 1150);
  return {
    glow: Math.max(0, up - down),
    glowScale: 0.72 + 0.42 * easeOut(span(t, 200, DURATION_MS)),
    rim: Math.sin(Math.PI * sweep) * 0.9,
    rimX: -1 + 2 * easeInOut(sweep),
    ring: ring > 0 && ring < 1 ? 0.85 * (1 - ring) : 0,
    ringScale: 1 + 1.4 * easeOut(ring),
    emote: t < 450 ? "sleep" : t < 750 ? "surprised" : t < DURATION_MS ? "happy" : "idle",
  };
}

// ── Collaborators (set by notchMochi.ts and fsm.ts) ─────────────────────────────────────────

export interface StartupMascot {
  /** Mochi is on screen in the notch (the Mascot feature is on and nothing hides it) */
  visible(): boolean;
  setEmote(name: EmoteName): void;
}
let mascot: StartupMascot | null = null;
export function attachMascot(m: StartupMascot): void {
  mascot = m;
}

let running: Sequence | null = null;
let played = false;

/** True while the sequence owns Mochi's mood (notchMochi must not override it). */
export function active(): boolean {
  return running !== null;
}

/** Ends the sequence now (short fade). Escape and clicks call this. */
export function skip(): void {
  running?.skip();
}

// ── Setting ─────────────────────────────────────────────────────────────────────────────────

export function reducedMotion(): boolean {
  try {
    if (window.localStorage.getItem("lyra-d-motion") === "reduce") return true;   // Settings -> Appearance -> Motion, or "reduce motion" by voice
  } catch { /* storage unavailable */ }
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

// ── Drawing ─────────────────────────────────────────────────────────────────────────────────

const CSS = `
#lyra-startup,#lyra-startup-front{position:absolute;left:50%;top:0;width:${COMPACT_W + 232}px;height:150px;margin-left:-${(COMPACT_W + 232) / 2}px;
  pointer-events:none;overflow:visible;contain:layout style}
#lyra-startup>*,#lyra-startup-front>*{position:absolute;opacity:0;will-change:opacity,transform}
#lyra-startup .ls-glow{left:50%;width:460px;height:170px;margin:-85px 0 0 -230px;border-radius:50%;
  background:radial-gradient(closest-side,rgba(150,215,255,.80) 0%,rgba(132,160,255,.46) 46%,rgba(188,140,255,.20) 74%,rgba(188,140,255,0) 100%);
  filter:blur(8px)}
#lyra-startup .ls-halo{left:50%;width:${COMPACT_W + 28}px;height:${NOTCH_H + 12}px;margin:-${(NOTCH_H + 12) / 2}px 0 0 -${(COMPACT_W + 28) / 2}px;
  border-radius:24px;box-shadow:0 0 22px 4px rgba(150,212,255,.80),0 0 54px 14px rgba(150,140,255,.38)}
#lyra-startup .ls-rim{width:104px;height:2px;margin:-1px 0 0 -52px;border-radius:2px;
  background:linear-gradient(90deg,rgba(190,235,255,0),rgba(228,248,255,1),rgba(190,235,255,0));filter:blur(.5px)}
#island.lyra-startup[data-mode="compact"] #notch-mochi:not(.off):not(.away){opacity:1!important;visibility:visible!important}
#island.lyra-startup #spotify-compact,#island.lyra-startup #volume-hud,#island.lyra-startup #timer-chip,#island.lyra-startup #notify-toast{opacity:0!important}
#lyra-startup-front .ls-ring{width:34px;height:34px;margin:-17px 0 0 -17px;border-radius:50%;
  border:1.5px solid rgba(190,232,255,.9);box-sizing:border-box}
`;
interface Layout {
  top: number; // island top edge inside the overlay (0 attached, 18 floating)
  mochiX: number; // Mochi's x relative to the overlay centre
  mochiY: number | null; // Mochi's centre y inside the overlay (null: use the pill centre)
}

class Sequence {
  private readonly el: HTMLElement;
  private readonly front: HTMLElement;
  private readonly glow: HTMLElement;
  private readonly halo: HTMLElement;
  private readonly rim: HTMLElement;
  private readonly ring: HTMLElement;
  private raf = 0;
  private t0 = 0;
  private skipFrom: { at: number; glow: number } | null = null;
  private lastEmote: EmoteName | null = null;
  private readonly cleanup: (() => void)[] = [];

  constructor(private readonly island: HTMLElement) {
    const style = document.createElement("style");
    style.id = "lyra-startup-css";
    style.textContent = CSS;
    document.head.appendChild(style);
    this.el = document.createElement("div");
    this.el.id = "lyra-startup";
    this.el.setAttribute("aria-hidden", "true");
    this.front = document.createElement("div");
    this.front.id = "lyra-startup-front";
    this.front.setAttribute("aria-hidden", "true");
    this.glow = this.child(this.el, "ls-glow");
    this.halo = this.child(this.el, "ls-halo");
    this.rim = this.child(this.el, "ls-rim");
    this.ring = this.child(this.front, "ls-ring");
    // Glow and light are BEHIND the island (they never cover the pill or its content); only the thin
    // ripple that leaves Mochi is drawn in front.
    island.parentElement?.insertBefore(this.el, island);
    island.parentElement?.insertBefore(this.front, island.nextSibling);
    // While it plays, Mochi is the only thing in the pill (music, timer, volume ... fade back in after).
    island.classList.add("lyra-startup");
    this.cleanup.push(() => style.remove(), () => this.el.remove(), () => this.front.remove(), () => island.classList.remove("lyra-startup"));

    const onKey = (e: KeyboardEvent) => e.key === "Escape" && this.skip();
    const onClick = () => this.skip();
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("pointerdown", onClick, true);
    this.cleanup.push(
      () => document.removeEventListener("keydown", onKey, true),
      () => document.removeEventListener("pointerdown", onClick, true),
    );
  }

  private child(parent: HTMLElement, cls: string): HTMLElement {
    const d = document.createElement("div");
    d.className = cls;
    parent.appendChild(d);
    return d;
  }

  start(): void {
    this.t0 = performance.now();
    const tick = (now: number) => {
      const t = now - this.t0;
      if (this.skipFrom) {
        const p = clamp01((now - this.skipFrom.at) / SKIP_FADE_MS);
        this.paint({ ...frameAt(DURATION_MS), glow: this.skipFrom.glow * (1 - p), emote: "idle" });
        if (p >= 1) return this.finish();
      } else {
        this.paint(frameAt(t));
        if (t >= DURATION_MS) return this.finish();
      }
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }

  skip(): void {
    if (this.skipFrom) return;
    const now = performance.now();
    this.skipFrom = { at: now, glow: frameAt(now - this.t0).glow };
  }

  private layout(): Layout {
    const r = this.island.getBoundingClientRect();
    const o = this.el.getBoundingClientRect();
    const oc = o.left + o.width / 2;
    // Follow the real Mochi element: it sits in the middle of the pill now (notchMochi.ts: left 50%, top 50%).
    // The old code assumed 12% from the left edge, so the ripple left from the wrong place.
    const m = document.getElementById("notch-mochi")?.getBoundingClientRect();
    if (m && m.width > 0) {
      return { top: r.top - o.top, mochiX: m.left + m.width / 2 - oc, mochiY: m.top + m.height / 2 - o.top };
    }
    return { top: r.top - o.top, mochiX: r.left + r.width / 2 - oc, mochiY: null };
  }

  /** Apply one frame. Pure styling: opacity and transform only. */
  paint(f: Frame): void {
    const lay = this.layout();
    const cy = Math.max(0, lay.top) + NOTCH_H / 2;
    const mochiHere = mascot?.visible() ?? false;
    this.glow.style.top = `${cy}px`;
    this.glow.style.opacity = String(f.glow);
    this.glow.style.transform = `scale(${f.glowScale})`;
    this.halo.style.top = `${cy}px`;
    this.halo.style.opacity = String(f.glow);
    this.halo.style.transform = `scale(${0.92 + 0.08 * clamp01((f.glowScale - 0.72) / 0.42)})`;
    this.rim.style.top = `${Math.max(0, lay.top) + NOTCH_H}px`;
    this.rim.style.left = "50%";
    this.rim.style.opacity = String(f.rim);
    this.rim.style.transform = `translateX(${f.rimX * (COMPACT_W / 2 - 40)}px)`;
    this.ring.style.top = `${lay.mochiY ?? cy}px`;
    this.front.style.top = "0";
    this.ring.style.left = `calc(50% + ${mochiHere ? lay.mochiX : 0}px)`;
    this.ring.style.opacity = String(f.ring);
    this.ring.style.transform = `scale(${f.ringScale})`;
    if (f.emote !== this.lastEmote) {
      this.lastEmote = f.emote;
      mascot?.setEmote(f.emote);
    }
  }

  private finish(): void {
    cancelAnimationFrame(this.raf);
    mascot?.setEmote("idle");
    for (const fn of this.cleanup.splice(0)) fn();
    running = null;
  }
}

// ── Entry point (called from fsm.launch) ────────────────────────────────────────────────────

export interface RunOptions {
  /** The FSM may let the animation play. It always does: nothing on the island stops it. */
  canPlay: () => boolean;
  /** Open the island (fsm.reveal): the pill springs out of the notch. */
  reveal: () => void;
}

/** Play the start-up sequence if it is on and allowed. Returns true if it started. Once per launch. */
export function run(opts: RunOptions): boolean {
  if (played || running) return false;
  played = true;
  if (reducedMotion() || !opts.canPlay()) return false;
  const island = document.getElementById("island");
  if (!island) return false;
  try {
    const seq = new Sequence(island);
    running = seq;
    opts.reveal();
    seq.start();
    return true;
  } catch {
    running = null;
    return false; // never get in the way of a normal launch
  }
}
