// The result deck: what Chat shows for a search. One look per kind of answer, every piece tappable.
//   knowledge  a person / film / series / anime: hero (poster, tilt on hover), "Watch now" shelf, result rows
//   video      YouTube: one BIG featured player card + the rest as rows (thumbnail, title, channel, play button)
//   web        Google & co: a featured result + rows
//   music      Spotify: one song per row with a play button, "More by <artist>"
// Every part has a FIXED height so island.ts can size the panel exactly (deckHeight below must match searchDeck.css).
// Header: the query + a source switch (Google / YouTube / Spotify dropdown, same on every deck).
// Footer: related searches, "Open in <provider>". Keyboard: 1-4 open that result, Up/Down move the highlight, Enter opens it.

import { h, svg } from "./dom";
import { ICONS } from "./icons";
import { Bridge } from "../core/bridge";
import type { KnowledgeCard, ResultItem, SearchResult } from "../core/state";

export type DeckKind = "knowledge" | "video" | "web" | "music";

const YT = /^(?:[\w-]+\.)*(?:youtube\.com|youtu\.be)(?:\/|$)/i;
const isYT = (it: ResultItem) => YT.test(String(it.url || "").replace(/^https?:\/\//i, ""));

export function deckKind(r: SearchResult): DeckKind {
  if (r.card) return "knowledge";
  // Spotify songs: tapping one plays it (see openLink).
  if (/spotify/i.test(r.provider || "") && r.items.some((it) => /^spotify:(?:track|find):/i.test(String(it.url || "")))) return "music";
  const items = r.items.slice(0, 4);
  if (items.length && items.every((it) => !!it.thumbnail && isYT(it))) return "video";
  return "web";
}

// ── Sizes (px). Keep in step with searchDeck.css ──
// BOTTOM = the panel's own 12px bottom padding (style.css, .chat-body) + 16px of clear space under the chips,
// so the footer buttons are fully inside the rounded island and not touching its edge.
// FOOT is 8 px taller than the 30 px chips so they sit inside their row with slack above and below (nothing can shave them).
const HEAD = 32, GAP = 10, FOOT = 38, BOTTOM = 28;
const HERO = 176, WATCH = 46;
const L_ROW = 54;                          // result row (knowledge + web): thumbnail, title, source, chevron
const V_FEAT = 160, V_ROW = 58, V_GAP = 4; // video: big first result, then rows
const W_FEAT = 112;
const S_ROW = 46, S_GAP = 4, S_LABEL = 18; // Spotify list: row height, gap, "More by ..." label

/** How many results the deck really shows for `r` (keys 1-4 and the height both depend on it). */
export function shownCount(r: SearchResult): number {
  const kind = deckKind(r);
  const n = r.items.length;
  if (kind === "knowledge") return Math.min(n, r.card?.watch?.length ? 2 : 3);
  return Math.min(n, 4);
}

/** Total height of the island while this deck is shown. */
export function deckHeight(r: SearchResult): number {
  const kind = deckKind(r);
  const n = shownCount(r);
  let body = HEAD;
  if (kind === "knowledge") {
    body += GAP + HERO + (r.card?.watch?.length ? GAP - 2 + WATCH : 0);
    if (n) body += GAP + n * L_ROW;
  } else if (kind === "video") {
    body += GAP + V_FEAT + (n > 1 ? GAP + (n - 1) * V_ROW + (n - 2) * V_GAP : 0);
  } else if (kind === "music") {
    const k = Math.min(n, 4);
    body += GAP + k * S_ROW + (k - 1) * S_GAP;
    const m = Math.min(r.more?.length ?? 0, 3);
    if (m) body += GAP + S_LABEL + GAP + m * S_ROW + (m - 1) * S_GAP;
  } else {
    body += GAP + W_FEAT + (n > 1 ? GAP + (n - 1) * L_ROW : 0);
  }
  return body + GAP + FOOT + BOTTOM;
}

// ── Small helpers ──
export function openLink(url: string): void {
  if (/^spotify:(?:track|find):/i.test(url)) {
    // A song card: the backend opens Spotify on that track, starts it and shows "Playing ..." on the island.
    void fetch("http://127.0.0.1:8765/spotify/play", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ uri: url }) }).catch(() => { void Bridge.openUrl(url + ":play"); });
  } else if (url) void Bridge.openUrl(url);
  window.dispatchEvent(new CustomEvent("lyra:search-link-clicked"));
}

