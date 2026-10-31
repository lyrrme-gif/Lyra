// Record tab: a red button that starts / stops a screen recording (backend/screen_record.py).
// Small like the other utility tabs. While recording it shows the running time and a pause button; afterwards
// a button opens the folder with the file.
//
// Cheap on purpose: the clock is counted here (one 1-second timer, only while it is recording, not paused and the
// tab is on screen), so the backend is asked for its status only every few seconds - and only while this tab is open.

import { visiblePoll, type VisiblePoll } from "../core/visiblePoll";
import "./record.css";
import { h, svg } from "./dom";
import { ICONS } from "./icons";
import type { ViewHost } from "./views";

const BASE = "http://127.0.0.1:8765";
const POLL_MS = 3000;

interface Status {
  ok?: boolean; recording?: boolean; paused?: boolean; stopping?: boolean; saving?: boolean;
  seconds?: number; size?: number; saved?: boolean; path?: string; error?: string; folder?: string;
  audio?: string; notice?: string; obs_ok?: boolean; obs_message?: string;
}

/** The last two parts of the save folder, e.g. "Videos / Lyra Recordings" (the full path is in the tooltip). */
const shortFolder = (p: string) => p.split(/[\\/]+/).filter(Boolean).slice(-2).join(" / ");

const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

const bytes = (n: number) => (n >= 1 << 30 ? `${(n / (1 << 30)).toFixed(1)} GB` : `${Math.max(1, Math.round(n / (1 << 20)))} MB`);

export function buildRecord(): ViewHost {
  const btn = h("button", { class: "rc-btn", type: "button", "aria-label": "Start recording" }, h("i", { class: "rc-dot" })) as HTMLButtonElement;
  const t1 = h("div", { class: "rc-t1", text: "Record screen" });
  const t2 = h("div", { class: "rc-t2", text: "Tap the red button to start" });
  const folder = h("button", { class: "rc-folder", type: "button", title: "Show in folder", "aria-label": "Show the recording in its folder" }, svg(ICONS.photo, 15, { stroke: 2 })) as HTMLButtonElement;
  const pauseIcon = svg(ICONS.pause, 15), playIcon = svg(ICONS.play, 15);
  const pauseBtn = h("button", { class: "rc-pause", type: "button", title: "Pause", "aria-label": "Pause recording" }, pauseIcon) as HTMLButtonElement;
  const el = h("div", { class: "view record-view" }, h("div", { class: "card rc-card" }, btn, h("div", { class: "rc-copy" }, t1, t2), pauseBtn, folder));

  let rec = false, paused = false, busy = false, active = false;
  let baseSeconds = 0, baseAt = 0;                       // the backend's recorded seconds, and when we heard it
  let poller: VisiblePoll | null = null, ticker = 0;

  const seconds = () => (rec && !paused ? baseSeconds + Math.floor((performance.now() - baseAt) / 1000) : baseSeconds);
  const stopTicker = () => { if (ticker) { window.clearInterval(ticker); ticker = 0; } };
  const syncTicker = () => {
    if (active && rec && !paused) {
      if (!ticker) ticker = window.setInterval(() => { if (!document.hidden) t1.textContent = clock(seconds()); }, 1000);
    } else stopTicker();
  };

  const paint = (s: Status | null) => {
    if (!s) { rec = false; paused = false; stopTicker(); t1.textContent = "Record screen"; t2.textContent = "Lyra's backend isn't reachable"; el.classList.remove("recording", "paused"); folder.style.display = "none"; pauseBtn.style.display = "none"; return; }
    rec = !!s.recording;
    paused = rec && !!s.paused;
    baseSeconds = s.seconds ?? 0; baseAt = performance.now();
    el.classList.toggle("recording", rec);
    el.classList.toggle("paused", paused);
    btn.setAttribute("aria-label", rec ? "Stop recording" : "Start recording");
    pauseBtn.style.display = rec ? "grid" : "none";
    pauseBtn.replaceChildren(paused ? playIcon : pauseIcon);
    pauseBtn.title = paused ? "Resume" : "Pause";
    pauseBtn.setAttribute("aria-label", paused ? "Resume recording" : "Pause recording");
    folder.style.display = !rec && s.saved ? "" : "none";
    if (rec) {
      t1.textContent = clock(baseSeconds);
      const size = s.size ? ` · ${bytes(s.size)}` : "";
      t2.textContent = s.notice ? s.notice : paused ? "Paused · tap ▶ to carry on, ■ to save" : `Recording with OBS${size} · tap to stop`;
    }
    else if (s.stopping || s.saving) { t1.textContent = "Saving…"; t2.textContent = "Putting the video together"; }
    else if (s.error && !s.saved) { t1.textContent = "Couldn't record"; t2.textContent = s.error; }
    else if (s.saved) { t1.textContent = "Saved"; t2.textContent = s.error ? s.error : s.folder ? `In ${shortFolder(s.folder)}` : "In your recordings folder"; }
    else if (s.obs_ok === false && s.obs_message) { t1.textContent = "Record screen"; t2.textContent = s.obs_message; }
    else { t1.textContent = "Record screen"; t2.textContent = s.folder ? `Tap to start (OBS) · saves in ${shortFolder(s.folder)}` : "Tap the red button to start"; }
    t2.title = s.folder ? `Recordings are saved in ${s.folder}. Change it in Settings → Recordings.` : "";
    syncTicker();
  };

  const call = async (path: string, post = false): Promise<Status | null> => {
    try {
      const r = await fetch(`${BASE}${path}`, post ? { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" } : undefined);
      return r.ok ? ((await r.json()) as Status) : null;
    } catch { return null; }
  };
  const poll = async () => { if (!busy) paint(await call("/record/status")); };

  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (busy) return;
    busy = true; btn.disabled = true; pauseBtn.disabled = true;
    t2.textContent = rec ? "Saving…" : "Starting…";
    paint(await call(rec ? "/record/stop" : "/record/start", true));
    busy = false; btn.disabled = false; pauseBtn.disabled = false;
  });
  pauseBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (busy || !rec) return;
    busy = true; btn.disabled = true; pauseBtn.disabled = true;
    paint(await call(paused ? "/record/resume" : "/record/pause", true));
    busy = false; btn.disabled = false; pauseBtn.disabled = false;
  });
  folder.addEventListener("click", (e) => { e.stopPropagation(); void call("/record/open-folder", true); });

  return {
    el,
    sync() {},
    setActive(on) {
      active = on;
      poller?.stop(); poller = null;
      stopTicker();
      if (on) poller = visiblePoll(() => poll(), { ms: POLL_MS });   // runs at once; cleared while the window is hidden
    },
  };
}
