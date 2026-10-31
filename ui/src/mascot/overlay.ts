// Everything the mascot draws besides the character: speech bubble, the pulsing
// ring + arrow used by "point", and the right-click menu.

import type { Rect } from "./native";

export interface MenuItem { label: string; run: () => void }
export interface Pt { x: number; y: number }

const SVG_NS = "http://www.w3.org/2000/svg";

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  e.className = cls;
  return e;
}

export class Overlay {
  private bubble = el("div", "mc-bubble");
  private bubbleTimer = 0;
  private bubbleOn = false;
  private ring = el("div", "mc-ring");
  private arrow = el("div", "mc-arrow");
  private label = el("div", "mc-label");
  private pointTimer = 0;
  private cursor = el("div", "mc-cursor");
  private cursorTimer = 0;
  private svg = document.createElementNS(SVG_NS, "svg");
  private menu: HTMLDivElement | null = null;
  private menuClose = 0;

  constructor(private stage: HTMLElement) {
    this.bubble.style.display = "none";
    this.ring.style.display = "none";
    this.arrow.style.display = "none";
    this.label.style.display = "none";
    this.cursor.style.display = "none";
    this.svg.setAttribute("class", "mc-draw");
    stage.append(this.svg, this.ring, this.arrow, this.label, this.cursor, this.bubble);
  }

  // ── Speech bubble ─────────────────────────────────────────────────────────
  say(text: string, ms: number, onDone?: () => void): void {
    window.clearTimeout(this.bubbleTimer);
    this.bubble.textContent = text;
    this.bubble.style.display = "block";
    this.bubble.classList.remove("in");
    void this.bubble.offsetWidth; // restart the pop-in
    this.bubble.classList.add("in");
    this.bubbleOn = true;
    this.bubbleTimer = window.setTimeout(() => { this.hideBubble(); onDone?.(); }, Math.max(600, ms));
  }

  hideBubble(): void {
    window.clearTimeout(this.bubbleTimer);
    this.bubbleOn = false;
    this.bubble.style.display = "none";
  }

  get speaking(): boolean { return this.bubbleOn; }

  /** Above the character; flips below near the top edge; clamped inside the screen. */
  placeBubble(ax: number, ay: number, size: number, W: number, H: number): void {
    if (!this.bubbleOn) return;
    const w = this.bubble.offsetWidth;
    const h = this.bubble.offsetHeight;
    let top = ay - size - 8 - h;
    const below = top < 6;
    if (below) top = Math.min(H - h - 6, ay + 10);
    let left = ax - w / 2;
    left = Math.max(6, Math.min(W - w - 6, left));
    const tail = Math.max(16, Math.min(w - 16, ax - left));
    this.bubble.classList.toggle("below", below);
    this.bubble.style.setProperty("--tail", `${tail}px`);
    this.bubble.style.transform = `translate(${left.toFixed(1)}px, ${top.toFixed(1)}px)`;
  }

  // ── Point: pulsing ring + arrow (+ optional label) ────────────────────────
  showPoint(tx: number, ty: number, fromX: number, fromY: number, label: string, ms: number): void {
    window.clearTimeout(this.pointTimer);
    this.ring.style.transform = `translate(${tx}px, ${ty}px)`;
    this.ring.style.display = "block";
    const ang = Math.atan2(ty - fromY, tx - fromX);
    const d = 46;
    this.arrow.style.transform =
      `translate(${tx - Math.cos(ang) * d}px, ${ty - Math.sin(ang) * d}px) rotate(${ang}rad)`;
    this.arrow.style.display = "block";
    if (label) {
      this.label.textContent = label;
      this.label.style.display = "block";
      this.label.style.transform = `translate(${tx + 24}px, ${ty + 22}px)`;
    } else {
      this.label.style.display = "none";
    }
    if (ms > 0) this.pointTimer = window.setTimeout(() => this.clearPoint(), ms);
  }

  clearPoint(): void {
    window.clearTimeout(this.pointTimer);
    this.clearDraw();
    this.ring.style.display = "none";
    this.arrow.style.display = "none";
    this.label.style.display = "none";
  }

