// Caption window: what Lyra heard (voice or typed) and her answer, as a pill near the
// bottom of the screen. It reads the same backend event feed as the island: while Lyra listens it
// shows "Listening", then what she heard, "Thinking" and finally the answer. Each line fades out
// by itself. Nothing is stored.
//
// The pill always carries its own small Mochi (the island keeps its own too); the floating Mochi no longer
// walks to the pill.

import "./caption.css";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { barHeights, envelopeLevel, simulatedLevel, WAVE_BARS } from "../island/wave";
import { createCharacter, gazeToward, liveCharacterCount, type Character, type EmoteName } from "../mascot/character";
import { onMusic } from "../mascot/dock";
import { isMochiKind, MOCHI_KINDS, type MochiKind } from "../core/mochiSound";
import { adaptivePoll, noteEvents } from "../core/pollLoop";
import { getFeatures, onFeatures } from "../core/featuresCache";
import { installTracer, trace } from "../core/trace";
import { FRAME_MS } from "../core/fps";
import { isReallyVisible, onVisibilityChange } from "../core/visibility";
installTracer();

const BASE = "http://127.0.0.1:8765";
const subs = document.getElementById("subs") as HTMLElement; // subtitles
const IS_TAURI = "__TAURI_INTERNALS__" in window;

let since = 0;
let backlog = true; // the first batch is history: never caption it
const startedAt = Date.now();
let hideTimer = 0;
let removeTimer = 0;
let currentPill: HTMLElement | null = null;
let lyraSpeaking = false;
let lyraThinking = false;
// A reply can arrive while an older/repeated thinking state is still in the
// event queue. Once the reply is shown, don't let that stale state replace it.
let replyReceived = false;
let speechEnvelope: { levels: number[]; hz: number; startMs: number } | null = null;
let waveFrame = 0;
let speakingSince = 0;
let cursorPosition: { x: number; y: number } | null = null;
let answerUntil = 0; // an answer stays at least until here, whatever the state events say

// ── Diagnostics: a few lines in Lyra's log (%LOCALAPPDATA%\Coucou\coucou.log) so a silent caption can be traced ──
let dbgLeft = 80;
function dbg(msg: string): void {
  if (!IS_TAURI || dbgLeft <= 0) return;
  dbgLeft--;
  void invoke("log_line", { message: `caption: ${msg}` }).catch(() => undefined);
}
dbg(`window started, backend ${BASE}`);

// ── Mochi settings ──
// The subtitle pill ALWAYS carries its own Mochi (exactly the one you see with the mascot feature off), and
// the Mochi on the island stays where it is: it appears in both places and never walks out to the pill.
async function readFeature(): Promise<void> {
  const p = (await getFeatures()) as { mascot_shape?: string } | null;
  if (!p) return;   // backend not up yet: keep the last value
  shapeName = p.mascot_shape ?? "cloud";
}
void readFeature();
// No timer: the shared cache refreshes on focus / visible / after a save and tells us (core/featuresCache.ts).
onFeatures(() => void readFeature());

/** Tell the overlay where the pill is (or that it is gone) so the Mochi can walk to it / leave. */
function announce(_pill: HTMLElement | null): void {
  // Nothing to tell the floating Mochi any more: the pill has its own.
}

// Each subtitle avatar is a small Lyra Mochi; keep the handle so the eyes / emote / mouth can be driven.
const avatars = new WeakMap<HTMLElement, Character>();
function currentAvatar(): Character | undefined {
  const el = currentPill?.querySelector<HTMLElement>(".lyra-avatar");
  return el ? avatars.get(el) : undefined;
}

/** Destroy the Mochi(s) inside a subtitle pill before it leaves the DOM. Removing the element alone left each
 *  Character registered in the shared animation loop (and its ResizeObserver / resize listener) forever. */
