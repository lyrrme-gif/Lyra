// Music tab — Spotify controls inside the island (cover, title, progress, transport) and its Lyrics view.
//
// It shows what Rust reads from the Windows media session and sends clicks back
// through the same `spotify_control` command as the small pill. No account, no
// login: if Spotify's desktop app is closed there is nothing to show, and the tab
// says so and offers to open it. It never invents a track.
//
// Look ("Liner"): the whole tab takes its colours from the album cover (coverTheme.ts / musicTheme.ts, via the existing
// artColor()), the lyrics view is cover + controls on the left and large lyrics on the right. All styling is in
// musicLiner.css. Cost notes (the app is already GPU heavy): no blur, no backdrop-filter, only opacity/transform
// animate; the one rAF loop below runs only while the lyrics are visible AND playing AND the window is really on
// screen, and does no layout reads (line positions are read once per line change, not per frame).

import { h, svg } from "../../../shared/views/dom";
import { ICONS } from "../../../shared/views/icons";
import { IS_TAURI } from "../../../shared/core/bridge";
import { EXTRAS_KEY, State, readExtras } from "../../../shared/core/state";
import { isReallyVisible, onVisibilityChange } from "../../../shared/core/visibility";
import { SpotifyFeed } from "../../../shared/spotify/feed";
import { currentPosition, formatTime, isSpotifySource, labels, progressFraction } from "../../../shared/spotify/model";
import { lineIndexAt } from "../lyricsLine";
import { UNSUNG_ALPHA, type CoverTheme } from "./coverTheme";
import { cleanTimedWords, evenWordTimings, fillAt, singWindow, splitWords, timedSpans, type TimedWord, type WordSpan } from "./lyricsFill";
import { cachedTheme, themeForCover } from "./musicTheme";
import type { ViewActions, ViewHost } from "./views";
import "./musicLiner.css";

const LYRICS_BASE = "http://127.0.0.1:8765";
/** Where (px from the top of the lyrics panel) the centre of the current line stays while the lyrics scroll. */
const LYR_ANCHOR_Y = 92;
/** Lines further than this from the current one all look the same (data-d is capped here). */
const DIST_CAP = 5;
/** Moving by at most this many lines only restyles the lines around the old and the new current line. */
const DIST_STEP = 6;
/** Cross-fade of the background gradient between two songs (CSS: .lm-bg transition 0.7 s) + a little. */
const FADE_MS = 800;

interface LyricLine { t: number; text: string; words?: TimedWord[] }
type LyricsView =
  | { kind: "loading" }
  | { kind: "none" }
  | { kind: "offline" }
  | { kind: "instrumental" }
  | { kind: "synced"; lines: LyricLine[] }
  | { kind: "plain"; text: string };

// Only successful lookups are cached; "offline" is retried on the next open.
const lyricsCache = new Map<string, LyricsView>();

type Mode = "preview" | "loading" | "closed" | "idle" | "playing" | "paused";

/** Time-stamped lines from the backend, cleaned: numeric stamps only, in order. */
function cleanLines(raw: unknown): LyricLine[] {
  if (!Array.isArray(raw)) return [];
  const out: LyricLine[] = [];
  for (const l of raw) {
    const t = Number((l as LyricLine)?.t);
    if (!Number.isFinite(t)) continue;
    const words = cleanTimedWords((l as LyricLine).words);          // real word timing, when the service had it
    out.push({ t, text: String((l as LyricLine).text ?? "").trim(), ...(words ? { words } : {}) });
  }
  return out.sort((a, b) => a.t - b.t);
}

const reduceMotionQuery = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;

