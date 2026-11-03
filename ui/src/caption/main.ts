// Caption window: Lyra's spoken answer as a plain black subtitle near the bottom of the screen. ONLY her answer.
//
//   - What you said, "Listening", "Thinking", the Mochi and the voice wave are NOT here: they live on the island
//     (src/island/lyraStatus.ts).
//   - A Mochi sound ("Done", "Opening Spotify" ...) has no subtitle at all: it is only the Mochi's sound + animation.
//
// It reads the same backend event feed as the island. The line fades out by itself. Nothing is stored.

import "./caption.css";
import { invoke } from "@tauri-apps/api/core";
import { adaptivePoll, noteEvents } from "../core/pollLoop";
import { installTracer } from "../core/trace";
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
// A reply can arrive while an older/repeated thinking state is still in the event queue.
// Once the reply is shown, don't let that stale state take the new turn's place.
let replyReceived = false;
let answerUntil = 0; // an answer stays at least until here, whatever the state events say

// ── Diagnostics: a few lines in Lyra's log (%LOCALAPPDATA%\Lyra\lyra.log) so a silent caption can be traced ──
let dbgLeft = 80;
function dbg(msg: string): void {
  if (!IS_TAURI || dbgLeft <= 0) return;
  dbgLeft--;
  void invoke("log_line", { message: `caption: ${msg}` }).catch(() => undefined);
}
dbg(`window started, backend ${BASE}`);

function scheduleHide(pill: HTMLElement, ms: number): void {
  window.clearTimeout(hideTimer);
  ms = Math.max(ms, answerUntil - Date.now());
  hideTimer = window.setTimeout(() => fade(pill), ms);
}

/** Fade the pill out and remove it. */
function fade(pill: HTMLElement): void {
  if (currentPill !== pill) return;
  pill.classList.remove("in");
  pill.classList.add("out");
  removeTimer = window.setTimeout(() => {
    if (currentPill === pill) currentPill = null;
    pill.remove();
  }, 400);
}

/** Take the subtitle away now (a new turn started, or the Mochi is explaining something): no minimum reading time. */
function dismiss(): void {
  window.clearTimeout(hideTimer);
  answerUntil = 0;
  if (currentPill) fade(currentPill);
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
}

/** Lyra's answer, as text on black. */
function show(raw: string): void {
  const text = clip(raw);
  if (!text) return;
  window.clearTimeout(hideTimer);
  window.clearTimeout(removeTimer);

  const body = document.createElement("span");
  body.className = "txt";
  body.textContent = text;
  const pill = document.createElement("div");
  pill.className = "pill lyra";
  pill.append(body);

  subs.replaceChildren(pill);
  currentPill = pill;
  reveal(pill);

  // An answer stays long enough to read; while she is still talking it stays until she is done (see the state handler).
  answerUntil = Date.now() + Math.min(20000, Math.max(6000, text.length * 55));
  scheduleHide(pill, 60000);
}

type Ev = {
  type?: string; role?: string; text?: string; state?: string; ts?: number;
  mochi?: string; mochi_silent?: boolean;
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

function handleMascotEvent(ev: Ev): void {
  const cmd = String((ev as any).cmd || "");
  if (cmd === "tour") {
    const steps = Array.isArray((ev as any).steps) ? (ev as any).steps : [];
    if (steps.length) {                                     // any tour (pointing or drawing): the Mochi has its own bubble
      const total = steps.reduce((n: number, s: any) => n + (Number(s?.ms) || 3500), 0);
      quietUntil = Date.now() + total + 12000;
      setQuiet(true);
      dismiss();
    }
  } else if (cmd === "stop" || cmd === "home" || (cmd === "emote" && (ev as any).name === "happy")) {
    quietUntil = Date.now() + 400;
  }
}

function handleEvent(ev: Ev): void {
  if (ev.type === "mascot") { handleMascotEvent(ev); return; }
  if (ev.type === "state") {
    const state = String(ev.state || "").toLowerCase();
    dbg(`state ${state}`);
    if (state !== "thinking") replyReceived = false;
    lyraThinking = state === "thinking" && !replyReceived;
    lyraSpeaking = state === "speaking";
    // A new turn has started (she is listening to you, or working on it): the last answer is over. The island
    // shows what she is doing now; the subtitle stays empty until the next answer.
    if (state === "listening" || lyraThinking) { dismiss(); return; }
    if (currentPill) {
      if (lyraSpeaking) {
        // Keep the answer up for as long as she talks.
        window.clearTimeout(hideTimer);
        window.clearTimeout(removeTimer);
        currentPill.classList.remove("out");
        currentPill.classList.add("in");
      } else {
        scheduleHide(currentPill, 3200);                     // she finished: a moment longer, then it goes
      }
    }
    return;
  }
  // A Mochi sound that is not a reply (tired, overheating ...): the Mochi's sound + animation only, no subtitle.
  if (ev.type === "mochi_sound") return;
  // Wave events belong to the island (the white wave); the caption has no wave any more.
  if (ev.type !== "chat" || !ev.text) return;
  const role = String(ev.role || "").toLowerCase();
  // What you said is not shown: only Lyra's answer is.
  if (role === "user" || role === "you") return;
  dbg(`chat lyra (${String(ev.text).length} chars)`);
  // The answer is authoritative: retire any thinking marker even if a state event was missed.
  lyraThinking = false;
  replyReceived = true;
  // The answer IS a Mochi sound ("Done", "Opening ..." instead of speech): no subtitle. When the setting is
  // "Both" (sound + her voice) mochi_silent is false: she speaks, so her words are shown.
  if (ev.mochi && ev.mochi_silent) return;
  show(String(ev.text));
}

adaptivePoll(poll, 200, 350, false);   // 200 ms while events arrive, 350 ms when quiet (this window polled while hidden before: unchanged)