  // ── Lyra's cursor: shown where she is about to press something ───────────
  showCursor(tx: number, ty: number, kind: "click" | "scroll", label: string, ms: number): void {
    window.clearTimeout(this.cursorTimer);
    this.cursor.replaceChildren();
    const arrow = el("div", "mc-cursor-arrow");
    this.cursor.append(arrow);
    if (label) { const tag = el("div", "mc-cursor-tag"); tag.textContent = label; this.cursor.append(tag); }
    this.cursor.dataset.kind = kind;
    this.cursor.style.transform = `translate(${tx}px, ${ty}px)`;
    this.cursor.style.display = "block";
    if (ms > 0) this.cursorTimer = window.setTimeout(() => this.hideCursor(), ms);
  }

  hideCursor(): void {
    window.clearTimeout(this.cursorTimer);
    this.cursor.style.display = "none";
  }

  // ── Drawing: hand-drawn looking marker strokes (circle / underline / arrow / box / line) ────
  clearDraw(): void {
    this.svg.replaceChildren();
  }

  /** Draws `shape` around the box (x0,y0)-(x1,y1) (stage pixels), the stroke growing over `ms`.
   * Returns the points the Mochi's hand should follow, in drawing order (about 24 of them). */
  drawShape(shape: string, x0: number, y0: number, x1: number, y1: number, ms: number, W: number, H: number): Pt[] {
    this.clearDraw();
    const bx0 = Math.min(x0, x1), bx1 = Math.max(x0, x1), by0 = Math.min(y0, y1), by1 = Math.max(y0, y1);
    const cx = (bx0 + bx1) / 2, cy = (by0 + by1) / 2;
    const w = Math.max(44, bx1 - bx0), h = Math.max(44, by1 - by0);
    const wob = (i: number) => Math.sin(i * 1.7) * 1.6 + Math.sin(i * 0.63 + 1) * 1.2;   // a steady hand is not that steady
    const strokes: Pt[][] = [];

    if (shape === "underline") {
      const n = 28, y = cy + h / 2 + 8, s: Pt[] = [];
      for (let i = 0; i <= n; i++) s.push({ x: cx - w / 2 + (w * i) / n, y: y + wob(i) });
      strokes.push(s);
    } else if (shape === "line") {
      const n = 24, s: Pt[] = [];
      for (let i = 0; i <= n; i++) s.push({ x: x0 + ((x1 - x0) * i) / n, y: y0 + ((y1 - y0) * i) / n + wob(i) * 0.6 });
      strokes.push(s);
    } else if (shape === "box") {
      const pad = 8, L = cx - w / 2 - pad, R = cx + w / 2 + pad, T = cy - h / 2 - pad, B = cy + h / 2 + pad, per = 9, s: Pt[] = [];
      const edge = (ax: number, ay: number, bx: number, by: number) => {
        for (let i = 0; i < per; i++) s.push({ x: ax + ((bx - ax) * i) / per + wob(s.length) * 0.5, y: ay + ((by - ay) * i) / per + wob(s.length + 3) * 0.5 });
      };
      edge(L, T, R, T); edge(R, T, R, B); edge(R, B, L, B); edge(L, B, L, T - 6);
      strokes.push(s);
    } else if (shape === "arrow") {
      const reach = Math.max(w, h) / 2 + 120;
      const dirs: Pt[] = [{ x: -0.8, y: -0.6 }, { x: 0.8, y: -0.6 }, { x: -0.8, y: 0.6 }, { x: 0.8, y: 0.6 }, { x: 0, y: -1 }];
      let d = dirs[0];
      for (const c of dirs) {
        const px = cx + c.x * reach, py = cy + c.y * reach;
        if (px > 30 && px < W - 30 && py > 30 && py < H - 30) { d = c; break; }
      }
      const len = Math.hypot(d.x, d.y), ux = d.x / len, uy = d.y / len;
      const sx = cx + ux * reach, sy = cy + uy * reach;
      const stop = Math.max(w, h) / 2 + 12;
      const tx = cx + ux * stop, ty = cy + uy * stop;
      const n = 22, s: Pt[] = [];
      for (let i = 0; i <= n; i++) {
        const t = i / n, bend = Math.sin(Math.PI * t) * 16;
        s.push({ x: sx + (tx - sx) * t - uy * bend, y: sy + (ty - sy) * t + ux * bend });
      }
      strokes.push(s);
      const back = Math.atan2(sy - ty, sx - tx), head = 24;
      for (const a of [0.5, -0.5]) {
        strokes.push([{ x: tx, y: ty }, { x: tx + Math.cos(back + a) * head, y: ty + Math.sin(back + a) * head }]);
      }
    } else {                                                  // circle (a little more than one turn)
      const n = 56, rx = w / 2 * 1.1 + 10, ry = h / 2 * 1.1 + 10, s: Pt[] = [];
      for (let i = 0; i <= n; i++) {
        const t = i / n, a = -1.9 + Math.PI * 2 * 1.08 * t, k = 1 + 0.05 * t;
        s.push({ x: cx + Math.cos(a) * rx * k + wob(i) * 0.6, y: cy + Math.sin(a) * ry * k + wob(i + 5) * 0.6 });
      }
      strokes.push(s);
    }

    const d = strokes.map((s) => s.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ")).join(" ");
    const mk = (stroke: string, width: number, opacity: number, glow: string): SVGPathElement => {
      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", d);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", stroke);
      path.setAttribute("stroke-width", String(width));
      path.setAttribute("stroke-linecap", "round");
      path.setAttribute("stroke-linejoin", "round");
      path.setAttribute("opacity", String(opacity));
      if (glow) path.setAttribute("style", `filter: ${glow}`);
      return path;
    };
    const halo = mk("#ffffff", 9, 0.92, "drop-shadow(0 2px 6px rgba(0,0,0,.35))");
    const ink = mk("#8f7bff", 5, 1, "drop-shadow(0 0 6px rgba(143,123,255,.9))");
    this.svg.append(halo, ink);
    for (const path of [halo, ink]) {                         // the stroke grows like a pen moving
      const len = path.getTotalLength();
      path.style.strokeDasharray = `${len}`;
      path.style.strokeDashoffset = `${len}`;
      void path.getBoundingClientRect();
      path.style.transition = `stroke-dashoffset ${Math.max(300, ms)}ms linear`;
      path.style.strokeDashoffset = "0";
    }

    const all = strokes.flat();
    const step = Math.max(1, Math.ceil(all.length / 24));
    return all.filter((_, i) => i % step === 0);
  }

