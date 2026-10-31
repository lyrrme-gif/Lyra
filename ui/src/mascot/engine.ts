// Movement engine: spring-damper follow, walking to targets, idle behaviour,
// reactions to Lyra's state, dragging and the mascot event commands.
// It only talks to the Character interface, so Step 2 can swap the art freely.

import { createCharacter, type Character, type EmoteName } from "./character";
import { native, savePref, type MonitorMode, type Rect } from "./native";
import type { Overlay } from "./overlay";
import { emitAway, emitMochiSound } from "./dock";
import { emitCarryState, emitNestQuery, type CarryStart, type NestInfo } from "./carry";
import { MOCHI_KINDS, mochiMode } from "../core/mochiSound";
import { CHAR_DEFS, RAGE_LINES } from "./characters";
import { isReallyVisible, onVisibilityChange } from "../core/visibility";

export type SizeName = "small" | "medium" | "large";
export const SIZE_PX: Record<SizeName, number> = { small: 64, medium: 84, large: 112 };

export interface DrawSpec { shape: string; x0: number; y0: number; x1: number; y1: number }   // 0-1 fractions of the screen
export interface TourStep { x: number; y: number; text?: string; ms?: number; emote?: EmoteName; draw?: DrawSpec }
export interface MascotCmd {
  cmd: string; text?: string; ms?: number; x?: number; y?: number; label?: string;
  name?: string; on?: boolean; steps?: TourStep[];
  /** "cursor": "click" | "scroll" | "hide" (x, y are 0-1 fractions of the screen, label is a short caption). */
  kind?: string;
}

const FOLLOW = { k: 70, maxV: 1700 };
const WALK = { k: 10, maxV: 460 };
const WANDER = { k: 6, maxV: 110 };
// The hand follows the marker stroke: stiff and fast so the pen stays on the line.
const PEN = { k: 70, maxV: 1100 };
const WANDER_AFTER_MS = 20_000;
// Walk down from the notch to the subtitles: faster than a normal walk, the trip is long.
const TRAVEL = { k: 14, maxV: 760 };
// Subtitle pill bottom edge above the monitor bottom: caption.rs BOTTOM_GAP (64) + caption.css #stage bottom (14).
const CAPTION_BOTTOM = 78;
// Pills get replaced (heard -> thinking -> answer) without a gap; only leave after a real pause.
const CAPTION_LEAVE_MS = 700;
// The Mochi lives in the island notch and only walks out to explain something on screen.
// HOME_Y is where its bottom anchor sits so the (shrunk) body lands in the middle of the notch.
const HOME_Y = 40;
const DOCK_BODY_PX = 30;      // body width of the Mochi inside the notch (see notchMochi.ts)
const BODY_W_FRAC = 0.684;    // body width / character box width
const AWAY_BEAT_MS = 4000;
// Dragging the Mochi between the island and the screen (see carry.ts).
const CARRY = { k: 90, maxV: 2600 };         // follows the cursor a little tighter than the old follow
const BODY_CENTRE = 58 / 120;                // the body's centre sits this far (of the box) above the bottom anchor
const CARRY_FAILSAFE_MS = 25_000;            // a carry whose release never arrived is let go after this long
const ZONE_MARGIN = 18;                      // how far outside the island's shape still counts as "on the island"
const NOTCH_ZONE = { halfW: 170, h: 84 };    // the notch area: counts while the island is hidden or only a notch
const HOP_MS = 560;                          // the little bounce when it is put down on the screen
const SLEEP_AFTER_MS = 180_000;
const EMOTES = new Set<string>([
  "idle", "happy", "think", "talk", "listen", "surprised", "sad", "sleep", "wave", "point", "celebrate", "error", "dizzy",
  "love", "wink", "tired", "focus", "hot", "thirsty", "ring", "lowbat", "munch", "shy", "grumpy", "sneeze", "dance", "gaming",
]);

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const num0 = (v: unknown, d: number) => (typeof v === "number" && isFinite(v) ? v : d);
const wait = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms));

/** Loudness 0..1 of the backend's speech envelope at wall-clock `nowMs`. */
function envLevel(levels: number[], hz: number, startMs: number, nowMs: number): number {
  const pos = ((nowMs - startMs) / 1000) * hz;
  if (pos < 0 || pos > levels.length - 1) return 0;
  const i = Math.floor(pos);
  const a = levels[i] ?? 0;
  const b = levels[i + 1] ?? a;
  return a + (b - a) * (pos - i);
}

function simLevel(t: number): number {
  const phrase = 0.5 + 0.5 * Math.sin(t * 1.3);
  const syl = Math.pow(0.5 + 0.5 * Math.sin(t * 9.5 + Math.sin(t * 2.1) * 2), 1.5);
  return Math.min(1, (0.15 + 0.85 * syl) * (0.55 + 0.45 * phrase));
}

export class Engine {
  private actor: HTMLDivElement;
  private scaler: HTMLDivElement;
  private size = SIZE_PX.medium;
  private sizeName: SizeName = "medium";
  private W = window.innerWidth;
  private H = window.innerHeight;
  private screenKey = "";

  private pos = { x: 0, y: 0 };      // anchor = bottom centre of the character
  private vel = { x: 0, y: 0 };
  private cursor = { x: -1, y: -1, seen: false };
  // Reject isolated coordinate jumps without making normal gaze changes wait
  // for several future cursor samples.
  private cursorSamples: Array<{ x: number; y: number }> = [];
  private lastMoveAt = performance.now();

  private gaming = false;
  private music = false;
  private asleep = false;
  private forcedSleep = false;
  private suspended = false;
  private dead = false;

  private walk: { x: number; y: number; params: { k: number; maxV: number } } | null = null;
  private walkDone: (() => void) | null = null;
  private runId = 0;                  // bumps on every script start/stop: cancels older scripts
  private scripted = false;
  private nextWanderAt = 0;

  private scriptEmote: { name: EmoteName; until: number } | null = null;
  private knightRage = 0;
  private knightLast = 0;
  private stateEmote: { name: EmoteName; until: number } | null = null;
  private shownEmote: EmoteName | null = null;
  private lyraState = "idle";

  private env: { levels: number[]; hz: number; startMs: number } | null = null;
  private speakingSince = 0;
  private mouth = 0;

  private caption: { w: number; h: number; lift: number } | null = null; // subtitle pill on screen (size)
  private atCaption = false;                                 // the Mochi is parked beside it
  private captionLeaveTimer = 0;
  private keepOutTimer = 0;
  private keepOut = false;                                 // after an explanation: stays on screen until sent home
  private forceHome = false;                               // an explicit "go home" beats stay-on-screen
  private stay = false;                                      // "Mochi stays on screen": never docks by itself

