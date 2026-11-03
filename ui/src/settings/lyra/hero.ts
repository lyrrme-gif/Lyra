// The living banner at the top of every settings page (the Lyra idea, done the Lyra way):
//  · a constellation sky: stars drift, twinkle, shy away from the pointer, and join up with thin
//    lines around it; a shooting star crosses now and then;
//  · three Mochis (Cloud, Fox, Cat) that wander about. Click one and it keeps hopping; click it
//    again while it is in the air and it hops higher; click it a lot and it gets dizzy and drags
//    the others along;
//  · the colour follows the page (each section has its own hue) and the Mochis react when you
//    switch pages.

import { createCharacter, gazeToward, type Character, type EmoteName } from "../../mascot/character";
import { isReallyVisible, onVisibilityChange } from "../../core/visibility";
import { FRAME_MS } from "../../core/fps";
import { isEco } from "../../core/eco";

export interface Hero {
  el: HTMLElement;
  /** Switch page: new colour, new words, a little hop from the Mochis. */
  set(id: string, eyebrow: string, title: string, sub: string): void;
  destroy(): void;
}

/** Hue per page; null = follow the accent colour. */
const HUES: Record<string, number | null> = {
  general: null, ai: 285, voice: 14, p2_dictation: 352, notetaker: 24, shortcuts: 52, statistics: 158,
  island: 232, mochi: 305, features: 150, apps: 146, live: 270, screen: 186,
  privacy: 212, files: 222, advanced: 252, about: null, search: null,
};
const PAGE_EMOTE: Record<string, EmoteName> = {
  general: "wave", ai: "think", voice: "listen", p2_dictation: "talk", notetaker: "focus", shortcuts: "point",
  statistics: "happy", island: "wink", mochi: "love", features: "celebrate", apps: "happy", live: "dance",
  screen: "surprised", privacy: "shy", files: "think", advanced: "focus", about: "wave", search: "think",
};
const JOY: EmoteName[] = ["happy", "celebrate", "love", "wink", "surprised", "dance"];
const CAST = ["cloud", "fox", "cat"] as const;

const rand = (a: number, b: number) => a + Math.random() * (b - a);
const pick = <T,>(xs: readonly T[]) => xs[Math.floor(Math.random() * xs.length)];

function accentHue(): number {
  const v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--p4-h"));
  return Number.isFinite(v) ? v : 285;
}
const shortest = (d: number) => ((((d + 180) % 360) + 360) % 360) - 180;
const motionOff = () =>
  matchMedia("(prefers-reduced-motion: reduce)").matches || document.documentElement.dataset.dMotion === "reduce";

interface Star { nx: number; ny: number; x: number; y: number; vx: number; vy: number; r: number; z: number; ph: number; sp: number; tint: boolean }
interface Mochi {
  ch: Character; wrap: HTMLElement; shape: string;
  x: number; tx: number; y: number; vy: number; dir: 1 | -1;
  mode: "idle" | "walk" | "air"; jumps: number; jumpV: number; next: number;
  sq: number; emote: EmoteName; emoteUntil: number; clicks: number[]; asleep: boolean; idleSince: number;
}

