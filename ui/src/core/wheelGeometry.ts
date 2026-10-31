// Launcher geometry, with no DOM so it can be tested: ring positions, what a pointer position
// points at, where a tooltip goes, and how the layout shrinks on a small screen.
// views/wheel.ts draws the launcher and uses this to read the pointer.
//
// The launcher is ONE ring of GROUP icons (Talk, Media, Time ...) with a grey × in the middle. Clicking a
// group does not open another circle: the ring stays where it is, the group turns black, and that group's
// tabs (2-4 of them) fan out in a small arc around it, on its outer side.
// Everything is in "design pixels" from the CENTRE of the window. The window opens in the middle
// of the active monitor (src-tauri/src/wheel.rs) and the layout scales down as a whole when the
// window is smaller than WHEEL_W × WHEEL_H, so nothing is ever clipped.

export const WHEEL_W = 640;            // design size of the window; Rust (wheel.rs) uses the same numbers
export const WHEEL_H = 440;
export const WHEEL_CX = WHEEL_W / 2;
export const WHEEL_CY = WHEEL_H / 2;

export const NODE_R = 26;              // a tab icon (a tab on its own in the ring)
export const TAB_R = 22;               // a tab icon around an open group
export const CAT_R = 26;               // a group icon (small, close together)
export const CENTER_R = 40;            // the black category in the middle of a category view
export const CLOSE_R = 16;             // the small grey × in the middle of the root view
export const HIT_SLACK = 9;            // how far outside an icon the pointer still counts as on it

export interface Pt { x: number; y: number }

/** `n` points evenly round a circle of `radius`, the first at `startDeg` (-90 = straight up). */
export function ring(n: number, radius: number, startDeg = -90): Pt[] {
  return Array.from({ length: n }, (_, i) => {
    const a = ((startDeg + (360 / n) * i) * Math.PI) / 180;
    return { x: Math.round(radius * Math.cos(a) * 100) / 100, y: Math.round(radius * Math.sin(a) * 100) / 100 };
  });
}

/** Where the categories go on the root view. */
export function rootLayout(n: number): Pt[] {
  if (n <= 0) return [];
  if (n === 1) return [{ x: 0, y: -80 }];
  return ring(n, n <= 2 ? 62 : n <= 4 ? 70 : n <= 6 ? 76 : 100);
}

/**
 * Where the open group's `n` tabs go: in an arc AROUND the group, on its outer side (away from the middle of the
 * ring), like the ring of tabs round a selected icon in the launcher concept. The ring stays where it is; only
 * this small arc appears. Tabs are `ARC_STEP` degrees apart; keep a group to 4 tabs (the layout check covers up to 4); more than 5 wrap onto a second, wider arc.
 */
export const ARC_R = 70;                                        // distance from the group to its tabs
export const ARC_STEP = 45;                                     // degrees between neighbouring tabs
export function tabsAround(group: Pt, n: number): Pt[] {
  if (n <= 0) return [];
  const len = Math.hypot(group.x, group.y);
  const out = len < 1 ? 0 : Math.atan2(group.y, group.x);       // straight away from the middle (a group in the middle opens to the right)
  const per = 5;
  return Array.from({ length: n }, (_, i) => {
    const ringIdx = Math.floor(i / per);
    const inRing = Math.min(per, n - ringIdx * per);
    const k = i - ringIdx * per;
    const rad = ARC_R + ringIdx * (TAB_R * 2 + 10);
    const a = out + ((k - (inRing - 1) / 2) * ARC_STEP * Math.PI) / 180;
    return { x: Math.round((group.x + rad * Math.cos(a)) * 100) / 100, y: Math.round((group.y + rad * Math.sin(a)) * 100) / 100 };
  });
}

/** A ring of tabs round the middle (kept for a registry with a single group). */
export function tabLayout(n: number): Pt[] {
  if (n <= 0) return [];
  if (n <= 9) return ring(n, n <= 2 ? 100 : n <= 4 ? 120 : 150);
  const inner = Math.floor(n / 2);
  return [...ring(n - inner, 180), ...ring(inner, 105, -90 + 180 / inner)];
}

/** How much to shrink the whole layout for a window of this size (never above 1). */
export function fitScale(winW: number, winH: number): number {
  if (!(winW > 0) || !(winH > 0)) return 1;
  return Math.min(1, winW / WHEEL_W, winH / WHEEL_H);
}

export type NodeKind = "dest" | "cat" | "center" | "close";
export interface HitNode extends Pt { r: number; kind: NodeKind }

