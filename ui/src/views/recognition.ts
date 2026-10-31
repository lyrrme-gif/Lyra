import { h, clear, svg } from "./dom";
import { ICONS } from "./icons";
import type { ViewHost } from "./views";
import { musicRecognitionHistoryFull, recognizeMusic } from "../lyra/client";
import { Bridge } from "../core/bridge";
import { State } from "../core/state";

interface Result { ok: boolean; title?: string; artist?: string; album?: string; artwork_url?: string; spotify_url?: string; youtube_url?: string; message?: string; source?: string; ts?: number; }

const SPOTIFY_GLYPH = `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M12 1.5C6.2 1.5 1.5 6.2 1.5 12S6.2 22.5 12 22.5 22.5 17.8 22.5 12 17.8 1.5 12 1.5zm4.86 15.12a.66.66 0 0 1-.9.22c-2.47-1.51-5.58-1.85-9.24-1.01a.66.66 0 1 1-.29-1.28c4.01-.91 7.42-.52 10.21 1.17a.66.66 0 0 1 .22.9zm1.3-2.89a.82.82 0 0 1-1.13.27c-2.83-1.74-7.14-2.24-10.48-1.23a.82.82 0 1 1-.48-1.57c3.8-1.16 8.54-.6 11.82 1.42a.82.82 0 0 1 .27 1.11zm.11-3.01c-3.39-2.01-8.98-2.2-12.21-1.21a.99.99 0 1 1-.57-1.89c3.7-1.13 9.86-.91 13.74 1.4a.99.99 0 0 1-.96 1.7z"/></svg>`;

