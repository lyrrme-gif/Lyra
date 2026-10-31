// Handwriting pad for the Study tab.
//
// Draws with Pointer Events, so a mouse, a finger and a pen (with pressure) all work. Strokes are kept as
// data (not just pixels): that gives undo, a crisp redraw when the island resizes, and a clean export —
// toPng() crops to the writing and renders black ink on white, which is what the handwriting reader wants.
// Nothing leaves the machine until the user presses "Read".

export interface Pt { x: number; y: number; p: number }
export type Stroke = Pt[];

const MAX_EXPORT_SIDE = 1100;      // px; keeps the PNG small without losing legibility
const PAD = 28;                    // px of white around the writing in the export
const MIN_WIDTH = 1.6;
const MAX_WIDTH = 4.2;

export class InkPad {
  readonly el: HTMLElement;
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private strokes: Stroke[] = [];
  private live: Stroke | null = null;
  private pointerId: number | null = null;
  private penSeen = false;
  private w = 0;
  private h = 0;
  private dpr = 1;
  private ro: ResizeObserver | null = null;
  /** Called after every change (stroke added, undone, cleared). */
  onChange: () => void = () => {};

  constructor() {
    this.canvas = document.createElement("canvas");
    this.canvas.className = "sm-ink-canvas";
    this.canvas.setAttribute("aria-label", "Handwriting area. Write your answer with a mouse, finger or pen.");
    this.canvas.style.touchAction = "none";                         // we own the gestures on this surface
    const ctx = this.canvas.getContext("2d");
    if (!ctx) throw new Error("canvas unavailable");
    this.ctx = ctx;
    this.el = document.createElement("div");
    this.el.className = "sm-ink";
    const hint = document.createElement("div");
    hint.className = "sm-ink-hint";
    hint.textContent = "Write here";
    this.el.append(hint, this.canvas);
    this.hint = hint;

    const c = this.canvas;
    c.addEventListener("pointerdown", (e) => this.down(e));
    c.addEventListener("pointermove", (e) => this.move(e));
    c.addEventListener("pointerup", (e) => this.up(e));
    c.addEventListener("pointercancel", (e) => this.up(e));
    c.addEventListener("lostpointercapture", (e) => this.up(e));
    // the island closes/collapses on stray clicks; the pad is a drawing surface, so keep them to ourselves
    for (const t of ["click", "dblclick", "contextmenu", "wheel"] as const) c.addEventListener(t, (e) => { e.stopPropagation(); if (t !== "click") e.preventDefault(); });

    if (typeof ResizeObserver !== "undefined") {
      this.ro = new ResizeObserver(() => this.fit());
      this.ro.observe(this.el);
    }
  }
  private hint: HTMLElement;

  // ── public ───────────────────────────────────────────────────────────────────────────────

  isEmpty(): boolean { return this.strokes.length === 0 && !this.live; }
  strokeCount(): number { return this.strokes.length; }

  /** Call when the pad becomes visible (it measures 0×0 while hidden). */
  fit(): void {
    const r = this.el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    if (Math.abs(r.width - this.w) < 0.5 && Math.abs(r.height - this.h) < 0.5 && dpr === this.dpr) return;
    this.w = r.width; this.h = r.height; this.dpr = dpr;
    this.canvas.width = Math.round(r.width * dpr);
    this.canvas.height = Math.round(r.height * dpr);
    this.canvas.style.width = `${r.width}px`;
    this.canvas.style.height = `${r.height}px`;
    this.redraw();
  }

  undo(): void {
    if (!this.strokes.length) return;
    this.strokes.pop();
    this.redraw(); this.changed();
  }

  clear(): void {
    if (this.isEmpty()) return;
    this.strokes = []; this.live = null;
    this.redraw(); this.changed();
  }

  destroy(): void { this.ro?.disconnect(); this.ro = null; }

