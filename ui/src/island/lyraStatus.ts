// Lyra's voice turn, drawn ON the island (the compact notch), in the same look as the previews on the setup pages:
//
//   listening  ->  [Mochi]  the words you are saying, as you say them     |||| (cursor)
//   dictating  ->  [Mochi]  [app icon]  the words you are saying           ||||
//   thinking   ->  [Mochi]  the words she heard (or "Thinking")            ...  (cursor)
//   speaking   ->  [Mochi]  white voice wave
//   done       ->  [Mochi]  the accurate text that was pasted              (check)
//
// The live words are only to show you what she hears while you talk: they come from a fast guess. When you let go,
// the accurate transcript replaces them (that is what runs as the command, or is pasted for dictation).
//
// The Mochi sits on the left. When it is out on the screen (the floating Mochi explaining something, or dragged out
// of the notch) it is not drawn here. The subtitle window (src/caption) only shows Lyra's spoken answer; a Mochi
// sound ("Done", "Opening Spotify") is neither listening, thinking nor speaking, so nothing is drawn for it.

import { createCharacter, gazeToward, type Character, type EmoteName } from "../mascot/character";
import { AWAY_TIMEOUT_MS, onAway } from "../mascot/dock";
import { DEFAULT_CONTEXT, autoHidden, watchContext, type AutoHideContext } from "../core/autohide";
import { getFeatures, onFeatures } from "../core/featuresCache";
import { IS_TAURI, onEvent } from "../core/bridge";
import { isReallyVisible } from "../core/visibility";
import { VoiceWave } from "./wave";

export type LyraStatusKind = "listening" | "thinking" | "speaking" | "done" | "circle";

const BASE = "http://127.0.0.1:8765";
const MOCHI_PX = 40;                       // the same size as the Mochi in the setup previews

const EMOTE: Record<LyraStatusKind, EmoteName> = { listening: "listen", thinking: "think", speaking: "talk", done: "happy", circle: "listen" };
const LABEL: Record<LyraStatusKind, string> = { listening: "Listening", thinking: "Thinking", speaking: "", done: "", circle: "Circle it" };

const ICON_CURSOR = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M5 3l14 8-6.5 1.8L9.5 19z"/></svg>';
// the dashed ring of Circle to ask: the same icon as the setup preview
const ICON_RING = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><ellipse cx="12" cy="12" rx="8.5" ry="6.5" stroke-dasharray="3.2 3"/></svg>';
const ICON_PEN = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20l4.5-1 10-10a2.1 2.1 0 0 0-3-3l-10 10z"/></svg>';
const ICON_CHECK = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';