export function buildRecognition(): ViewHost {
  // One calm stage with three states: idle (tap to listen) · listening · result.
  const title = h("div", { class: "rec-hidden" });
  const sub = h("div", { class: "rec-hidden" });
  const scanIcon = h("div", { class: "rec-art" }, svg(ICONS.waveform, 30, { stroke: 2.2 }));
  const button = h("button", { class: "rec-orb", type: "button", title: "Identify music", "aria-label": "Identify music" },
    h("i", { class: "rec-ring r1" }), h("i", { class: "rec-ring r2" }), h("i", { class: "rec-ring r3" }),
    h("span", { class: "rec-orb-core" }, svg(ICONS.waveform, 34, { stroke: 2.2 })));
  const scanTitle = h("div", { class: "rec-title", text: "Identify music" });
  const scanSub = h("div", { class: "rec-sub", text: "Tap to listen" });
  const wave = h("div", { class: "rec-wave", "aria-hidden": "true" }, ...[0, 1, 2, 3, 4, 5, 6].map((i) => h("span", { style: `animation-delay:${i * 0.09}s` })));
  const spotifyButton = h("button", { class: "rec-open", type: "button", title: "Open in Spotify", "aria-label": "Open recognized song in Spotify" });
  spotifyButton.innerHTML = `${SPOTIFY_GLYPH}<span>Open</span>`;
  const copy = h("div", { class: "rec-copy" }, scanTitle, scanSub, wave, spotifyButton);
  const scanCard = h("div", { class: "rec-stage" }, h("div", { class: "rec-visual" }, button, scanIcon), copy);
  let currentSpotifyUrl = "";
  spotifyButton.addEventListener("click", (e) => { e.stopPropagation(); if (currentSpotifyUrl) void Bridge.openUrl(currentSpotifyUrl); });
  const history = h("div", { class: "rec-history" });
  const body = h("div", { class: "rec-body" }, scanCard, history, title, sub);
  const el = h("div", { class: "view recognition-view" }, body);
  let busy = false;
  let active = false; // Recognition tab visible on an expanded island

  function setScanIcon(url?: string, success = false) {
    clear(scanIcon);
    if (url) {
      const img = h("img", { alt: "", src: url }) as HTMLImageElement;
      img.addEventListener("error", () => { clear(scanIcon); scanIcon.append(svg(success ? ICONS.music : ICONS.waveform, 30, success ? undefined : { stroke: 2.2 })); }, { once: true });
      scanIcon.append(img);
    } else {
      scanIcon.append(svg(success ? ICONS.music : ICONS.waveform, 30, success ? undefined : { stroke: 2.2 }));
    }
  }

  function renderResult(r: Result | null) {
    scanCard.classList.remove("has-result", "has-error");
    currentSpotifyUrl = r?.ok ? String(r.spotify_url || "") : "";
    spotifyButton.classList.toggle("on", !!currentSpotifyUrl);
    if (!r) {
      setScanIcon();
      scanTitle.textContent = "Identify music";
      scanSub.textContent = "Tap to listen";
      return;
    }
    if (r.ok) {
      scanCard.classList.add("has-result");
      setScanIcon(r.artwork_url, true);
      scanTitle.textContent = r.title || "Unknown title";
      scanSub.textContent = r.artist || "Unknown artist";
      return;
    }
    scanCard.classList.add("has-error");
    setScanIcon();
    scanTitle.textContent = r.source === "capture" ? "Couldn't hear the audio" : "No match";
    scanSub.textContent = r.message || "Try another part of the song";
  }

  function renderHistory(items: any[]) {
    clear(history);
    for (const x of items.slice(0, 12)) {
      const url = String(x.spotify_url || "");
      const art = x.artwork_url
        ? (h("img", { class: "rec-row-art", alt: "", src: String(x.artwork_url) }) as HTMLElement)
        : h("span", { class: "rec-row-art ph" }, svg(ICONS.music, 14));
      const open = h("button", { class: "rec-row-sp", type: "button", title: url ? "Open in Spotify" : "No Spotify link", "aria-label": "Open in Spotify" });
      open.innerHTML = SPOTIFY_GLYPH;
      open.toggleAttribute("disabled", !url);
      open.addEventListener("click", (e) => { e.stopPropagation(); if (url) void Bridge.openUrl(url); });
      history.append(h("div", { class: "rec-row" }, art,
        h("div", { class: "rec-row-text" }, h("div", { class: "rec-row-title", text: String(x.title || "Unknown") }), h("div", { class: "rec-row-artist", text: String(x.artist || "") })),
        open));
    }
  }

  const LOCAL_HISTORY_KEY = "lyra.musicRecognitionHistory";

  function readLocalHistory(): any[] {
    try {
      const raw = localStorage.getItem(LOCAL_HISTORY_KEY);
      const data = raw ? JSON.parse(raw) : [];
      return Array.isArray(data) ? data : [];
    } catch { return []; }
  }

  function writeLocalHistory(items: any[]) {
    try { localStorage.setItem(LOCAL_HISTORY_KEY, JSON.stringify(items.slice(0, 50))); } catch { /* storage can be unavailable */ }
  }

  function mergeHistory(remote: any[], local: any[]): any[] {
    const merged: any[] = [];
    const sameEvent = (a: any, b: any) => {
      const titleA = String(a?.title || "").trim().toLowerCase();
      const artistA = String(a?.artist || "").trim().toLowerCase();
      const titleB = String(b?.title || "").trim().toLowerCase();
      const artistB = String(b?.artist || "").trim().toLowerCase();
      if (!titleA || !artistA || titleA !== titleB || artistA !== artistB) return false;
      const ta = Number(a?.ts || 0), tb = Number(b?.ts || 0);
      return !ta || !tb || Math.abs(ta - tb) <= 15_000;
    };
    for (const x of [...remote, ...local]) {
      if (!x?.title && !x?.artist) continue;
      const idx = merged.findIndex((y) => sameEvent(x, y));
      if (idx < 0) {
        merged.push({ ...x });
        continue;
      }
      const prev = merged[idx];
      merged[idx] = {
        ...prev,
        ...x,
        album: x?.album || prev?.album || "",
        artwork_url: x?.artwork_url || prev?.artwork_url || "",
        spotify_url: x?.spotify_url || prev?.spotify_url || "",
        youtube_url: x?.youtube_url || prev?.youtube_url || "",
        ts: Math.max(Number(prev?.ts || 0), Number(x?.ts || 0)),
      };
    }
    return merged.sort((a, b) => Number(b?.ts || 0) - Number(a?.ts || 0)).slice(0, 50);
  }

  const SESSION_KEY = "lyra.musicRecognitionSession";

  /** A new backend session means a new app run: drop the cached history. */
  async function syncSession(): Promise<any[]> {
    const full = await musicRecognitionHistoryFull();
    if (!full) return [];
    try {
      if (full.session && localStorage.getItem(SESSION_KEY) !== full.session) {
        localStorage.removeItem(LOCAL_HISTORY_KEY);
        localStorage.setItem(SESSION_KEY, full.session);
      }
    } catch { /* storage can be unavailable */ }
    return full.items;
  }
  const sessionReady = syncSession();

  async function refreshHistory(): Promise<any[]> {
    const remote = await syncSession();
    const merged = mergeHistory(remote, readLocalHistory());
    writeLocalHistory(merged);
    renderHistory(merged);
    return merged;
  }

  function resetTop() {
    sub.textContent = "Play a song and let Lyra identify it.";
    renderResult(null);
  }

  const onActivity = (ev: Event) => {
    const d = (ev as CustomEvent<any>).detail || {};
    if (d.kind !== "music_recognition") return;
    if (d.phase === "start") {
      sub.textContent = d.subtitle || "Listening… checking a short system-audio sample.";
      scanCard.classList.remove("has-result", "has-error");
      setScanIcon();
      scanTitle.textContent = "Listening…";
      scanSub.textContent = "";
      button.classList.add("busy"); scanCard.classList.add("listening"); State.recognitionBusy = true;
      button.disabled = true;
      busy = true;
    }
  };
  const onResult = async (ev: Event) => {
    const d = (ev as CustomEvent<Result>).detail;
    if (!d) return;
    busy = false;
    button.classList.remove("busy"); scanCard.classList.remove("listening"); State.recognitionBusy = false;
    button.disabled = false;
    sub.textContent = d.ok ? "Song recognized." : (d.source === "capture" ? "System audio could not be captured." : "No match found — try another part of the song.");
    // The event and the history endpoint can arrive with slightly different
    // metadata. Merge them so artwork/Spotify survives even if one event was
    // produced by an older backend/UI bridge.
    const local = readLocalHistory();
    const eventItem = { ...d, ts: d.ts || Date.now() };
    const mergedLocal = mergeHistory([eventItem], local);
    writeLocalHistory(mergedLocal);
    const items = await refreshHistory();
    const match = items.find((x) =>
      String(x?.title || "").trim().toLowerCase() === String(d.title || "").trim().toLowerCase() &&
      String(x?.artist || "").trim().toLowerCase() === String(d.artist || "").trim().toLowerCase()
    );
    if (!active) { resetTop(); return; }
    renderResult({ ...(match || {}), ...d,
      artwork_url: d.artwork_url || match?.artwork_url || "",
      spotify_url: d.spotify_url || match?.spotify_url || "",
      album: d.album || match?.album || "",
    });
  };
  const onHistoryResult = (ev: Event) => {
    const d = (ev as CustomEvent<Result>).detail;
    if (!d?.ok) return;
    const item = { ...d, ts: d.ts || Date.now() };
    const merged = mergeHistory([item], readLocalHistory());
    writeLocalHistory(merged);
    // If the Recognition page is currently visible, refresh its visible list too.
    if (el.classList.contains("active")) renderHistory(merged);
  };
  window.addEventListener("lyra:music-recognition-activity", onActivity);
  window.addEventListener("lyra:music-recognition-result", onResult);
  window.addEventListener("lyra:music-recognition-history", onHistoryResult);

  button.addEventListener("click", async () => {
    if (busy) return;
    busy = true; button.classList.add("busy"); scanCard.classList.add("listening"); State.recognitionBusy = true; button.disabled = true;
    sub.textContent = "Listening… checking a short system-audio sample.";
    scanCard.classList.remove("has-result", "has-error");
    setScanIcon();
    scanTitle.textContent = "Listening…";
    scanSub.textContent = "";
    const r = await recognizeMusic(true);
    busy = false; button.classList.remove("busy"); scanCard.classList.remove("listening"); State.recognitionBusy = false; button.disabled = false;
    sub.textContent = r?.ok ? "Song recognized." : (r?.source === "capture" ? "System audio could not be captured." : "No match found — try another part of the song.");
    const item = { ...(r as Result), ts: Date.now() };
    const local = mergeHistory([item], readLocalHistory());
    writeLocalHistory(local);
    const items = await refreshHistory();
    const match = items.find((x) =>
      String(x?.title || "").trim().toLowerCase() === String(item.title || "").trim().toLowerCase() &&
      String(x?.artist || "").trim().toLowerCase() === String(item.artist || "").trim().toLowerCase()
    );
    if (!active) { resetTop(); return; }
    renderResult({ ...(match || {}), ...item,
      artwork_url: item.artwork_url || match?.artwork_url || "",
      spotify_url: item.spotify_url || match?.spotify_url || "",
      album: item.album || match?.album || "",
    });
  });

  return {
    el,
    sync() {},
    setActive(on: boolean) {
      active = on;
      // The top card always starts empty when the tab opens and is cleared as
      // soon as the island shrinks again; results live in "Previous recognitions".
      if (!busy) resetTop();
      if (on) {
        void sessionReady.then(() => {
          const local = readLocalHistory();
          if (local.length) renderHistory(local); else renderHistory([]);
          void refreshHistory();
        });
      }
    },
  };
}
