// Camera tab: live preview with effects, photo and video capture, and gestures.
//
// The camera is on only while this tab is on screen (selected AND the island open): it is released the
// moment you switch tab or the island closes, so the camera light goes out and other apps (Zoom, Teams...)
// can use it. A recording in progress is finished and saved first, never thrown away.
//
// How it works: the camera feeds a hidden <video>; every frame is drawn onto a <canvas> with the chosen
// effect (a canvas filter). The canvas is what you see, what a photo is taken from and what is recorded
// (canvas.captureStream + MediaRecorder), so preview, photo and video always look the same.
// Files are POSTed to the backend (backend/camera_store.py), which saves them in
// Pictures \ Lyra Camera (photos) and Videos \ Lyra Camera (videos).
//
// Gestures
//   tap shutter ............ photo            hold shutter ........... record while held, release to stop
//   red button ............. start / stop video (hands-free)
//   swipe preview left/right  next / previous effect       double-tap preview ... mirror on / off
// Keys (while the tab is open): P photo · R record · M mirror · F effects · ← → effects · Esc closes the tray.

import "./camera.css";
import { h, svg } from "../../../shared/views/dom";
import { ICONS } from "../../../shared/views/icons";
import type { ViewHost } from "./views";

const BASE = "http://127.0.0.1:8765";
const HOLD_MS = 450;            // press the shutter this long and it becomes a video
const SWIPE_PX = 48;            // sideways distance that counts as a swipe on the preview
const DOUBLE_TAP_MS = 300;
const UI_IDLE_MS = 3200;        // controls fade out after this long without touching
const MAX_REC_S = 15 * 60;      // a recording is kept in memory until it ends, so it is capped
const TIMERS = [0, 3, 10];

type CamState = "idle" | "requesting" | "live" | "denied" | "nodevice" | "busy" | "unsupported" | "error";

interface Effect { id: string; label: string; filter: string; vignette?: number; sw: string }
const EFFECTS: Effect[] = [
  { id: "none", label: "Natural", filter: "none", sw: "linear-gradient(135deg,#c9d1dc,#7b8594)" },
  { id: "vivid", label: "Vivid", filter: "saturate(1.5) contrast(1.08)", sw: "linear-gradient(135deg,#ff5f6d,#ffc371)" },
  { id: "warm", label: "Warm", filter: "sepia(.28) saturate(1.2) hue-rotate(-8deg) brightness(1.04)", sw: "linear-gradient(135deg,#ffb36b,#ff7a45)" },
  { id: "cool", label: "Cool", filter: "saturate(1.1) hue-rotate(12deg) brightness(1.03) contrast(1.05)", sw: "linear-gradient(135deg,#7fd1ff,#4a7dff)" },
  { id: "fade", label: "Fade", filter: "contrast(.85) brightness(1.1) saturate(.78)", vignette: 0.18, sw: "linear-gradient(135deg,#e6dfd4,#b5aca0)" },
  { id: "mono", label: "Mono", filter: "grayscale(1)", sw: "linear-gradient(135deg,#d9d9d9,#555)" },
  { id: "noir", label: "Noir", filter: "grayscale(1) contrast(1.45) brightness(.92)", vignette: 0.5, sw: "linear-gradient(135deg,#777,#050505)" },
  { id: "soft", label: "Soft", filter: "blur(.7px) brightness(1.07) saturate(1.1)", sw: "linear-gradient(135deg,#ffe3ec,#f6b8d0)" },
];

// ── tiny persistence (never fails the tab) ───────────────────────────────────
const LS = "lyra.camera.";
const load = (k: string, d: string) => { try { return localStorage.getItem(LS + k) ?? d; } catch { return d; } };
const save = (k: string, v: string) => { try { localStorage.setItem(LS + k, v); } catch { /* storage unavailable */ } };

const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
const shortFolder = (p: string) => p.split(/[\\/]+/).filter(Boolean).slice(-2).join(" / ");

interface Saved { ok?: boolean; folder?: string; name?: string; error?: string }

function pickMime(): { mime: string; ext: string } {
  const MR = (window as unknown as { MediaRecorder?: typeof MediaRecorder }).MediaRecorder;
  const tries: Array<[string, string]> = [
    ["video/mp4;codecs=avc1.42E01E,mp4a.40.2", "mp4"], ["video/mp4", "mp4"],
    ["video/webm;codecs=vp9,opus", "webm"], ["video/webm;codecs=vp8,opus", "webm"], ["video/webm", "webm"],
  ];
  for (const [m, e] of tries) if (MR?.isTypeSupported?.(m)) return { mime: m, ext: e };
  return { mime: "", ext: "webm" };
}

