// "Who started this load?" for the Settings log (settings/settingsLog.ts).
//
// A request is started from one of four places: while a tab is being BUILT (a card that fetches in its
// constructor), by a card that waited for its tab (whenVisible), by a poll (visiblePoll), or by the user acting on
// the shown tab. fetch() starts synchronously inside those calls, so a tiny owner stack is enough to tell them
// apart: the code wraps its start in withOwner("kind:tab:detail", fn) and the tracer reads currentOwner() when the
// request begins. No timers, no DOM work, nothing runs unless something asks.
//
// Imports nothing, so core/ and settings/ can both use it.

export type LoadListener = (kind: string, msg: string, extra?: Record<string, unknown>) => void;
let listener: LoadListener | null = null;
/** The Settings log registers here to hear about lazy cards (waiting / fired). */
export function setLoadListener(l: LoadListener | null): void { listener = l; }

const stack: string[] = [];

/** "build:ai:idle", "lazy:voice:models", "poll:p3_statistics", or "" when nobody announced themselves. */
export function currentOwner(): string { return stack.length ? stack[stack.length - 1] : ""; }

/** Run `fn` with `owner` as the current owner. Only the synchronous part counts (where fetch() is called). */
export function withOwner<T>(owner: string, fn: () => T): T {
  stack.push(owner);
  try { return fn(); } finally { stack.pop(); }
}

// ── tab panels ──────────────────────────────────────────────────────────────────────────────
const panelIds = new WeakMap<Element, string>();
export function registerPanel(id: string, el: Element): void { panelIds.set(el, id); }
/** Which Settings tab does this element live in? "" if it is not inside a registered panel. */
export function tabOf(el: Element | null | undefined): string {
  const p = el && el.closest ? el.closest(".panel") : null;
  return (p && panelIds.get(p)) || "";
}

/** "build:ai:idle" -> "ai", "lazy:voice:x" -> "voice", anything else -> "". */
export function ownerTab(owner: string): string {
  const m = /^(?:build|lazy):([a-z0-9_]+)/.exec(owner);
  return m ? m[1] : "";
}

// ── cards waiting for their tab (lazy.ts) ───────────────────────────────────────────────────────
export interface LazyCard { el: Element; label: string; tab: string; since: number }
const waiting = new Set<LazyCard>();

/** A card announced "I load when my tab is first shown". The tab is known from the build that created it. */
export function lazyWaiting(el: Element, label: string): LazyCard {
  const card: LazyCard = { el, label, tab: ownerTab(currentOwner()), since: performance.now() };
  waiting.add(card);
  return card;
}
export function lazyFired(card: LazyCard): string {
  waiting.delete(card);
  const tab = card.tab || tabOf(card.el) || "?";
  listener?.("lazy", `${tab}${card.label ? ":" + card.label : ""} loaded on first view after waiting ${Math.round(performance.now() - card.since)} ms`);
  return tab;
}
/** How many cards still wait, per tab. */
export function lazyCounts(): Record<string, number> {
  const out: Record<string, number> = {};
  for (const c of waiting) {
    const t = c.tab || tabOf(c.el) || "?";
    out[t] = (out[t] ?? 0) + 1;
  }
  return out;
}

/** `div.card.p3-chart` style label for polls that gave no name. */
export function elLabel(el: Element | undefined | null): string {
  if (!el) return "?";
  const cls = Array.from(el.classList).slice(0, 2).join(".");
  return (el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + (cls ? "." + cls : "")).slice(0, 30);
}
