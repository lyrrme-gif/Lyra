// Whole-screen previews for Settings: a tiny Windows desktop (wallpaper, taskbar, cursor) with the island on it, so
// every setting and feature is seen in context instead of as a cropped thumbnail. Pure DOM + CSS (screenPreview.css),
// decorative (aria-hidden), no backend. Animations only run while a preview is actually on screen.
//
//   screenPreview({ island: { size: "wide", html: ISL.music() }, layer: ..., cursor: [x0, y0, x1, y1] })
//
// Used by: the island options and theme picker (General), Live Activities and (featureScenes.ts) every row of Features.

import "./screenPreview.css";
import { h } from "../../shared/views/dom";
import { createCharacter } from "../../shared/mascot/character";
import { isReallyVisible } from "../../shared/core/visibility";
import { onMochiShape } from "./mochiPanel";

export type IslandSize = "dot" | "pill" | "wide" | "panel";
export interface IslandSpec {
  size?: IslandSize;
  shape?: "island" | "notch";
  material?: "black" | "frost" | "glass" | "light";
  bg?: string; accent?: string; glow?: string;
  html?: string;
  /** Animate the island popping in and out (a notification). */
  pop?: boolean;
  /** A real element (the Live Activities snapshot) instead of html. */
  el?: HTMLElement;
}
export interface ScreenSpec {
  island?: IslandSpec | null;
  /** Extra HTML on the desktop (windows, wheel, dock ...). Goes under the island. */
  layer?: string;
  /** Extra elements on the desktop. */
  els?: HTMLElement[];
  /** Cursor path in cqw: [fromX, fromY, toX, toY]. false = no cursor. */
  cursor?: [number, number, number, number] | false;
  light?: boolean;
  swapWall?: boolean;
  accent?: string;
}

// ── only animate what is on screen ───────────────────────────────────────────────────────────
let io: IntersectionObserver | null = null;
function watch(el: HTMLElement): void {
  if (typeof IntersectionObserver === "undefined") { el.classList.add("run"); return; }
  io ??= new IntersectionObserver((entries) => {
    for (const e of entries) e.target.classList.toggle("run", e.isIntersecting && isReallyVisible());
  }, { rootMargin: "40px" });
  io.observe(el);
}

/** A real Mochi in a preview island: follows the character chosen in Settings, animates only while on screen. */
function attachMochi(host: HTMLElement, root: HTMLElement): void {
  const ch = createCharacter(host, { tight: true, sharp: 2 });
  onMochiShape((sh) => ch.setShape?.(sh));
  ch.setEmote("idle");
  let seen = false;
  new IntersectionObserver((es) => ch.setActive?.(es.some((e) => e.isIntersecting))).observe(root);
  const t0 = performance.now();
  const timer = window.setInterval(() => {
    if (root.isConnected) seen = true; else if (seen) { window.clearInterval(timer); ch.destroy(); return; }
    if (!root.classList.contains("run") || !isReallyVisible()) return;
    ch.look(Math.cos(((performance.now() - t0) / 6000) * Math.PI * 2) * 0.8, 0.25);
  }, 120);
}

const CURSOR = `<svg class="sp-cur" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3l14 8-6 2 4 7-3 1-4-7-5 4z" fill="#fff" stroke="#000" stroke-width="1.3" stroke-linejoin="round"/></svg>`;
const TASKBAR = `<div class="sp-task"><i></i><i></i><i class="on"></i><i></i><i></i><em>9:41</em></div>`;