function host(url: string, source?: string): string {
  if (source?.trim()) return source.trim();
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return "web"; }
}

function favicon(domain: string, size = 64): string {
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=${size}`;
}

/** A site icon (letters until the real favicon loads). */
function siteMark(label: string, url: string, cls = ""): HTMLElement {
  const mark = h("div", { class: `sd-mark ${cls}`.trim(), text: label.replace(/[^a-z0-9]/gi, "").slice(0, 2).toUpperCase() || "W" });
  let d = "";
  try { d = new URL(url).hostname; } catch { /* no url */ }
  if (d) {
    const img = h("img", { src: favicon(d), alt: "", draggable: "false", referrerpolicy: "no-referrer" }) as HTMLImageElement;
    img.addEventListener("load", () => { mark.textContent = ""; mark.append(img); mark.classList.add("has-icon"); });
  }
  return mark;
}

/** A picture. Sent without a Referer (many CDNs refuse hot-linked images). If it still fails to load,
 *  `fallback()` (a letter / site icon) takes its place instead of leaving an empty dark box. */
function picture(url: string, cls: string, fallback?: () => HTMLElement | null): HTMLElement {
  const img = h("img", { class: cls, src: url, alt: "", draggable: "false", referrerpolicy: "no-referrer" }) as HTMLImageElement;
  img.addEventListener("error", () => {
    const rep = fallback?.();
    if (rep && img.parentElement) img.replaceWith(rep);
    else img.classList.add("broken");
  });
  return img;
}

const arrow = () => svg(ICONS.arrowUpRight, 12);
const playIcon = (s: number) => svg(ICONS.play, s);

/** Marks `el` as the highlighted row of its deck (hover and Up/Down move it; Enter opens it). */
function activate(el: HTMLElement): void {
  el.closest(".sd")?.querySelectorAll(".sd-nav.is-active").forEach((n) => { if (n !== el) n.classList.remove("is-active"); });
  el.classList.add("is-active");
}

function tap(el: HTMLElement, url: string): HTMLElement {
  if (!url) return el;
  el.setAttribute("role", "link");
  el.tabIndex = 0;
  el.classList.add("sd-nav");
  el.addEventListener("pointerenter", () => activate(el));
  el.addEventListener("click", (e) => { if ((e.target as HTMLElement).closest("button")) return; openLink(url); });
  el.addEventListener("keydown", (e) => {
    const k = (e as KeyboardEvent).key;
    if (k === "Enter" || k === " ") { e.preventDefault(); openLink(url); }
  });
  return el;
}

/** Up / Down: move the highlight through the deck's rows. Returns true when the key was used. */
export function deckStep(delta: number): boolean {
  const rows = Array.from(document.querySelectorAll<HTMLElement>(".sd .sd-nav"));
  if (!rows.length) return false;
  const cur = rows.findIndex((n) => n.classList.contains("is-active"));
  const next = cur < 0 ? (delta > 0 ? 0 : rows.length - 1) : (cur + delta + rows.length) % rows.length;
  activate(rows[next]);
  rows[next].scrollIntoView({ block: "nearest" });
  return true;
}

/** Enter: open the highlighted row. Returns true when there was one. */
export function deckEnter(): boolean {
  const row = document.querySelector<HTMLElement>(".sd .sd-nav.is-active");
  if (!row) return false;
  row.click();
  return true;
}

function indexed<T extends HTMLElement>(el: T, i: number): T {
  el.dataset.index = String(i);
  el.style.setProperty("--i", String(i));
  return el;
}

// ── Knowledge hero ──
const BRANDS: Record<string, { domain: string; tint: string }> = {
  instagram: { domain: "instagram.com", tint: "linear-gradient(135deg,#feda75,#d62976 55%,#4f5bd5)" },
  x: { domain: "x.com", tint: "#000" },
  twitter: { domain: "x.com", tint: "#000" },
  facebook: { domain: "facebook.com", tint: "#1877f2" },
  tiktok: { domain: "tiktok.com", tint: "linear-gradient(135deg,#25f4ee,#111 45%,#fe2c55)" },
  youtube: { domain: "youtube.com", tint: "#ff0033" },
  imdb: { domain: "imdb.com", tint: "#f5c518" },
  website: { domain: "", tint: "rgba(255,255,255,.18)" },
};

function orb(label: string, url: string): HTMLElement {
  const key = label.trim().toLowerCase();
  const brand = BRANDS[key];
  let d = brand?.domain ?? "";
  if (!d) { try { d = new URL(url).hostname; } catch { /* none */ } }
  const b = h("button", { class: "sd-orb", type: "button", title: label, "aria-label": label });
  b.style.setProperty("--tint", brand?.tint ?? "rgba(255,255,255,.18)");
  const fallback = h("span", { text: label.slice(0, 1).toUpperCase() });
  b.append(fallback);
  if (d) {
    const img = h("img", { src: favicon(d, 64), alt: "", draggable: "false", referrerpolicy: "no-referrer" }) as HTMLImageElement;
    img.addEventListener("load", () => { fallback.remove(); b.append(img); });
  }
  b.addEventListener("click", (e) => { e.stopPropagation(); openLink(url); });
  return b;
}

function hero(card: KnowledgeCard): HTMLElement {
  const poster = card.image
    ? h("div", { class: "sd-poster" }, picture(card.image, "sd-poster-img", () => h("span", { class: "sd-poster-none", text: card.title.slice(0, 1).toUpperCase() })), h("i", { class: "sd-gloss" }))
    : h("div", { class: "sd-poster sd-poster-none" }, h("span", { text: card.title.slice(0, 1).toUpperCase() }));
  const actions = h("div", { class: "sd-actions" });
  if (card.url) {
    const label = card.urlLabel || "Wikipedia";
    const main = h("button", { class: "sd-main", type: "button" },
      /^wiki/i.test(label) ? h("b", { class: "sd-wiki", text: "W" }) : null,
      h("span", { text: label }),
      /^wiki/i.test(label) ? null : arrow());
    main.addEventListener("click", (e) => { e.stopPropagation(); openLink(card.url!); });
    actions.append(main);
  }
  for (const s of card.socials.slice(0, 6)) actions.append(orb(s.label, s.url));

  const el = h("div", { class: "sd-hero" },
    h("div", { class: "sd-hero-bg" }),
    poster,
    h("div", { class: "sd-hero-body" },
      h("div", { class: "sd-title", text: card.title }),
      card.subtitle ? h("div", { class: "sd-sub", text: card.subtitle }) : null,
      card.extract ? h("div", { class: "sd-extract", text: card.extract }) : null,
      actions,
    ),
  );
  if (card.image) el.style.setProperty("--poster", `url("${card.image.replace(/"/g, "%22")}")`);
  // The poster leans toward the pointer.
  el.addEventListener("pointermove", (e) => {
    const r = poster.getBoundingClientRect();
    const px = Math.max(-1, Math.min(1, (e.clientX - (r.left + r.width / 2)) / 220));
    const py = Math.max(-1, Math.min(1, (e.clientY - (r.top + r.height / 2)) / 180));
    poster.style.setProperty("--ry", `${(px * 9).toFixed(1)}deg`);
    poster.style.setProperty("--rx", `${(-py * 8).toFixed(1)}deg`);
  });
  el.addEventListener("pointerleave", () => { poster.style.setProperty("--ry", "0deg"); poster.style.setProperty("--rx", "0deg"); });
  return el;
}

