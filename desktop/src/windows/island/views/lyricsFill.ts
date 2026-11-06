// Word-by-word fill for the current lyric line.
//
// IMPORTANT: this is an ESTIMATE, not real word timing. The lyrics backend (LRCLIB / NetEase) only gives one
// timestamp per LINE. The words of a line are spread EVENLY over the time the line is sung, so a long word fills as
// fast as a short one and a pause inside a line is not known. Everything the UI needs is behind `WordTimingFn` /
// `fillAt()`: when word-level timestamps exist, return real spans from a function of the same shape and nothing else
// has to change.
//
// No DOM and no imports: testable with plain node.

/** One word's sung window, in ms from the start of the song. */
export interface WordSpan {
  start: number;
  end: number;
}

/** Turns the words of a line into their sung windows. Replace `evenWordTimings` with a real one later. */
export type WordTimingFn = (words: string[], lineStartMs: number, lineEndMs: number) => WordSpan[];

/** A word with REAL timing (from the lyrics service, when it has word-by-word lyrics). `text` keeps its own spaces. */
export interface TimedWord {
  t: number;      // start, ms from the start of the song
  d: number;      // duration, ms
  text: string;
}

/** Sung windows straight from real word timing. Replaces the even split for lines that have it. */
export function timedSpans(words: TimedWord[]): WordSpan[] {
  return words.map((w) => ({ start: w.t, end: w.t + Math.max(1, w.d) }));
}

/** Checks the data from the service: numbers only, in order, text present. Returns null when it is not usable. */
export function cleanTimedWords(raw: unknown): TimedWord[] | null {
  if (!Array.isArray(raw) || !raw.length) return null;
  const out: TimedWord[] = [];
  for (const w of raw) {
    const t = Number((w as TimedWord)?.t);
    const d = Number((w as TimedWord)?.d);
    const text = String((w as TimedWord)?.text ?? "");
    if (!Number.isFinite(t) || !Number.isFinite(d) || text === "") return null;
    out.push({ t, d: Math.max(1, d), text });
  }
  out.sort((a, b) => a.t - b.t);
  return out;
}

/** A line is never "sung" for longer than this, however long the gap to the next line is (instrumental break). */
export const MAX_SING_MS = 6500;
/** ...and never faster than this (two lines stamped almost together). */
export const MIN_SING_MS = 700;
/** The last line has no next line: assume this. */
export const LAST_LINE_MS = 4000;

const CJK = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]/;

/** Words of a line. Whitespace-separated; text with no spaces that is mostly CJK is split per character. */
export function splitWords(text: string): string[] {
  const t = text.trim();
  if (!t) return [];
  const spaced = t.split(/\s+/);
  if (spaced.length === 1 && spaced[0].length > 3 && CJK.test(spaced[0])) return Array.from(spaced[0]);
  return spaced;
}

/** The window in which a line is sung: from its stamp to the next line's, clamped to [MIN, MAX]. */
export function singWindow(lineStartMs: number, nextStartMs: number | null): { start: number; end: number } {
  const gap = nextStartMs == null ? LAST_LINE_MS : nextStartMs - lineStartMs;
  const dur = Math.min(MAX_SING_MS, Math.max(MIN_SING_MS, gap));
  return { start: lineStartMs, end: lineStartMs + dur };
}

/** Even spread: every word gets the same share of the window. */
export const evenWordTimings: WordTimingFn = (words, lineStartMs, lineEndMs) => {
  const n = words.length;
  if (!n) return [];
  const step = (lineEndMs - lineStartMs) / n;
  return words.map((_, i) => ({ start: lineStartMs + i * step, end: lineStartMs + (i + 1) * step }));
};

/**
 * How far the line is filled at `posMs`: `whole` words are completely filled, word `whole` (if any) is `frac`
 * (0..1) filled, the rest are empty. `whole === spans.length` means the whole line is filled (frac is then 0).
 */
export function fillAt(spans: WordSpan[], posMs: number): { whole: number; frac: number } {
  const n = spans.length;
  let whole = 0;
  while (whole < n && posMs >= spans[whole].end) whole++;
  if (whole >= n) return { whole: n, frac: 0 };
  const s = spans[whole];
  const len = s.end - s.start;
  const frac = len > 0 ? Math.min(1, Math.max(0, (posMs - s.start) / len)) : 0;
  return { whole, frac };
}