export function buildMusic(actions: Pick<ViewActions, "relayout">): ViewHost {
  // ── DOM ─────────────────────────────────────────────────────────────────────
  // Two background layers: a new song's gradient fades in on the idle one over the old one (opacity only).
  const bgA = h("div", { class: "lm-bg", "aria-hidden": "true" });
  const bgB = h("div", { class: "lm-bg", "aria-hidden": "true" });
  let front = bgA;

  const img = h("img", { alt: "" }) as HTMLImageElement;
  img.draggable = false;
  img.decoding = "async";
  const art = h("div", { class: "lm-art" }, img, h("div", { class: "lm-ph" }, svg(ICONS.music, 30)));
  // A cover that fails to decode falls back to the placeholder.
  img.addEventListener("error", () => art.classList.remove("has-art"));

  const title = h("div", { class: "lm-title" });
  const artist = h("div", { class: "lm-artist" });
  // Small animated playback wave sits AFTER the title, never before it.
  const musicWave = h("div", { class: "lm-wave", "aria-hidden": "true" }, ...Array.from({ length: 5 }, () => h("i")));
  const titleLine = h("div", { class: "lm-titleline" }, title, musicWave);
  const text = h("div", { class: "lm-meta" }, titleLine, artist);

  const cur = h("span");
  const dur = h("span");
  const fill = h("i");
  const bar = h("div", { class: "lm-bar", role: "slider", "aria-label": "Song position", tabindex: "0" }, fill);
  const progress = h("div", { class: "lm-progress" }, cur, bar, dur);

  const lyrBtn = h(
    "button",
    { class: "lm-btn side lyr", title: "Lyrics", "aria-label": "Lyrics", "aria-pressed": "false", type: "button" },
    svg(ICONS.lyrics, 15),
  ) as HTMLButtonElement;

  const transport = (label: string, path: string, size: number, action: "toggle" | "next" | "previous", extra = "") => {
    const b = h(
      "button",
      { class: `lm-btn ${extra}`.trim(), title: label, "aria-label": label, type: "button" },
      svg(path, size),
    ) as HTMLButtonElement;
    let pointerHandled = false;
    const activate = (event?: Event) => {
      event?.preventDefault();
      event?.stopPropagation();
      if (!b.disabled) void SpotifyFeed.control(action);
    };
    b.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 && event.pointerType === "mouse") return;
      pointerHandled = true;
      activate(event);
      window.setTimeout(() => { pointerHandled = false; }, 450);
    }, { passive: false });
    b.addEventListener("click", (event) => {
      if (pointerHandled) { event.preventDefault(); event.stopPropagation(); return; }
      activate(event);
    });
    return b;
  };
  const shuffleBtn = h(
    "button",
    { class: "lm-btn side shuffle", title: "Shuffle", "aria-label": "Shuffle", "aria-pressed": "false", type: "button" },
    svg(ICONS.shuffle, 17, { stroke: 2 }),
  ) as HTMLButtonElement;
  let shufflePointer = false;
  const activateShuffle = (event?: Event) => {
    event?.preventDefault(); event?.stopPropagation();
    shufflePointer = true;
    window.setTimeout(() => { shufflePointer = false; }, 450);
    if (shuffleBtn.disabled) return;
    // Quick press-and-bounce so the click always feels registered, even if the
    // player takes a moment to report the new shuffle state.
    shuffleBtn.animate(
      [{ transform: "scale(1)" }, { transform: "scale(0.78)", offset: 0.3 }, { transform: "scale(1.12)", offset: 0.65 }, { transform: "scale(1)" }],
      { duration: 300, easing: "cubic-bezier(.2,.8,.2,1)" },
    );
    void SpotifyFeed.control("shuffle");
  };
  shuffleBtn.addEventListener("pointerdown", (event) => activateShuffle(event), { passive:false });
  shuffleBtn.addEventListener("click", (event) => { if (shufflePointer) { event.preventDefault(); event.stopPropagation(); return; } activateShuffle(event); });
  const prevBtn = transport("Previous", ICONS.backFill, 20, "previous");
  const toggleBtn = transport("Play / pause", ICONS.play, 23, "toggle", "main");
  const nextBtn = transport("Next", ICONS.forwardFill, 20, "next");
  // Visual order (CSS `order`): Lyrics · Previous · Play · Next · Shuffle.
  const controls = h("div", { class: "lm-controls" }, shuffleBtn, prevBtn, toggleBtn, nextBtn, lyrBtn);

  const openBtn = h(
    "button",
    { class: "btn primary lm-open", type: "button", onclick: () => SpotifyFeed.launch() },
    h("span", { text: "Open Spotify" }),
  );

  // Lyrics panel: a synced column (moved with transform), a plain-text box, or a status message. CSS shows one.
  const track = h("div", { class: "lm-track" });
  const plainBox = h("div", { class: "lm-plain" });
  const msgBox = h("div", { class: "lm-msg" });
  const lyrics = h("div", { class: "lm-lyrics", title: "Lyrics from LRCLIB", "data-kind": "msg" }, track, plainBox, msgBox);
  const lyrClose = h("button", { class: "lm-close", type: "button", title: "Close lyrics", "aria-label": "Close lyrics" }, svg("M6 6l12 12M18 6 6 18", 14, { stroke: 2.2 })) as HTMLButtonElement;

  const left = h("div", { class: "lm-left" }, art, text, progress, controls, openBtn);
  const stage = h("div", { class: "lm-stage" }, left, lyrics, lyrClose);
  // Music is intentionally full-bleed. There is no inner .card surface in the DOM.
  const el = h("div", { class: "view music-view lm-view" }, bgA, bgB, stage);
  el.style.setProperty("--lm-unsung", String(UNSUNG_ALPHA));

  // ── Theme from the cover ────────────────────────────────────────────────────
  const THEME_VARS = ["--cv-rgb", "--cv-light-rgb", "--cv-dark-rgb", "--cv-hi-rgb", "--cv-bg-a", "--cv-bg-b", "--cv-glow-rgb", "--cv-glow-a"];
  let themed: CoverTheme | null = null;
  let themeToken = 0;
  let fadeTimer: number | undefined;

  function applyTheme(t: CoverTheme | null) {
    if (t === themed) return;
    themed = t;
    window.clearTimeout(fadeTimer);
    if (!t) {
      // No usable colour (or no cover): back to the island theme, gradient fades out.
      for (const k of THEME_VARS) el.style.removeProperty(k);
      el.classList.remove("lm-themed");
      bgA.classList.remove("on", "top");
      bgB.classList.remove("on", "top");
      return;
    }
    for (const [k, v] of Object.entries(t.vars)) el.style.setProperty(k, v);
    el.classList.add("lm-themed");
    // Fade the new gradient in on the idle layer, on top of the old one; drop the old one once it is covered.
    const back = front === bgA ? bgB : bgA;
    back.style.setProperty("--bg-a", t.bgA);
    back.style.setProperty("--bg-b", t.bgB);
    back.style.setProperty("--bg-glow", t.glow);
    back.style.setProperty("--bg-glow-a", t.glowA);
    back.style.setProperty("--bg-glow-a2", t.glowA2);
    back.classList.add("on", "top");
    front.classList.remove("top");
    front = back;
    fadeTimer = window.setTimeout(() => (front === bgA ? bgB : bgA).classList.remove("on"), FADE_MS);
  }

  /** Called when the cover changes. Cached covers apply at once; a new one waits for ONE artColor() call. */
  let lastCover: string | null = null;
  /** Settings -> "Music player colours": "island" keeps the Music tab on the island theme and ignores the cover. */
  const followCover = () => readExtras().musicTheme !== "island";
  function updateTheme(url: string | null) {
    lastCover = url;
    const token = ++themeToken;
    if (!url || !followCover()) { applyTheme(null); return; }
    const hit = cachedTheme(url);
    if (hit !== undefined) { applyTheme(hit); return; }
    // Until the colour is known the previous song's theme stays: no flash back to the default.
    void themeForCover(url).then((t) => { if (token === themeToken) applyTheme(t); });
  }

  // The Settings window writes the choice to localStorage: re-apply at once, no restart.
  window.addEventListener("storage", (e) => { if (e.key === EXTRAS_KEY) updateTheme(lastCover); });

  // ── Rendering ───────────────────────────────────────────────────────────────
  bar.addEventListener("click", (event: MouseEvent) => {
    const rect = bar.getBoundingClientRect();
    const total = SpotifyFeed.state?.durationMs;
    if (!rect.width || !total || !Number.isFinite(total)) return;
    const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    SpotifyFeed.seek(fraction * total);
  });
  bar.addEventListener("keydown", (event: KeyboardEvent) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = event.shiftKey ? 0.1 : 0.05;
    const state = SpotifyFeed.state;
    const total = state?.durationMs;
    const pos = state ? currentPosition(state, SpotifyFeed.receivedAt, performance.now()) : null;
    if (!total || pos == null) return;
    const next = Math.max(0, Math.min(total, pos + (event.key === "ArrowRight" ? step : -step) * total));
    SpotifyFeed.seek(next);
  });

  let shownArt: string | null = null;
  let lastTrack = "";
  let active = false;
  let unsubscribe: (() => void) | null = null;
  let unsubscribeVisibility: (() => void) | null = null;
  let ticker: number | undefined;

  // ── Lyrics ──────────────────────────────────────────────────────────────────
  let lyricsOn = false;
  let lyricsTrack = ""; // trackKey the panel currently shows / is loading
  let lyricsView: LyricsView = { kind: "loading" };

  // Synced state: one element per line (built once per song), the line being sung, and its words.
  let lineEls: HTMLElement[] = [];
  let lineWords: (HTMLElement[] | null)[] = [];   // spans of a line, built the first time it is the current one
  let curIdx = -2;                                 // -2 nothing painted yet, -1 before the first line
  let curSpans: WordSpan[] = [];
  let curWordEls: HTMLElement[] = [];
  let fillWhole = -1;
  let fillFrac = -1;

  function setKind(kind: "synced" | "plain" | "msg") {
    lyrics.dataset.kind = kind;
  }

  function resetSynced() {
    track.replaceChildren();
    track.style.transform = "";
    lineEls = [];
    lineWords = [];
    curIdx = -2;
    curSpans = [];
    curWordEls = [];
    fillWhole = -1;
    fillFrac = -1;
  }

  function paintLyrics() {
    resetSynced();
    plainBox.textContent = "";
    msgBox.textContent = "";
    lyrics.scrollTop = 0;
    const v = lyricsView;
    if (v.kind === "synced") {
      setKind("synced");
      const frag = document.createDocumentFragment();
      for (const line of v.lines) {
        const d = h("div", { class: "lm-line", text: line.text || "\u266a" });
        lineEls.push(d);
        lineWords.push(null);
        frag.append(d);
      }
      track.append(frag);
      syncLyricsLine(true);
    } else if (v.kind === "plain") {
      setKind("plain");
      plainBox.textContent = v.text;
    } else {
      setKind("msg");
      msgBox.textContent =
        v.kind === "loading" ? "Finding lyrics…"
        : v.kind === "instrumental" ? "Instrumental ♪"
        : v.kind === "offline" ? "Couldn't reach the lyrics service"
        : "No lyrics found for this song";
    }
    syncLoop();
  }

  /** Lines get a distance from the current one (data-d); CSS turns it into opacity + scale. */
  function setDistances(idx: number, prev: number) {
    const n = lineEls.length;
    let from = 0;
    let to = n - 1;
    if (prev >= -1 && Math.abs(idx - prev) <= DIST_STEP) {
      from = Math.max(0, Math.min(idx, prev) - DIST_CAP - 1);
      to = Math.min(n - 1, Math.max(idx, prev) + DIST_CAP + 1);
    }
    for (let i = from; i <= to; i++) {
      const v = String(Math.min(DIST_CAP, Math.abs(i - idx)));
      if (lineEls[i].dataset.d !== v) lineEls[i].dataset.d = v;
    }
  }

  /** Makes line `idx` the current one: words are split into spans the first time, and their even timing is set. */
  function activateLine(lines: LyricLine[], idx: number) {
    if (curIdx >= 0) lineEls[curIdx]?.classList.remove("cur");
    curIdx = idx;
    curSpans = [];
    curWordEls = [];
    fillWhole = -1;
    fillFrac = -1;
    if (idx < 0) return;
    const lineEl = lineEls[idx];
    const textOfLine = lines[idx].text || "\u266a";
    const timed = lines[idx].words ?? null;       // real word timing (NetEase), else the estimate below
    let words = lineWords[idx];
    if (!words) {
      lineEl.textContent = "";
      if (timed) {
        // One span per timed word (a word can be one syllable); its own trailing space stays as plain text.
        words = timed.map((tw) => {
          const body = tw.text.replace(/\s+$/, "");
          const s = h("span", { class: "lm-w", text: body });
          lineEl.append(s);
          if (body.length !== tw.text.length) lineEl.append(" ");
          return s;
        });
      } else {
        const parts = splitWords(textOfLine);
        words = parts.map((w, i) => {
          if (i > 0 && !/[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]/.test(w)) lineEl.append(" ");
          const s = h("span", { class: "lm-w", text: w });
          lineEl.append(s);
          return s;
        });
      }
      lineWords[idx] = words;
    } else {
      for (const w of words) { w.classList.remove("done"); w.style.removeProperty("--p"); }
    }
    curWordEls = words;
    if (timed) {
      curSpans = timedSpans(timed);                                   // real timing
    } else {
      const win = singWindow(lines[idx].t, lines[idx + 1]?.t ?? null);
      curSpans = evenWordTimings(words.map((w) => w.textContent || ""), win.start, win.end);   // estimate
    }
    lineEl.classList.add("cur");
  }

  /** Moves the column so the line's centre sits at LYR_ANCHOR_Y. One layout read per LINE change, none per frame. */
  function scrollToLine(idx: number, snap: boolean) {
    const target = lineEls[Math.max(0, idx)];
    if (!target) return;
    const y = target.offsetTop + target.offsetHeight / 2 - LYR_ANCHOR_Y;
    if (snap) track.classList.add("snap");
    track.style.transform = `translate3d(0, ${-Math.round(y)}px, 0)`;
    if (snap) requestAnimationFrame(() => requestAnimationFrame(() => track.classList.remove("snap")));
  }

  /** Word-by-word fill of the current line (estimated: see lyricsFill.ts). Writes only what changed; usually one style var. */
  function paintFill(pos: number) {
    if (curIdx < 0 || !curWordEls.length) return;
    let { whole, frac } = fillAt(curSpans, pos);
    // Reduced motion: a word lights up whole when its turn starts, no smooth fill.
    if (reduceMotionQuery?.matches) { if (frac > 0) whole += 1; frac = 0; }
    const n = curWordEls.length;
    if (whole !== fillWhole) {
      for (let i = 0; i < n; i++) {
        curWordEls[i].classList.toggle("done", i < whole);
        if (i !== whole) curWordEls[i].style.removeProperty("--p");
      }
      fillWhole = whole;
      fillFrac = -1;
    }
    if (whole < n && Math.abs(frac - fillFrac) >= 0.004) {
      curWordEls[whole].style.setProperty("--p", frac.toFixed(3));
      fillFrac = frac;
    }
  }

  function currentPos(): number | null {
    const s = SpotifyFeed.state;
    return s ? currentPosition(s, SpotifyFeed.receivedAt, performance.now()) : null;
  }

  function syncLyricsLine(force = false) {
    if (!lyricsOn || lyricsView.kind !== "synced") return;
    const pos = currentPos();
    const lines = lyricsView.lines;
    if (pos == null) {
      // The player reports no position: nothing to follow, but the lines must still be readable (first lines, top).
      if (curIdx === -2) { activateLine(lines, -1); setDistances(-1, -2); scrollToLine(-1, true); }
      return;
    }
    const idx = lineIndexAt(lines, pos);
    if (force || idx !== curIdx) {
      const prev = curIdx;
      const first = prev === -2;
      activateLine(lines, idx);
      setDistances(idx, prev);
      scrollToLine(idx, force || first);
    }
    paintFill(pos);
  }

  // The fill needs a steady clock; nothing else does. It runs ONLY while the lyrics are visible and the song plays.
  let raf = 0;
  function wantLoop(): boolean {
    return active && lyricsOn && lyricsView.kind === "synced" && SpotifyFeed.state?.status === "playing" && isReallyVisible();
  }
  function loop() {
    raf = 0;
    if (!wantLoop()) return;
    syncLyricsLine();
    raf = requestAnimationFrame(loop);
  }
  function syncLoop() {
    if (wantLoop()) {
      if (!raf) raf = requestAnimationFrame(loop);
    } else if (raf) {
      cancelAnimationFrame(raf);
      raf = 0;
    }
  }

  async function loadLyrics() {
    const s = SpotifyFeed.state;
    const key = s?.trackKey ?? "";
    if (!s || !key || !s.title) return;
    lyricsTrack = key;
    if (!isSpotifySource(s)) { lyricsView = { kind: "none" }; paintLyrics(); return; }   // not Spotify: no lookup
    const cached = lyricsCache.get(key);
    if (cached) {
      lyricsView = cached;
      paintLyrics();
      return;
    }
    lyricsView = { kind: "loading" };
    paintLyrics();
    const q = new URLSearchParams({
      title: s.title,
      artist: s.artist || "",
      album: s.album || "",
      duration: s.durationMs ? String(Math.round(s.durationMs / 1000)) : "",
    });
    let view: LyricsView;
    try {
      const r = await fetch(`${LYRICS_BASE}/lyrics?${q}`);
      if (!r.ok) throw new Error(String(r.status));
      const d = await r.json();
      if (d.error) view = { kind: "offline" };
      else if (!d.found) view = { kind: "none" };
      else if (d.instrumental) view = { kind: "instrumental" };
      else {
        const lines = cleanLines(d.synced);
        const plain = String(d.plain || "").trim();
        if (lines.length) view = { kind: "synced", lines };
        else if (plain) view = { kind: "plain", text: plain };
        else view = { kind: "none" };
      }
    } catch {
      view = { kind: "offline" };
    }
    if (view.kind !== "offline") lyricsCache.set(key, view);
    if (lyricsTrack !== key) return; // the song changed while we were fetching
    lyricsView = view;
    paintLyrics();
  }

  let animTimer: number | undefined;
  function playToggleAnimation(on: boolean) {
    // Button: quick press-and-bounce.
    lyrBtn.animate(
      [
        { transform: "scale(1)" },
        { transform: "scale(0.78)", offset: 0.3 },
        { transform: "scale(1.16)", offset: 0.65 },
        { transform: "scale(1)" },
      ],
      { duration: 340, easing: "cubic-bezier(.2,.8,.2,1)" },
    );
    // Panel: lyrics rise in when opening; the player rises back in when closing.
    const cls = on ? "lm-anim-in" : "lm-anim-out";
    el.classList.remove("lm-anim-in", "lm-anim-out");
    void el.offsetWidth; // restart the CSS animations
    el.classList.add(cls);
    window.clearTimeout(animTimer);
    animTimer = window.setTimeout(() => el.classList.remove(cls), 600);
  }

  /** `relayout`: the island is open on this tab, so it must grow / shrink for the lyrics layout. */
  function setLyrics(on: boolean, animate = false, relayout = false) {
    if (animate && on !== lyricsOn) playToggleAnimation(on);
    const changed = on !== lyricsOn;
    lyricsOn = on;
    State.lyricsOpen = on;
    el.classList.toggle("lm-lyrics-on", on);
    lyrBtn.classList.toggle("on", on);
    lyrBtn.setAttribute("aria-pressed", String(on));
    lyrBtn.title = on ? "Hide lyrics" : "Lyrics";
    if (changed && relayout && State.mode === "expanded") actions.relayout(!on);
    if (on) void loadLyrics();
    else syncLoop();
  }

  function toggleLyrics() {
    setLyrics(!lyricsOn, true, true);
  }

  let lyricsPointer = false;
  const activateLyrics = (event?: Event) => {
    event?.preventDefault();
    event?.stopPropagation();
    lyricsPointer = true;
    window.setTimeout(() => { lyricsPointer = false; }, 450);
    toggleLyrics();
  };
  lyrBtn.addEventListener("pointerdown", (event) => activateLyrics(event), { passive: false });
  lyrBtn.addEventListener("click", (event) => {
    if (lyricsPointer) { event.preventDefault(); event.stopPropagation(); return; }
    activateLyrics(event);
  });

  let lyricsClosePointer = false;
  const closeLyrics = (event?: Event) => {
    event?.preventDefault();
    event?.stopPropagation();
    (event as Event | undefined)?.stopImmediatePropagation?.();
    lyricsClosePointer = true;
    window.setTimeout(() => { lyricsClosePointer = false; }, 450);
    setLyrics(false, true, true);
  };
  // Capture the close interaction before any island drag/window handler can consume it.
  const onClosePointer = (event: Event) => {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    try { (event.currentTarget as HTMLElement).setPointerCapture?.((event as PointerEvent).pointerId); } catch {}
    closeLyrics(event);
  };
  lyrClose.addEventListener("pointerdown", onClosePointer, { passive: false, capture: true });
  lyrClose.addEventListener("mousedown", onClosePointer, { passive: false, capture: true });
  lyrClose.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    if (!lyricsClosePointer) closeLyrics(event);
  }, true);

  function mode(): Mode {
    if (!IS_TAURI) return "preview";
    const s = SpotifyFeed.state;
    if (!s) return "loading";
    return s.running ? s.status : "closed";
  }

  let shownCur = "";
  let shownDur = "";
  function renderProgress() {
    const s = SpotifyFeed.state;
    const pos = s ? currentPosition(s, SpotifyFeed.receivedAt, performance.now()) : null;
    const total = s?.durationMs ?? null;
    progress.classList.toggle("none", pos == null || total == null);
    let c = "";
    let d = "";
    if (pos == null || total == null) {
      // Nothing to measure: don't leave the previous song's time behind.
      fill.style.transform = "scaleX(0)";
    } else {
      fill.style.transform = `scaleX(${progressFraction(pos, total)})`;
      c = formatTime(pos);
      d = `-${formatTime(Math.max(0, total - pos))}`;   // time remaining ("-3:15"), like the Apple players
    }
    if (c !== shownCur) { shownCur = c; cur.textContent = c; }
    if (d !== shownDur) { shownDur = d; dur.textContent = d; }
  }

  function render() {
    const s = SpotifyFeed.state;
    const m = mode();
    el.dataset.mode = m;

    // Title / artist lines — never a made-up track.
    let t = "Spotify";
    let a = "";
    if (m === "preview") a = "Works inside the Lyra app";
    else if (m === "loading") a = "Looking for Spotify…";
    else if (m === "closed") {
      t = "Spotify isn't running";
      a = "Open it to control your music here";
    } else if (s) ({ title: t, artist: a } = labels(s));
    if (title.textContent !== t) { title.textContent = t; title.title = t; }
    if (artist.textContent !== a) { artist.textContent = a; artist.title = a; }

    const track = s?.trackKey ?? "";
    if (track !== lastTrack) {
      if (lastTrack !== "" && track !== "") {
        // Apple Music-style track change: artwork pops in, the title slides up and settles (opacity + transform only).
        const spring = "cubic-bezier(.34,1.56,.64,1)";
        art.animate([{ scale: "0.84", opacity: 0.35 }, { scale: "1", opacity: 1 }], { duration: 560, easing: spring });
        text.animate([{ opacity: 0, translate: "0 10px" }, { opacity: 1, translate: "0 0" }], { duration: 480, easing: "cubic-bezier(.22,1,.36,1)" });
      }
      lastTrack = track;
      if (lyricsOn) void loadLyrics();
    }

    // The lyrics button only makes sense while a song is loaded.
    const hasSong = m === "playing" || m === "paused";
    if (!hasSong && lyricsOn) setLyrics(false, false, true);

    // Cover: only touch the <img> when the picture actually changed. The theme follows the cover.
    const wanted = m === "closed" || m === "loading" || m === "preview" ? null : SpotifyFeed.art;
    if (wanted !== shownArt) {
      shownArt = wanted;
      if (wanted) {
        img.src = wanted;
        art.classList.add("has-art");
      } else {
        img.removeAttribute("src");
        art.classList.remove("has-art");
      }
      updateTheme(wanted);
    }

    // Transport
    prevBtn.disabled = !s?.canPrev;
    nextBtn.disabled = !s?.canNext;
    toggleBtn.disabled = !s?.canToggle;
    const playing = s?.status === "playing";
    musicWave.classList.toggle("paused", !playing);
    toggleBtn.replaceChildren(svg(playing ? ICONS.pause : ICONS.play, 20));
    const verb = playing ? "Pause" : "Play";
    toggleBtn.title = verb;
    toggleBtn.setAttribute("aria-label", verb);

    // Shuffle: lit while on. If the player reports nothing about shuffle, the button
    // is dimmed (it still tries when clicked, and lights up if the player answers).
    const shuffleOn = s?.shuffle === true;
    const shuffleKnown = s?.shuffle != null || s?.canShuffle === true;
    shuffleBtn.disabled = !hasSong;
    shuffleBtn.classList.toggle("on", shuffleOn);
    shuffleBtn.classList.toggle("unsupported", hasSong && !shuffleKnown);
    shuffleBtn.setAttribute("aria-pressed", String(shuffleOn));
    const shuffleLabel = shuffleOn
      ? "Shuffle: on"
      : hasSong && !shuffleKnown
        ? "Shuffle (this player doesn't report its shuffle state)"
        : "Shuffle: off";
    shuffleBtn.title = shuffleLabel;
    shuffleBtn.setAttribute("aria-label", shuffleLabel);

    renderProgress();
    // A new position report (seek, pause, track change) re-aims the lyrics at once, even while paused.
    syncLyricsLine();
    syncLoop();
  }

  // ── Lifecycle: only talk to Rust while the tab is really on screen ──────────
  return {
    el,
    sync: render,
    setActive(on: boolean) {
      if (on === active) return;
      active = on;
      if (on) {
        unsubscribe = SpotifyFeed.subscribe(render);
        unsubscribeVisibility = onVisibilityChange(() => syncLoop());
        // Spotify reports on change only, so the bar is advanced locally (the lyric fill has its own rAF loop).
        ticker = window.setInterval(() => {
          if (SpotifyFeed.state?.status === "playing" && isReallyVisible()) renderProgress();
        }, 250);
        render();
      } else {
        unsubscribe?.();
        unsubscribe = null;
        unsubscribeVisibility?.();
        unsubscribeVisibility = null;
        window.clearInterval(ticker);
        ticker = undefined;
        syncLoop();                                   // stops the fill loop
        if (lyricsOn) setLyrics(false);               // always reopen on the normal controls (the island is closing / leaving the tab: it sizes itself)
        window.clearTimeout(fadeTimer);
      }
      void SpotifyFeed.setActive(on);
    },
  };
}