  /** Black-on-white PNG of just the writing, as a data URL; null if nothing is drawn. */
  toPng(): string | null {
    const all = this.strokes.filter((s) => s.length);
    if (!all.length) return null;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const s of all) for (const p of s) { x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x); y1 = Math.max(y1, p.y); }
    const bw = Math.max(24, x1 - x0), bh = Math.max(24, y1 - y0);
    const scale = Math.min(3, MAX_EXPORT_SIDE / Math.max(bw + PAD * 2, bh + PAD * 2));
    const out = document.createElement("canvas");
    out.width = Math.max(1, Math.round((bw + PAD * 2) * scale));
    out.height = Math.max(1, Math.round((bh + PAD * 2) * scale));
    const g = out.getContext("2d");
    if (!g) return null;
    g.fillStyle = "#fff";
    g.fillRect(0, 0, out.width, out.height);
    g.setTransform(scale, 0, 0, scale, (PAD - x0) * scale, (PAD - y0) * scale);
    this.paint(g, all, "#000", 1.25);                                // slightly bolder: easier to read than the screen ink
    return out.toDataURL("image/png");
  }

  // ── pointer handling ─────────────────────────────────────────────────────────────────────

  private at(e: PointerEvent): Pt {
    const r = this.canvas.getBoundingClientRect();
    const p = e.pointerType === "pen" ? (e.pressure || 0.5) : 0.5;
    return { x: e.clientX - r.left, y: e.clientY - r.top, p };
  }

  private down(e: PointerEvent): void {
    if (e.pointerType === "pen") this.penSeen = true;
    else if (e.pointerType === "touch" && this.penSeen) return;     // palm rejection once a pen has been used
    if (e.button !== 0 && e.pointerType === "mouse") return;
    if (this.pointerId !== null) return;
    e.preventDefault(); e.stopPropagation();
    this.fit();
    this.pointerId = e.pointerId;
    try { this.canvas.setPointerCapture(e.pointerId); } catch { /* not fatal */ }
    this.live = [this.at(e)];
    this.hint.classList.add("gone");
    this.redraw();
  }

  private move(e: PointerEvent): void {
    if (e.pointerId !== this.pointerId || !this.live) return;
    e.preventDefault();
    const evs = typeof e.getCoalescedEvents === "function" ? e.getCoalescedEvents() : [];
    for (const ev of evs.length ? evs : [e]) {
      const p = this.at(ev);
      const last = this.live[this.live.length - 1];
      if (Math.hypot(p.x - last.x, p.y - last.y) >= 0.8) this.live.push(p);
    }
    this.redraw();
  }

  private up(e: PointerEvent): void {
    if (e.pointerId !== this.pointerId) return;
    this.pointerId = null;
    try { this.canvas.releasePointerCapture(e.pointerId); } catch { /* already released */ }
    const s = this.live;
    this.live = null;
    if (s && s.length) {
      if (s.length === 1) s.push({ ...s[0], x: s[0].x + 0.01 });    // a tap makes a dot
      this.strokes.push(s);
      this.changed();
    }
    this.redraw();
  }

  private changed(): void {
    this.hint.classList.toggle("gone", !this.isEmpty());
    this.onChange();
  }

  // ── drawing ──────────────────────────────────────────────────────────────────────────────

  private redraw(): void {
    const g = this.ctx;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, this.canvas.width, this.canvas.height);
    g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    const ink = getComputedStyle(this.canvas).color || "#fff";
    this.paint(g, this.live ? [...this.strokes, this.live] : this.strokes, ink, 1);
  }

  /** Smooth strokes: quadratic curves through the midpoints of consecutive samples, width follows pressure. */
  private paint(g: CanvasRenderingContext2D, strokes: Stroke[], color: string, widthScale: number): void {
    g.strokeStyle = color; g.fillStyle = color;
    g.lineCap = "round"; g.lineJoin = "round";
    for (const s of strokes) {
      if (s.length < 2) continue;
      const width = (p: number) => (MIN_WIDTH + (MAX_WIDTH - MIN_WIDTH) * Math.min(1, Math.max(0, p))) * widthScale;
      if (s.length === 2 || (Math.abs(s[0].x - s[s.length - 1].x) < 0.05 && s.length < 3)) {
        g.beginPath(); g.arc(s[0].x, s[0].y, width(s[0].p) / 2 + 0.4, 0, Math.PI * 2); g.fill();
        if (s.length === 2 && Math.hypot(s[1].x - s[0].x, s[1].y - s[0].y) > 0.5) {
          g.lineWidth = width(s[0].p); g.beginPath(); g.moveTo(s[0].x, s[0].y); g.lineTo(s[1].x, s[1].y); g.stroke();
        }
        continue;
      }
      let prev = s[0];
      for (let i = 1; i < s.length - 1; i++) {
        const cur = s[i], nxt = s[i + 1];
        const mx = (cur.x + nxt.x) / 2, my = (cur.y + nxt.y) / 2;
        const sx = i === 1 ? prev.x : (prev.x + cur.x) / 2, sy = i === 1 ? prev.y : (prev.y + cur.y) / 2;
        g.lineWidth = width((prev.p + cur.p) / 2);
        g.beginPath(); g.moveTo(sx, sy); g.quadraticCurveTo(cur.x, cur.y, mx, my); g.stroke();
        prev = cur;
      }
      const last = s[s.length - 1];
      g.lineWidth = width(last.p);
      g.beginPath(); g.moveTo((prev.x + last.x) / 2, (prev.y + last.y) / 2); g.lineTo(last.x, last.y); g.stroke();
    }
  }
}
