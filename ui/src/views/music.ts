// Music tab — Spotify controls inside the island (cover, title, progress, transport).
//
// It shows what Rust reads from the Windows media session and sends clicks back
// through the same `spotify_control` command as the small pill. No account, no
// login: if Spotify's desktop app is closed there is nothing to show, and the tab
// says so and offers to open it. It never invents a track.

import { h, svg } from "./dom";
import { ICONS } from "./icons";
import { IS_TAURI } from "../core/bridge";
import { State } from "../core/state";
import { SpotifyFeed } from "../spotify/feed";
import { currentPosition, formatTime, isSpotifySource, labels, progressFraction } from "../spotify/model";
import type { ViewHost } from "./views";

const LYRICS_BASE = "http://127.0.0.1:8765";
const EQ_BARS = 11;

interface LyricLine { t: number; text: string }
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

export function buildMusic(): ViewHost {
  // ── DOM ─────────────────────────────────────────────────────────────────────
  const ambience = h("div", { class: "m-ambience", "aria-hidden": "true" });
  const img = h("img", { alt: "" }) as HTMLImageElement;
  img.draggable = false;
  img.decoding = "async";
  const art = h("div", { class: "m-art" }, img, h("div", { class: "m-ph" }, svg(ICONS.music, 30)));
  // A cover that fails to decode falls back to the placeholder.
  img.addEventListener("error", () => art.classList.remove("has-art"));

  const title = h("div", { class: "m-title" });
  const artist = h("div", { class: "m-artist" });
  // Small animated playback wave sits AFTER the title, never before it.
  const musicWave = h("div", { class: "m-music-wave", "aria-hidden": "true" }, ...Array.from({ length: 5 }, () => h("i")));
  const titleLine = h("div", { class: "m-title-line" }, title, musicWave);
  const text = h("div", { class: "m-text" }, titleLine, artist);
  // Same look as the voice wave on the notch: white bars, tallest in the middle,
  // tapering to dots at the ends. Spotify gives us no audio levels, so it is animated.
  const eqBars: HTMLElement[] = [];
  for (let i = 0; i < EQ_BARS; i++) {
    const x = (i - (EQ_BARS - 1) / 2) / ((EQ_BARS - 1) / 2);
    const amp = 0.12 + 0.88 * Math.exp(-(x * 1.7) * (x * 1.7));
    const b = h("i");
    b.style.setProperty("--amp", amp.toFixed(3));
    b.style.animationDuration = `${(0.55 + ((i * 7) % 5) * 0.13).toFixed(2)}s`;
    b.style.animationDelay = `${(-((i * 3) % 7) * 0.11).toFixed(2)}s`;
    eqBars.push(b);
  }
  // Build these before the main tree: v35 accidentally referenced them before declaration.
  const lyrArtImg = h("img", { class: "m-lyr-art", alt: "" }) as HTMLImageElement;
  lyrArtImg.draggable = false;
  const lyrMetaTitle = h("div", { class: "m-lyr-title" });
  const lyrMetaArtist = h("div", { class: "m-lyr-artist" });
  const lyrClose = h("button", { class: "m-lyr-close", type: "button", title: "Close lyrics", "aria-label": "Close lyrics" }, svg("M6 6l12 12M18 6 6 18", 17)) as HTMLButtonElement;
  const lyrTop = h("div", { class: "m-lyr-top" }, lyrArtImg, h("div", { class: "m-lyr-meta" }, lyrMetaTitle, lyrMetaArtist), lyrClose);
  const lyrInner = h("div", { class: "m-lyr-inner" });
  const lyrics = h("div", { class: "m-lyrics", title: "Lyrics from LRCLIB" }, lyrTop, lyrInner);
  const cur = h("span");
  const dur = h("span");
  const fill = h("i");
  const bar = h("div", { class: "m-bar", role: "slider", "aria-label": "Song position", tabindex: "0" }, fill);
  const progress = h("div", { class: "m-progress" }, cur, bar, dur);
  // Compact player actions: the reference has no top-row action buttons.
  // Keep Lyrics accessible as a dedicated transport-side control instead.
  const lyrBtn = h(
    "button",
    { class: "m-lyr-btn transport-side lyrics-side", title: "Lyrics", "aria-label": "Lyrics", "aria-pressed": "false", type: "button" },
    svg(ICONS.lyrics, 15),
  ) as HTMLButtonElement;

  const transport = (label: string, path: string, size: number, action: "toggle" | "next" | "previous", extra = "") => {
    const b = h(
      "button",
      { class: `m-btn ${extra}`.trim(), title: label, "aria-label": label, type: "button" },
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
    { class: "m-action transport-side shuffle", title: "Shuffle", "aria-label": "Shuffle", "aria-pressed": "false", type: "button" },
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
  const controls = h("div", { class: "m-controls" }, shuffleBtn, prevBtn, toggleBtn, nextBtn, lyrBtn);
  const head = h("div", { class: "m-head" }, text);

  const openBtn = h(
    "button",
    { class: "btn primary m-open", type: "button", onclick: () => SpotifyFeed.launch() },
    h("span", { text: "Open Spotify" }),
  );

  const main = h("div", { class: "m-main" }, head, lyrics, progress, controls, openBtn);
  const row = h("div", { class: "music" }, art, main);
  // Music is intentionally full-bleed. There is no inner .card surface in the DOM.
  const el = h("div", { class: "view music-view" }, ambience, row);

  // ── Rendering ───────────────────────────────────────────────────────────────
  bar.addEventListener("click", (event: MouseEvent) => {
    const rect = bar.getBoundingClientRect();
    const total = SpotifyFeed.state?.durationMs;
    if (!rect.width || !total || !Number.isFinite(total)) return;
    const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    SpotifyFeed.seek(fraction * total);
    bar.style.setProperty("--seek-preview", `${fraction * 100}%`);
    window.setTimeout(() => bar.style.removeProperty("--seek-preview"), 220);
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
  let ticker: number | undefined;

  // ── Lyrics ──────────────────────────────────────────────────────────────────
  let lyricsOn = false;
  let lyricsTrack = ""; // trackKey the panel currently shows / is loading
  let lyricsView: LyricsView = { kind: "loading" };
  let lineEls: HTMLElement[] = [];
  let shownLine = -2;

  function paintLyrics() {
    lyrInner.replaceChildren();
    lineEls = [];
    shownLine = -2;
    lyrics.classList.toggle("plain", lyricsView.kind === "plain");
    const s = SpotifyFeed.state;
    lyrMetaTitle.textContent = s?.title ?? "Lyrics";
    lyrMetaArtist.textContent = s?.artist ?? "";
    if (SpotifyFeed.art) lyrArtImg.src = SpotifyFeed.art;
    else lyrArtImg.removeAttribute("src");
    const v = lyricsView;
    if (v.kind === "synced") {
      updateLyricsPosition(true);
    } else if (v.kind === "plain") {
      const d = h("div", { class: "m-lyr-line cur", text: v.text });
      lineEls.push(d);
      lyrInner.append(d);
    } else {
      const msg =
        v.kind === "loading" ? "Finding lyrics…"
        : v.kind === "instrumental" ? "Instrumental ♪"
        : v.kind === "offline" ? "Couldn't reach the lyrics service"
        : "No lyrics found for this song";
      lyrInner.append(h("div", { class: "m-lyr-msg", text: msg }));
    }
  }

  function updateLyricsPosition(force = false) {
    if (!lyricsOn || lyricsView.kind !== "synced") return;
    const s = SpotifyFeed.state;
    const pos = s ? currentPosition(s, SpotifyFeed.receivedAt, performance.now()) : null;
    if (pos == null) return;
    const lines = lyricsView.lines;
    let idx = -1;
    for (let i = 0; i < lines.length && lines[i].t <= pos + 150; i++) idx = i;
    if (!force && idx === shownLine) return;
    const direction = shownLine >= 0 && idx !== shownLine ? (idx > shownLine ? "up" : "down") : "in";
    shownLine = idx;
    lyrInner.replaceChildren();
    lineEls = [];
    for (let offset = -1; offset <= 1; offset++) {
      const item = lines[idx + offset];
      const d = h("div", { class: `m-lyr-line ${offset === 0 ? "cur" : "side"} lyric-${direction}`.trim(), text: item?.text || "" });
      if (!item) d.classList.add("empty");
      lineEls.push(d);
      lyrInner.append(d);
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
      else if (Array.isArray(d.synced) && d.synced.length) view = { kind: "synced", lines: d.synced };
      else view = { kind: "plain", text: String(d.plain || "") };
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
    const cls = on ? "m-anim-in" : "m-anim-out";
    el.classList.remove("m-anim-in", "m-anim-out");
    void el.offsetWidth; // restart the CSS animations
    el.classList.add(cls);
    window.clearTimeout(animTimer);
    animTimer = window.setTimeout(() => el.classList.remove(cls), 600);
  }

  function setLyrics(on: boolean, animate = false) {
    if (animate && on !== lyricsOn) playToggleAnimation(on);
    lyricsOn = on;
    State.lyricsOpen = on;
    el.classList.toggle("lyrics-on", on);
    lyrBtn.classList.toggle("on", on);
    lyrBtn.setAttribute("aria-pressed", String(on));
    lyrBtn.title = on ? "Hide lyrics" : "Lyrics";
    if (on) void loadLyrics();
  }

  function toggleLyrics() {
    setLyrics(!lyricsOn, true);
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
    setLyrics(false, true);
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
  // Also catch taps on the expanded ::before hit pad via the top bar.
  lyrTop.addEventListener("pointerdown", (event) => {
    const t = event.target as HTMLElement | null;
    if (t === lyrClose || lyrClose.contains(t as Node) || t?.closest?.(".m-lyr-close")) {
      onClosePointer(event);
    }
  }, { passive: false, capture: true });

  function mode(): Mode {
    if (!IS_TAURI) return "preview";
    const s = SpotifyFeed.state;
    if (!s) return "loading";
    return s.running ? s.status : "closed";
  }

  function renderProgress() {
    const s = SpotifyFeed.state;
    const pos = s ? currentPosition(s, SpotifyFeed.receivedAt, performance.now()) : null;
    const total = s?.durationMs ?? null;
    progress.classList.toggle("none", pos == null || total == null);
    if (pos == null || total == null) {
      // Nothing to measure: don't leave the previous song's time behind.
      cur.textContent = "";
      dur.textContent = "";
      fill.style.transform = "scaleX(0)";
      return;
    }
    fill.style.transform = `scaleX(${progressFraction(pos, total)})`;
    cur.textContent = formatTime(pos);
    // Frost shows time remaining ("-3:15") like the Apple players; other themes show the total.
    dur.textContent = `-${formatTime(Math.max(0, total - pos))}`;
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
    title.textContent = t;
    title.title = t;
    artist.textContent = a;
    artist.title = a;

    const track = s?.trackKey ?? "";
    if (track !== lastTrack) {
      if (lastTrack !== "" && track !== "") {
        // Apple Music-style track change: artwork pops in, the title slides up and settles.
        const spring = "cubic-bezier(.34,1.56,.64,1)";
        art.animate([{ scale: "0.84", opacity: 0.35 }, { scale: "1", opacity: 1 }], { duration: 560, easing: spring });
        text.animate([{ opacity: 0, translate: "0 10px", filter: "blur(4px)" }, { opacity: 1, translate: "0 0", filter: "blur(0)" }], { duration: 480, easing: "cubic-bezier(.22,1,.36,1)" });
      }
      lastTrack = track;
      if (lyricsOn) void loadLyrics();
    }

    // The lyrics button only makes sense while a song is loaded.
    const hasSong = m === "playing" || m === "paused";
    if (!hasSong && lyricsOn) setLyrics(false);

    // Cover: only touch the <img> when the picture actually changed.
    const wanted = m === "closed" || m === "loading" || m === "preview" ? null : SpotifyFeed.art;
    if (wanted !== shownArt) {
      shownArt = wanted;
      if (wanted) {
        img.src = wanted;
        ambience.style.backgroundImage = `url("${wanted.replace(/"/g, '\\"')}")`;
        art.classList.add("has-art");
        el.classList.add("has-ambience");
      } else {
        img.removeAttribute("src");
        ambience.style.backgroundImage = "";
        art.classList.remove("has-art");
        el.classList.remove("has-ambience");
      }
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
        // Spotify reports on change only, so the bar is advanced locally.
        ticker = window.setInterval(() => {
          if (SpotifyFeed.state?.status === "playing") renderProgress();
          updateLyricsPosition();
        }, 250);
        render();
      } else {
        unsubscribe?.();
        unsubscribe = null;
        window.clearInterval(ticker);
        ticker = undefined;
        if (lyricsOn) setLyrics(false); // always reopen on the normal controls
      }
      void SpotifyFeed.setActive(on);
    },
  };
}