export function screenPreview(spec: ScreenSpec = {}): HTMLElement {
  const root = h("div", { class: `sp${spec.light ? " light" : ""}${spec.swapWall ? " swap" : ""}`, "aria-hidden": "true" });
  if (spec.accent) root.style.setProperty("--sp-a", spec.accent);
  root.append(h("i", { class: "sp-wall" }));
  if (spec.layer) root.insertAdjacentHTML("beforeend", spec.layer);
  if (spec.els) root.append(...spec.els);
  const isl = spec.island === undefined ? { size: "pill" as IslandSize, html: ISL.idle() } : spec.island;
  if (isl) {
    const cls = ["sp-isl", isl.size ?? "pill", isl.shape === "notch" ? "notch" : "", isl.material && isl.material !== "black" ? isl.material : "", isl.pop ? "pop" : ""].filter(Boolean).join(" ");
    const el = h("div", { class: cls });
    if (isl.bg) el.style.setProperty("--sp-bg", isl.bg);
    if (isl.accent) el.style.setProperty("--sp-a", isl.accent);
    if (isl.glow) { el.style.setProperty("--sp-glow", isl.glow); el.style.setProperty("--sp-ring", `color-mix(in srgb, ${isl.accent ?? "#fff"} 55%, transparent)`); }
    if (isl.el) el.append(isl.el); else el.innerHTML = isl.html ?? "";
    root.append(el);
    el.querySelectorAll<HTMLElement>(".sp-mslot").forEach((slot) => attachMochi(slot, root));
  }
  if (spec.cursor !== false) {
    const [x0, y0, x1, y1] = spec.cursor ?? [60, 38, 72, 30];
    root.insertAdjacentHTML("beforeend", CURSOR);
    const c = root.lastElementChild as HTMLElement;
    c.style.cssText = `--x0:${x0};--y0:${y0};--x1:${x1};--y1:${y1}`;
  }
  root.insertAdjacentHTML("beforeend", TASKBAR);
  watch(root);
  return root;
}

// ── island contents ──────────────────────────────────────────────────────────────────────────
const L = (w: number, cls = "") => `<b class="sp-ln ${cls}" style="--sp-w:${w}"></b>`;
const SQ = (c = "") => `<i class="sp-sq"${c ? ` style="--sp-c:${c}"` : ""}></i>`;
const COL = (cls: string, ...x: string[]) => `<span class="sp-col ${cls}">${x.join("")}</span>`;
const ROW = (cls: string, ...x: string[]) => `<span class="sp-row ${cls}">${x.join("")}</span>`;
const FACE = `<i class="sp-face"><u></u><u></u></i>`;
/** The real Mochi goes here (same engine as the notch), in the character chosen in Settings. */
const MSLOT = `<i class="sp-mslot"></i>`;
const MIC = `<svg class="sp-mic" viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/></svg>`;
const EQ = `<i class="sp-eq"><u></u><u></u><u></u><u></u></i>`;
const RING = `<svg class="sp-ring" viewBox="0 0 20 20"><circle cx="10" cy="10" r="7.5" class="bg"/><circle cx="10" cy="10" r="7.5" class="fg"/></svg>`;
const PATHS: Record<string, string> = {
  bell: "M6 16v-4.5a6 6 0 0 1 12 0V16l1.6 2H4.4zM10 20.5a2 2 0 0 0 4 0",
  cal: "M5 6h14v13H5zM5 10h14M9 4v4M15 4v4",
  check: "M5 12.5 9.5 17 19 7.5",
  mic: "M12 4a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V7a3 3 0 0 1 3-3zM6 11a6 6 0 0 0 12 0M12 17v3",
  note: "M9 17V6l10-2v11M9 17a2.5 2.5 0 1 1-1-2M19 15a2.5 2.5 0 1 1-1-2",
  cam: "M4 8h4l1.5-2h5L16 8h4v11H4zM12 11.5a3 3 0 1 0 .01 0",
  sun: "M12 8a4 4 0 1 0 .01 0M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.4 1.4M17.6 17.6 19 19M5 19l1.4-1.4M17.6 6.4 19 5",
  sliders: "M5 7h14M5 12h14M5 17h14M9 5v4M15 10v4M8 15v4",
  clip: "M8 5h8v3H8zM6 7h12v13H6z",
  down: "M12 4v11M7 11l5 5 5-5M5 20h14",
  wifi: "M3 9a13 13 0 0 1 18 0M6 12.5a8.5 8.5 0 0 1 12 0M9 16a4 4 0 0 1 6 0M12 19.5v.01",
  batt: "M3 8h15v8H3zM21 11v2M6 11v2M9 11v2",
  search: "M11 5a6 6 0 1 0 .01 0M16 16l4 4",
  code: "M9 7l-5 5 5 5M15 7l5 5-5 5",
  lang: "M4 6h9M8.5 4v2M6 6c1 4 3 6 6 7M11 6c-1 4-3 6-6 7M13 20l4-9 4 9M14.5 17h5",
  book: "M5 5h6a2 2 0 0 1 2 2v13a2 2 0 0 0-2-2H5zM19 5h-6",
  bolt: "M13 3 5 13h6l-1 8 8-10h-6z",
  chip: "M7 7h10v10H7zM10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4",
  speaker: "M4 10v4h4l5 4V6L8 10zM16 9a4 4 0 0 1 0 6",
  palette: "M12 4a8 8 0 1 0 0 16c1.5 0 2-1 1.5-2-.6-1.2.2-2.4 1.6-2.4H17a3 3 0 0 0 3-3A8 8 0 0 0 12 4",
  stats: "M5 19V11M10 19V6M15 19v-6M20 19V9",
  grid: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  list: "M9 7h11M9 12h11M9 17h11M4.5 7h.01M4.5 12h.01M4.5 17h.01",
  cursor: "M5 3l14 8-6 2 4 7-3 1-4-7-5 4z",
  rec: "M12 8a4 4 0 1 0 .01 0",
  cloud: "M7 18a4 4 0 0 1-.6-8A6 6 0 0 1 18 11a3.5 3.5 0 0 1-.5 7z",
  mail: "M3 7l9 6 9-6M3 7v10h18V7z",
  globe: "M12 3a9 9 0 1 0 .01 0M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18",
};
const IC = (name: string, c = "") => `<i class="sp-ic"${c ? ` style="--sp-c:${c}"` : ""}><svg viewBox="0 0 24 24"><path d="${PATHS[name] ?? PATHS.grid}"/></svg></i>`;