function dropPill(pill: HTMLElement | null | undefined): void {
  if (!pill) return;
  for (const el of Array.from(pill.querySelectorAll<HTMLElement>(".lyra-avatar"))) {
    const ch = avatars.get(el);
    if (ch) { try { ch.destroy(); } catch { /* already gone */ } avatars.delete(el); }
  }
}

/** Leak probe: node count, live Mochis, wave loop, before/after each pill. Read it in ui.log. */
let countLeft = 400;
function counters(tag: string): void {
  if (countLeft <= 0) return;
  countLeft--;
  // Via the page tracer: lands in ui.log as `ui:caption:leak-probe`.
  trace("leak-probe", `${tag}: nodes=${document.body.getElementsByTagName("*").length} mochis=${liveCharacterCount()} waveLoop=${waveFrame ? 1 : 0} pills=${subs.children.length}`);
}

function lookAt(clientX: number, clientY: number): void {
  cursorPosition = { x: clientX, y: clientY };
  const avatar = currentPill?.querySelector<HTMLElement>(".lyra-avatar");
  const ch = avatar ? avatars.get(avatar) : undefined;
  if (!avatar || !ch) return;
  const [gx, gy] = gazeToward(avatar, clientX, clientY);
  ch.look(gx, gy);
}

// The caption window is click-through, so native cursor coordinates are needed
// on Windows; DOM pointer events are not delivered to it.
void listen<{ x: number; y: number }>("caption-cursor", ({ payload }) => lookAt(payload.x, payload.y))
  .catch((err) => dbg(`cursor feed unavailable: ${String(err).slice(0, 120)}`));

// Music (relayed by the island): new subtitle avatars groove too.
let musicOn = false;
let shapeName = "cloud";
void onMusic((on) => { musicOn = on; }).catch(() => undefined);

function makeAvatar(emote: EmoteName = "idle", sharp = 0): HTMLSpanElement | null {
  const avatar = document.createElement("span");
  avatar.className = "lyra-avatar";
  avatar.setAttribute("aria-hidden", "true");
  const ch = createCharacter(avatar, sharp ? { tight: true, sharp } : { tight: true });
  ch.setEmote(emote);
  ch.setVibing?.(musicOn);
  ch.setShape?.(shapeName);
  avatars.set(avatar, ch);
  return avatar;
}

function scheduleHide(pill: HTMLElement, ms: number): void {
  window.clearTimeout(hideTimer);
  if (pill.classList.contains("lyra") && !pill.classList.contains("thinking")) {
    ms = Math.max(ms, answerUntil - Date.now());
  }
  hideTimer = window.setTimeout(() => {
    if (currentPill !== pill) return;
    pill.classList.remove("in");
    pill.classList.add("out");
    announce(null);
    removeTimer = window.setTimeout(() => {
      if (currentPill === pill) currentPill = null;
      dropPill(pill);
      pill.remove();
      counters("pill removed");
    }, 400);
  }, ms);
}

function clip(text: string): string {
  const t = text.replace(/\s+/g, " ").trim();
  return t.length > 260 ? `${t.slice(0, 257)}…` : t;
}

/** Fade the pill in. No requestAnimationFrame: WebView2 pauses it for a window it thinks is hidden,
 *  which would leave the pill invisible. A forced reflow lets the CSS transition run instead. */
function reveal(pill: HTMLElement): void {
  void pill.offsetWidth;
  if (pill.offsetHeight > 48) pill.classList.add("multi");
  pill.classList.add("in");
  announce(pill);
}

/** `mochi`: this line is a Mochi sound's subtitle (the Mochi plays that kind's emote). `mochiMs` > 0: no speech
 *  follows, so the pill leaves by itself after that long. */