  private dragging = false;
  // Put down on the screen by the user: it stays there until it is dragged back onto the island (or sent home).
  private placed: { x: number; y: number } | null = null;
  // Held in the cursor's hand after being pulled out of the island's Mochi tab (the release comes from Rust).
  private carry: { gx: number; gy: number; pop: number; pop0: number; since: number } | null = null;
  // A flight back into the island: eased, with an arc, ending in dock().
  private flight: { t0: number; dur: number; arc: number; from: { x: number; y: number; p: number }; to: { x: number; y: number; p: number }; done: () => void } | null = null;
  private nest: (NestInfo & { at: number }) | null = null;
  private overZone = false;        // held above the island: it will land there
  private lastNestQ = 0;
  private hop = 0;                 // when it was put down (the bounce), 0 = none
  private pop = 1;                 // the scale last applied (1 = full size, small = shrunk into the notch)
  private away = false;           // true while the Mochi is out of the notch
  private awayBeat = 0;
  private homeTimer = 0;
  private facing: 1 | -1 = 1;
  private lastActive = false;
  private lastRects = "";
  private lastRectAt = 0;
  private raf = 0;
  private timer = 0;
  private fun = 0;
  private last = performance.now();
  private dockTick = 0; // docked in the notch: nothing animates, so work at ~10 fps instead of 60

  private ch: Character;

  constructor(stage: HTMLElement, private ov: Overlay) {
    this.actor = document.createElement("div");
    this.actor.className = "mc-actor";
    this.scaler = document.createElement("div");
    this.scaler.className = "mc-scale";
    this.ch = createCharacter(this.scaler); // the character mounts itself inside the scaler
    this.actor.appendChild(this.scaler);
    stage.appendChild(this.actor);
    this.applySize();
    this.actor.style.display = "none"; // docked in the island: the island draws it
    this.ch.setActive?.(false);
    this.pos = this.homePos();
    this.bindPointer();
    onVisibilityChange(() => this.schedule());
    this.schedule();
  }

  // ── Public API ────────────────────────────────────────────────────────────
  setScreen(w: number, h: number, key = `${w}x${h}`): void {
    if (w <= 0 || h <= 0) return;
    const changed = w !== this.W || h !== this.H || key !== this.screenKey;
    if (!changed) return;
    if (w !== this.W || h !== this.H) {
      this.pos.x = (this.pos.x / this.W) * w;
      this.pos.y = (this.pos.y / this.H) * h;
    }
    this.W = w;
    this.H = h;
    this.screenKey = key;
    // Cursor events are window-relative. A monitor change invalidates the last
    // sample even when both displays use the same resolution.
    this.cursor.seen = false;
    this.cursorSamples = [];
    this.vel = { x: 0, y: 0 };
    if (this.walk) { this.walk = null; this.walkDone?.(); }
  }

  setPrefs(sizeName: string | undefined, stay?: boolean): void {
    const s = (sizeName && sizeName in SIZE_PX ? sizeName : "medium") as SizeName;
    this.sizeName = s;
    if (SIZE_PX[s] !== this.size) { this.size = SIZE_PX[s]; this.applySize(); }
    if (!this.scripted) this.setMonitor("island");
    if (typeof stay === "boolean") this.setStay(stay);
  }

  /** "Stay on screen" setting, or kept out after an explanation. */
  private stays(): boolean { return this.stay || this.keepOut || !!this.placed; }

  /** Stay on screen: the Mochi comes out and parks by the subtitles; off: it goes back to the notch. */
  setStay(on: boolean): void {
    if (on === this.stay) return;
    this.stay = on;
    if (this.suspended || this.dragging) return;
    if (on) { if (!this.away && !this.scripted) void this.goPark(); }
    else if (this.away && !this.atCaption && !this.scripted && !this.keepOut && !this.placed) void this.comeHome();
  }