export const ISL = {
  // The resting island is just the Mochi, alone in the middle (same as the real notch).
  idle: () => `<i class="sp-mslot solo"></i>`,
  search: () => `${MSLOT}<span class="sp-tx m" style="flex:1">Search or ask Lyra</span>${MIC}`,
  /** The expanded island, in a theme's own colours (themeScene). */
  themePanel: () => `${ROW("", FACE, L(18, "b"), `<span style="flex:1"></span>`, `<i class="sp-dot"></i>`)}${ROW("", `<i class="sp-sq art"></i>`, COL("grow", L(26, "b"), L(16)), EQ)}<span class="sp-bar"><i></i></span><span class="sp-ctl"><i></i><i class="play acc"></i><i></i></span>`,
  music: () => ROW("", SQ("#e0526a"), COL("grow", L(24, "b"), L(16)), EQ),
  musicPanel: () => `${ROW("", SQ("#e0526a"), COL("grow", L(26, "b"), L(18)), EQ)}<span class="sp-bar"><i></i></span><span class="sp-ctl"><i></i><i class="play"></i><i></i></span>`,
  timer: () => `${RING}<span class="sp-tx">9:58</span><span class="sp-tx m">focus</span><span style="flex:1"></span><i class="sp-dot pulse" style="background:#ff9f0a"></i>`,
  toast: (c = "#25d366", ic = "mail") => `${IC(ic, c)}${COL("grow", L(26, "b"), L(18))}`,
  volume: () => `${IC("speaker", "#3d7bff")}<span class="sp-bar"><i></i></span><span class="sp-tx m">62</span>`,
  rec: () => `<i class="sp-dot r pulse"></i><span class="sp-tx">REC</span><span class="sp-tx m">00:12</span>`,
  iconLines: (ic: string, c = "", a = 26, b = 18) => `${IC(ic, c)}${COL("grow", L(a, "b"), L(b))}`,
  chat: () => `<span class="sp-bub me sp-seq"></span><span class="sp-bub ly sp-seq d2">${L(26, "b")}${L(18)}</span>`,
  todo: () => ROW("", `<i class="sp-chk on"></i>`, L(30, "b")) + ROW("", `<i class="sp-chk tick"></i>`, L(24, "b")) + ROW("", `<i class="sp-chk"></i>`, L(18)),
  clipboard: () => `<span class="sp-card sp-seq">${L(34, "b")}${L(22)}</span><span class="sp-card sp-seq d2">${L(26, "b")}${L(30)}</span>`,
  controls: () => ROW("", IC("sun", "#ffb340"), `<span class="sp-bar"><i></i></span>`, `<i class="sp-tog on"></i>`) + ROW("", IC("wifi", "#3d7bff"), L(20, "b"), `<i class="sp-tog on" style="margin-left:auto"></i>`) + ROW("", IC("speaker", "#30d158"), L(16, "b"), `<i class="sp-tog" style="margin-left:auto"></i>`),
  stats: () => `<span class="sp-chart"><u style="height:40%"></u><u style="height:70%"></u><u style="height:55%"></u><u style="height:90%"></u><u style="height:65%"></u></span>`,
  code: () => `<span class="sp-col sp-code" style="gap:1.2cqw">${L(30)}${L(40)}${L(26)}${L(36)}${L(20)}${L(32)}</span>`,
  files: () => ROW("", IC("search", "#5b8cff"), L(26, "b")) + `<span class="sp-card">${ROW("", SQ("#5b8cff"), COL("", L(26, "b"), L(16)))}</span>`,
  card: () => `<span class="sp-card sp-flip" style="height:15cqw;align-items:center">${L(30, "b")}${L(20)}</span>`,
  steps: () => ROW("", `<i class="sp-chk on"></i>`, L(32, "b")) + ROW("", `<i class="sp-chk on"></i>`, L(26, "b")) + ROW("", `<i class="sp-chk tick"></i>`, L(30, "b")),
  translate: () => `${IC("lang", "#3d7bff")}<span class="sp-tx">Hello</span><span class="sp-tx m">→</span><span class="sp-tx" style="color:#8fb4ff">Bonjour</span>`,
  battery: () => `${IC("batt", "#30d158")}${L(14, "b")}${IC("wifi", "#3d7bff")}${L(10)}`,
  wave: () => `${IC("note", "#3d7bff")}<i class="sp-eq" style="gap:.6cqw;height:3.4cqw"><u></u><u></u><u></u><u></u><u></u></i>${COL("grow", L(22, "b"), L(14))}`,
};