function show(who: "you" | "lyra", raw: string, mochi: MochiKind | null = null, mochiMs = 0): void {
  const text = clip(raw);
  if (!text) return;
  window.clearTimeout(hideTimer);
  window.clearTimeout(removeTimer);

  const label = document.createElement("span");
  label.className = "who";
  label.textContent = who === "you" ? "You" : "Lyra";
  const body = document.createElement("span");
  body.className = "txt";
  body.textContent = text;
  const copy = document.createElement("span");
  copy.className = "lyra-copy";
  copy.append(label, body);
  const avatar = makeAvatar(who === "lyra" && lyraSpeaking ? "talk" : who === "lyra" && mochi ? MOCHI_KINDS[mochi].emote : "idle");
  const wave = document.createElement("span");
  wave.className = "voice-wave";
  wave.setAttribute("aria-hidden", "true");
  for (let i = 0; i < WAVE_BARS; i++) wave.append(document.createElement("i"));
  const pill = document.createElement("div");
  pill.className = `pill ${who}${who === "lyra" && lyraSpeaking ? " speaking" : ""}${who === "lyra" && mochi ? " mochi" : ""}`;
  if (who === "lyra") pill.append(...(avatar ? [avatar] : []), copy, wave);
  else pill.append(label, body);

  if (currentPill && currentPill !== pill) dropPill(currentPill);
  subs.replaceChildren(pill);
  currentPill = pill;
  if (cursorPosition) lookAt(cursorPosition.x, cursorPosition.y);
  if (who === "lyra") drawWave();
  reveal(pill);
  counters(`show ${who}`);

  // What was heard waits for the answer; an answer stays long enough to read.
  if (who === "lyra" && mochi && mochiMs > 0) {
    // Nobody speaks over it: show the subtitle for the length of the animation, then let the Mochi rest.
    const stay = Math.max(2200, mochiMs + 800);
    answerUntil = Date.now() + stay;
    scheduleHide(pill, stay);
    window.setTimeout(() => { if (currentPill === pill && !lyraSpeaking) currentAvatar()?.setEmote("idle"); }, mochiMs);
    return;
  }
  if (who === "lyra") answerUntil = Date.now() + Math.min(20000, Math.max(6000, text.length * 55));
  const ms = who === "you" ? 9000 : 60000;
  scheduleHide(pill, ms);
}

/** "Listening" while Lyra hears you, "Thinking" while she works on it. */
function showStatus(kind: "listening" | "thinking"): void {
  window.clearTimeout(hideTimer);
  window.clearTimeout(removeTimer);
  const label = document.createElement("span");
  label.className = "who";
  label.textContent = "Lyra";
  const body = document.createElement("span");
  body.className = "txt thinking-text";
  body.textContent = kind === "listening" ? "Listening" : "Thinking";
  const copy = document.createElement("span");
  copy.className = "lyra-copy";
  copy.append(label, body);
  const avatar = makeAvatar(kind === "listening" ? "listen" : "think");
  const dots = document.createElement("span");
  dots.className = "thinking-dots";
  dots.setAttribute("aria-label", kind === "listening" ? "Lyra is listening" : "Lyra is thinking");
  for (let i = 0; i < 3; i++) dots.append(document.createElement("i"));
  const pill = document.createElement("div");
  pill.className = `pill lyra thinking ${kind}`;
  pill.append(...(avatar ? [avatar] : []), copy, dots);
  if (currentPill && currentPill !== pill) dropPill(currentPill);
  subs.replaceChildren(pill);
  currentPill = pill;
  if (cursorPosition) lookAt(cursorPosition.x, cursorPosition.y);
  reveal(pill);
  // Never leave a status stuck on screen if the turn ends without an answer.
  scheduleHide(pill, kind === "listening" ? 30000 : 60000);
}

onVisibilityChange((v) => { if (v && lyraSpeaking && !waveFrame) drawWave(); });

