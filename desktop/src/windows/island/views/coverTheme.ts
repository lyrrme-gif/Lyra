// Music tab colours, derived from the cover's average colour.
//
// Input is the "r,g,b" string that `artColor()` (pulse.ts) already returns: this file does NOT read images, it only
// turns that one colour into a set of CSS variables. No DOM and no imports, so it can be tested with plain node.
//
// Readability rule: the tab always shows white text on the gradient, so the gradient stops are darkened (towards
// black, keeping the hue) until white text on them reaches a minimum WCAG contrast, even for a bright yellow or
// white cover. The glow is a soft radial of the lighter cover colour, and its strength is capped so that the
// brightest point of the glow still keeps white text legible.

export type RGB = [number, number, number];

const BLACK: RGB = [0, 0, 0];
const WHITE: RGB = [255, 255, 255];

/** White text on the gradient start / end must reach at least this contrast (WCAG: 4.5 = normal text AA). */
export const MIN_CONTRAST_BG_A = 7;
export const MIN_CONTRAST_BG_B = 10;
/** The brightest point of the glow (glow colour at full strength over the gradient start). */
export const MIN_CONTRAST_GLOW = 5.5;
/** Opacity of the not-yet-sung part of the current lyric line (white-ish text). Checked: with 0.66 it keeps >= 3:1
 *  contrast, even on the brightest point of the glow of the brightest covers (see the contrast tests). */
export const UNSUNG_ALPHA = 0.66;

const clamp255 = (n: number) => Math.max(0, Math.min(255, Math.round(n)));

export function parseRgb(s: string | null | undefined): RGB | null {
  if (!s) return null;
  const p = s.split(",").map((x) => Number(x.trim()));
  if (p.length !== 3 || p.some((n) => !Number.isFinite(n))) return null;
  return [clamp255(p[0]), clamp255(p[1]), clamp255(p[2])];
}

export const mix = (a: RGB, b: RGB, t: number): RGB => [
  clamp255(a[0] + (b[0] - a[0]) * t),
  clamp255(a[1] + (b[1] - a[1]) * t),
  clamp255(a[2] + (b[2] - a[2]) * t),
];

const str = (c: RGB) => `${c[0]},${c[1]},${c[2]}`;

/** WCAG relative luminance, 0..1. */
export function luminance(c: RGB): number {
  const f = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
}

/** WCAG contrast ratio between two colours, 1..21. */
export function contrast(a: RGB, b: RGB): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

export const contrastWithWhite = (c: RGB) => contrast(WHITE, c);

/** Darkens `c` towards black, in small steps, until white text on it reaches `min` contrast. */
export function darkenUntil(c: RGB, min: number): RGB {
  for (let t = 0; t <= 1.0001; t += 0.03) {
    const o = mix(c, BLACK, t);
    if (contrastWithWhite(o) >= min) return o;
  }
  return BLACK;
}

export interface CoverTheme {
  /** Custom properties for the music view root (the "--cv-*" set). */
  vars: Record<string, string>;
  /** Same colours as plain "r,g,b" strings, for the per-layer copies the cross-fade needs. */
  bgA: string;
  bgB: string;
  glow: string;
  glowA: string;
  glowA2: string;
}

/** Builds the theme for a cover colour, or null when there is no usable colour (caller falls back to the island theme). */
export function buildCoverTheme(rgb: string | null | undefined): CoverTheme | null {
  const base = parseRgb(rgb);
  if (!base) return null;

  const light = mix(base, WHITE, 0.38);
  const dark = mix(base, BLACK, 0.5);
  const hi = mix(base, WHITE, 0.8);                        // highlighted lyric text, progress, active buttons

  const bgA = darkenUntil(mix(base, BLACK, 0.3), MIN_CONTRAST_BG_A);
  const bgB = darkenUntil(mix(base, BLACK, 0.72), MIN_CONTRAST_BG_B);

  // Glow: as strong as it can be while white text still reads on its brightest point.
  let a = 0.5;
  while (a > 0 && contrastWithWhite(mix(bgA, light, a)) < MIN_CONTRAST_GLOW) a -= 0.02;
  a = Math.max(0, Math.round(a * 100) / 100);
  const a2 = Math.round(a * 55) / 100;

  const t: CoverTheme = {
    vars: {
      "--cv-rgb": str(base),
      "--cv-light-rgb": str(light),
      "--cv-dark-rgb": str(dark),
      "--cv-hi-rgb": str(hi),
      "--cv-bg-a": str(bgA),
      "--cv-bg-b": str(bgB),
      "--cv-glow-rgb": str(light),
      "--cv-glow-a": String(a),
    },
    bgA: str(bgA),
    bgB: str(bgB),
    glow: str(light),
    glowA: String(a),
    glowA2: String(a2),
  };
  return t;
}