function watchShelf(card: KnowledgeCard): HTMLElement {
  const tiles = (card.watch ?? []).slice(0, 4).map((w, i) => {
    const logo = w.logo ? picture(w.logo, "sd-wlogo") : h("span", { class: "sd-wlogo sd-wlogo-none", text: w.label.slice(0, 1) });
    const b = h("button", { class: "sd-watch-tile", type: "button", title: `Watch on ${w.label}` }, logo, h("span", { text: w.label }), playIcon(11));
    b.style.setProperty("--i", String(i));
    b.addEventListener("click", (e) => { e.stopPropagation(); openLink(w.url); });
    return b;
  });
  return h("div", { class: "sd-watch" }, h("span", { class: "sd-watch-label" }, playIcon(11), "Watch now"), ...tiles);
}

// ── Brand marks ──
const GOOGLE_G = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="#4285F4" d="M23.49 12.27c0-.79-.07-1.54-.19-2.27H12v4.51h6.47c-.29 1.48-1.14 2.73-2.4 3.58v3h3.86c2.26-2.09 3.56-5.17 3.56-8.82z"/><path fill="#34A853" d="M12 24c3.24 0 5.95-1.08 7.93-2.91l-3.86-3c-1.08.72-2.45 1.16-4.07 1.16-3.13 0-5.78-2.11-6.73-4.96H1.29v3.09C3.26 21.3 7.31 24 12 24z"/><path fill="#FBBC05" d="M5.27 14.29c-.25-.72-.38-1.49-.38-2.29s.14-1.57.38-2.29V6.62H1.29a11.86 11.86 0 0 0 0 10.76l3.98-3.09z"/><path fill="#EA4335" d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.42-3.42C17.95 1.19 15.24 0 12 0 7.31 0 3.26 2.7 1.29 6.62l3.98 3.09C6.22 6.86 8.87 4.75 12 4.75z"/></svg>';
const YT_LOGO = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><rect x="1" y="4.5" width="22" height="15" rx="4.2" fill="#ff0033"/><path fill="#fff" d="M10 8.9v6.2l5.4-3.1z"/></svg>';
const spotifyLogo = (s = 22) => `<svg viewBox="0 0 24 24" width="${s}" height="${s}" aria-hidden="true"><path fill="currentColor" d="M12 1.5C6.2 1.5 1.5 6.2 1.5 12S6.2 22.5 12 22.5 22.5 17.8 22.5 12 17.8 1.5 12 1.5zm4.86 15.12a.66.66 0 0 1-.9.22c-2.47-1.51-5.58-1.85-9.24-1.01a.66.66 0 1 1-.29-1.28c4.01-.91 7.42-.52 10.21 1.17a.66.66 0 0 1 .22.9zm1.3-2.89a.82.82 0 0 1-1.13.27c-2.83-1.74-7.14-2.24-10.48-1.23a.82.82 0 1 1-.48-1.57c3.8-1.16 8.54-.6 11.82 1.42a.82.82 0 0 1 .27 1.11zm.11-3.01c-3.39-2.01-8.98-2.2-12.21-1.21a.99.99 0 1 1-.57-1.89c3.7-1.13 9.86-.91 13.74 1.4a.99.99 0 0 1-.96 1.7z"/></svg>`;