let waveLast = 0;
function drawWave(ts?: number): void {
  if (waveFrame) cancelAnimationFrame(waveFrame);
  waveFrame = 0;
  const wave = currentPill?.querySelector<HTMLElement>(".voice-wave");
  if (!wave || !lyraSpeaking) return;
  if (!isReallyVisible()) return;                      // resumed by onVisibilityChange below
  // 60 fps (a faster screen would otherwise redraw 120+ times a second)
  if (ts !== undefined && ts - waveLast < FRAME_MS) { waveFrame = requestAnimationFrame(drawWave); return; }
  waveLast = ts ?? performance.now();
  const bars = Array.from(wave.children) as HTMLElement[];
  const now = Date.now();
  const env = speechEnvelope;
  const level = env
    ? envelopeLevel(env, now)
    : now - speakingSince > 900 ? simulatedLevel(now / 1000) : 0.52;
  const heights = barHeights(level, now / 1000, bars.length);
  currentAvatar()?.setMouth(level);
  bars.forEach((bar, i) => {
    bar.style.height = `${heights[i].toFixed(1)}px`;
  });
  waveFrame = requestAnimationFrame(drawWave);
}

type Ev = {
  type?: string; role?: string; text?: string; state?: string; levels?: number[]; hz?: number; start_ms?: number; ts?: number;
  mochi?: string; mochi_subtitle?: string; mochi_silent?: boolean; mochi_ms?: number; kind?: string; subtitle?: string; ms?: number;
};

let failures = 0;
let connected = false;

async function poll(): Promise<void> {
  try {
    const res = await fetch(`${BASE}/events?since=${since}`);
    if (!res.ok) {
      if (++failures === 1) dbg(`events request answered HTTP ${res.status}`);
      return;
    }
    if (!connected) { connected = true; dbg("connected to the event feed"); }
    failures = 0;
    const data = (await res.json()) as { events?: Ev[]; next?: number };
    if (typeof data.next === "number") since = data.next; // also follows a restarted backend
    noteEvents(data.events?.length ?? 0);
    if (backlog) {
      backlog = false;
      // The window can start just before a voice turn. Keep events published
      // after this window opened while ignoring the bridge's older history.
      for (const ev of data.events || []) {
        if (Number(ev.ts || 0) >= startedAt) safeHandle(ev);
      }
      return;
    }
    for (const ev of data.events || []) safeHandle(ev);
  } catch (err) {
    connected = false;
    if (++failures === 1 || failures === 50) dbg(`events request failed: ${String(err).slice(0, 120)}`);
  }
}

function safeHandle(ev: Ev): void {
  try { handleEvent(ev); } catch (err) { dbg(`event ${ev.type} failed: ${String(err).slice(0, 160)}`); }
}

// While the Mochi explains something on screen (it draws and talks in its own bubble) the subtitles
// would only cover what it points at, so the whole caption window goes quiet until the tour ends.
let quietUntil = 0;
function setQuiet(on: boolean): void { document.body.classList.toggle("quiet", on); }
window.setInterval(() => { if (quietUntil && Date.now() > quietUntil) { quietUntil = 0; setQuiet(false); } }, 400);

function handleMascotEvent(ev: Ev): boolean {
  const cmd = String((ev as any).cmd || "");
  if (cmd === "tour") {
    const steps = Array.isArray((ev as any).steps) ? (ev as any).steps : [];
    if (steps.length) {                                     // any tour (pointing or drawing): the Mochi has its own bubble
      const total = steps.reduce((n: number, s: any) => n + (Number(s?.ms) || 3500), 0);
      quietUntil = Date.now() + total + 12000;
      setQuiet(true);
      if (currentPill) { dropPill(currentPill); currentPill.remove(); currentPill = null; announce(null); }
    }
  } else if (cmd === "stop" || cmd === "home" || (cmd === "emote" && (ev as any).name === "happy")) {
    quietUntil = Date.now() + 400;
  }
  return true;
}