  // ── Right-click menu ──────────────────────────────────────────────────────
  openMenu(x: number, y: number, W: number, H: number, items: MenuItem[], onChange: () => void): void {
    this.closeMenu();
    const m = el("div", "mc-menu");
    for (const it of items) {
      const b = el("button", "mc-item");
      b.textContent = it.label;
      b.addEventListener("click", (ev) => { ev.stopPropagation(); this.closeMenu(); onChange(); it.run(); });
      m.appendChild(b);
    }
    m.addEventListener("pointerleave", () => {
      window.clearTimeout(this.menuClose);
      this.menuClose = window.setTimeout(() => { this.closeMenu(); onChange(); }, 900);
    });
    m.addEventListener("pointerenter", () => window.clearTimeout(this.menuClose));
    m.style.visibility = "hidden";
    this.stage.appendChild(m);
    const w = m.offsetWidth, h = m.offsetHeight;
    m.style.left = `${Math.max(4, Math.min(W - w - 4, x))}px`;
    m.style.top = `${Math.max(4, Math.min(H - h - 4, y))}px`;
    m.style.visibility = "visible";
    this.menu = m;
    onChange();
    // Never leave the menu open if the pointer never enters it.
    this.menuClose = window.setTimeout(() => { this.closeMenu(); onChange(); }, 4000);
  }

  closeMenu(): void {
    window.clearTimeout(this.menuClose);
    this.menu?.remove();
    this.menu = null;
  }

  menuRect(): Rect | null {
    if (!this.menu) return null;
    const r = this.menu.getBoundingClientRect();
    return { x: r.left, y: r.top, w: r.width, h: r.height };
  }
}
