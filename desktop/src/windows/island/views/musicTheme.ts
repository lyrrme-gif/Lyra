// Per-cover theme cache for the Music tab.
//
// The colour itself comes from the existing `artColor()` (pulse.ts: 16x16 average, lifted to a vivid mid-tone, null when
// the image can't be read). This file only remembers the answer, once per cover, so a song that comes back (or a tab
// that is reopened) does not decode the picture again. A null result (unreadable image) is remembered too.

import { artColor } from "../pulse";
import { buildCoverTheme, type CoverTheme } from "./coverTheme";

const MAX_ENTRIES = 48;
const done = new Map<string, CoverTheme | null>();
const pending = new Map<string, Promise<CoverTheme | null>>();

/** Covers are data URLs (can be 100 KB+): key the cache by a short hash instead of holding the string. */
export function coverKey(url: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < url.length; i++) {
    h ^= url.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return `${url.length}:${(h >>> 0).toString(36)}`;
}

function remember(key: string, t: CoverTheme | null) {
  done.delete(key);
  done.set(key, t);
  while (done.size > MAX_ENTRIES) {
    const oldest = done.keys().next().value;
    if (oldest === undefined) break;
    done.delete(oldest);
  }
}

/** The theme if this cover was already looked at (null = looked at, no usable colour), undefined = not yet. */
export function cachedTheme(url: string): CoverTheme | null | undefined {
  return done.get(coverKey(url));
}

/** The theme for a cover: from the cache, or one `artColor()` call. Never rejects; null = fall back to the island theme. */
export function themeForCover(url: string | null | undefined): Promise<CoverTheme | null> {
  if (!url) return Promise.resolve(null);
  const key = coverKey(url);
  if (done.has(key)) return Promise.resolve(done.get(key) ?? null);
  let p = pending.get(key);
  if (!p) {
    p = artColor(url)
      .then((rgb) => buildCoverTheme(rgb))
      .catch(() => null)
      .then((t) => {
        remember(key, t);
        pending.delete(key);
        return t;
      });
    pending.set(key, p);
  }
  return p;
}