export type WheelHit = { kind: "node"; index: number } | { kind: "none" };

/** What is the pointer pointing at? `dx`/`dy` are design pixels from the centre. The closest icon within its circle plus a small slack wins. */
export function constellationHit(dx: number, dy: number, nodes: readonly HitNode[]): WheelHit {
  let best = -1;
  let bestD = Infinity;
  nodes.forEach((n, i) => {
    const d = Math.hypot(dx - n.x, dy - n.y);
    if (d <= n.r + HIT_SLACK && d < bestD) { best = i; bestD = d; }
  });
  return best >= 0 ? { kind: "node", index: best } : { kind: "none" };
}

export type TipSide = "right" | "left" | "above" | "below";
export interface TipPlacement { side: TipSide; x: number; y: number; w: number; h: number }

const TIP_GAP = 9;
const rectHitsCircle = (rx: number, ry: number, rw: number, rh: number, c: HitNode) => {
  const nx = Math.max(rx, Math.min(c.x, rx + rw));
  const ny = Math.max(ry, Math.min(c.y, ry + rh));
  return Math.hypot(c.x - nx, c.y - ny) < c.r + 2;
};

/**
 * Where the tooltip pill goes next to node `at`: beside it, or above / below it when the sides are
 * taken. A spot that would leave the window or cover another icon is penalised; among equally good
 * spots the one facing away from the middle wins (like a label on a ring). Returns the pill's
 * top-left corner (design pixels from the centre).
 */
export function tooltipPlacement(at: HitNode, others: readonly HitNode[], w: number, h: number): TipPlacement {
  const halfW = WHEEL_W / 2 - 6;
  const halfH = WHEEL_H / 2 - 6;
  const dirs: Record<TipSide, Pt> = { right: { x: 1, y: 0 }, left: { x: -1, y: 0 }, above: { x: 0, y: -1 }, below: { x: 0, y: 1 } };
  const len = Math.hypot(at.x, at.y);
  const out = len < 1 ? { x: 1, y: 0.3 } : { x: at.x / len, y: at.y / len };    // away from the middle
  const spot = (side: TipSide) => {
    let x: number, y: number;
    if (side === "right") { x = at.x + at.r + TIP_GAP; y = at.y - h / 2; }
    else if (side === "left") { x = at.x - at.r - TIP_GAP - w; y = at.y - h / 2; }
    else if (side === "above") { x = at.x - w / 2; y = at.y - at.r - TIP_GAP - h; }
    else { x = at.x - w / 2; y = at.y + at.r + TIP_GAP; }
    x = Math.max(-halfW, Math.min(halfW - w, x));
    y = Math.max(-halfH, Math.min(halfH - h, y));
    let penalty = 0;
    // Pushed back inside the window, a side spot may now sit on the icon itself: that is as bad as covering another.
    for (const o of others) if (rectHitsCircle(x, y, w, h, o)) penalty += 500;
    if (side === "right" && x < at.x + at.r) penalty += 500;
    if (side === "left" && x + w > at.x - at.r) penalty += 500;
    return { side, x, y, penalty, face: dirs[side].x * out.x + dirs[side].y * out.y };
  };
  const all = (["right", "left", "above", "below"] as TipSide[]).map(spot);
  all.sort((a, b) => a.penalty - b.penalty || b.face - a.face);
  const pick = all[0];
  return { side: pick.side, x: pick.x, y: pick.y, w, h };
}

/**
 * Arrow-key navigation: the nearest icon in that direction, within a 60° cone. `from` null = start
 * from the middle. The × and the middle "back" icon are never keyboard targets (Esc / Backspace do those).
 */
export function neighbour(from: Pt | null, nodes: readonly HitNode[], dir: "left" | "right" | "up" | "down", skip = -1): number {
  const o = from ?? { x: 0, y: 0 };
  const v = dir === "left" ? { x: -1, y: 0 } : dir === "right" ? { x: 1, y: 0 } : dir === "up" ? { x: 0, y: -1 } : { x: 0, y: 1 };
  let best = -1;
  let bestScore = Infinity;
  nodes.forEach((n, i) => {
    if (i === skip || n.kind === "close" || n.kind === "center") return;
    const dx = n.x - o.x;
    const dy = n.y - o.y;
    const dist = Math.hypot(dx, dy);
    if (dist < 1) return;
    const cos = (dx * v.x + dy * v.y) / dist;
    if (cos < 0.5) return;
    const s = dist * (2 - cos);
    if (s < bestScore) { best = i; bestScore = s; }
  });
  return best;
}