function handleEvent(ev: Ev): void {
  if (ev.type === "mascot") { handleMascotEvent(ev); return; }
  if (ev.type === "state") {
    const state = String(ev.state || "").toLowerCase();
    dbg(`state ${state}`);
    if (state !== "thinking") replyReceived = false;
    lyraThinking = state === "thinking" && !replyReceived;
    const wasSpeaking = lyraSpeaking;
    lyraSpeaking = state === "speaking";
    if (lyraSpeaking && !wasSpeaking) speakingSince = Date.now();
    if (!lyraSpeaking) {
      speechEnvelope = null;
      if (waveFrame) cancelAnimationFrame(waveFrame);
      waveFrame = 0;
    }
    if (currentPill?.classList.contains("lyra") && !currentPill.classList.contains("thinking")) {
      if (lyraSpeaking) {
        window.clearTimeout(hideTimer);
        window.clearTimeout(removeTimer);
        currentPill.classList.remove("out");
        currentPill.classList.add("in");
        announce(currentPill);
      } else scheduleHide(currentPill, 3200);
      currentPill.classList.toggle("speaking", lyraSpeaking);
      const av = currentAvatar();
      // A Mochi sound's emote plays out by itself (see show()); the "idle" state right after it must not cut it short.
      if (lyraSpeaking || !currentPill.classList.contains("mochi")) av?.setEmote(lyraSpeaking ? "talk" : "idle");
      if (!lyraSpeaking) av?.setMouth(0);
      if (lyraSpeaking) drawWave();
    }
    if (state === "listening") { showStatus("listening"); return; }
    if (lyraThinking) { showStatus("thinking"); return; }
    // Her voice has started but no answer text arrived: never keep "Thinking" / "Listening"
    // on screen over the sound of the answer.
    if (lyraSpeaking && currentPill?.classList.contains("thinking")) {
      scheduleHide(currentPill, 0);
      return;
    }
    // Back to rest with only a status on screen (e.g. a cancelled push-to-talk): let it go.
    if ((state === "idle" || state === "standby") && currentPill?.classList.contains("thinking")) {
      scheduleHide(currentPill, currentPill.classList.contains("listening") ? 500 : 1500);
    }
    return;
  }
  if (ev.type === "wave") {
    if (Array.isArray(ev.levels) && ev.levels.length && Number(ev.hz) > 0) {
      speechEnvelope = { levels: ev.levels, hz: Number(ev.hz), startMs: Number(ev.start_ms) || Date.now() };
      drawWave();
    }
    return;
  }
  // A Mochi sound that is not a reply (tired, overheating ...): its subtitle, with the Mochi's emote.
  if (ev.type === "mochi_sound") {
    if (!isMochiKind(ev.kind) || lyraSpeaking) return;
    dbg(`mochi sound ${ev.kind}`);
    show("lyra", String(ev.subtitle || MOCHI_KINDS[ev.kind].subtitle), ev.kind, Number(ev.ms) || MOCHI_KINDS[ev.kind].ms);
    return;
  }
  if (ev.type !== "chat" || !ev.text) return;
  const role = String(ev.role || "").toLowerCase();
  const who = role === "user" || role === "you" ? "you" : "lyra";
  dbg(`chat ${who} (${String(ev.text).length} chars)`);
  // The answer is authoritative: retire any thinking indicator even if a
  // state event was missed while the caption window was starting or polling.
  if (who === "lyra") {
    lyraThinking = false;
    replyReceived = true;
  }
  // "Done" / "Opening ..." as a Mochi sound: when it replaces the speech, the subtitle is the short version.
  const mochi = who === "lyra" && isMochiKind(ev.mochi) ? ev.mochi : null;
  if (mochi && ev.mochi_silent) show(who, String(ev.mochi_subtitle || ev.text), mochi, Number(ev.mochi_ms) || MOCHI_KINDS[mochi].ms);
  else show(who, String(ev.text), mochi);
}

adaptivePoll(poll, 200, 350, false);   // 200 ms while events arrive, 350 ms when quiet (this window polled while hidden before: unchanged)