// The three places Lyra can search. The dropdown / tabs send a fresh request for the same words.
const SOURCES: Array<{ id: DeckKind; label: string; logo: string; cmd: (q: string) => string }> = [
  { id: "web", label: "Google", logo: GOOGLE_G, cmd: (q) => `search the web for ${q}` },
  { id: "video", label: "YouTube", logo: YT_LOGO, cmd: (q) => `search ${q} on YouTube` },
  { id: "music", label: "Spotify", logo: spotifyLogo(16), cmd: (q) => `search ${q} on Spotify` },
];

/** "who is tom holland" -> "tom holland": the words to hand to another source. */
function bareQuery(q: string): string {
  return q.replace(/^(?:hey\s+)?(?:lyra[,\s]+)?(?:who|what)(?:['’]?s|\s+is|\s+was)\s+/i, "").replace(/[?.!\s]+$/, "") || q;
}

// ── Result rows (knowledge + web): thumbnail, title, "source · detail", chevron ──
function listRow(item: ResultItem, i: number): HTMLElement {
  const url = String(item.url || "");
  const src = host(url, item.source);
  const visual = item.thumbnail ? picture(item.thumbnail, "sd-lr-thumb", () => siteMark(src, url)) : siteMark(src, url);
  const sub = [src, item.detail?.trim()].filter(Boolean).join(" · ");
  const el = h("div", { class: "sd-lrow" },
    visual,
    h("div", { class: "sd-lr-body" },
      h("div", { class: "sd-lr-title", text: item.label }),
      h("div", { class: "sd-lr-sub", text: sub }),
    ),
    h("span", { class: "sd-chev" }, svg(ICONS.chevronRight, 16, { stroke: 2 })),
  );
  return tap(indexed(el, i), url);
}

function listRows(items: ResultItem[], first: number): HTMLElement {
  return h("div", { class: "sd-list" }, ...items.map((it, k) => listRow(it, first + k)));
}

// A Spotify song as one row: album art, name (artist under it), round play button. Tap = play it.
function musicRow(item: ResultItem, i: number): HTMLElement {
  const url = String(item.url || "");
  const artist = (item.channel || item.detail || "").trim();
  const mark = () => h("div", { class: "sd-srow-art sd-srow-none", text: "♪" });
  const art = item.thumbnail ? picture(item.thumbnail, "sd-srow-art", mark) : mark();
  const el = h("div", { class: "sd-srow", title: `Play ${item.label} on Spotify` },
    art,
    h("div", { class: "sd-srow-body" },
      h("div", { class: "sd-srow-title", text: item.label }),
      artist ? h("div", { class: "sd-srow-artist", text: artist }) : null,
    ),
    h("span", { class: "sd-srow-play" }, playIcon(13)),
  );
  return tap(indexed(el, i), url);
}

function webFeatured(item: ResultItem): HTMLElement {
  const url = String(item.url || "");
  const src = host(url, item.source);
  let visual: HTMLElement;
  if (item.thumbnail) {
    const media = h("div", { class: "sd-wf-media" });
    media.append(picture(item.thumbnail, "sd-wf-img", () => {
      media.classList.add("sd-wf-none");        // no picture after all: the site's icon on the tinted tile
      return siteMark(src, url, "big");
    }));
    visual = media;
  } else {
    visual = h("div", { class: "sd-wf-media sd-wf-none" }, siteMark(src, url, "big"));
  }
  const el = h("div", { class: "sd-wfeat" },
    visual,
    h("div", { class: "sd-wf-body" },
      h("div", { class: "sd-wf-top" }, h("span", { class: "sd-pill", text: src }), item.published ? h("span", { class: "sd-dim", text: item.published }) : null),
      h("div", { class: "sd-wf-title", text: item.label }),
      item.detail ? h("div", { class: "sd-wf-desc", text: item.detail }) : null,
    ),
    h("span", { class: "sd-go sd-go-big" }, arrow()),
    h("i", { class: "sd-key", text: "1" }),
  );
  return tap(indexed(el, 1), url);
}

function duration(text?: string): HTMLElement | null {
  return text ? h("span", { class: "sd-dur", text }) : null;
}

// The first video stays BIG: a player card on the left, channel / title / Watch on the right.
function videoFeatured(item: ResultItem): HTMLElement {
  const url = String(item.url || "");
  const media = h("div", { class: "sd-vf-media" },
    picture(item.thumbnail || "", "sd-vf-img"),
    h("span", { class: "sd-play" }, h("i", { class: "sd-ring" }), playIcon(22)),
    duration(item.duration),
  );
  const chan = item.channel?.trim() || "YouTube";
  const el = h("div", { class: "sd-vfeat" },
    media,
    h("div", { class: "sd-vf-body" },
      h("div", { class: "sd-vf-chan" }, h("span", { class: "sd-avatar", text: chan.slice(0, 1).toUpperCase() }), h("span", { text: chan })),
      h("div", { class: "sd-vf-title", text: item.label }),
      item.published ? h("div", { class: "sd-dim", text: item.published }) : null,
      item.detail ? h("div", { class: "sd-vf-desc", text: item.detail }) : null,
      h("span", { class: "sd-watchbtn" }, playIcon(12), "Watch"),
    ),
    h("i", { class: "sd-key", text: "1" }),
  );
  return tap(indexed(el, 1), url);
}

// ...and the rest are rows: thumbnail with its length, title, channel · age, play button.
function videoRows(items: ResultItem[]): HTMLElement {
  return h("div", { class: "sd-vlist" }, ...items.map((item, k) => {
    const url = String(item.url || "");
    const meta = [item.channel?.trim(), item.published?.trim()].filter(Boolean).join(" · ");
    const el = h("div", { class: "sd-vrow" },
      h("div", { class: "sd-vr-media" }, picture(item.thumbnail || "", "sd-vr-img"), duration(item.duration)),
      h("div", { class: "sd-vr-body" },
        h("div", { class: "sd-vr-title", text: item.label }),
        meta ? h("div", { class: "sd-vr-meta", text: meta }) : null,
      ),
      h("span", { class: "sd-vr-play" }, playIcon(13)),
    );
    return tap(indexed(el, k + 2), url);
  }));
}

// ── Header: the query + where it was answered. The provider is a dropdown that re-runs the search elsewhere. ──
function providerSwitch(r: SearchResult, kind: DeckKind, ask: (text: string) => void): HTMLElement {
  const bare = bareQuery(r.title.trim());
  const name = (r.provider || (kind === "video" ? "YouTube" : "Google")).trim();
  const here = SOURCES.find((s) => s.id === kind && (kind !== "web" || /google/i.test(name))) ?? null;
  const logo = (here ?? SOURCES.find((s) => s.label.toLowerCase() === name.toLowerCase()))?.logo;
  const btn = h("button", { class: "sd-prov", type: "button", "aria-haspopup": "menu", "aria-expanded": "false", title: "Search somewhere else" },
    logo ? h("span", { class: "sd-prov-logo", html: logo }) : svg(ICONS.search, 12),
    h("span", { class: "sd-prov-name", text: name }),
    svg(ICONS.chevronRight, 12, { stroke: 2.4 }),
  );
  // Spotify only makes sense for songs: it is offered from the Spotify deck, never from Google / YouTube.
  const offered = kind === "music" ? SOURCES : SOURCES.filter((s) => s.id !== "music");
  const menu = h("div", { class: "sd-menu", role: "menu" }, ...offered.map((s) => {
    const on = s === here;
    const b = h("button", { class: `sd-mi${on ? " is-on" : ""}`, type: "button", role: "menuitem" },
      h("span", { class: "sd-prov-logo", html: s.logo }), h("span", { text: s.label }), on ? svg(ICONS.check, 13, { stroke: 2.4 }) : null);
    b.addEventListener("click", (e) => { e.stopPropagation(); close(); if (!on) ask(s.cmd(bare)); });
    return b;
  }));
  const wrap = h("div", { class: "sd-provwrap" }, btn, menu);
  const away = (e: Event) => { if (!wrap.contains(e.target as Node)) close(); };
  const esc = (e: KeyboardEvent) => { if (e.key === "Escape" && wrap.classList.contains("open")) { e.stopPropagation(); close(); } };
  function close() {
    wrap.classList.remove("open"); btn.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", away, true); document.removeEventListener("keydown", esc, true);
  }
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (wrap.classList.contains("open")) { close(); return; }
    wrap.classList.add("open"); btn.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", away, true); document.addEventListener("keydown", esc, true);
  });
  return wrap;
}

// ── Footer: related searches + "Open in <provider>" (music: "Search on Spotify" + the Enter hint) ──
function footer(r: SearchResult, kind: DeckKind, ask: (text: string) => void): HTMLElement {
  const bare = bareQuery(r.title.trim());
  if (kind === "music") {
    const out = h("button", { class: "sd-chip sd-out sd-out-sp", type: "button" }, h("span", { class: "sd-prov-logo", html: spotifyLogo(16) }), h("span", { text: "Search on Spotify" }), arrow());
    out.addEventListener("click", (e) => { e.stopPropagation(); openLink(`spotify:search:${encodeURIComponent(bare)}`); });
    return h("div", { class: "sd-foot sd-foot-music" }, out, h("span", { class: "sd-hint" }, h("kbd", { text: "Enter" }), "to play"));
  }
  const chips: HTMLElement[] = [];
  for (const rel of (r.related ?? []).slice(0, 3)) {
    const b = h("button", { class: "sd-chip sd-rel", type: "button", title: rel }, svg(ICONS.search, 11), h("span", { text: rel }));
    b.addEventListener("click", (e) => { e.stopPropagation(); ask(`search the web for ${rel}`); });
    chips.push(b);
  }
  const yt = kind === "video";
  const out = h("button", { class: "sd-chip sd-out", type: "button" },
    h("span", { class: "sd-prov-logo", html: yt ? YT_LOGO : GOOGLE_G }), h("span", { text: yt ? "Open in YouTube" : "Open in Google" }), arrow());
  out.addEventListener("click", (e) => {
    e.stopPropagation();
    openLink(yt ? `https://www.youtube.com/results?search_query=${encodeURIComponent(bare)}` : `https://www.google.com/search?q=${encodeURIComponent(bare)}`);
  });
  // The related chips wrap inside a one-row box: the ones that do not fit are simply hidden, never squeezed.
  return h("div", { class: "sd-foot" }, h("div", { class: "sd-rels" }, ...chips), h("i", { class: "sd-vsep" }), out);
}

/** The whole deck for `r`. `ask(text)` sends a new request through Chat (related searches, source switch). */
export function buildDeck(r: SearchResult, ask: (text: string) => void): HTMLElement {
  const kind = deckKind(r);
  const items = r.items.slice(0, shownCount(r));
  const q = r.title.trim();
  const head = h("div", { class: "sd-head" },
    h("div", { class: "sd-q" }, svg(ICONS.search, 13), h("span", { text: q })),
    providerSwitch(r, kind, ask),
  );
  const parts: Array<HTMLElement | null> = [head];
  if (kind === "knowledge") {
    parts.push(hero(r.card!));
    if (r.card!.watch?.length) parts.push(watchShelf(r.card!));
    if (items.length) parts.push(listRows(items, 1));
  } else if (kind === "video") {
    parts.push(videoFeatured(items[0]));
    if (items.length > 1) parts.push(videoRows(items.slice(1)));
  } else if (kind === "music") {
    parts.push(h("div", { class: "sd-slist" }, ...items.map((it, k) => musicRow(it, k + 1))));
    const more = (r.more ?? []).slice(0, 3);
    if (more.length) {
      parts.push(h("div", { class: "sd-more-label", text: r.moreTitle || "More like this" }));
      parts.push(h("div", { class: "sd-slist" }, ...more.map((it, k) => musicRow(it, items.length + k + 1))));
    }
  } else {
    parts.push(webFeatured(items[0]));
    if (items.length > 1) parts.push(listRows(items.slice(1), 2));
  }
  parts.push(footer(r, kind, ask));
  const deck = h("div", { class: `search-results-block sd sd-${kind}` }, ...parts);
  if (kind === "music") deck.querySelector(".sd-slist .sd-nav")?.classList.add("is-active");   // first song is ready for Enter
  return deck;
}

/** Press 1-4 on the keyboard to open that result. */
export function openNth(r: SearchResult, n: number): boolean {
  const kind = deckKind(r);
  const item = n <= shownCount(r) ? r.items[n - 1] : undefined;
  if (!item?.url) {
    if (kind === "knowledge" && n === 1 && r.card?.url) { openLink(r.card.url); return true; }
    return false;
  }
  openLink(item.url);
  return true;
}

// ── The small card a search leaves in the conversation: tap it to open the deck again ──
export function buildPeek(r: SearchResult, open: () => void): HTMLElement {
  const kind = deckKind(r);
  let visual: HTMLElement;
  let sub: string;
  if (kind === "knowledge") {
    visual = r.card!.image ? h("div", { class: "sd-pk-poster" }, picture(r.card!.image, "sd-pk-img")) : h("div", { class: "sd-pk-poster sd-pk-none", text: r.card!.title.slice(0, 1) });
    sub = r.card!.subtitle || `${r.items.length} results`;
  } else if (kind === "video" || kind === "music") {
    visual = h("div", { class: "sd-pk-video" }, picture(r.items[0]?.thumbnail || "", "sd-pk-img"), h("span", { class: "sd-play sd-play-s" }, playIcon(11)));
    sub = kind === "music" ? `Spotify · ${r.items.length} songs` : `${r.items.length} videos`;
  } else {
    const first = r.items[0];
    visual = h("div", { class: "sd-pk-site" }, siteMark(host(first?.url || "", first?.source), first?.url || ""));
    sub = `${r.provider ? `${r.provider} · ` : ""}${r.items.length} results`;
  }
  const el = h("div", { class: "sd-peek", role: "button", tabindex: "0", title: "Open the results" },
    visual,
    h("div", { class: "sd-pk-body" },
      h("div", { class: "sd-pk-title", text: kind === "knowledge" ? r.card!.title : r.title }),
      h("div", { class: "sd-pk-sub", text: sub }),
    ),
    h("span", { class: "sd-pk-open" }, "Open", arrow()),
  );
  el.addEventListener("click", (e) => { e.stopPropagation(); open(); });
  el.addEventListener("keydown", (e) => {
    const k = (e as KeyboardEvent).key;
    if (k === "Enter" || k === " ") { e.preventDefault(); open(); }
  });
  return el;
}

export const PEEK_H = 64;