const CSS = `
#lyra-status{position:absolute;inset:0;z-index:95;display:flex;align-items:center;gap:10px;box-sizing:border-box;padding:0 16px 0 14px;
  border-radius:inherit;overflow:visible;pointer-events:none;opacity:0;visibility:hidden;color:#cfd6e6;
  transition:opacity .18s ease,visibility 0s linear .18s}
#island[data-mode="compact"] #lyra-status.on{opacity:1;visibility:visible;transition:opacity .18s ease}
#lyra-status .ls-mochi{flex:0 0 ${MOCHI_PX}px;width:${MOCHI_PX}px;position:relative;transform:translateY(5px)}
#lyra-status.no-mochi .ls-mochi{position:absolute;left:14px;visibility:hidden}
#lyra-status.no-mochi{padding-left:20px}
/* dictation: the program you are dictating into, as in the dictation preview */
#lyra-status .ls-app{display:none;flex:none;width:26px;height:26px;border-radius:8px;overflow:hidden;background:#3b82f6;color:#fff;place-items:center}
#lyra-status.dictating .ls-app{display:grid}
#lyra-status .ls-app img{display:block;width:100%;height:100%;object-fit:cover}
#lyra-status .ls-mid{flex:1 1 auto;min-width:0;display:flex;align-items:center;justify-content:flex-start;overflow:hidden}
/* the words: newest on the right, older words fade out on the left */
#lyra-status .ls-live{display:none;flex:1 1 auto;min-width:0;justify-content:flex-start;overflow:hidden}
/* a sentence longer than the room: keep the NEWEST words in view, older ones fade out on the left */
#lyra-status .ls-live.over{justify-content:flex-end;-webkit-mask-image:linear-gradient(90deg,transparent 0,#000 14%);mask-image:linear-gradient(90deg,transparent 0,#000 14%)}
#lyra-status .ls-live span{flex:none;white-space:nowrap;font:600 15px var(--font,system-ui,sans-serif);color:#fff;letter-spacing:.005em}
#lyra-status.has-text .ls-live{display:flex}
#lyra-status.has-text.pending .ls-live span{color:#e9edf8}
/* thinking: a soft light sweeps over her words while she works on them */
#lyra-status[data-kind="thinking"].pending .ls-live span,#lyra-status[data-kind="thinking"] .ls-label{background:linear-gradient(100deg,#8c93a6 0%,#fff 45%,#8c93a6 90%);background-size:220% 100%;-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;animation:ls-shimmer 1.8s linear infinite}
@keyframes ls-shimmer{from{background-position:120% 0}to{background-position:-100% 0}}
#lyra-status .ls-live.corrected span{animation:ls-fix .38s ease both}
@keyframes ls-fix{0%{opacity:.35;transform:translateY(3px)}100%{opacity:1;transform:none}}
#lyra-status .ls-text{display:none;align-items:center;gap:8px;white-space:nowrap;font:700 15px var(--font,system-ui,sans-serif);color:#fff}
#lyra-status[data-kind="listening"]:not(.has-text) .ls-text,#lyra-status[data-kind="thinking"]:not(.has-text) .ls-text,#lyra-status[data-kind="circle"] .ls-text{display:flex}
#lyra-status[data-kind="circle"] .ls-text .ls-dots{display:none}
#lyra-status .ls-dots{display:inline-flex;align-items:center;gap:3px;height:12px}
#lyra-status .ls-dots i{display:block;width:4px;height:4px;border-radius:50%;background:#fff;opacity:.4;animation:ls-dot 1s ease-in-out infinite}
#lyra-status .ls-dots i:nth-child(2){animation-delay:.16s}
#lyra-status .ls-dots i:nth-child(3){animation-delay:.32s}
@keyframes ls-dot{0%,60%,100%{opacity:.35;transform:translateY(0)}30%{opacity:1;transform:translateY(-3px)}}
/* right side, as in the setup preview: the blue bars (or dots while she thinks) and the round cursor button */
#lyra-status .ls-right{display:none;flex:none;align-items:center;gap:12px}
#lyra-status[data-kind="listening"] .ls-right,#lyra-status[data-kind="thinking"] .ls-right,#lyra-status[data-kind="done"] .ls-right,#lyra-status[data-kind="circle"] .ls-right{display:flex}
#lyra-status .ls-bars{display:inline-flex;align-items:center;gap:3px;height:20px}
#lyra-status .ls-bars i{display:block;width:3.5px;height:5px;border-radius:2px;background:#5b9bff;box-shadow:0 0 8px rgba(91,155,255,.55);animation:ls-bar .7s ease-in-out infinite}
#lyra-status .ls-bars i:nth-child(2){animation-delay:.12s}
#lyra-status .ls-bars i:nth-child(3){animation-delay:.24s}
#lyra-status .ls-bars i:nth-child(4){animation-delay:.36s}
#lyra-status .ls-bars i:nth-child(5){animation-delay:.48s}
@keyframes ls-bar{0%,100%{height:5px}50%{height:20px}}
#lyra-status .ls-btn{display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:rgba(59,130,246,.28);color:#7fb0ff}
#lyra-status[data-kind="listening"] .ls-btn{animation:ls-ring 1.6s ease-out infinite}
@keyframes ls-ring{0%{box-shadow:0 0 0 0 rgba(91,155,255,.45)}100%{box-shadow:0 0 0 9px rgba(91,155,255,0)}}
#lyra-status[data-kind="thinking"] .ls-bars,#lyra-status[data-kind="done"] .ls-bars{display:none}
#lyra-status:not([data-kind="thinking"]) .ls-right .ls-dots{display:none}
#lyra-status[data-kind="done"] .ls-btn{background:rgba(52,199,89,.22);color:#5fe08a}
#lyra-status:not([data-kind="done"]) .ls-check{display:none}
#lyra-status[data-kind="done"] .ls-cursor{display:none}
/* Circle to ask: as in the setup preview, a purple round button with the dashed ring (before the blue bars) instead of the cursor */
#lyra-status .ls-ring{display:none}
#lyra-status.circling .ls-right{flex-direction:row-reverse}
#lyra-status.circling .ls-btn{background:rgba(185,131,255,.28);color:#c9a2ff;animation:none}
#lyra-status.circling .ls-cursor{display:none}
#lyra-status.circling .ls-ring{display:inline-grid}
/* dictation has no cursor button: it is not something you click */
#lyra-status.dictating[data-kind="listening"] .ls-btn,#lyra-status.dictating[data-kind="thinking"] .ls-btn{display:none}
/* speaking: the VoiceWave from wave.ts, white. style.css positions it for the old layout, so it is a plain flex item here. */
#lyra-status #voice-wave{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);z-index:auto;height:34px}
#lyra-status #voice-wave .vw-bar{background:#fff;box-shadow:0 0 7px rgba(255,255,255,.5)}
/* While the island shows Lyra, nothing else is drawn in the notch (music, timer, volume, toast, pulse). */
#island[data-mode="compact"].lyra-status #spotify-compact,#island[data-mode="compact"].lyra-status #volume-hud,
#island[data-mode="compact"].lyra-status #timer-chip,#island[data-mode="compact"].lyra-status #notify-toast,
#island[data-mode="compact"].lyra-status #island-pulse{opacity:0!important;visibility:hidden!important}
`;