export function buildCamera(): ViewHost {
  // ── elements ───────────────────────────────────────────────────────────────
  const video = h("video", { class: "cam-src" }) as HTMLVideoElement;
  video.muted = true; video.autoplay = true; video.playsInline = true;
  const canvas = h("canvas", { class: "cam-canvas" }) as HTMLCanvasElement;
  const ctx = canvas.getContext("2d", { alpha: false })!;
  const filtersOk = !!ctx && "filter" in ctx;

  const msgIcon = h("div", { class: "cam-msg-ico" }, svg(ICONS.cameraFill, 26, { evenodd: true }));
  const msgText = h("div", { class: "cam-msg-text" });
  const msgSub = h("div", { class: "cam-msg-sub" });
  const msgBtns = h("div", { class: "cam-msg-btns" });
  const msg = h("div", { class: "cam-msg", role: "status", "aria-live": "polite" }, msgIcon, msgText, msgSub, msgBtns);

  const flash = h("div", { class: "cam-flash", "aria-hidden": "true" });
  const count = h("div", { class: "cam-count", "aria-hidden": "true" });
  const hint = h("div", { class: "cam-hint", "aria-hidden": "true" });
  const toast = h("button", { class: "cam-toast", type: "button", role: "status" }) as HTMLButtonElement;

  // top bar: status pill (live / REC) on the left, small toggles on the right
  const pillDot = h("i", { class: "cam-pill-dot" });
  const pillText = h("span", { class: "cam-pill-text" });
  const pill = h("div", { class: "cam-pill" }, pillDot, pillText);
  const mkTool = (icon: string, label: string, opts: { stroke?: number; evenodd?: boolean } = { stroke: 2 }) =>
    h("button", { class: "cam-tool", type: "button", title: label, "aria-label": label }, svg(icon, 15, opts)) as HTMLButtonElement;
  const flipBtn = mkTool(ICONS.restart, "Switch camera", { stroke: 2.2 });
  const micBtn = mkTool(ICONS.mic, "Record with sound: off");
  const mirrorBtn = mkTool(ICONS.contrast, "Mirror preview");
  const folderBtn = mkTool(ICONS.folder, "Show last capture in folder", { stroke: 1.9 });
  const tools = h("div", { class: "cam-tools" }, folderBtn, flipBtn, micBtn, mirrorBtn);
  const top = h("div", { class: "cam-top" }, pill, tools);

  // bottom bar: effects + timer (left), shutter (centre), record (right)
  const fxBtn = h("button", { class: "cam-chip", type: "button", "aria-haspopup": "true", "aria-expanded": "false", title: "Effects (F)" },
    svg(ICONS.sliders, 14, { stroke: 2 }), h("span", { class: "cam-chip-text", text: "Natural" })) as HTMLButtonElement;
  const fxLabel = fxBtn.querySelector(".cam-chip-text") as HTMLElement;
  const timerBtn = h("button", { class: "cam-chip cam-chip-sq", type: "button", title: "Self-timer", "aria-label": "Self-timer: off" },
    svg(ICONS.timerFill, 14, { evenodd: true }), h("span", { class: "cam-chip-text", text: "" })) as HTMLButtonElement;
  const timerLabel = timerBtn.querySelector(".cam-chip-text") as HTMLElement;
  const shutter = h("button", { class: "cam-shutter", type: "button", title: "Tap for a photo, hold to record (P)", "aria-label": "Take photo" }, h("i", { class: "cam-shutter-core" })) as HTMLButtonElement;
  const recBtn = h("button", { class: "cam-rec", type: "button", title: "Start recording (R)", "aria-label": "Start recording" }, h("i", { class: "cam-rec-core" })) as HTMLButtonElement;
  const bottom = h("div", { class: "cam-bottom" },
    h("div", { class: "cam-side cam-side-l" }, fxBtn, timerBtn),
    shutter,
    h("div", { class: "cam-side cam-side-r" }, recBtn));

  // effects tray
  const tray = h("div", { class: "cam-tray", role: "listbox", "aria-label": "Effects", "aria-hidden": "true" });
  const chips = EFFECTS.map((e) => {
    const sw = h("i", { class: "cam-sw" }); sw.style.background = e.sw;
    const b = h("button", { class: "cam-fx", type: "button", role: "option", "data-fx": e.id, tabindex: "-1" }, sw, h("span", { text: e.label })) as HTMLButtonElement;
    b.addEventListener("click", (ev) => { ev.stopPropagation(); setEffect(e.id); window.setTimeout(() => openTray(false), 220); });
    tray.append(b);
    return b;
  });

  const ui = h("div", { class: "cam-ui" }, top, tray, bottom);
  const el = h("div", { class: "view camera-view", "data-state": "idle", "data-no-swipe": "" },
    video, canvas, msg, flash, count, hint, ui, toast);

  // ── state ──────────────────────────────────────────────────────────────────
  let state: CamState = "idle";
  let stream: MediaStream | null = null;
  let seq = 0;                       // bumped on every start/stop so a slow permission prompt can't revive a closed tab
  let active = false;
  let devices: MediaDeviceInfo[] = [];
  let fellBack = false;              // the chosen camera could not be opened and the default one was used instead
  let deviceId = load("device", "");
  let fxId = load("fx", "none");
  let mirror = load("mirror", "1") === "1";
  let micOn = load("mic", "0") === "1";
  let timerIdx = Math.max(0, TIMERS.indexOf(Number(load("timer", "0"))));
  let trayOpen = false;
  let lastSaved = false;

  let loopSeq = 0;
  let vignetteCache: { g: CanvasGradient; k: number } | null = null;

  let recorder: MediaRecorder | null = null;
  let recChunks: Blob[] = [];
  let recStream: MediaStream | null = null;
  let micStream: MediaStream | null = null;
  let recStart = 0, recTimer = 0, recExt = "webm", recHeld = false, recStarting = false, stopWhenStarted = false;
  let recFinish: Promise<void> | null = null;

  let countdownTimer = 0, countdownLeft = 0;
  let uiTimer = 0, toastTimer = 0, hintTimer = 0;
  let holdTimer = 0, holdFired = false;
  let retry: (() => void) | null = null;
  let lastTap = 0;

  // Windows lists more than the webcam: the Windows Hello infrared / depth sensor shows up as a "camera" too, and a
  // webview cannot open it. Leave those out of the list the flip button cycles through.
  const IR = /\b(ir|infrared|depth)\b|windows hello/i;
  const usable = () => devices.filter((d) => !IR.test(d.label));
  const fx = () => EFFECTS.find((e) => e.id === fxId) ?? EFFECTS[0];
  const recording = () => !!recorder && recorder.state !== "inactive";

  // ── UI state painting ──────────────────────────────────────────────────────
  const setState = (s: CamState, text = "", sub = "", buttons: HTMLElement[] = []) => {
    state = s;
    el.dataset.state = s;
    msgText.textContent = text; msgSub.textContent = sub;
    msgBtns.replaceChildren(...buttons);
    paintPill(); showUi();
  };
  const msgButton = (label: string, onClick: () => void, primary = false) =>
    h("button", { class: `cam-msg-btn${primary ? " primary" : ""}`, type: "button", text: label,
      onclick: (e: Event) => { e.stopPropagation(); onClick(); } }) as HTMLButtonElement;

  const paintPill = () => {
    const rec = recording();
    el.classList.toggle("recording", rec);
    pill.dataset.mode = rec ? "rec" : state === "live" ? "live" : "off";
    if (rec) pillText.textContent = `REC ${clock(Math.floor((performance.now() - recStart) / 1000))}${micStream ? " \u00B7 sound" : ""}`;
    else pillText.textContent = state === "live" ? "Camera on" : "";
    shutter.disabled = state !== "live";
    recBtn.disabled = state !== "live" && !rec;
    fxBtn.disabled = timerBtn.disabled = state !== "live";
    recBtn.setAttribute("aria-label", rec ? "Stop recording" : "Start recording");
    recBtn.title = rec ? "Stop recording (R)" : "Start recording (R)";
    recBtn.setAttribute("aria-pressed", String(rec));
    flipBtn.style.display = usable().length > 1 ? "" : "none";
    folderBtn.style.display = lastSaved ? "" : "none";
    micBtn.disabled = rec;                                    // the sound switch can't change mid-recording
  };
  const paintToggles = () => {
    const t = TIMERS[timerIdx];
    timerLabel.textContent = t ? `${t}s` : "";
    timerBtn.classList.toggle("on", t > 0);
    timerBtn.setAttribute("aria-label", t ? `Self-timer: ${t} seconds` : "Self-timer: off");
    micBtn.classList.toggle("on", micOn);
    micBtn.classList.toggle("off", !micOn);
    micBtn.title = `Record with sound: ${micOn ? "on" : "off"}`;
    micBtn.setAttribute("aria-label", micBtn.title);
    mirrorBtn.classList.toggle("on", mirror);
    mirrorBtn.setAttribute("aria-pressed", String(mirror));
    fxLabel.textContent = fx().label;
    chips.forEach((c) => { const on = c.dataset.fx === fxId; c.classList.toggle("on", on); c.setAttribute("aria-selected", String(on)); });
  };

  const showToast = (text: string, kind: "ok" | "err" = "ok", action?: () => void, ms = 3200) => {
    toast.textContent = text;
    toast.dataset.kind = kind;
    toast.classList.add("show");
    toast.tabIndex = action ? 0 : -1;
    toast.style.pointerEvents = action ? "auto" : "none";
    retry = action ?? null;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => { toast.classList.remove("show"); retry = null; }, kind === "err" ? Math.max(ms, 6000) : ms);
  };
  const flashHint = (text: string) => {
    hint.textContent = text; hint.classList.remove("show"); void hint.offsetWidth; hint.classList.add("show");
    window.clearTimeout(hintTimer); hintTimer = window.setTimeout(() => hint.classList.remove("show"), 900);
  };

  // controls fade out when you are not using them, leaving only the picture (and the REC pill)
  const showUi = () => {
    ui.classList.remove("idle");
    window.clearTimeout(uiTimer);
    if (state !== "live" || trayOpen || countdownLeft > 0) return;
    uiTimer = window.setTimeout(() => ui.classList.add("idle"), UI_IDLE_MS);
  };

  // ── effects ────────────────────────────────────────────────────────────────
  function setEffect(id: string, announce = false) {
    if (!EFFECTS.some((e) => e.id === id)) return;
    fxId = id; save("fx", id); paintToggles();
    if (announce) flashHint(fx().label);
  }
  const stepEffect = (d: number) => {
    const i = EFFECTS.findIndex((e) => e.id === fxId);
    setEffect(EFFECTS[(i + d + EFFECTS.length) % EFFECTS.length].id, true);
  };
  function openTray(on: boolean) {
    if (on && state !== "live") return;
    trayOpen = on;
    tray.classList.toggle("open", on);
    tray.setAttribute("aria-hidden", String(!on));
    fxBtn.setAttribute("aria-expanded", String(on));
    chips.forEach((c) => { c.tabIndex = on ? 0 : -1; });
    if (on) {
      const sel = chips.find((c) => c.classList.contains("on"));
      if (sel) { tray.scrollLeft = sel.offsetLeft - (tray.clientWidth - sel.offsetWidth) / 2; sel.focus({ preventScroll: true }); }
    }
    showUi();
  }

  // ── drawing: camera -> canvas (with the effect) ────────────────────────────
  const drawFrame = () => {
    const vw = video.videoWidth, vh = video.videoHeight;
    if (!vw || !vh || video.readyState < 2) return;
    if (canvas.width !== vw || canvas.height !== vh) { canvas.width = vw; canvas.height = vh; vignetteCache = null; }
    const f = fx();
    ctx.save();
    if (mirror) { ctx.translate(vw, 0); ctx.scale(-1, 1); }
    if (filtersOk) ctx.filter = f.filter;
    ctx.drawImage(video, 0, 0, vw, vh);
    ctx.restore();
    if (filtersOk) ctx.filter = "none";
    if (f.vignette) {
      if (!vignetteCache || vignetteCache.k !== f.vignette) {
        const g = ctx.createRadialGradient(vw / 2, vh / 2, Math.min(vw, vh) * 0.35, vw / 2, vh / 2, Math.hypot(vw, vh) / 2);
        g.addColorStop(0, "rgba(0,0,0,0)"); g.addColorStop(1, `rgba(0,0,0,${f.vignette})`);
        vignetteCache = { g, k: f.vignette };
      }
      ctx.fillStyle = vignetteCache.g; ctx.fillRect(0, 0, vw, vh);
    }
  };
  const startLoop = () => {
    const mine = ++loopSeq;
    const v = video as HTMLVideoElement & { requestVideoFrameCallback?: (cb: () => void) => number };
    const tick = () => {
      if (mine !== loopSeq) return;
      if (!document.hidden) drawFrame();
      if (v.requestVideoFrameCallback) v.requestVideoFrameCallback(tick); else requestAnimationFrame(tick);
    };
    tick();
  };

  // ── camera on / off ────────────────────────────────────────────────────────
  const releaseCamera = () => {
    seq++; loopSeq++;
    if (stream) { for (const t of stream.getTracks()) t.stop(); stream = null; }
    video.pause(); video.srcObject = null;
  };

  const errorFor = (name: string): { text: string; sub: string; blocked: boolean; state: CamState } => {
    if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError")
      return { text: "Camera access is off", sub: "Allow it in Windows Settings \u203A Privacy \u203A Camera, then try again.", blocked: true, state: "denied" };
    if (name === "NotFoundError" || name === "OverconstrainedError" || name === "DevicesNotFoundError")
      return { text: "No camera found", sub: "Plug one in, or check it isn\u2019t switched off.", blocked: false, state: "nodevice" };
    if (name === "NotReadableError" || name === "TrackStartError" || name === "AbortError")
      return { text: "Camera is in use", sub: "Another app is using it. Close that app, then try again.", blocked: false, state: "busy" };
    if (name === "NotSupportedError")
      return { text: "Camera isn\u2019t available here", sub: "This window can\u2019t use a camera.", blocked: false, state: "unsupported" };
    return { text: "Couldn\u2019t start the camera", sub: "Try again in a moment.", blocked: false, state: "error" };
  };

  const refreshDevices = async () => {
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      devices = all.filter((d) => d.kind === "videoinput");
    } catch { devices = []; }
    paintPill();
  };

  const start = async () => {
    if (stream || state === "requesting") return;
    const mine = ++seq;
    setState("requesting", "Starting camera\u2026", "If Windows asks, choose Allow.");
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new DOMException("unsupported", "NotSupportedError");
      const base: MediaTrackConstraints = { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } };
      const open = (id: string) => navigator.mediaDevices.getUserMedia({ video: id ? { ...base, deviceId: { exact: id } } : { ...base, facingMode: "user" }, audio: false });
      const pause = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms));
      let s: MediaStream;
      try {
        s = await open(deviceId);
      } catch (e) {
        const n = (e as DOMException)?.name;
        // A camera we picked earlier that can't be opened now (unplugged, an IR sensor, held by another app): forget it
        // and use the default camera, so "Try again" can never get stuck on the same broken choice.
        if (deviceId && n !== "NotAllowedError" && n !== "SecurityError") {
          deviceId = ""; save("device", ""); fellBack = true;
          try { s = await open(""); }
          catch (e2) { if ((e2 as DOMException)?.name !== "NotReadableError") throw e2; await pause(500); s = await open(""); }
        } else if (n === "NotReadableError") {
          await pause(500);                      // the camera was only just released (e.g. after switching): give Windows a moment
          s = await open(deviceId);
        } else throw e;
      }
      if (mine !== seq) { for (const t of s.getTracks()) t.stop(); return; }   // the tab was left while the camera was starting
      stream = s;
      s.getVideoTracks()[0]?.addEventListener("ended", () => {
        if (stream !== s) return;
        if (recording()) void endRecording();
        releaseCamera();
        setState("busy", "Camera disconnected", "It was unplugged or taken over by another app.", [msgButton("Try again", () => void start(), true)]);
      });
      video.srcObject = s;
      await video.play().catch(() => { /* muted video may autoplay */ });
      await refreshDevices();
      const cur = s.getVideoTracks()[0]?.getSettings().deviceId;
      if (cur) { deviceId = cur; save("device", cur); }
      setState("live");
      startLoop();
      paintToggles();
      if (fellBack) { fellBack = false; showToast("That camera couldn\u2019t be opened. Using the default one.", "err"); }
      else if (load("tips", "0") !== "1") { save("tips", "1"); showToast("Tap for a photo \u00B7 hold for video \u00B7 swipe for effects", "ok", undefined, 4500); }
    } catch (e) {
      if (mine !== seq) return;
      fellBack = false;
      if (stream) { for (const t of stream.getTracks()) t.stop(); stream = null; }
      const r = errorFor((e as DOMException)?.name ?? "");
      const btns = [msgButton("Try again", () => void start(), true)];
      if (r.blocked) btns.push(msgButton("Open Settings", () => void post("/camera/privacy-settings")));
      setState(r.state, r.text, r.sub, btns);
    }
  };

  // If Windows' camera privacy switch changes while we are showing "off", try again on our own.
  void (async () => {
    try {
      const p = await navigator.permissions?.query({ name: "camera" as PermissionName });
      p?.addEventListener("change", () => { if (active && p.state === "granted" && state !== "live" && state !== "requesting") void start(); });
    } catch { /* the Permissions API doesn't know "camera" here */ }
  })();

  const switchCamera = async () => {
    const list = usable();
    if (list.length < 2 || recording() || state !== "live") return;
    const i = list.findIndex((d) => d.deviceId === deviceId);
    const next = list[(i + 1) % list.length];
    deviceId = next.deviceId; save("device", deviceId);
    releaseCamera(); setState("idle");
    await new Promise<void>((r) => window.setTimeout(r, 250));   // let Windows release the first camera before opening the next
    await start();
    if (state === "live" && !toast.classList.contains("show")) flashHint((next.label || "Camera switched").slice(0, 28));
  };

  // ── talking to the backend ─────────────────────────────────────────────────
  async function post(path: string): Promise<Saved | null> {
    try {
      const r = await fetch(`${BASE}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      return r.ok ? ((await r.json()) as Saved) : null;
    } catch { return null; }
  }
  const upload = async (blob: Blob, kind: "photo" | "video", ext: string): Promise<Saved> => {
    try {
      const r = await fetch(`${BASE}/camera/upload?kind=${kind}&ext=${ext}`, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: blob });
      const j = (await r.json().catch(() => ({}))) as Saved;
      return r.ok && j.ok ? j : { ok: false, error: j.error || "Lyra\u2019s backend refused the file." };
    } catch { return { ok: false, error: "Lyra\u2019s backend isn\u2019t reachable." }; }
  };
  const saveBlob = async (blob: Blob, kind: "photo" | "video", ext: string): Promise<void> => {
    const label = kind === "photo" ? "Photo" : "Video";
    const res = await upload(blob, kind, ext);
    if (res.ok) {
      lastSaved = true; paintPill();
      showToast(`${label} saved \u00B7 ${res.folder ? shortFolder(res.folder) : "Lyra Camera"}`, "ok", () => void post("/camera/reveal"));
    } else {
      // The capture stays in memory so a failed save never loses it: tap the message to try again.
      showToast(`Couldn\u2019t save ${label.toLowerCase()}: ${res.error ?? "unknown error"} Tap to retry.`, "err", () => void saveBlob(blob, kind, ext));
    }
  };

  // ── photo ──────────────────────────────────────────────────────────────────
  const takePhoto = () => {
    if (state !== "live" || !canvas.width) return;
    drawFrame();
    flash.classList.remove("go"); void flash.offsetWidth; flash.classList.add("go");
    shutter.classList.remove("pop"); void shutter.offsetWidth; shutter.classList.add("pop");
    canvas.toBlob((b) => {
      if (!b) { showToast("Couldn\u2019t capture the photo.", "err"); return; }
      void saveBlob(b, "photo", "jpg");
    }, "image/jpeg", 0.93);
  };

  const cancelCountdown = () => {
    window.clearInterval(countdownTimer); countdownTimer = 0; countdownLeft = 0;
    count.classList.remove("show"); el.classList.remove("counting"); showUi();
  };
  const photoWithTimer = () => {
    if (countdownLeft > 0) { cancelCountdown(); flashHint("Cancelled"); return; }
    const t = TIMERS[timerIdx];
    if (!t) { takePhoto(); return; }
    countdownLeft = t; el.classList.add("counting"); showUi();
    const paint = () => { count.textContent = String(countdownLeft); count.classList.remove("show"); void count.offsetWidth; count.classList.add("show"); };
    paint();
    countdownTimer = window.setInterval(() => {
      countdownLeft--;
      if (countdownLeft <= 0) { cancelCountdown(); takePhoto(); } else paint();
    }, 1000);
  };

  // ── video ──────────────────────────────────────────────────────────────────
  const cleanupRecStreams = () => {
    micStream?.getTracks().forEach((t) => t.stop()); micStream = null;
    recStream?.getTracks().forEach((t) => t.stop()); recStream = null;
  };

  const startRecording = async (held = false) => {
    if (state !== "live" || recording() || recStarting || recFinish || !canvas.width) return;
    const MR = (window as unknown as { MediaRecorder?: typeof MediaRecorder }).MediaRecorder;
    if (!MR || !canvas.captureStream) { showToast("This window can\u2019t record video.", "err"); return; }
    recStarting = true; stopWhenStarted = false;
    try {
      cancelCountdown();
      recHeld = held; recChunks = [];
      recStream = canvas.captureStream(30);
      if (micOn) {
        try {
          micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false });
          for (const t of micStream.getAudioTracks()) recStream.addTrack(t);
        } catch {
          micStream = null;
          showToast("Microphone unavailable. Recording without sound.", "err");
        }
      }
      if (state !== "live") { cleanupRecStreams(); return; }   // the tab was left while the microphone prompt was open
      const { mime, ext } = pickMime();
      recExt = ext;
      try {
        recorder = new MR(recStream, { ...(mime ? { mimeType: mime } : {}), videoBitsPerSecond: 4_000_000 });
      } catch {
        cleanupRecStreams(); recorder = null;
        showToast("Couldn\u2019t start recording.", "err"); return;
      }
      recorder.ondataavailable = (e) => { if (e.data && e.data.size) recChunks.push(e.data); };
      recStart = performance.now();
      recorder.start(1000);
      recTimer = window.setInterval(() => {
        paintPill();
        if ((performance.now() - recStart) / 1000 >= MAX_REC_S) { showToast("Reached the 15 minute limit. Saving\u2026"); void endRecording(); }
      }, 500);
      openTray(false); paintPill(); showUi();
    } finally {
      recStarting = false;
    }
    if (stopWhenStarted) { stopWhenStarted = false; void endRecording(); }   // you let go of the shutter while it was still starting
  };

  /** Stop the recorder, wait for the last chunk and save the file. Safe to call twice. */
  const endRecording = (): Promise<void> => {
    if (recFinish) return recFinish;
    const r = recorder;
    if (!r || r.state === "inactive") return Promise.resolve();
    window.clearInterval(recTimer); recTimer = 0;
    recFinish = new Promise<void>((resolve) => {
      r.onstop = async () => {
        const type = r.mimeType || (recExt === "mp4" ? "video/mp4" : "video/webm");
        const blob = new Blob(recChunks, { type });
        recChunks = []; recorder = null; cleanupRecStreams(); recFinish = null;
        paintPill();
        if (blob.size > 0) await saveBlob(blob, "video", recExt); else showToast("Nothing was recorded.", "err");
        resolve();
      };
      try { r.stop(); } catch { void r.onstop?.(new Event("stop")); }
    });
    paintPill();
    return recFinish;
  };

  const toggleRecording = () => { if (recording()) void endRecording(); else void startRecording(false); };

  // ── gestures ───────────────────────────────────────────────────────────────
  // Shutter: tap = photo, hold = record until you let go.
  const releaseHold = () => {
    window.clearTimeout(holdTimer);
    shutter.classList.remove("down", "holding");
    const wasHold = holdFired; holdFired = false;
    if (wasHold && recHeld) { if (recording()) void endRecording(); else stopWhenStarted = true; }
    return wasHold;
  };
  shutter.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || shutter.disabled) return;
    e.stopPropagation();
    holdFired = false;
    shutter.setPointerCapture?.(e.pointerId);
    shutter.classList.add("down");
    if (recording()) return;                                  // while recording a tap takes a still
    holdTimer = window.setTimeout(() => { holdFired = true; shutter.classList.add("holding"); void startRecording(true); }, HOLD_MS);
  });
  shutter.addEventListener("pointerup", (e) => {
    e.stopPropagation();
    if (!shutter.classList.contains("down")) return;
    if (releaseHold() || shutter.disabled) return;
    if (recording()) takePhoto(); else photoWithTimer();
  });
  shutter.addEventListener("pointercancel", () => { releaseHold(); });
  shutter.addEventListener("click", (e) => {                  // keyboard / assistive tech only (a real click has detail >= 1)
    e.stopPropagation();
    if (e.detail === 0 && !shutter.disabled) { if (recording()) takePhoto(); else photoWithTimer(); }
  });
  shutter.addEventListener("contextmenu", (e) => e.preventDefault());

  recBtn.addEventListener("click", (e) => { e.stopPropagation(); toggleRecording(); });
  fxBtn.addEventListener("click", (e) => { e.stopPropagation(); openTray(!trayOpen); });
  timerBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    timerIdx = (timerIdx + 1) % TIMERS.length; save("timer", String(TIMERS[timerIdx])); paintToggles();
    flashHint(TIMERS[timerIdx] ? `Timer ${TIMERS[timerIdx]}s` : "Timer off");
  });
  micBtn.addEventListener("click", (e) => {
    e.stopPropagation(); micOn = !micOn; save("mic", micOn ? "1" : "0"); paintToggles();
    flashHint(micOn ? "Sound on" : "Sound off");
  });
  mirrorBtn.addEventListener("click", (e) => { e.stopPropagation(); toggleMirror(); });
  flipBtn.addEventListener("click", (e) => { e.stopPropagation(); void switchCamera(); });
  folderBtn.addEventListener("click", (e) => { e.stopPropagation(); void post("/camera/reveal"); });
  toast.addEventListener("click", (e) => { e.stopPropagation(); const r = retry; if (r) { toast.classList.remove("show"); retry = null; r(); } });

  function toggleMirror() { mirror = !mirror; save("mirror", mirror ? "1" : "0"); paintToggles(); flashHint(mirror ? "Mirror on" : "Mirror off"); }

  // Preview: swipe sideways = change effect, double-tap = mirror, any touch brings the controls back.
  let sx = 0, sy = 0, sid = -1, st = 0;
  el.addEventListener("pointerdown", (e) => {
    showUi();
    if ((e.target as Element).closest("button, .cam-tray")) { sid = -1; return; }
    sx = e.clientX; sy = e.clientY; sid = e.pointerId; st = performance.now();
  });
  el.addEventListener("pointermove", () => showUi());
  el.addEventListener("pointerup", (e) => {
    if (e.pointerId !== sid) return;
    sid = -1;
    if (state !== "live") return;
    const dx = e.clientX - sx, dy = e.clientY - sy, dt = performance.now() - st;
    if (Math.abs(dx) >= SWIPE_PX && Math.abs(dx) > Math.abs(dy) * 1.6) { stepEffect(dx < 0 ? 1 : -1); lastTap = 0; return; }
    if (Math.abs(dx) < 8 && Math.abs(dy) < 8 && dt < 350) {
      const now = performance.now();
      if (trayOpen) { openTray(false); lastTap = 0; return; }
      if (now - lastTap < DOUBLE_TAP_MS) { toggleMirror(); lastTap = 0; } else lastTap = now;
    }
  });
  el.addEventListener("pointercancel", () => { sid = -1; });

  // Keys, only while the tab is open and nothing is being typed.
  document.addEventListener("keydown", (e: KeyboardEvent) => {
    if (!active || e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target as HTMLElement | null;
    if (t && (t.isContentEditable || /^(input|textarea|select)$/i.test(t.tagName))) return;
    const k = e.key.toLowerCase();
    if (k === "escape" && trayOpen) { e.stopPropagation(); openTray(false); fxBtn.focus({ preventScroll: true }); return; }
    if (state !== "live") return;
    if (k === "p") { e.preventDefault(); if (recording()) takePhoto(); else photoWithTimer(); }
    else if (k === "r") { e.preventDefault(); toggleRecording(); }
    else if (k === "m") { e.preventDefault(); toggleMirror(); }
    else if (k === "f") { e.preventDefault(); openTray(!trayOpen); }
    else if (k === "arrowright" && !trayOpen) { e.preventDefault(); stepEffect(1); }
    else if (k === "arrowleft" && !trayOpen) { e.preventDefault(); stepEffect(-1); }
  });

  // Let go of the camera if the whole window goes to the background (a recording is saved first),
  // and take it back when the window returns while the tab is still the one on screen.
  document.addEventListener("visibilitychange", () => {
    if (!active) return;
    if (document.hidden) void stop(); else void start();
  });
  navigator.mediaDevices?.addEventListener?.("devicechange", () => { if (stream) void refreshDevices(); });

  const stop = async () => {
    const pending = recording() ? endRecording() : null;     // finish and save the video first
    cancelCountdown(); openTray(false);
    window.clearTimeout(holdTimer); shutter.classList.remove("down", "holding"); holdFired = false;
    releaseCamera();
    setState("idle");
    if (pending) await pending;
  };

  paintToggles();
  paintPill();

  return {
    el,
    sync() { /* nothing to refresh */ },
    setActive(on: boolean) {
      active = on;
      if (on) { void start(); showUi(); } else void stop();
    },
  };
}
