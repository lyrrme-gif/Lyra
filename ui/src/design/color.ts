// Small colour helpers shared by every window (no dependencies).
// hex → OKLCH hue lets the user's accent swatch drive the whole tonal palette.

export interface Oklch { l: number; c: number; h: number }

const lin = (v: number) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);

/** "#rrggbb" (or "#rgb") → OKLCH. Returns null when the string is not a hex colour. */
export function hexToOklch(hex: string): Oklch | null {
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  let s = m[1];
  if (s.length === 3) s = s.split("").map((c) => c + c).join("");
  const r = lin(parseInt(s.slice(0, 2), 16) / 255);
  const g = lin(parseInt(s.slice(2, 4), 16) / 255);
  const b = lin(parseInt(s.slice(4, 6), 16) / 255);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const mm = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const ss = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const L = 0.2104542553 * l + 0.793617785 * mm - 0.0040720468 * ss;
  const a = 1.9779984951 * l - 2.428592205 * mm + 0.4505937099 * ss;
  const bb = 0.0259040371 * l + 0.7827717662 * mm - 0.808675766 * ss;
  const c = Math.hypot(a, bb);
  let h = (Math.atan2(bb, a) * 180) / Math.PI;
  if (h < 0) h += 360;
  return { l: L, c, h };
}

/** Hue (0–360) of a hex colour, or `fallback` when it cannot be read. */
export function hueOf(hex: string, fallback = 285): number {
  const o = hexToOklch(hex);
  return o && o.c > 0.01 ? Math.round(o.h) : fallback;
}