export class LyraStatus {
  readonly el: HTMLElement;
  private readonly wave = new VoiceWave();
  private readonly mochiHost: HTMLElement;
  private readonly labelEl: HTMLElement;
  private readonly liveEl: HTMLElement;
  private readonly liveSpan: HTMLElement;
  private readonly appEl: HTMLElement;
  private mochi: Character | null = null;
  private kind: LyraStatusKind | null = null;
  private away = false;
  private awayTimer = 0;
  private shape = "cloud";
  private hideCtx: AutoHideContext = DEFAULT_CONTEXT;
  private raf = 0;
  private cursor: { x: number; y: number } | null = null;
  private text = "";
  private isFinal = false;
  private appKey = "";
  private readonly icons = new Map<string, string>();

  constructor() {
    if (!document.getElementById("lyra-status-css")) {
      const style = document.createElement("style");
      style.id = "lyra-status-css";
      style.textContent = CSS;
      document.head.appendChild(style);
    }
    this.mochiHost = document.createElement("span");
    this.mochiHost.className = "ls-mochi";
    this.mochiHost.setAttribute("aria-hidden", "true");

    this.appEl = document.createElement("span");
    this.appEl.className = "ls-app";

    // the words (or the status label while nothing was heard yet)
    this.liveSpan = document.createElement("span");
    this.liveEl = document.createElement("div");
    this.liveEl.className = "ls-live";
    this.liveEl.append(this.liveSpan);
    this.labelEl = document.createElement("span");
    this.labelEl.className = "ls-label";
    const textWrap = document.createElement("span");
    textWrap.className = "ls-text";
    const dots = document.createElement("span");
    dots.className = "ls-dots";
    for (let i = 0; i < 3; i++) dots.append(document.createElement("i"));
    textWrap.append(this.labelEl, dots);

    const mid = document.createElement("div");
    mid.className = "ls-mid";
    mid.append(textWrap, this.liveEl, this.wave.el);

    const right = document.createElement("div");
    right.className = "ls-right";
    const bars = document.createElement("span");
    bars.className = "ls-bars";
    for (let i = 0; i < 5; i++) bars.append(document.createElement("i"));
    const rdots = document.createElement("span");
    rdots.className = "ls-dots";
    for (let i = 0; i < 3; i++) rdots.append(document.createElement("i"));
    const btn = document.createElement("span");
    btn.className = "ls-btn";
    const cursor = document.createElement("span");
    cursor.className = "ls-cursor";
    cursor.innerHTML = ICON_CURSOR;
    const check = document.createElement("span");
    check.className = "ls-check";
    check.innerHTML = ICON_CHECK;
    const ring = document.createElement("span");
    ring.className = "ls-ring";
    ring.innerHTML = ICON_RING;
    btn.append(cursor, ring, check);
    right.append(bars, rdots, btn);

    this.el = document.createElement("div");
    this.el.id = "lyra-status";
    this.el.setAttribute("aria-hidden", "true");
    this.el.append(this.mochiHost, this.appEl, mid, right);
    this.paintApp();

    // The Mochi is out on the screen: the floating Mochi window repeats "away" as a heartbeat while it is out
    // (the same channel the idle notch Mochi follows, see mascot/dock.ts), so a crashed overlay cannot hide ours for good.
    void onAway((a) => {
      window.clearTimeout(this.awayTimer);
      this.away = a;
      if (a) this.awayTimer = window.setTimeout(() => { this.away = false; this.paintMochi(); }, AWAY_TIMEOUT_MS);
      this.paintMochi();
    });
    // Like the notch Mochi, it steps aside while the screen is being shared.
    void watchContext((c) => { this.hideCtx = c; this.paintMochi(); });
    // Which Mochi (cloud / fox / cat ...) the user picked in Settings. The island Mochi is shown whether or not the
    // floating mascot feature is on: it is Lyra's face while she listens, thinks and talks.
    const readShape = async () => {
      const p = (await getFeatures()) as { mascot_shape?: string } | null;
      if (!p) return;
      this.shape = p.mascot_shape ?? "cloud";
      this.mochi?.setShape?.(this.shape);
    };
    void readShape();
    onFeatures(() => void readShape());

    // Its eyes follow the cursor while she listens, thinks and talks (the same cursor feed as the idle notch Mochi).
    const look = (x: number, y: number) => {
      this.cursor = x > -5000 && y > -5000 ? { x, y } : null;        // (-10000, -10000) = "no cursor" (a game is in front)
      this.aim();
    };
    if (IS_TAURI) void onEvent<{ x: number; y: number }>("cursor", (c) => look(c.x, c.y));
    else window.addEventListener("mousemove", (e) => look(e.clientX, e.clientY));
    // The island grows / moves when her turn starts, so re-aim from the last known position now and then.
    window.setInterval(() => { if (this.kind !== null && isReallyVisible()) this.aim(); }, 400);
  }

