// Run:  node --test ui/src/mascot/eyeWander.test.mjs   (Node >= 22.18, no build step)
import test from "node:test";
import assert from "node:assert/strict";
import { EyeWander, EYE_IDLE_S, GLANCE_MIN_S, GLANCE_SPAN_S } from "./eyeWander.ts";

const seq = (...v) => { let i = 0; return () => v[i++ % v.length]; };      // a fixed "random" sequence

test("while the cursor keeps moving the eyes just follow it and never wander", () => {
  const w = new EyeWander();
  for (let t = 0; t < 30; t += 0.1) {
    assert.equal(w.see(Math.sin(t), 0.2, t), true);
    assert.equal(w.tick(t, true, 0, 0), null);
  }
  assert.equal(w.wandering, false);
});

test("nothing happens before the idle time, then the eyes start glancing around", () => {
  const w = new EyeWander(seq(0.9, 0.9, 0.9, 0.9));
  w.see(0.4, -0.2, 0);
  assert.equal(w.tick(EYE_IDLE_S - 0.5, true, 0.4, -0.2), null);
  const g = w.tick(EYE_IDLE_S + 0.1, true, 0.4, -0.2);
  assert.ok(g && w.wandering, "first glance right after the idle time");
  assert.ok(Math.abs(g[0]) <= 0.9 && Math.abs(g[1]) <= 0.55);
});

test("glances come slowly: 4 to 9 seconds apart, never on every step", () => {
  const w = new EyeWander(seq(0.5, 0.7, 0.3, 0.8));
  w.see(0, 0, 0);
  const at = [];
  for (let t = 0; t <= 120; t += 1 / 60) if (w.tick(t, true, 0, 0)) at.push(t);
  assert.ok(at.length >= 8 && at.length <= 20, `${at.length} glances in ~114 s of wandering`);
  for (let i = 1; i < at.length; i++) {
    const gap = at[i] - at[i - 1];
    assert.ok(gap >= GLANCE_MIN_S - 0.05 && gap <= GLANCE_MIN_S + GLANCE_SPAN_S + 0.05, `gap ${gap.toFixed(2)} s`);
  }
});

test("many steps in the same instant (a time jump or doubled loop) give one glance, not a burst", () => {
  const w = new EyeWander(seq(0.9, 0.9, 0.9, 0.9));
  w.see(0, 0, 0);
  let n = 0;
  for (let i = 0; i < 500; i++) if (w.tick(7, true, 0, 0)) n++;
  assert.equal(n, 1);
});

test("a glance is a real move away from where the eyes already look", () => {
  const w = new EyeWander(seq(0.51, 0.51, 0.9, 0.9, 0.9, 0.9));     // first try is almost the same spot (0.02, 0.02): rejected
  w.see(0, 0, 0);
  const g = w.tick(7, true, 0, 0);
  assert.ok(Math.abs(g[0]) + Math.abs(g[1]) > 0.4, JSON.stringify(g));
});

test("sometimes it looks straight ahead", () => {
  const w = new EyeWander(seq(0.5, 0.1));                            // 0.5 for the timer, 0.1 < 0.22 = look ahead
  w.see(0.3, 0.3, 0);
  assert.deepEqual(w.tick(7, true, 0.3, 0.3), [0, 0]);
});

test("a polling owner repeating the same cursor spot does not snap the eyes back", () => {
  const w = new EyeWander();
  w.see(0.5, 0.1, 0);
  w.tick(7, true, 0.5, 0.1);                                         // wandering now
  assert.equal(w.see(0.5, 0.1, 7.1), false);
  assert.equal(w.see(0.51, 0.1, 7.2), false);                        // jitter below the threshold is the same spot
  assert.equal(w.wandering, true);
});

test("the moment the cursor moves, following resumes and wandering stops", () => {
  const w = new EyeWander();
  w.see(0.5, 0.1, 0);
  w.tick(7, true, 0.5, 0.1);
  assert.equal(w.see(-0.6, 0.3, 8), true);
  assert.equal(w.wandering, false);
  assert.equal(w.tick(8.1, true, -0.6, 0.3), null);                  // and the idle clock starts again
  assert.equal(w.tick(8 + EYE_IDLE_S - 0.2, true, -0.6, 0.3), null);
  assert.ok(w.tick(8 + EYE_IDLE_S + 0.2, true, -0.6, 0.3));
});

test("a Mochi that stops being 'idle' (talking, sleeping, reduced motion) stops wandering and looks at the cursor again", () => {
  const w = new EyeWander();
  w.see(0.4, -0.3, 0);
  w.tick(7, true, 0.4, -0.3);
  assert.deepEqual(w.tick(7.5, false, 0.9, 0.5), [0.4, -0.3]);       // handed back to the cursor's last spot, once
  assert.equal(w.tick(7.6, false, 0.4, -0.3), null);
  assert.equal(w.wandering, false);
});

test("a Mochi that may not wander never does, however long it waits", () => {
  const w = new EyeWander();
  w.see(0, 0, 0);
  for (let t = 0; t < 120; t += 0.5) assert.equal(w.tick(t, false, 0, 0), null);
});
