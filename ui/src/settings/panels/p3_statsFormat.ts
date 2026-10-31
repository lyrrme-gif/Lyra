// Phase 3 - pure helpers for the Statistics panel (no DOM, so they can be tested with plain node).

export interface SeriesPoint { key: string; words: number; dictations: number }

/** "45 s", "12 min", "1 h 05 min", "130 h". Zero is "0 min" so the counter never looks broken. */
export function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds));
  if (s === 0) return "0 min";
  if (s < 60) return `${s} s`;
  const min = Math.round(s / 60);
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return h >= 100 || m === 0 ? `${h} h` : `${h} h ${String(m).padStart(2, "0")} min`;
}

/** 1234 -> "1,234" (the user's locale decides the separator). */
export function formatCount(n: number, locale?: string): string {
  return Math.round(n).toLocaleString(locale);
}

/** A round top for the y axis (1, 2, 5 x 10^k) so the gridlines read as 0 / half / full. */
export function niceMax(maxValue: number): number {
  if (!(maxValue > 0)) return 10;
  const exp = Math.pow(10, Math.floor(Math.log10(maxValue)));
  const f = maxValue / exp;
  const step = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
  return Math.max(2, step * exp);
}

export interface Bar { x: number; y: number; w: number; h: number; point: SeriesPoint; index: number }

/** Bars for a plot area of width x height. Zero values get h = 0 (the caller draws a stub). */
export function barGeometry(points: SeriesPoint[], width: number, height: number, top: number): { bars: Bar[]; max: number } {
  const max = niceMax(Math.max(0, ...points.map((p) => p.words)));
  const n = Math.max(1, points.length);
  const slot = width / n;
  const w = Math.max(2, Math.min(36, slot * 0.64));
  const bars = points.map((p, index) => {
    const h = p.words > 0 ? Math.max(3, (p.words / max) * height) : 0;
    return { x: index * slot + (slot - w) / 2, y: top + height - h, w, h, point: p, index };
  });
  return { bars, max };
}

/** Which bars get a label under them, so a 30-day chart is not a wall of text. */
export function labelEvery(count: number): number {
  return count <= 8 ? 1 : count <= 16 ? 2 : count <= 31 ? 5 : Math.ceil(count / 8);
}

function parseKey(key: string): Date {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(y, (m || 1) - 1, d || 1);
}

/** Short axis label for a bar: weekday for a week, "20 Oct" for longer ranges, "Oct" for months. */
export function axisLabel(key: string, granularity: "day" | "month", count: number, locale?: string): string {
  const d = parseKey(key);
  if (granularity === "month") return d.toLocaleDateString(locale, { month: "short" });
  if (count <= 8) return d.toLocaleDateString(locale, { weekday: "short" });
  return d.toLocaleDateString(locale, { day: "numeric", month: "short" });
}

/** Full text for a bar's tooltip: "Tue 20 Oct: 1,240 words in 9 dictations". */
export function barTitle(p: SeriesPoint, granularity: "day" | "month", locale?: string): string {
  const d = parseKey(p.key);
  const when = granularity === "month"
    ? d.toLocaleDateString(locale, { month: "long", year: "numeric" })
    : d.toLocaleDateString(locale, { weekday: "short", day: "numeric", month: "short" });
  if (p.words === 0) return `${when}: nothing dictated`;
  return `${when}: ${formatCount(p.words, locale)} ${p.words === 1 ? "word" : "words"} in ${p.dictations} ${p.dictations === 1 ? "dictation" : "dictations"}`;
}

/** "3 days" / "1 day". */
export function plural(n: number, one: string, many = one + "s"): string {
  return `${formatCount(n)} ${n === 1 ? one : many}`;
}

/** A friendly one-liner about the streak. */
export function streakLine(current: number, longest: number): string {
  if (current <= 0) return longest > 0 ? `Your best streak is ${plural(longest, "day")}. Dictate today to start a new one.` : "Dictate today to start a streak.";
  if (current >= longest) return current === 1 ? "A streak begins: 1 day so far." : `${plural(current, "day")} in a row, your longest yet.`;
  return `${plural(current, "day")} in a row. Your best is ${plural(longest, "day")}.`;
}