  private aim(): void {
    if (!this.mochi || !this.mochiWanted()) return;
    if (!this.cursor) { this.mochi.look(0, 0); return; }
    const [gx, gy] = gazeToward(this.mochiHost, this.cursor.x, this.cursor.y);
    this.mochi.look(gx, gy);
  }

  /** The Mochi is drawn only while a status shows and it is not out on the screen (or hidden by a screen share). */
  private mochiWanted(): boolean {
    return this.kind !== null && !this.away && !autoHidden("always", this.hideCtx);
  }

  private paintMochi(): void {
    const want = this.mochiWanted();
    this.el.classList.toggle("no-mochi", !want);
    // Created the first time it is needed and then kept: it only draws (setActive) while it is shown.
    if (want && !this.mochi) {
      this.mochi = createCharacter(this.mochiHost, { tight: true, sharp: 2 });
      this.mochi.setShape?.(this.shape);
      this.mochi.setEmote(this.kind ? EMOTE[this.kind] : "idle");
    }
    this.mochi?.setActive?.(want);
    if (want) this.aim();
  }

  /** Show `kind` on the island, or nothing (null). Safe to call with the same kind again. */
  set(kind: LyraStatusKind | null): void {
    if (kind === this.kind) return;
    const was = this.kind;
    this.kind = kind;
    this.el.classList.toggle("on", kind !== null);
    if (kind) this.el.dataset.kind = kind; else delete this.el.dataset.kind;
    // A new turn starts: the words of the last one go. (Listening -> thinking -> speaking keeps them.)
    if (kind === null || (kind === "listening" && was !== "listening")) this.paintText("", false, false);
    if (kind) this.labelEl.textContent = this.labelFor(kind);
    this.el.classList.toggle("pending", !this.isFinal && (kind === "listening" || kind === "thinking"));
    this.wave.setSpeaking(kind === "speaking");
    this.paintMochi();
    this.mochi?.setEmote(kind ? EMOTE[kind] : "idle");
    if (kind !== "speaking") this.mochi?.setMouth(0);
    this.syncMouth();
  }