// ── ready-made scenes for settings ───────────────────────────────────────────────────────────

/** The island open (search bar) or resting, in the shape and material chosen. */
export function islandScene(o: { shape?: "island" | "notch"; material?: "black" | "frost" | "glass"; state?: "open" | "rest" } = {}): HTMLElement {
  const open = o.state === "open";
  return screenPreview({
    island: { size: open ? "wide" : "pill", shape: o.shape ?? "island", material: o.material ?? "black", html: open ? ISL.search() : ISL.idle() },
    cursor: open ? [44, 30, 52, 12] : [62, 40, 66, 30],
  });
}

/** How the tabs are reached: the wheel at the cursor, the scroller under the island, or both. */
export function navScene(kind: "wheel" | "scroller" | "both"): HTMLElement {
  const dots = ["#6d8bff", "#e0526a", "#30d158", "#ffb340", "#a97bff", "#3dc6e6"];
  const wheel = `<div class="sp-wheel">${dots.map((c, i) => `<i class="${i === 1 ? "hot" : ""}" style="--sp-r:${i * 60}deg;--sp-c:${c}"></i>`).join("")}</div>`;
  const dock = `<div class="sp-dock"><i style="--sp-c:#6d8bff" class="on"></i><i style="--sp-c:#e0526a"></i><i style="--sp-c:#30d158"></i><em>›</em></div>`;
  return screenPreview({
    layer: (kind !== "scroller" ? wheel : "") + (kind !== "wheel" ? dock : ""),
    island: { size: "pill", html: ISL.idle() },
    cursor: kind === "wheel" ? [50, 36, 58, 46] : [40, 16, 52, 14],
  });
}

/** One island theme on the desktop, with the island EXPANDED (that is where a theme shows). */
export function themeScene(t: { material: string; accent: string; glow: string }): HTMLElement {
  return screenPreview({
    island: { size: "panel", bg: t.material, accent: t.accent, glow: t.glow, html: ISL.themePanel() },
    cursor: false,
  });
}

/** For Live Activities: the island's own snapshot (real DOM) dropped on the new desktop. */
export function snapshotScene(island: HTMLElement, w: number, hgt: number): HTMLElement {
  const slot = h("div", { class: "sp-snap" }, island);
  slot.style.cssText = `position:absolute;left:50%;top:3cqw;width:${w}px;height:${hgt}px;transform-origin:50% 0;translate:-50% 0;`;
  const root = screenPreview({ island: null, els: [slot], cursor: false });
  const fit = () => { const k = (root.clientWidth || 300) * 0.0033 * (420 / 420); slot.style.scale = String(Math.min(1, (root.clientWidth * 0.6) / Math.max(w, 1)) || k); };
  requestAnimationFrame(fit);
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(fit).observe(root);
  return root;
}