export function createHero(): Hero {
  const el = document.createElement("div");
  el.className = "hero";
  const eyebrowEl = document.createElement("div"); eyebrowEl.className = "hero-eyebrow";
  const titleEl = document.createElement("h1"); titleEl.className = "hero-title";
  const subEl = document.createElement("p"); subEl.className = "hero-sub";
  const copy = document.createElement("div"); copy.className = "hero-copy";
  copy.append(eyebrowEl, titleEl, subEl);
  const canvas = document.createElement("canvas"); canvas.className = "hero-stars";
  const lane = document.createElement("div"); lane.className = "hero-lane";
  el.append(canvas, lane, copy);
  const ctx = canvas.getContext("2d")!;

  let W = 0, H = 0, dpr = 1;
  let hueCur = accentHue(), hueTarget = hueCur;
  let ptr: { x: number; y: number } | null = null;     // pointer inside the hero, hero coordinates
  let gaze: { x: number; y: number } | null = null;    // pointer anywhere in the window
  let running = true, visible = true;
  let t = 0, last = performance.now();
  let shoot: { x: number; y: number; vx: number; vy: number; life: number } | null = null;
  let nextShoot = 3;

  // ── Stars ────────────────────────────────────────────────────────────────
  const stars: Star[] = Array.from({ length: 84 }, () => ({
    nx: Math.random(), ny: Math.random(), x: 0, y: 0, vx: 0, vy: 0,
    r: rand(0.6, 1.9), z: rand(0.35, 1), ph: rand(0, 6.28), sp: rand(0.6, 2.2), tint: Math.random() < 0.34,
  }));
  const homeStars = () => { for (const s of stars) { s.x = s.nx * W; s.y = s.ny * H; s.vx = s.vy = 0; } };

  function resize() {
    const r = el.getBoundingClientRect();
    if (!r.width) return;
    dpr = isEco() ? 1 : Math.min(2, window.devicePixelRatio || 1);   // Eco mode: a quarter of the pixels to fill, same 60 fps
    const first = !W;
    W = r.width; H = r.height;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (first) homeStars();
    layoutLane();
    if (motionOff()) drawStars(0, true);
  }

  function drawStars(dt: number, still = false) {
    ctx.clearRect(0, 0, W, H);
    hueCur += shortest(hueTarget - hueCur) * (1 - Math.exp(-4 * dt));
    const hs = hueCur;

    if (ptr && !still) {                                   // a soft light that follows the pointer
      const g = ctx.createRadialGradient(ptr.x, ptr.y, 0, ptr.x, ptr.y, 170);
      g.addColorStop(0, `hsl(${hs} 90% 68% / .17)`); g.addColorStop(1, `hsl(${hs} 90% 68% / 0)`);
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    }

    const near: Star[] = [];
    for (const s of stars) {
      let prox = 0;
      if (!still) {
        const hx = s.nx * W, hy = s.ny * H;
        let ax = (hx - s.x) * 38 - s.vx * 7, ay = (hy - s.y) * 38 - s.vy * 7;
        if (ptr) {
          const dx = s.x - ptr.x, dy = s.y - ptr.y, d = Math.hypot(dx, dy) || 1;
          if (d < 130) { prox = 1 - d / 130; const push = prox * prox * 2600 * s.z; ax += (dx / d) * push; ay += (dy / d) * push; }
        }
        s.vx += ax * dt; s.vy += ay * dt;
        s.x += s.vx * dt + Math.sin(t * 0.25 + s.ph) * 0.06 * s.z;     // slow drift on top
        s.y += s.vy * dt + Math.cos(t * 0.21 + s.ph) * 0.05 * s.z;
        if (prox > 0.08 && near.length < 16) near.push(s);
      }
      const tw = still ? 0.8 : 0.5 + 0.5 * Math.sin(t * s.sp + s.ph);
      const a = Math.min(1, (0.28 + 0.52 * tw) * (0.55 + 0.45 * s.z) + prox * 0.5);
      const rad = s.r * (1 + prox * 0.9);
      ctx.fillStyle = s.tint ? `hsl(${hs} 92% 72% / ${a})` : `hsl(${hs} 40% 94% / ${a})`;
      ctx.beginPath(); ctx.arc(s.x, s.y, rad, 0, 6.2832); ctx.fill();
      if (prox > 0.35) {                                   // a little glow on the ones the pointer is waking
        ctx.fillStyle = `hsl(${hs} 92% 72% / ${prox * 0.18})`;
        ctx.beginPath(); ctx.arc(s.x, s.y, rad * 3.4, 0, 6.2832); ctx.fill();
      }
    }

    if (near.length > 1) {                                 // constellation lines around the pointer
      ctx.lineWidth = 1;
      for (let i = 0; i < near.length; i++) for (let j = i + 1; j < near.length; j++) {
        const d = Math.hypot(near[i].x - near[j].x, near[i].y - near[j].y);
        if (d < 90) {
          ctx.strokeStyle = `hsl(${hs} 90% 78% / ${(1 - d / 90) * 0.4})`;
          ctx.beginPath(); ctx.moveTo(near[i].x, near[i].y); ctx.lineTo(near[j].x, near[j].y); ctx.stroke();
        }
      }
    }

    if (!still) {                                          // shooting star
      nextShoot -= dt;
      if (!shoot && nextShoot <= 0) {
        shoot = { x: rand(W * 0.15, W * 0.7), y: rand(-10, H * 0.25), vx: rand(520, 700), vy: rand(150, 260), life: 1 };
        nextShoot = rand(6, 12);
      }
      if (shoot) {
        shoot.x += shoot.vx * dt; shoot.y += shoot.vy * dt; shoot.life -= dt * 1.35;
        if (shoot.life <= 0 || shoot.x > W + 40 || shoot.y > H + 40) shoot = null;
        else {
          const k = 0.11, g = ctx.createLinearGradient(shoot.x, shoot.y, shoot.x - shoot.vx * k, shoot.y - shoot.vy * k);
          g.addColorStop(0, `hsl(${hs} 100% 92% / ${shoot.life})`); g.addColorStop(1, `hsl(${hs} 100% 80% / 0)`);
          ctx.strokeStyle = g; ctx.lineWidth = 1.6; ctx.lineCap = "round";
          ctx.beginPath(); ctx.moveTo(shoot.x, shoot.y); ctx.lineTo(shoot.x - shoot.vx * k, shoot.y - shoot.vy * k); ctx.stroke();
        }
      }
    }
  }

  // ── Mochis ───────────────────────────────────────────────────────────────
  const GRAV = 1750;
  const mochis: Mochi[] = CAST.map((shape) => {
    const wrap = document.createElement("div");
    wrap.className = "hm";
    wrap.title = "Poke me";
    lane.append(wrap);
    const ch = createCharacter(wrap, { tight: true, sharp: 2 });
    ch.setShape?.(shape);
    ch.setEmote("idle");
    return {
      ch, wrap, shape, x: 0, tx: 0, y: 0, vy: 0, dir: 1, mode: "idle", jumps: 0, jumpV: 0, next: rand(0.4, 1.6),
      sq: 0, emote: "idle", emoteUntil: 0, clicks: [], asleep: false, idleSince: 0,
    } as Mochi;
  });

  const bounds = (): [number, number] => {
    const lo = Math.max(W * 0.56, 380), hi = Math.max(lo + 60, W - 92);
    return [lo, hi];
  };
  function layoutLane() {
    const [lo, hi] = bounds();
    mochis.forEach((m, i) => {
      const home = lo + ((hi - lo) * (i + 0.5)) / mochis.length;
      if (!m.x) m.x = m.tx = home;
      m.x = Math.min(hi, Math.max(lo, m.x)); m.tx = Math.min(hi, Math.max(lo, m.tx));
    });
  }

  function emote(m: Mochi, e: EmoteName, secs: number) {
    if (m.emote !== e) { m.emote = e; m.ch.setEmote(e); }
    m.emoteUntil = secs > 0 ? t + secs : 0;
  }
  function takeOff(m: Mochi, v: number) {
    m.mode = "air"; m.vy = v; m.sq = -0.55;                // stretch on the way up
    m.idleSince = t;
  }
  function hop(m: Mochi, hops: number, v = 380) {
    if (motionOff()) { emote(m, pick(JOY), 1.2); return; }
    m.jumps = Math.min(8, Math.max(m.jumps, hops)); m.jumpV = v;
    if (m.mode !== "air") takeOff(m, v);
  }
  function wake(m: Mochi) { if (m.asleep) { m.asleep = false; emote(m, "surprised", 0.9); } }

  function poke(m: Mochi) {
    const now = performance.now();
    m.clicks = m.clicks.filter((c) => now - c < 1500); m.clicks.push(now);
    wake(m);
    m.idleSince = t;
    if (m.clicks.length >= 7) {                            // too much poking: dizzy, and the others join the panic
      m.clicks = [];
      emote(m, "dizzy", 1.9); hop(m, 2, 300);
      mochis.forEach((o, i) => { if (o !== m) window.setTimeout(() => { wake(o); emote(o, "surprised", 1.2); hop(o, 2, 330); }, 120 + i * 110); });
      return;
    }
    const combo = m.clicks.length;
    hop(m, m.jumps + 2 + Math.min(3, combo - 1), 360 + Math.min(4, combo) * 36);   // every poke adds hops, and a bit of height
    emote(m, pick(JOY), 2.2);
    for (const o of mochis) {                              // the neighbours look over, the near one cheers
      if (o === m || o.asleep) continue;
      if (Math.abs(o.x - m.x) < 130 && Math.random() < 0.6) { emote(o, "happy", 1.2); if (o.mode !== "air") hop(o, 1, 250); }
    }
  }
  mochis.forEach((m) => m.wrap.addEventListener("pointerdown", (e) => { e.preventDefault(); poke(m); }));

  function stepMochi(m: Mochi, dt: number) {
    const [lo, hi] = bounds();
    if (m.emoteUntil && t >= m.emoteUntil && !m.asleep) { m.emoteUntil = 0; m.emote = "idle"; m.ch.setEmote("idle"); }

    if (m.mode === "air") {
      m.vy -= GRAV * dt; m.y += m.vy * dt;
      if (m.y <= 0) {
        m.y = 0; m.sq = 1;                                 // squash on landing
        if (m.jumps > 0) m.jumps--;
        if (m.jumps > 0) { m.jumpV *= 0.8; takeOff(m, Math.max(170, m.jumpV)); m.sq = 0.85; }
        else { m.mode = "idle"; m.next = t + rand(1.2, 3); }
      }
    } else if (m.mode === "walk") {
      const d = m.tx - m.x, sp = 70;
      if (Math.abs(d) < 2) { m.mode = "idle"; m.ch.setMotion(0); m.next = t + rand(1.4, 4.2); }
      else { m.dir = d > 0 ? 1 : -1; m.x += m.dir * Math.min(Math.abs(d), sp * dt); m.ch.setFacing(m.dir); m.ch.setMotion(sp * 1.4); }
    } else if (!m.asleep && t >= m.next && !motionOff()) {
      const r = Math.random();
      if (r < 0.5) {                                       // wander, but not onto a friend
        let tx = rand(lo, hi);
        for (let k = 0; k < 8 && mochis.some((o) => o !== m && Math.abs(o.tx - tx) < 66); k++) tx = rand(lo, hi);
        m.tx = tx; m.mode = "walk";
      }
      else if (r < 0.72) hop(m, 1, rand(240, 330));
      else if (r < 0.82) emote(m, pick(["wink", "happy", "love"] as const), 1.6);
      m.next = t + rand(1.6, 4.5);
      if (m.mode === "idle" && t - m.idleSince > 26) {     // nobody has played with it for a while: nap
        m.asleep = true; emote(m, "sleep", 0);
      }
    }
    if (m.mode !== "walk") m.ch.setMotion(0);
    m.sq += (0 - m.sq) * (1 - Math.exp(-14 * dt));

    // eyes follow the pointer
    if (gaze && !m.asleep && m.emote !== "dizzy") { const [gx, gy] = gazeToward(m.wrap, gaze.x, gaze.y); m.ch.look(gx, gy); }

    const sy = 1 - m.sq * 0.2, sx = 1 + m.sq * 0.18;
    m.wrap.style.transform = `translate3d(${m.x - 36}px,${-m.y}px,0) scale(${sx.toFixed(3)},${sy.toFixed(3)})`;
  }

  // ── Loop, pointer, visibility ────────────────────────────────────────────
  let looping = false;
  function frame(now: number) {
    if (!running) { looping = false; return; }
    // Parked / hidden window: stop asking for frames at all (resumed by onVisibilityChange below).
    if (!isReallyVisible() || !visible) { looping = false; return; }
    requestAnimationFrame(frame);
    // 60 fps while the window is visible (30 when another window has focus and this one is just in the background)
    if (now - last < (document.hasFocus() ? FRAME_MS : 33)) return;
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    if (!visible || document.hidden || !W) return;
    t += dt;
    if (!motionOff()) drawStars(dt);
    for (const m of mochis) stepMochi(m, dt);
    // keep the Mochis from piling up: standing close, they shuffle apart
    const [lo, hi] = bounds();
    for (let i = 0; i < mochis.length; i++) for (let j = i + 1; j < mochis.length; j++) {
      const a = mochis[i], b = mochis[j], d = b.x - a.x;
      if (Math.abs(d) < 52 && a.mode !== "air" && b.mode !== "air") {
        const push = (52 - Math.abs(d)) * 3 * dt * (d >= 0 ? 1 : -1);
        a.x = Math.min(hi, Math.max(lo, a.x - push)); b.x = Math.min(hi, Math.max(lo, b.x + push));
        a.tx = Math.min(hi, Math.max(lo, a.tx - push)); b.tx = Math.min(hi, Math.max(lo, b.tx + push));
      }
    }
  }

  const onMove = (e: PointerEvent) => {
    gaze = { x: e.clientX, y: e.clientY };
    const r = el.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    ptr = x > -30 && x < r.width + 30 && y > -30 && y < r.height + 30 ? { x, y } : null;
  };
  const onLeave = () => { ptr = null; gaze = null; };
  window.addEventListener("pointermove", onMove, { passive: true });
  document.addEventListener("mouseleave", onLeave);
  window.addEventListener("blur", onLeave);

  const ro = new ResizeObserver(resize); ro.observe(el);
  const io = new IntersectionObserver((es) => {
    visible = es[es.length - 1].isIntersecting;
    for (const m of mochis) m.ch.setActive?.(visible);
    if (visible) startLoop();
  });
  io.observe(el);
  const startLoop = () => { if (looping || !running) return; looping = true; requestAnimationFrame((n) => { last = n; frame(n); }); };
  onVisibilityChange((v) => { if (v) startLoop(); });
  looping = true;
  requestAnimationFrame((n) => { last = n; frame(n); });

  // ── Page switching ───────────────────────────────────────────────────────
  let lastId = "";
  function set(id: string, eyebrow: string, title: string, sub: string) {
    const hue = HUES[id];
    hueTarget = hue == null ? accentHue() : hue;
    el.style.setProperty("--hh", String(hueTarget));
    const changed = id !== lastId;
    eyebrowEl.textContent = eyebrow; eyebrowEl.hidden = !eyebrow;
    titleEl.textContent = title; subEl.textContent = sub; subEl.hidden = !sub;
    if (changed) {
      copy.classList.remove("swap"); void copy.offsetWidth; copy.classList.add("swap");
      if (lastId) {                                        // not on the very first paint
        const lead = mochis[Math.floor(Math.random() * mochis.length)];
        wake(lead); emote(lead, PAGE_EMOTE[id] ?? "happy", 2.4);
        mochis.forEach((m, i) => { if (!m.asleep) window.setTimeout(() => hop(m, 1, 260), i * 130); });
      }
    }
    lastId = id;
  }

  return {
    el, set,
    destroy() {
      running = false;
      window.removeEventListener("pointermove", onMove); document.removeEventListener("mouseleave", onLeave);
      window.removeEventListener("blur", onLeave); ro.disconnect(); io.disconnect();
      mochis.forEach((m) => m.ch.destroy());
    },
  };
}