  /** The words so far (a fast guess while you talk), or with `final` the accurate text that replaces them. */
  setLive(text: string, final: boolean): void {
    const t = (text || "").replace(/\s+/g, " ").trim();
    if (!final && t === this.text) return;
    this.paintText(t, final, final && this.text !== "" && t !== this.text);
  }

  private paintText(t: string, final: boolean, corrected: boolean): void {
    this.text = t;
    this.isFinal = final;
    this.liveSpan.textContent = t;
    this.el.classList.toggle("has-text", t !== "");
    this.liveEl.classList.toggle("over", this.liveSpan.offsetWidth > this.liveEl.clientWidth + 1);   // too long for the room
    this.el.classList.toggle("pending", !final && (this.kind === "listening" || this.kind === "thinking"));
    if (corrected) {                                                  // the accurate text replaced the guess: a small fade shows it
      this.liveEl.classList.remove("corrected");
      void this.liveEl.offsetWidth;
      this.liveEl.classList.add("corrected");
    }
  }

  private circling = false;

  private labelFor(kind: LyraStatusKind): string {
    return this.circling && kind === "thinking" ? "Looking at it" : LABEL[kind];
  }

  /** Circle to ask is running: show its purple ring (like the setup preview) next to the bars / dots. */
  setCircle(on: boolean): void {
    if (on === this.circling) return;
    this.circling = on;
    this.el.classList.toggle("circling", on);
    if (this.kind) this.labelEl.textContent = this.labelFor(this.kind);
  }

  /** Dictation: show the icon of the program you dictate into (or none, when it is not dictation). */
  setDictation(on: boolean, app = ""): void {
    this.el.classList.toggle("dictating", on);
    if (on && app && app !== this.appKey) { this.appKey = app; this.paintApp(); void this.loadIcon(app); }
    if (!on) this.appKey = "";
  }

  private paintApp(): void {
    const icon = this.appKey ? this.icons.get(this.appKey) : "";
    if (icon) {
      const img = document.createElement("img");
      img.alt = "";
      img.src = icon;
      this.appEl.replaceChildren(img);
      this.appEl.style.background = "transparent";
    } else {
      this.appEl.innerHTML = ICON_PEN;                                // until the real icon arrives, or when it has none
      this.appEl.style.background = "#3b82f6";
    }
  }

  private async loadIcon(app: string): Promise<void> {
    if (this.icons.has(app)) { this.paintApp(); return; }
    try {
      const r = await fetch(`${BASE}/app/icon?name=${encodeURIComponent(app)}`);
      if (!r.ok) return;
      const d = (await r.json()) as { icon?: string };
      if (d?.icon) { this.icons.set(app, d.icon); if (this.appKey === app) this.paintApp(); }
    } catch { /* backend not up yet: the pen tile stays */ }
  }

  /** The backend's loudness envelope for the sentence that is playing (the "wave" event). */
  feedWave(levels: number[], hz: number, startMs: number): void {
    this.wave.feed(levels, hz, startMs);
  }

  /** The Mochi's mouth follows the same loudness the bars show. */
  private syncMouth(): void {
    if (this.raf) { cancelAnimationFrame(this.raf); this.raf = 0; }
    if (this.kind !== "speaking") return;
    const step = () => {
      this.raf = 0;
      if (this.kind !== "speaking") return;
      this.mochi?.setMouth(this.wave.currentLevel);
      this.raf = requestAnimationFrame(step);
    };
    this.raf = requestAnimationFrame(step);
  }
}
