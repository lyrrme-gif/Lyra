// Idle eye wandering for a resting Mochi.
//
// Mochi's eyes follow whatever point its owner gives it (usually the mouse). When that point has not changed for a few
// seconds - the mouse stopped, or there is no mouse - the eyes start glancing around on their own, one slow glance
// every several seconds, and go straight back to following the cursor the moment it moves again. Pure logic, no DOM: character.ts asks
// it what to do on every animation step, and src/mascot/eyeWander.test.mjs checks it.

/** Seconds the eye target must stay the same before the eyes start wandering. */
export const EYE_IDLE_S = 6;

/** Seconds between one glance and the next: MIN + a random part of SPAN (4 to 9 s). Deliberately slow. */
export const GLANCE_MIN_S = 4;
export const GLANCE_SPAN_S = 5;

/** A move of the target by less than this (sum of x and y, in -1..1 units) is the same spot: mouse jitter is not "moved". */
const SAME_SPOT = 0.03;

export class EyeWander {
  private extX = 0;
  private extY = 0;
  private changedAt = 0;      // when the owner last pointed the eyes somewhere new
  private active = false;     // wandering right now
  private next = 0;           // when to glance somewhere else

  private readonly rnd: () => number;
  private readonly idleS: number;

  constructor(rnd: () => number = Math.random, idleS: number = EYE_IDLE_S) {
    this.rnd = rnd;
    this.idleS = idleS;
  }

  get wandering(): boolean { return this.active; }

  /** The owner points the eyes at (x, y) at animation time t. Returns whether the eyes should be moved there:
   *  yes when it is a new spot (the cursor moved: following resumes, wandering stops), no when it is the same spot
   *  again while wandering (a polling owner repeating the last cursor position must not snap the eyes back). */
  see(x: number, y: number, t: number): boolean {
    const moved = Math.abs(x - this.extX) + Math.abs(y - this.extY) > SAME_SPOT;
    this.extX = x; this.extY = y;
    if (moved) { this.changedAt = t; this.active = false; return true; }
    return !this.active;
  }

  /** Called on every animation step. `allowed` = a resting Mochi that may wander (plain "idle" emote, motion not reduced);
   *  curX / curY = where the eyes point now. Returns the new eye target while wandering (null = leave the eyes alone);
   *  when wandering has to stop it returns the cursor's last position once, so the eyes go back to it. */
  tick(t: number, allowed: boolean, curX: number, curY: number): [number, number] | null {
    if (this.active && !allowed) { this.active = false; return [this.extX, this.extY]; }
    if (!allowed || t - this.changedAt < this.idleS) return null;
    if (!this.active) { this.active = true; this.next = t; }
    if (t < this.next) return null;
    this.next = t + GLANCE_MIN_S + this.rnd() * GLANCE_SPAN_S;
    if (this.rnd() < 0.22) return [0, 0];                       // now and then: straight ahead
    let x = 0, y = 0;
    for (let i = 0; i < 4; i++) {                               // a glance worth seeing: not the spot they already look at
      x = (this.rnd() * 2 - 1) * 0.9;
      y = (this.rnd() * 2 - 1) * 0.55;
      if (Math.abs(x - curX) + Math.abs(y - curY) > 0.4) break;
    }
    return [x, y];
  }
}