  setCursor(x: number, y: number): void {
    // During a monitor switch the native window can move between cursor samples.
    // Ignore coordinates calculated against its previous origin; those can send
    // the gaze to a corner for a frame (or keep it there while the cursor rests).
    if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || y < 0 || x >= this.W || y >= this.H) {
      // A handoff can produce a brief sample against the previous window origin.
      // Ignore it without resetting gaze: snapping to center between valid
      // samples makes the eyes appear to bounce back against the cursor.
      return;
    }
    const previous = this.cursor;
    // A huge one-frame reversal is usually a stale sample during monitor
    // handoff. Require a second nearby sample to confirm it; ordinary movement
    // is accepted immediately.
    if (previous.seen && Math.hypot(x - previous.x, y - previous.y) > Math.min(this.W, this.H) * 0.42) {
      const pending = this.cursorSamples[this.cursorSamples.length - 1];
      this.cursorSamples = [{ x, y }];
      if (!pending || Math.hypot(x - pending.x, y - pending.y) > 36) return;
    } else {
      this.cursorSamples = [];
    }
    const filtered = { x, y };
    const moved = Math.hypot(filtered.x - this.cursor.x, filtered.y - this.cursor.y) > 3;
    this.cursor = { ...filtered, seen: true };
    if (!moved) return;
    this.lastMoveAt = performance.now();
    if (this.asleep && !this.forcedSleep) this.wake();
    // Mouse moved while idling: stop wandering.
    if (this.walk && this.walk.params === WANDER) { this.walk = null; this.walkDone?.(); }
  }

  /** Subtitle pill appeared / changed / went away (from the caption window). */
  setCaption(c: { visible: boolean; w: number; h: number }): void {
    window.clearTimeout(this.captionLeaveTimer);
    if (!c.visible) {
      if (!this.caption) return;
      this.captionLeaveTimer = window.setTimeout(() => this.leaveCaption(), CAPTION_LEAVE_MS);
      return;
    }
    if (this.suspended) return;
    this.caption = { w: Math.max(40, c.w), h: Math.max(30, c.h), lift: Math.max(0, c.lift ?? 0) };
    if (this.atCaption) { this.holdAtCaption(); return; }
    if (this.scripted || this.carry || this.flight) return; // a tour / goto / point / drag owns the Mochi right now
    void this.goCaption();
  }

  /** Music started / stopped (relayed from the island, see dock.ts): a little groove and floating notes. */
  setMusic(on: boolean): void {
    if (on === this.music) return;               // the island repeats "playing" as a heartbeat
    this.music = on;
    this.ch.setVibing?.(on);
    if (on && this.asleep && !this.forcedSleep) this.wake();
  }

  /** A game (Valorant …) is in front: the Mochi plays with a controller and never takes the mouse. */
  setGaming(on: boolean): void {
    if (on === this.gaming) return;
    this.gaming = on;
    if (on) { this.ov.hideBubble(); this.ov.clearPoint(); this.ov.closeMenu(); this.scriptEmote = null; }
  }

  /** Settings → Mochi → Character (cloud, fox, cat, bunny, panda or knight). */
  setShape(name: string | undefined): void {
    this.shapeId = name ?? "cloud";
    this.ch.setShape?.(this.shapeId);
  }
  private shapeId = "cloud";

  setSuspended(on: boolean): void {
    this.suspended = on;
    if (on) { this.ov.hideBubble(); this.ov.clearPoint(); this.ov.closeMenu(); }
    else this.last = performance.now();
    this.schedule();
  }

  /** Lyra's state from the backend: idle | listening | thinking | speaking | executing | success | error | waiting. */
  setLyraState(state: string): void {
    const now = performance.now();
    this.lyraState = state;
    if (state !== "idle" && state !== "waiting") this.wake();
    switch (state) {
      case "listening": this.stateEmote = { name: "listen", until: 0 }; break;
      case "thinking":
      case "executing": this.stateEmote = { name: "think", until: 0 }; break;
      case "speaking":
        this.stateEmote = { name: "talk", until: 0 };
        this.speakingSince = Date.now();
        this.env = null;
        break;
      case "success": this.stateEmote = { name: "happy", until: now + 2200 }; break;
      case "error": this.stateEmote = { name: "error", until: now + 2800 }; break;
      default: this.stateEmote = null;
    }
    if (state !== "speaking") this.env = null;
  }

  /** The backend's "wave" event: loudness envelope for the current sentence. */
  feedWave(levels: number[], hz: number, startMs: number): void {
    if (this.lyraState !== "speaking") return;
    if (startMs < this.speakingSince - 3000) return;
    this.env = levels.length && hz > 0 ? { levels, hz, startMs } : null;
  }

  /** One mascot event from the backend (see the event contract in the plan). */
  command(c: MascotCmd): void {
    if ((this.carry || this.flight) && c.cmd !== "cursor") return;   // in the user's hand (or flying in): not now
    const num = (v: unknown, d: number) => (typeof v === "number" && isFinite(v) ? v : d);
    switch (c.cmd) {
      case "say": this.wake(); void this.doSay(String(c.text ?? ""), num(c.ms, 4000)); break;
      case "goto": this.wake(); void this.doGoto(num(c.x, 0.5), num(c.y, 0.5)); break;
      case "point":
        this.wake();
        void this.doPoint(num(c.x, 0.5), num(c.y, 0.5), String(c.label ?? ""), num(c.ms, 3000));
        break;
      case "emote": {
        const n = String(c.name ?? "");
        if (EMOTES.has(n)) { this.wake(); this.scriptEmote = { name: n as EmoteName, until: performance.now() + Math.max(200, num(c.ms, 3000)) }; }
        break;
      }
      case "cursor": this.doCursor(c); break;
      case "home": if (this.away) { this.wake(); void this.doHome(); } break;
      case "tour": this.wake(); void this.doTour(Array.isArray(c.steps) ? c.steps : []); break;
      case "stop": this.ov.hideCursor(); this.stop(); if (this.away && !this.stays()) void this.comeHome(); break;
    }
  }

  destroy(): void {
    if (this.carry) void native.carry(false);
    this.dead = true;
    cancelAnimationFrame(this.raf);
    window.clearTimeout(this.timer);
    window.clearInterval(this.fun);
    this.runId++;
    this.ch.destroy();
    this.actor.remove();
  }

  // ── Dragging between the island and the screen (see carry.ts) ─────────────
  /** The island's shape and where its Mochi tab keeps the Mochi (answer to our query, see queryNest). */
  setNest(n: NestInfo): void { this.nest = { ...n, at: performance.now() }; }

  private queryNest(): void { this.lastNestQ = performance.now(); emitNestQuery(); }
  private freshNest(): NestInfo | null {
    return this.nest && performance.now() - this.nest.at < 4000 ? this.nest : null;
  }

  /** Is (x, y) on the island? Its real shape when the island told us, and always the notch area. */
  private overIsland(x: number, y: number): boolean {
    const n = this.freshNest();
    if (n && n.rect.w > 0 && n.rect.h > 0) {
      const ox = this.W / 2 - n.winW / 2;
      const m = ZONE_MARGIN;
      if (x >= ox + n.rect.x - m && x <= ox + n.rect.x + n.rect.w + m && y >= n.rect.y - m && y <= n.rect.y + n.rect.h + m) return true;
    }
    return Math.abs(x - this.W / 2) <= NOTCH_ZONE.halfW && y <= NOTCH_ZONE.h;
  }

  /** The user pulled the Mochi out of the island's Mochi tab: it hops out of the tab into the cursor's hand. */
  carryStart(c: CarryStart): void {
    if (this.suspended || this.gaming || this.dead) { emitCarryState({ end: "island" }); return; }   // nowhere to put it down
    window.clearTimeout(this.homeTimer);
    window.clearTimeout(this.keepOutTimer);
    this.cancelFlight();
    this.stop();
    this.atCaption = false;
    this.keepOut = false;
    this.placed = null;
    this.setMonitor("island");
    // Starts exactly where the Mochi sits in the tab (island coordinates -> this window's), at the tab's size.
    const pop0 = clamp(c.body / (BODY_W_FRAC * this.size), 0.4, 3);
    this.pos = { x: this.W / 2 - c.winW / 2 + c.fx, y: c.fy + pop0 * this.size * BODY_CENTRE };
    this.vel = { x: 0, y: 0 };
    if (!this.away) {
      this.away = true;
      this.schedule();
      this.actor.style.display = "";
      this.ch.setActive?.(true);
      this.awayBeat = performance.now();
      emitAway(true);
    }
    this.setPop(pop0);
    this.carry = { gx: c.gx, gy: c.gy, pop: pop0, pop0, since: performance.now() };
    this.dragging = true;
    this.actor.classList.add("dragging");
    this.wake();
    this.lastMoveAt = performance.now();
    this.shownEmote = null;
    this.queryNest();
    void native.carry(true);     // Rust reports where the button is let go ("mascot-drop") and raises this window
  }

  /** The button was let go (Rust), at (x, y) in this window; outside the screen = wherever it was last. */
  carryDrop(x: number, y: number): void {
    const c = this.carry;
    if (!c) return;
    this.carry = null;
    void native.carry(false);
    this.dragging = false;
    this.actor.classList.remove("dragging");
    const ok = Number.isFinite(x) && Number.isFinite(y) && x >= 0 && y >= 0 && x < this.W && y < this.H;
    if (ok) this.cursor = { x, y, seen: true };
    this.releaseMochi(this.cursor.seen ? this.cursor.x : this.pos.x, this.cursor.seen ? this.cursor.y : this.pos.y);
  }

  /** Let go of the Mochi (from either direction): over the island it flies in, anywhere else it stays on the screen. */
  private releaseMochi(x: number, y: number): void {
    this.overZone = false;
    if (this.overIsland(x, y)) this.dockIn();
    else this.placeHere();
  }

  /** Put down on the screen: a little bounce, and it stays there until it is dragged back onto the island. */
  private placeHere(): void {
    const s = this.size;
    this.pos.x = clamp(this.pos.x, s / 2, this.W - s / 2);
    this.pos.y = clamp(this.pos.y, s, this.H);
    this.placed = { x: this.pos.x, y: this.pos.y };
    this.keepOut = false;
    this.vel = { x: 0, y: 0 };
    this.hop = performance.now();
    this.lastMoveAt = performance.now();
    this.scriptEmote = { name: "happy", until: performance.now() + 1500 };
    emitCarryState({ end: "screen" });
  }

  /** Flies into the island: into the Mochi tab where its Mochi sits, or else into the notch; then it docks. */
  private dockIn(): void {
    const n = this.freshNest();
    let to: { x: number; y: number; p: number };
    if (n?.spot) {
      const p = clamp(n.spot.body / (BODY_W_FRAC * this.size), 0.4, 3);
      to = { x: this.W / 2 - n.winW / 2 + n.spot.cx, y: n.spot.cy + p * this.size * BODY_CENTRE, p };
    } else {
      const h = this.homePos();
      to = { x: h.x, y: h.y, p: clamp(DOCK_BODY_PX / (BODY_W_FRAC * this.size), 0.3, 0.9) };
    }
    const dist = Math.hypot(to.x - this.pos.x, to.y - this.pos.y);
    this.flyTo(to, clamp(280 + dist * 0.5, 340, 760), clamp(dist * 0.2, 16, 90), () => {
      this.placed = null;
      this.scripted = false;
      this.scriptEmote = null;
      this.dock();
      emitCarryState({ end: "island" });
    });
  }

  private flyTo(to: { x: number; y: number; p: number }, dur: number, arc: number, done: () => void): void {
    this.stop();
    const now = performance.now();
    this.flight = { t0: now, dur, arc, from: { x: this.pos.x, y: this.pos.y, p: this.pop }, to, done };
    this.vel = { x: 0, y: 0 };
    this.scripted = true;
    this.scriptEmote = { name: "happy", until: now + dur + 250 };
    this.setFacing(to.x >= this.pos.x ? 1 : -1);
  }

  /** A script (or a new drag) takes over in mid-flight: the Mochi stays out, so the island's tab keeps its nest. */
  private cancelFlight(): void {
    if (!this.flight) return;
    this.flight = null;
    emitCarryState({ end: "screen" });
  }

  /** Where the held Mochi's anchor goes: the cursor keeps hold of the same spot on its body while it shrinks. */
  private carryTarget(): { x: number; y: number } | null {
    const c = this.carry;
    if (!c || !this.cursor.seen) return null;
    const k = c.pop / c.pop0;
    return {
      x: clamp(this.cursor.x - c.gx * k, this.size / 2, this.W - this.size / 2),
      y: this.cursor.y - c.gy * k + c.pop * this.size * BODY_CENTRE,
    };
  }

  private stepFlight(now: number): void {
    const f = this.flight;
    if (!f) return;
    const t = clamp((now - f.t0) / f.dur, 0, 1);
    const e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;      // ease in and out
    this.pos.x = f.from.x + (f.to.x - f.from.x) * e;
    this.pos.y = f.from.y + (f.to.y - f.from.y) * e - Math.sin(Math.PI * t) * f.arc;   // a little hop on the way
    this.setPop(f.from.p + (f.to.p - f.from.p) * e);
    this.vel = { x: 0, y: 0 };
    if (t >= 1) { this.flight = null; f.done(); }
  }

  /** How high the bounce lifts it right now (px): two decaying hops after it is put down. */
  private hopLift(now: number): number {
    if (!this.hop) return 0;
    const t = (now - this.hop) / HOP_MS;
    if (t >= 1) { this.hop = 0; return 0; }
    return Math.abs(Math.sin(t * Math.PI * 2.5)) * 26 * (1 - t);
  }

  // ── Scripts (say / goto / point / tour / home) ────────────────────────────
  private begin(): number {
    window.clearTimeout(this.homeTimer);
    window.clearTimeout(this.keepOutTimer);
    this.cancelFlight();
    this.atCaption = false;
    this.leave();
    this.runId++;
    this.walk = null;
    this.walkDone?.();
    this.walkDone = null;
    this.ov.clearPoint();
    return this.runId;
  }

  /** Script finished: walk back into the notch and dock. */
  private async end(id: number): Promise<void> {
    if (id !== this.runId) return;
    if (this.stays() && this.away && !this.forceHome) {   // stays out: park by the subtitles instead of going home
      const spot = this.placed ?? this.captionSpot();      // where the user put it down, else beside the subtitles
      await this.walkTo(spot.x, spot.y, WALK);
      if (id !== this.runId) return;
      this.scripted = false;
      this.scriptEmote = null;
      this.setFacing(1);
      return;
    }
    await this.goHome(id);
    if (id !== this.runId) return;
    this.scripted = false;
    this.scriptEmote = null;
    this.dock();
  }

  /** Step out of the notch (pops out at the notch, growing as it walks away). */
  private leave(): void {
    if (this.away) return;
    this.away = true;
    this.schedule();
    const h = this.homePos();
    this.pos = { x: h.x, y: h.y };
    this.vel = { x: 0, y: 0 };
    this.awayBeat = performance.now();
    this.applyPop(0);
    this.actor.style.display = "";
    this.ch.setActive?.(true);
    emitAway(true);
  }

  /** Back in the notch: hide this copy, the island shows its own again. */
  private dock(): void {
    if (!this.away) return;
    this.away = false;
    this.schedule();
    this.actor.style.display = "none";
    this.ch.setActive?.(false);
    this.ov.clearPoint();
    this.shownEmote = null;
    emitAway(false);
  }

  private async comeHome(): Promise<void> {
    this.keepOut = false;
    this.placed = null;
    if (!this.away) return;
    const id = this.begin();
    this.scripted = true;
    this.setMonitor("island");
    this.forceHome = true;
    await wait(80);
    await this.end(id);
    this.forceHome = false;
  }

  /** Scale: shrunk to the notch size at home, full size once it is a little way out. */
  private applyPop(dist: number): void {
    const min = clamp(DOCK_BODY_PX / (BODY_W_FRAC * this.size), 0.3, 0.9);
    this.setPop(min + (1 - min) * clamp((dist - 14) / 200, 0, 1));
  }

  private setPop(pop: number): void {
    this.pop = pop;
    this.scaler.style.transform = `scale(${((this.size / 120) * pop).toFixed(3)})`;
  }

  stop(): void {
    this.runId++;
    this.walk = null;
    this.walkDone?.();
    this.walkDone = null;
    this.scripted = false;
    this.scriptEmote = null;
    this.ov.hideBubble();
    this.ov.clearPoint();
  }

  // ── Subtitles: climb down from the notch, stand beside the pill, go home when it is gone ──
  /** Where the Mochi stands: just left of the pill, body centred on the pill's height. */
  private captionSpot(): { x: number; y: number } {
    const c = this.caption ?? { w: 220, h: 46, lift: 0 };
    const s = this.size;
    const x = clamp(this.W / 2 - c.w / 2 - 8 - (s * BODY_W_FRAC) / 2, s / 2 + 6, this.W - s / 2 - 6);
    // The body's centre sits 58/120 of the box above the bottom anchor.
    const cy = this.H - CAPTION_BOTTOM - c.lift - c.h / 2;
    return { x, y: clamp(cy + s * (58 / 120), s + 6, this.H - 6) };
  }

  private async goCaption(): Promise<void> {
    const id = this.begin();      // pops out of the notch and cancels older scripts
    this.scripted = true;         // no wandering / sleeping while it keeps the subtitles company
    this.atCaption = true;        // after begin(), which clears it
    this.setMonitor("island");    // the caption lives on the island's monitor
    await wait(80);
    if (id !== this.runId) return;
    const spot = this.captionSpot();
    await this.walkTo(spot.x, spot.y, TRAVEL);
    if (id !== this.runId) return;
    this.setFacing(1);            // face the text (state emotes: think while thinking, talk + mouth while answering)
  }

  /** Pill grew / shrank / was replaced: slide to the new spot without restarting the trip. */
  private holdAtCaption(): void {
    if (!this.caption || !this.away) return;
    const spot = this.captionSpot();
    if (Math.hypot(spot.x - this.pos.x, spot.y - this.pos.y) < 4) return;
    void this.walkTo(spot.x, spot.y, WALK);
  }

  private leaveCaption(): void {
    this.caption = null;
    if (!this.atCaption) return;
    this.atCaption = false;
    if (!this.away) return;
    if (this.stays()) {                       // stays out: beside the subtitles, or back where the user put it down
      this.scripted = false; this.scriptEmote = null;
      if (this.placed) void this.walkTo(this.placed.x, this.placed.y, WALK);
      return;
    }
    void this.comeHome();
  }

  /** Stay on screen: step out of the notch and walk to the parking spot by the subtitles. */
  private async goPark(): Promise<void> {
    const id = this.begin();
    this.scripted = true;
    this.setMonitor("island");
    await wait(80);
    if (id !== this.runId) return;
    await this.end(id);
  }

  private async doSay(text: string, ms: number): Promise<void> {
    if (!text) return;
    if (!this.away) this.setMonitor("island"); // docked: the bubble drops from the notch
    this.scriptEmote = { name: "talk", until: performance.now() + ms };
    this.ov.say(text, ms);
  }

  private async doGoto(nx: number, ny: number): Promise<void> {
    const id = this.begin();
    this.scripted = true;
    this.setMonitor("island");
    await wait(80); // let Rust move the window to the island's monitor
    if (id !== this.runId) return;
    await this.walkTo(clamp(nx, 0, 1) * this.W, clamp(ny, 0, 1) * this.H, WALK);
    await this.end(id);
  }

  /** Stand beside the target (never on it), then ring + arrow + label. */
  private standBeside(tx: number, ty: number): { x: number; y: number } {
    const s = this.size;
    const left = tx - 34 - s / 2 >= s / 2 + 6;
    const x = left ? tx - 34 - s / 2 : tx + 34 + s / 2;
    const y = clamp(ty + s / 2, s + 6, this.H - 6);
    return { x: clamp(x, s / 2 + 6, this.W - s / 2 - 6), y };
  }

  private async pointAt(id: number, nx: number, ny: number, label: string, ms: number, text?: string, emote?: EmoteName): Promise<void> {
    const tx = clamp(nx, 0, 1) * this.W;
    const ty = clamp(ny, 0, 1) * this.H;
    const stand = this.standBeside(tx, ty);
    await this.walkTo(stand.x, stand.y, WALK);
    if (id !== this.runId) return;
    this.facing = tx >= this.pos.x ? 1 : -1;
    this.ch.setFacing(this.facing);
    this.scriptEmote = { name: emote ?? "point", until: performance.now() + ms };
    this.ov.showPoint(tx, ty, this.pos.x, this.pos.y - this.size / 2, label, ms);
    if (text) this.ov.say(text, ms);
    await wait(ms);
  }

  /** Explain with the hands: walk beside the thing, speak, then draw the shape with the marker
   * (the hand follows the stroke), step back and keep pointing until the step's time is up. */
  private async explainAt(id: number, d: DrawSpec, ms: number, text: string | undefined, emote: EmoteName): Promise<void> {
    const t0 = performance.now();
    const px = (v: number) => clamp(Number(v), 0, 1) * this.W;
    const py = (v: number) => clamp(Number(v), 0, 1) * this.H;
    const x0 = px(d.x0), y0 = py(d.y0), x1 = px(d.x1), y1 = py(d.y1);
    const left = Math.min(x0, x1), right = Math.max(x0, x1), bottom = Math.max(y0, y1);
    const s = this.size;
    const side = left - 40 - s / 2 >= s / 2 + 6 ? -1 : 1;                       // stand on the side with room
    const stand = {
      x: clamp(side < 0 ? left - 40 - s / 2 : right + 40 + s / 2, s / 2 + 6, this.W - s / 2 - 6),
      y: clamp(bottom, s + 6, this.H - 6),
    };
    await this.walkTo(stand.x, stand.y, WALK);
    if (id !== this.runId) return;
    this.facing = side < 0 ? 1 : -1;                                              // look toward the thing
    this.ch.setFacing(this.facing);
    this.scriptEmote = { name: emote, until: performance.now() + ms };
    if (text) this.ov.say(text, ms);
    await wait(250);
    if (id !== this.runId) return;

    const drawMs = clamp(ms * 0.4, 800, 2200);
    const pts = this.ov.drawShape(d.shape, x0, y0, x1, y1, drawMs, this.W, this.H);
    const seg = drawMs / Math.max(1, pts.length);
    for (const p of pts) {                                                        // the hand (not the feet) rides the stroke
      if (id !== this.runId) return;
      this.walk = { x: p.x - this.facing * s * 0.3, y: p.y + s * 0.35, params: PEN };
      await wait(seg);
    }
    if (id !== this.runId) return;
    await this.walkTo(stand.x, stand.y, WALK);                                    // step back; the drawing stays
    if (id !== this.runId) return;
    this.scriptEmote = { name: emote, until: performance.now() + Math.max(600, ms) };
    await wait(Math.max(0, ms - (performance.now() - t0)));
  }

  /** Lyra's own cursor while she presses things for the user (guide_act.py). It never moves the Mochi, and the
   * overlay stays click-through, so the real click goes to the app underneath. */
  private doCursor(c: MascotCmd): void {
    if (c.kind === "hide") { this.ov.hideCursor(); return; }
    if (typeof c.x !== "number" || typeof c.y !== "number" || !isFinite(c.x) || !isFinite(c.y)) return;
    this.setMonitor("cursor");   // the pointer glides there first, so this is the monitor it is on
    this.ov.showCursor(clamp(c.x, 0, 1) * this.W, clamp(c.y, 0, 1) * this.H, c.kind === "scroll" ? "scroll" : "click",
      String(c.label ?? ""), num0(c.ms, 4000));
  }

  private async doPoint(nx: number, ny: number, label: string, ms: number): Promise<void> {
    const id = this.begin();
    this.scripted = true;
    this.setMonitor("island");
    await wait(80);
    if (id !== this.runId) return;
    await this.pointAt(id, nx, ny, label, ms);
    if (id !== this.runId) return;
    this.ov.clearPoint();
    await this.end(id);
  }

  private async doTour(steps: TourStep[]): Promise<void> {
    const id = this.begin();
    this.scripted = true;
    this.setMonitor("island");
    await wait(80);
    for (const s of steps.slice(0, 12)) {
      if (id !== this.runId) return;
      const ms = clamp(typeof s.ms === "number" ? s.ms : 3500, 800, 20000);
      const emote = s.emote && EMOTES.has(s.emote) ? s.emote : "point";
      if (s.draw && isFinite(Number(s.draw.x0)) && isFinite(Number(s.draw.y0)) && isFinite(Number(s.draw.x1)) && isFinite(Number(s.draw.y1))) {
        await this.explainAt(id, s.draw, ms, s.text, emote);
      } else {
        await this.pointAt(id, Number(s.x), Number(s.y), "", ms, s.text, emote);
      }
      if (id !== this.runId) return;
      this.ov.clearPoint();
      this.ov.hideBubble();
    }
    if (id !== this.runId) return;
    if (steps.some((s) => s.draw)) {            // an explanation: the Mochi stays out on the screen, where it is
      this.keepOut = true;
      this.scripted = false;
      this.scriptEmote = null;
      this.ov.hideBubble();
      window.clearTimeout(this.keepOutTimer);          // not stuck on the screen: back to the island after a few seconds
      this.keepOutTimer = window.setTimeout(() => {
        if (this.keepOut && !this.stay && !this.placed && !this.dragging && id === this.runId) void this.comeHome();
      }, 6000);
      return;
    }
    if (!this.placed) await this.goHome(id);
    if (id !== this.runId) return;
    await this.end(id);
  }

  private async doHome(): Promise<void> {
    this.keepOut = false;
    this.placed = null;
    const id = this.begin();
    this.scripted = true;
    this.setMonitor("island");
    this.forceHome = true;
    await wait(80);
    if (id !== this.runId) return;
    await this.end(id);
    this.forceHome = false;
  }

  private async goHome(id: number): Promise<void> {
    const h = this.homePos();
    await this.walkTo(h.x, h.y, WALK);
    if (id !== this.runId) return;
    // Rest here until the cursor moves away.
  }

  private walkTo(x: number, y: number, params: { k: number; maxV: number }): Promise<void> {
    return new Promise<void>((resolve) => {
      this.walkDone?.();
      this.walk = { x, y, params };
      const timer = window.setTimeout(() => done(), 9000);
      const done = () => { window.clearTimeout(timer); if (this.walkDone === done) this.walkDone = null; resolve(); };
      this.walkDone = done;
    });
  }

  private homePos(): { x: number; y: number } {
    return { x: this.W / 2, y: HOME_Y };
  }

  private setMonitor(mode: MonitorMode): void { void native.monitor(mode); }

  private wake(): void {
    if (!this.asleep) return;
    this.asleep = false;
    this.forcedSleep = false;
    this.lastMoveAt = performance.now();
  }

  // ── Pointer interaction (only reachable while Rust has click-through off) ──
  private bindPointer(): void {
    let down: { x: number; y: number; gx: number; gy: number } | null = null;
    let moved = false;
    const taps: number[] = [];

    this.actor.addEventListener("pointerdown", (e) => {
      if (this.gaming) return;                        // never take a click away from the game
      if (e.button !== 0) return;
      this.actor.setPointerCapture(e.pointerId);
      void native.raise();             // drawn over the island too, so it can be dragged onto it
      this.queryNest();
      down = { x: e.clientX, y: e.clientY, gx: e.clientX - this.pos.x, gy: e.clientY - this.pos.y };
      moved = false;
    });
    this.actor.addEventListener("pointermove", (e) => {
      if (!down) return;
      if (!moved && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 5) {
        moved = true;
        this.dragging = true;
        this.actor.classList.add("dragging");
        this.cancelFlight();
        this.stop();
        this.wake();
      }
      if (moved) {
        this.pos.x = clamp(e.clientX - down.gx, this.size / 2, this.W - this.size / 2);
        this.pos.y = clamp(e.clientY - down.gy, this.size, this.H);
        this.vel = { x: 0, y: 0 };
      }
    });
    const up = (e: PointerEvent) => {
      if (!down) return;
      const wasDrag = moved;
      down = null;
      this.dragging = false;
      this.actor.classList.remove("dragging");
      try { this.actor.releasePointerCapture(e.pointerId); } catch { /* already released */ }
      if (wasDrag) this.releaseMochi(e.clientX, e.clientY);     // onto the island: it flies in; anywhere else: it stays there
      if (!wasDrag) {
        this.wake();
        const now = performance.now();
        taps.push(now);
        while (taps.length && now - taps[0] > 3000) taps.shift();
        if (CHAR_DEFS[this.shapeId]) {
          // No Mochi gets dizzy any more: every poke in a row (while it is still angry) makes it angrier, each in its own way (see RAGE_LINES in characters.ts).
          this.knightRage = now - this.knightLast > 4200 ? 1 : Math.min(6, this.knightRage + 1);
          this.knightLast = now;
          const lvl = this.knightRage;
          this.ch.provoke?.(lvl);
          this.scriptEmote = null;
          void mochiMode().then((m) => {
            if (m === "speak") return;
            emitMochiSound("rage");
            const line = RAGE_LINES[this.shapeId]?.[lvl - 1];
            if (line) this.ov.say(line, 1500);
          });
        } else if (taps.length >= 5) {
          taps.length = 0;
          this.scriptEmote = { name: "dizzy", until: now + 2800 };
          // Clicked over and over: the dizzy sound + a subtitle (only when Settings -> Voice allows Mochi sounds).
          void mochiMode().then((m) => {
            if (m === "speak") return;
            emitMochiSound("dizzy");
            this.ov.say(MOCHI_KINDS.dizzy.subtitle, MOCHI_KINDS.dizzy.ms);
          });
        } else if (taps.length >= 3) {
          this.scriptEmote = { name: "grumpy", until: now + 1800 };      // poked too often
        } else if (this.music) {
          this.scriptEmote = { name: "dance", until: now + 4000 };       // a click while music plays = a little dance
        } else {
          this.scriptEmote = { name: "wave", until: now + 1500 };
        }
      }
    };
    // Hover over it for a moment and it enjoys the attention (mostly love, sometimes shy).
    let petTimer = 0;
    this.actor.addEventListener("pointerenter", () => {
      window.clearTimeout(petTimer);
      petTimer = window.setTimeout(() => {
        if (this.dragging || this.scriptEmote || this.asleep || this.gaming) return;
        if (performance.now() - this.knightLast < 6000) return;   // still angry: no cuddles
        this.scriptEmote = { name: Math.random() < 0.7 ? "love" : "shy", until: performance.now() + 2600 };
      }, 1300);
    });
    this.actor.addEventListener("pointerleave", () => window.clearTimeout(petTimer));

    // Every few minutes, when it has nothing to do, it does something small and silly.
    let nextFun = performance.now() + 90_000 + Math.random() * 90_000;
    this.fun = window.setInterval(() => {
      const t = performance.now();
      if (t < nextFun || this.gaming || !this.away || this.dragging || this.asleep || this.scriptEmote || this.stateEmote || this.scripted) return;
      nextFun = t + 120_000 + Math.random() * 120_000;
      const hour = new Date().getHours();
      const late = hour >= 23 || hour < 5;
      const roll = Math.random();
      this.scriptEmote = late && roll < 0.55 ? { name: "tired", until: t + 3600 }
        : roll < 0.7 ? { name: "wink", until: t + 1500 }
        : { name: "sneeze", until: t + 2400 };
    }, 15_000);

    this.actor.addEventListener("pointerup", up);
    this.actor.addEventListener("pointercancel", up);

    this.actor.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      this.ov.openMenu(e.clientX + 4, e.clientY + 4, this.W, this.H, [
        { label: "Back to island", run: () => void this.comeHome() },
        { label: `Size: ${this.sizeName}`, run: () => {
          const order: SizeName[] = ["small", "medium", "large"];
          const next = order[(order.indexOf(this.sizeName) + 1) % order.length];
          this.setPrefs(next);
          savePref({ mascot_size: next });
        } },
        { label: `Stay on screen: ${this.stay ? "on" : "off"}`, run: () => {
          this.setStay(!this.stay);
          savePref({ feature_mascot_stay: this.stay });
        } },
        { label: "Hide", run: () => savePref({ feature_mascot: false }) },
      ], () => this.reportRects(true));
    });
  }

  // ── Frame loop ────────────────────────────────────────────────────────────
  private applySize(): void {
    this.scaler.style.transform = `scale(${this.size / 120})`;
    this.actor.style.width = `${this.size}px`;
    this.actor.style.height = `${this.size}px`;
    this.scaler.style.marginLeft = `${(this.size - 120) / 2}px`;
    this.scaler.style.marginTop = `${this.size - 120}px`;
  }

  /** The ONLY place the loop is (re)scheduled: clears any pending tick first, so two loops can never run.
   *  Away from the notch it runs on rAF; docked or suspended it ticks at ~10 fps on a timer. */
  private schedule(): void {
    cancelAnimationFrame(this.raf);
    window.clearTimeout(this.timer);
    this.raf = 0; this.timer = 0;
    if (this.dead) return;
    if (this.away && !this.suspended && isReallyVisible()) this.raf = requestAnimationFrame((n) => this.frame(n));
    else this.timer = window.setTimeout(() => this.frame(performance.now()), isReallyVisible() ? 100 : 1000);
  }

  private frame(t: number): void {
    this.raf = 0; this.timer = 0;
    this.schedule();
    if (this.suspended) { this.last = t; return; }
    if (!this.away && t - this.dockTick < 100) return;
    this.dockTick = t;
    const dt = clamp((t - this.last) / 1000, 0.001, 0.033);
    this.last = t;
    const now = performance.now();
    const idleFor = now - this.lastMoveAt;

    // Docked in the notch: nothing to move or draw here, only a speech bubble may hang below it.
    if (!this.away) {
      const hp = this.homePos();
      this.pos.x = hp.x; this.pos.y = hp.y; this.vel = { x: 0, y: 0 };
      this.ov.placeBubble(this.pos.x, this.pos.y, this.size, this.W, this.H);
      if (this.lastActive) { this.lastActive = false; void native.setActive(false); }
      this.reportRects(false);
      return;
    }
    if (now - this.awayBeat > AWAY_BEAT_MS) { this.awayBeat = now; emitAway(true); }
    if (this.carry && now - this.carry.since > CARRY_FAILSAFE_MS) this.carryDrop(-1, -1);   // the release never came

    // Idle behaviour: wander after 20 s, sleep after 3 min.
    if (!this.scripted && !this.dragging && !this.asleep && !this.music && idleFor > SLEEP_AFTER_MS) {
      this.asleep = true;
      this.walk = null; this.walkDone?.();
    }
    if (!this.stays() && !this.scripted && !this.dragging && !this.asleep && !this.walk && idleFor > WANDER_AFTER_MS && now > this.nextWanderAt) {
      this.nextWanderAt = now + 4000 + Math.random() * 4000;
      const a = Math.random() * Math.PI * 2, r = 50 + Math.random() * 90;
      const s = this.size;
      void this.walkTo(
        clamp(this.pos.x + Math.cos(a) * r, s / 2 + 6, this.W - s / 2 - 6),
        clamp(this.pos.y + Math.sin(a) * r * 0.6, s + 6, this.H - 6),
        WANDER,
      );
    }

    // Where to go.
    let target: { x: number; y: number } | null = null;
    let params = FOLLOW;
    if (this.carry) {
      target = this.carryTarget();                  // held in the cursor's hand
      params = CARRY;
    } else if (!this.dragging) {
      if (this.walk) { target = this.walk; params = this.walk.params; }
      // Cursor tracking is for gaze only. Keep the body parked unless a
      // scripted walk or idle wander explicitly gives it a target.
    }

    if (this.flight) {
      this.stepFlight(now);                         // flying into the island
    } else if (target) {
      const c = 2 * Math.sqrt(params.k) * 0.92;
      this.vel.x += (params.k * (target.x - this.pos.x) - c * this.vel.x) * dt;
      this.vel.y += (params.k * (target.y - this.pos.y) - c * this.vel.y) * dt;
      const sp = Math.hypot(this.vel.x, this.vel.y);
      if (sp > params.maxV) { this.vel.x *= params.maxV / sp; this.vel.y *= params.maxV / sp; }
      this.pos.x += this.vel.x * dt;
      this.pos.y += this.vel.y * dt;
      if (this.walk && Math.hypot(target.x - this.pos.x, target.y - this.pos.y) < 8 && sp < 40) {
        this.walk = null;
        const d = this.walkDone; this.walkDone = null; d?.();
      }
    } else if (!this.dragging) {
      const damp = Math.exp(-dt * 8);
      this.vel.x *= damp; this.vel.y *= damp;
      this.pos.x += this.vel.x * dt; this.pos.y += this.vel.y * dt;
    }
    this.pos.x = clamp(this.pos.x, this.size / 2, this.W - this.size / 2);
    this.pos.y = clamp(this.pos.y, HOME_Y, this.H);
    if (this.carry) {                               // grows from the tab's size to its normal size as it leaves
      this.carry.pop += (1 - this.carry.pop) * (1 - Math.exp(-dt * 9));
      this.setPop(this.carry.pop);
    } else if (this.flight) {
      // stepFlight set the scale
    } else if (this.placed) {                       // put down by the user: full size wherever it is (even near the notch)
      this.setPop(this.pop + (1 - this.pop) * (1 - Math.exp(-dt * 10)));
    } else {
      const h = this.homePos();
      this.applyPop(Math.hypot(this.pos.x - h.x, this.pos.y - h.y));
    }

    // Held above the island (either direction): it looks happy and the island lights up its nest.
    if (this.dragging) {
      const over = this.cursor.seen && this.overIsland(this.cursor.x, this.cursor.y);
      if (over !== this.overZone) { this.overZone = over; emitCarryState({ over }); }
      if (now - this.lastNestQ > 300) this.queryNest();   // keep the island's shape fresh while dragging
    }

    const speed = Math.hypot(this.vel.x, this.vel.y);

    // Facing: along the motion when moving, otherwise toward the cursor.
    if (Math.abs(this.vel.x) > 60) this.setFacing(this.vel.x > 0 ? 1 : -1);
    else if (this.cursor.seen && !this.scripted && Math.abs(this.cursor.x - this.pos.x) > 40) {
      this.setFacing(this.cursor.x > this.pos.x ? 1 : -1);
    }

    // Eyes follow the cursor.
    if (this.cursor.seen && !this.asleep) {
      // Normalize against the face size. The mascot sits close to the cursor while
      // following it, so a screen-sized divisor made the pupils barely move.
      // Eye centers sit at y=70/120, measured from the character's top.
      // `pos.y` is its bottom anchor, so the gaze origin is 58/120 above it.
      // A face-sized range keeps normal cursor motion clearly visible.
      const faceX = this.pos.x;
      const faceY = this.pos.y - this.size * (1 - 70 / 120);
      const gazeRange = this.size * 0.45;
      this.ch.look(
        clamp((this.cursor.x - faceX) / gazeRange, -1, 1),
        // Mochi's positive pitch moves the eyes upward in the canvas projection.
        clamp((faceY - this.cursor.y) / gazeRange, -1, 1),
      );
    } else this.ch.look(0, 0);
    this.ch.setMotion(speed);

    // Mouth: speech envelope while speaking; a gentle simulated flutter while a say-bubble is up.
    let mouthTarget = 0;
    const nowMs = Date.now();
    if (this.lyraState === "speaking") {
      if (this.env) mouthTarget = envLevel(this.env.levels, this.env.hz, this.env.startMs, nowMs);
      else mouthTarget = nowMs - this.speakingSince > 900 ? simLevel(nowMs / 1000) : 0.5;
    } else if (this.ov.speaking) mouthTarget = simLevel(nowMs / 1000) * 0.8;
    this.mouth += (mouthTarget - this.mouth) * (1 - Math.exp(-dt * (mouthTarget > this.mouth ? 28 : 10)));
    this.ch.setMouth(this.mouth);

    // Emote layers: scripted > asleep > Lyra's state > idle.
    const want = this.resolveEmote(now);
    if (want !== this.shownEmote) { this.shownEmote = want; this.ch.setEmote(want); }

    // Place everything.
    const lift = this.hopLift(now);
    this.actor.style.transform = `translate(${(this.pos.x - this.size / 2).toFixed(1)}px, ${(this.pos.y - this.size - lift).toFixed(1)}px)`;
    this.ov.placeBubble(this.pos.x, this.pos.y, this.size, this.W, this.H);

    const active = this.dragging || this.scripted || !!this.walk || speed > 8;
    if (active !== this.lastActive) { this.lastActive = active; void native.setActive(active); }
    this.reportRects(false);
  }

  private setFacing(dir: 1 | -1): void {
    if (dir === this.facing) return;
    this.facing = dir;
    this.ch.setFacing(dir);
  }

  private resolveEmote(now: number): EmoteName {
    if (this.gaming) return "gaming";                  // a game is in front: it plays too
    if (this.scriptEmote && (this.scriptEmote.until === 0 || now < this.scriptEmote.until)) return this.scriptEmote.name;
    this.scriptEmote = null;
    if (this.dragging) return this.overZone ? "happy" : "surprised";
    if (this.asleep) return "sleep";
    if (this.stateEmote && (this.stateEmote.until === 0 || now < this.stateEmote.until)) return this.stateEmote.name;
    this.stateEmote = null;
    return "idle";
  }

  /** Tells Rust which window rectangles take the mouse (character + open menu). */
  private reportRects(force: boolean): void {
    const now = performance.now();
    if (!force && now - this.lastRectAt < 50) return;
    const s = this.size;
    const rects: Rect[] = this.away ? [{ x: this.pos.x - s / 2, y: this.pos.y - s, w: s, h: s }] : [];
    const m = this.ov.menuRect();
    if (m) rects.push(m);
    const key = rects.map((r) => `${Math.round(r.x)},${Math.round(r.y)},${Math.round(r.w)},${Math.round(r.h)}`).join("|");
    if (key === this.lastRects) return;
    this.lastRects = key;
    this.lastRectAt = now;
    void native.setRects(rects.map((r) => ({ x: r.x, y: r.y, w: r.w, h: r.h })));
  }
}
