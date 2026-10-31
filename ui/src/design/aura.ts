// Voice aura: a ring of "stars" that swells with a 0–1 level. No microphone access here:
// whoever owns a level (the island, the wake-word engine) feeds it with setLevel(), or fires
//   window.dispatchEvent(new CustomEvent("lyra-mic-level", { detail: 0.4 }))
// With no input it breathes slowly, so the tile never looks dead. Reduced motion → still, calm ring.

import { isReallyVisible, onVisibilityChange } from "../core/visibility";
import { FRAME_MS } from "../core/fps";

const NS = "http://www.w3.org/2000/svg";
const N = 28;

export interface Aura { el: HTMLElement; setLevel(v: number): void; destroy(): void }

export function createAura(opts: { size?: number; label?: string } = {}): Aura {
  const el = document.createElement("div");
  el.className = "p4-aura";
  el.setAttribute("role", "img");
  el.setAttribute("aria-label", opts.label ?? "Voice activity");
  const size = opts.size ?? 160;
  el.style.width = el.style.height = `${size}px`;
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "-100 -100 200 200");
  svg.setAttribute("aria-hidden", "true");
  const core = document.createElementNS(NS, "circle");
  core.setAttribute("fill", "currentColor"); core.setAttribute("opacity", ".2");
  const ring = document.createElementNS(NS, "circle");
  ring.setAttribute("fill", "none"); ring.setAttribute("stroke", "currentColor"); ring.setAttribute("opacity", ".25");
  const line = document.createElementNS(NS, "polyline");
  line.setAttribute("fill", "none"); line.setAttribute("stroke", "currentColor"); line.setAttribute("stroke-width", ".8"); line.setAttribute("opacity", ".35");
  svg.append(core, ring, line);
  const dots: SVGCircleElement[] = [];
  for (let i = 0; i < N; i++) { const c = document.createElementNS(NS, "circle"); c.setAttribute("fill", "currentColor"); svg.append(c); dots.push(c); }
  el.append(svg);

  const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  const still = () => !!mq?.matches || document.documentElement.dataset.dMotion === "reduce";
  let target = 0, level = 0, raf = 0, lastDraw = 0;
  let onScreen = true;   // fails open until the observer reports; false = its tab is not showing, so no frames at all
  const t0 = performance.now();

  const draw = (now: number) => {
    if (!isReallyVisible() || !onScreen) { raf = 0; return; }   // parked / hidden / tab not showing: stop; resumed below
    if (now - lastDraw < FRAME_MS) { raf = requestAnimationFrame(draw); return; }   // 60 fps on 120/144 Hz screens
    lastDraw = now;
    const t = (now - t0) / 1000;
    const idle = still() ? 0.12 : 0.1 + 0.05 * Math.sin(t * 1.3);
    level += (Math.max(target, idle) - level) * 0.18;
    const base = 54;
    core.setAttribute("r", (base * 0.62 + level * base * 0.5).toFixed(1));
    ring.setAttribute("r", (base + 4).toFixed(1));
    const pts: string[] = [];
    dots.forEach((d, i) => {
      const f = i / N;
      const env = Math.exp(-Math.abs(f - 0.5) * 1.2) * 0.9 + 0.15;
      const wob = still() ? 0.6 : 0.5 + 0.5 * Math.sin(t * (3 + i * 0.37) + i * 1.3);
      const b = Math.min(1, level * env * (0.55 + 0.7 * wob));
      const a = f * Math.PI * 2 - Math.PI / 2 + (still() ? 0 : t * 0.12);
      const r = base + b * base * 0.75;
      const x = Math.cos(a) * r, y = Math.sin(a) * r;
      d.setAttribute("cx", x.toFixed(1)); d.setAttribute("cy", y.toFixed(1)); d.setAttribute("r", (1.6 + b * 3.4).toFixed(1));
      pts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    });
    pts.push(pts[0]);
    line.setAttribute("points", pts.join(" "));
    raf = still() && Math.abs(level - Math.max(target, idle)) < 0.002 ? 0 : requestAnimationFrame(draw);
  };
  const kick = () => { if (!raf) raf = requestAnimationFrame(draw); };
  const onLevel = (e: Event) => { const v = Number((e as CustomEvent).detail); if (Number.isFinite(v)) { target = Math.min(1, Math.max(0, v)); kick(); } };
  window.addEventListener("lyra-mic-level", onLevel);
  const offVis = onVisibilityChange((v) => { if (v) kick(); });
  const io = typeof IntersectionObserver !== "undefined"
    ? new IntersectionObserver((es) => { onScreen = es[es.length - 1].isIntersecting; if (onScreen) kick(); })
    : null;
  io?.observe(el);
  mq?.addEventListener?.("change", kick);
  kick();
  return {
    el,
    setLevel(v) { target = Math.min(1, Math.max(0, v)); kick(); },
    destroy() { cancelAnimationFrame(raf); raf = 0; offVis(); io?.disconnect(); window.removeEventListener("lyra-mic-level", onLevel); },
  };
}
