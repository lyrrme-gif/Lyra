// First-run setup and tour ("Meet Lyra"): one light card, one idea per page, a preview you can try.
//
// Pages: welcome · voice models (required) · talk (Right Ctrl) · commands · dictate (Left Alt) · circle to ask (Ctrl, Ctrl) · look · island ·
// short replies · Mochi · tab wheel · one page per feature group · WhatsApp · API keys · privacy · done.
// Nothing is saved until the last page, except what has to be live to be tried (look, island, Mochi) and the
// things that are real actions (a model download, linking WhatsApp, testing a key).
// main.ts hands in everything the wizard needs to read and write (see WizardCtx), so this file has no
// knowledge of where settings live.

import "./setupWizard.css";
import { h, clear } from "../views/dom";
import { FEATURES, FEATURE_GROUPS, type FeatureDef } from "../core/features";
import { featurePreview } from "./featureScenes";
import { islandScene } from "./screenPreview";
import { THEME_LOOKS, themePreview } from "./themePreview";
import { createCharacter, gazeToward, type Character } from "../mascot/character";
import { CHAR_DEFS, CHARACTER_IDS, RAGE_LINES } from "../mascot/characters";
import { Bridge } from "../core/bridge";
import { onTourSignal } from "../core/tour";
import { readExtras, writeExtras } from "../core/state";
import { appsPost, fetchApps, type AppView } from "../core/appsApi";
import { playMochi } from "../core/mochiSound";
import { AUTOHIDE_HINT, AUTOHIDE_MODES, normalizeAutoHide, type AutoHideMode } from "../core/autohide";

export interface WizPrefs { data: Record<string, any>; set(key: string, value: any): void; flush(): Promise<boolean> }

export interface WizardCtx {
  root: HTMLElement;
  replay: boolean;
  lyra: string;                                   // backend base URL
  loadPrefs(group: string): Promise<WizPrefs>;
  featureIsOn(f: FeatureDef, p: WizPrefs): boolean;
  applyFeature(f: FeatureDef, on: boolean, p: WizPrefs): void;
  theme: { read(): string; write(t: string): void };
  position: { read(): "floating" | "attached"; write(p: "floating" | "attached"): void };
  settings: { islandAutoHide: AutoHideMode; wakeMode: "dwell" | "instant"; screen: string; autostart: boolean };
  saveSettings(): Promise<void>;
  markDone(): void;
}

// ── Data ─────────────────────────────────────────────────────────────────────

const THEMES = THEME_LOOKS;
const MATERIALS: Array<{ id: "black" | "frost" | "glass"; name: string }> = [{ id: "black", name: "Black" }, { id: "frost", name: "Frost" }, { id: "glass", name: "Glass" }];

const REPLY_MODES: Array<{ id: "mochi" | "both" | "speak"; name: string; note: string }> = [
  { id: "mochi", name: "Mochi sounds", note: "The Mochi plays a sound and a little animation, with a subtitle, instead of speaking." },
  { id: "both", name: "Both", note: "The sound and the voice." },
  { id: "speak", name: "Speak", note: "Voice only." },
];

const PROVIDERS = [
  { id: "openrouter", name: "OpenRouter", key: "openrouter_api_key", placeholder: "sk-or-…", url: "https://openrouter.ai/settings/keys",
    note: "One key, hundreds of models. Has free ones.", steps: ["Sign in or create an account", "Open Keys and press Create Key", "Copy it and paste it here"] },
  { id: "gemini", name: "Google Gemini", key: "gemini_api_key", placeholder: "AIza…", url: "https://aistudio.google.com/api-keys",
    note: "Fast and generous free tier. Sees images too.", steps: ["Sign in with your Google account", "Open API keys and create one", "Copy it and paste it here"] },
  { id: "groq", name: "Groq", key: "groq_api_key", placeholder: "gsk_…", url: "https://console.groq.com/keys",
    note: "Very fast answers. Free tier.", steps: ["Create or sign in to a Groq account", "Open API Keys and create one", "Copy it and paste it here"] },
  { id: "ollama", name: "Ollama", key: "ollama_api_key", placeholder: "No key needed", url: "https://ollama.com/download",
    note: "Runs on your PC. No key, no internet.", steps: ["Install and start Ollama", "Run: ollama pull llama3.2:3b", "Press Continue. Nothing else is needed"] },
] as const;
type ProviderId = typeof PROVIDERS[number]["id"];

type StepId = "welcome" | "talk" | "commands" | "dictate" | "circle" | "look" | "island" | "replies" | "mochi" | "wheel"
  | "f-island" | "f-daily" | "f-pc" | "record" | "f-helpers" | "whatsapp" | "models" | "keys" | "privacy" | "ready";
// "models" sits right after "welcome": speech recognition is required (without it dictation and commands are poor), and every
// page after it (talk, commands, dictate, circle ...) uses it. First setup cannot continue until it is downloaded.
const STEPS: StepId[] = ["welcome", "models", "talk", "commands", "dictate", "circle", "look", "island", "replies", "mochi", "wheel",
  "f-island", "f-daily", "f-pc", "record", "f-helpers", "whatsapp", "keys", "privacy", "ready"];

const OWN_PAGE_IDS = new Set(["mascot", "mascot_stay", "circle_ask", "record"]);        // they have their own page, so the feature pages skip them
const DICTATION_PHRASE = "Hi Sam, can we move our meeting to Friday? Thanks!";

/** What you can say, and what you get. Each has a small scene that plays on the Commands page. */
interface Command { say: string; short: string; scene: () => HTMLElement }
const COMMANDS: Command[] = [
  { say: "Search lo-fi beats on YouTube", short: "Search the web or YouTube", scene: () => h("div", { class: "wz-cs wz-cs-search", html:
    `<div class="wz-cs-head"><b class="yt">▶</b><span>YouTube · lo-fi beats</span></div>
     <div class="wz-cs-vid"><i style="background:linear-gradient(135deg,#7a5cff,#2b3a8f)"></i><span><b>lofi hip hop radio · beats to relax/study to</b><small>Live · 12K watching</small></span></div>
     <div class="wz-cs-vid"><i style="background:linear-gradient(135deg,#ff8f6b,#c2406a)"></i><span><b>1 hour of calm lo-fi · focus mix</b><small>1:02:14 · 2.1M views</small></span></div>
     <div class="wz-cs-vid"><i style="background:linear-gradient(135deg,#3fc6b0,#1d6e80)"></i><span><b>Rainy day lo-fi · no ads</b><small>48:20 · 640K views</small></span></div>` }) },
  { say: "Play some Daft Punk", short: "Play music", scene: () => h("div", { class: "wz-cs wz-cs-music", html:
    `<i class="art"></i><div class="meta"><b>Get Lucky</b><small>Daft Punk</small><div class="bar"><u></u></div></div><div class="ctl"><span>⏮</span><span class="play">❚❚</span><span>⏭</span></div>` }) },
  { say: "Set a timer for 10 minutes", short: "Timers and reminders", scene: () => h("div", { class: "wz-cs wz-cs-timer", html:
    `<div class="ring"><b>9:58</b></div><div class="meta"><b>Timer</b><small>10 minutes · stays in the small island</small></div>` }) },
  { say: "Remind me at 5 pm to call mum", short: "Reminders", scene: () => h("div", { class: "wz-cs wz-cs-remind", html:
    `<span class="bell">🔔</span><div class="meta"><b>Reminder set</b><small>Call mum · today, 5:00 PM</small></div>` }) },
  { say: "What's the weather tomorrow?", short: "Weather", scene: () => h("div", { class: "wz-cs wz-cs-weather", html:
    `<span class="sun">☀</span><div class="meta"><b>24°</b><small>Tomorrow · mostly sunny · H 26° L 17°</small></div>` }) },
  { say: "Add bread to my to-do list", short: "Your to-do list", scene: () => h("div", { class: "wz-cs wz-cs-todo", html:
    `<div class="row"><i></i><span>Call the bank</span></div><div class="row"><i></i><span>Finish the report</span></div><div class="row new"><i></i><span>Bread</span></div>` }) },
  { say: "Translate good morning to French", short: "Translate", scene: () => h("div", { class: "wz-cs wz-cs-trans", html:
    `<small>English → French</small><b>Bonjour</b><small class="dim">“Good morning”</small>` }) },
  { say: "Start recording", short: "Record your screen", scene: () => h("div", { class: "wz-cs wz-cs-rec", html:
    `<span class="dot"></span><div class="meta"><b>Recording</b><small>00:03 · say “stop recording” to finish</small></div>` }) },
];

// ── Small builders ────────────────────────────────────────────────────────────

const ICON_CURSOR = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M5 3l14 8-6.5 1.8L9.5 19z"/></svg>';
const ICON_MAIL = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M3.5 7l8.5 6 8.5-6"/></svg>';
const ICON_LINK = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>';
const ICON_WAVE = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M4 10v4M8 6v12M12 9v6M16 4v16M20 9v6"/></svg>';
const ICON_RING = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><ellipse cx="12" cy="12" rx="8.5" ry="6.5" stroke-dasharray="3.2 3"/></svg>';
const ICON_VOICE = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12"/></svg>';

const icon = (svg: string, cls = ""): HTMLElement => h("span", { class: cls, html: svg });

function toggle(on: boolean, onChange: (v: boolean) => void): HTMLElement {
  const el = h("button", { class: "wz-switch" + (on ? " on" : ""), type: "button", role: "switch", "aria-checked": on ? "true" : "false" });
  el.addEventListener("click", () => {
    const next = !el.classList.contains("on");
    el.classList.toggle("on", next);
    el.setAttribute("aria-checked", String(next));
    onChange(next);
  });
  return el;
}

const key = (text: string, on = false): HTMLElement => h("span", { class: "wz-key" + (on ? " on" : ""), text });
const chip = (text: string, cls = ""): HTMLElement => h("span", { class: "wz-chip " + cls, text });
const bars = (): HTMLElement => h("span", { class: "wz-bars" }, h("i", {}), h("i", {}), h("i", {}), h("i", {}));

/** A pretend desktop: wallpaper, taskbar, and whatever goes on it. */
function desk(...kids: Array<Node | null>): HTMLElement {
  return h("div", { class: "wz-desk" },
    ...kids,
    h("div", { class: "wz-taskbar" },
      h("span", { class: "wz-tb-dots" }, h("i", {}), h("i", {}), h("i", {}), h("i", {})),
      h("span", { class: "wz-tb-clock", text: "10:24" })));
}

const mb = (n: number) => `${Math.round(n / 1e6)} MB`;

// ── The wizard ────────────────────────────────────────────────────────────────

export async function showSetupWizard(ctx: WizardCtx): Promise<void> {
  const { root, replay, lyra } = ctx;
  let step = 0;

  const [featPrefs, privacyPrefs, voicePrefs] = await Promise.all([ctx.loadPrefs("features"), ctx.loadPrefs("privacy"), ctx.loadPrefs("voice")]);

  const ex = readExtras();
  const choices = {
    theme: ctx.theme.read(),
    material: (ex.compactStyle as string) || "black",
    position: ctx.position.read(),
    autoHide: normalizeAutoHide(ctx.settings.islandAutoHide) as AutoHideMode,
    wake: (ctx.settings.wakeMode === "instant" ? "instant" : "dwell") as "dwell" | "instant",
    screen: String(ctx.settings.screen || "primary"),
    reply: (["mochi", "both", "speak"].includes(String(voicePrefs.data.mochi_sound_mode)) ? String(voicePrefs.data.mochi_sound_mode) : "mochi") as "mochi" | "both" | "speak",
    mic: String(voicePrefs.data.audio_input || "Default Microphone"),
    ttsEngine: String(voicePrefs.data.tts_engine || "edge"),
    autostart: !!ctx.settings.autostart,
    provider: "openrouter" as ProviderId,
    keys: { openrouter_api_key: "", gemini_api_key: "", groq_api_key: "", ollama_api_key: "" } as Record<string, string>,
    features: {} as Record<string, boolean>,
    shape: String(featPrefs.data.mascot_shape || "cloud"),
  };
  if (!CHAR_DEFS[choices.shape]) choices.shape = "cloud";
  for (const f of FEATURES) choices.features[f.id] = ctx.featureIsOn(f, featPrefs);

  let hasSavedKey = false;
  let micList: string[] = [];
  let finishError = "";
  let forceFinish = false;
  const downloading = { stt: false, tts: false };
  let sttReady = false;      // the speech model is installed (first setup cannot go past the voice-model page without it)
  const keyState: Record<string, { text: string; ok: boolean | null }> = {};
  try {
    const r = await fetch(`${lyra}/settings/llm`).catch(() => null);
    if (r?.ok) {
      const l = await r.json();
      if (PROVIDERS.some((p) => p.id === l.provider)) choices.provider = l.provider;
      hasSavedKey = !!(l.has_openrouter || l.has_gemini || l.has_groq || l.has_ollama);
      for (const p of PROVIDERS) if (l[`has_${p.id}`]) keyState[p.id] = { text: p.id === "ollama" ? "Ready" : "Key saved", ok: true };
    }
  } catch { /* defaults are fine */ }
  void fetch(`${lyra}/voice/devices`).then((r) => (r.ok ? r.json() : null)).then((d) => { if (d?.inputs) micList = d.inputs; }).catch(() => undefined);

  // ── chrome ──
  const card = h("div", { class: "wz-card" });
  const back = h("button", { class: "wz-back", type: "button", "aria-label": "Back", html: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg>' });
  const skip = h("button", { class: "wz-skip", type: "button", text: replay ? "Close" : "Skip" });
  const main = h("div", { class: "wz-main" });
  const next = h("button", { class: "wz-next", type: "button", text: "Continue" }) as HTMLButtonElement;
  const dots = h("div", { class: "wz-dots" });
  card.append(back, skip, main, h("div", { class: "wz-foot" }, next, dots));
  const shell = h("div", { class: "wz" }, card);

  // pages can hand back a cleanup (timers, key listeners, Mochis) that runs when the page is left
  let cleanup: Array<() => void> = [];
  const onLeave = (fn: () => void) => cleanup.push(fn);

  // ── Mochis: the real character, and its eyes follow the mouse ──
  const watchers = new Set<{ host: HTMLElement; ch: Character }>();
  let lastMouse: [number, number] | null = null;
  const onMove = (e: MouseEvent) => { lastMouse = [e.clientX, e.clientY]; for (const w of watchers) { const [dx, dy] = gazeToward(w.host, e.clientX, e.clientY); w.ch.look(dx, dy); } };
  window.addEventListener("mousemove", onMove);
  const spawn = (host: HTMLElement, px: number, opts: { shape?: string; follow?: boolean } = {}): Character => {
    host.style.width = `${px}px`;
    const ch = createCharacter(host, { tight: true, sharp: 2 });
    ch.setShape?.(opts.shape ?? choices.shape);
    ch.setEmote("idle");
    const rec = { host, ch };
    if (opts.follow !== false) { watchers.add(rec); if (lastMouse) { const [dx, dy] = gazeToward(host, lastMouse[0], lastMouse[1]); ch.look(dx, dy); } }
    onLeave(() => { watchers.delete(rec); ch.destroy(); });
    return ch;
  };

  const title = (t: string, accent = ""): HTMLElement => {
    const el = h("h1", { class: "wz-title" });
    if (accent) { const i = t.indexOf(accent); el.append(t.slice(0, i), h("span", { class: "wz-accent", text: accent }), t.slice(i + accent.length)); } else el.textContent = t;
    return el;
  };
  const sub = (t: string): HTMLElement => h("p", { class: "wz-sub", text: t });
  const hint = (...kids: Array<Node | string>): HTMLElement => h("div", { class: "wz-hint" }, ...kids);
  const status = (t = ""): HTMLElement => h("div", { class: "wz-status", text: t });

  /** The little black island with the Mochi in it, as the real one looks. */
  const islandMock = (kind: "listen" | "dictate" | "circle" | "reply", extra?: Node): { el: HTMLElement; set(on: boolean): void } => {
    const m = h("span", { class: "wz-isl-mochi" });
    const el = h("div", { class: `wz-isl ${kind === "circle" ? "dictate circle" : kind}` }, m);
    spawn(m, 40);
    if (kind === "listen") el.append(h("div", { class: "wz-isl-row" }, bars(), h("span", { class: "wz-isl-btn" }, icon(ICON_CURSOR))));
    else if (kind === "circle") el.append(h("div", { class: "wz-isl-row mid" }, h("span", { class: "wz-isl-btn" }, icon(ICON_RING)), bars()));
    else if (kind === "dictate") el.append(h("div", { class: "wz-isl-row mid" }, h("span", { class: "wz-isl-app" }, icon(ICON_MAIL)), bars()));
    if (extra) el.append(extra);
    return { el, set: (on) => el.classList.toggle("live", on) };
  };

  // ── a key you really hold ──
  const holdKey = (code: string, page: StepId, onDown: () => void, onUp: () => void) => {
    let down = false;
    const kd = (e: KeyboardEvent) => { if (e.code !== code || STEPS[step] !== page) return; e.preventDefault(); if (!down) { down = true; onDown(); } };
    const ku = (e: KeyboardEvent) => { if (e.code !== code) return; e.preventDefault(); if (down) { down = false; onUp(); } };
    window.addEventListener("keydown", kd, true); window.addEventListener("keyup", ku, true);
    onLeave(() => { window.removeEventListener("keydown", kd, true); window.removeEventListener("keyup", ku, true); });
  };

  // ── pages ──
  const pageWelcome = () => {
    const slot = h("div", { class: "wz-hero-mochi" });
    const ch = spawn(slot, 200);
    ch.setEmote("wave");
    const t = window.setTimeout(() => ch.setEmote("idle"), 1600);
    onLeave(() => window.clearTimeout(t));
    let lvl = 0, last = 0;
    slot.addEventListener("click", () => {                              // poke it
      const now = performance.now();
      lvl = now - last > 4200 ? 1 : Math.min(6, lvl + 1); last = now;
      ch.provoke?.(lvl);
    });
    main.append(h("div", { class: "wz-center" }, slot, title("Meet Lyra", "Lyra"),
      sub("Your voice, in control. Right on your PC."),
      h("p", { class: "wz-small", text: "Lyra lives at the top of your screen. Go on, point at it." })));
    next.textContent = "Get started";
  };

  const pageTalk = () => {
    const isl = islandMock("listen");
    const k = key("Right Ctrl");
    const st = status("Try it: hold Right Ctrl.");
    main.append(
      title("Hold Right Ctrl and talk"), sub("Open apps, search the web, set reminders. It happens while you’re still talking."),
      desk(isl.el),
      hint("Hold", k, "and speak. Let go when you’re done."), st);
    holdKey("ControlRight", "talk", () => { isl.set(true); k.classList.add("on"); st.textContent = "Listening… let go when you’re done."; st.classList.remove("ok"); },
      () => { isl.set(false); k.classList.remove("on"); st.textContent = "✓ That’s it. Lyra only listens while you hold the key."; st.classList.add("ok"); });
  };

  const pageCommands = () => {
    const stage = h("div", { class: "wz-cmd-stage" });
    const phrase = h("span", { class: "wz-cmd-phrase" });
    const m = h("span", { class: "wz-isl-mochi" });
    const isl = h("div", { class: "wz-isl cmd" }, m, h("div", { class: "wz-cmd-say" }, bars(), phrase));
    spawn(m, 40);
    const result = h("div", { class: "wz-cmd-result" });
    stage.append(isl, result);
    const list = h("div", { class: "wz-cmd-list" });
    let idx = 0, timers: number[] = [];
    const items = COMMANDS.map((c, i) => {
      const b = h("button", { class: "wz-cmd-item", type: "button" }, h("strong", { text: `“${c.say}”` }), h("small", { text: c.short }));
      b.addEventListener("click", () => { play(i); schedule(); });
      list.append(b);
      return b;
    });
    const play = (i: number) => {
      timers.forEach((t) => window.clearTimeout(t)); timers = [];
      idx = i;
      items.forEach((b, j) => b.classList.toggle("on", j === i));
      const c = COMMANDS[i];
      isl.classList.add("live"); stage.classList.remove("done");
      clear(result); phrase.textContent = "";
      let n = 0;
      const typeNext = () => { n++; phrase.textContent = `“${c.say.slice(0, n)}${n < c.say.length ? "" : "”"}`; if (n < c.say.length) timers.push(window.setTimeout(typeNext, 34)); else timers.push(window.setTimeout(() => { isl.classList.remove("live"); result.append(c.scene()); stage.classList.add("done"); }, 380)); };
      typeNext();
    };
    let auto = 0;
    const schedule = () => { window.clearInterval(auto); auto = window.setInterval(() => play((idx + 1) % COMMANDS.length), 6400); };
    onLeave(() => { timers.forEach((t) => window.clearTimeout(t)); window.clearInterval(auto); });
    main.append(title("Just ask for it"), sub("Hold Right Ctrl and say it like you would to a person. Lyra does it and shows you the result."),
      h("div", { class: "wz-cmd" }, list, h("div", { class: "wz-cmd-side" }, h("div", { class: "wz-cmd-desk" }, stage))),
      h("p", { class: "wz-small", text: "These are a few of many. Ask in your own words." }));
    play(0); schedule();
  };

  const pageDictate = () => {
    const isl = islandMock("dictate");
    const keyTile = h("div", { class: "wz-bigkey" }, h("strong", { text: "Alt" }), h("small", { text: "Left" }));
    const body = h("div", { class: "wz-mail-body" });
    const mail = h("div", { class: "wz-mail" },
      h("div", { class: "wz-mail-head" }, icon(ICON_MAIL), h("span", { text: "New message" })),
      h("div", { class: "wz-mail-row" }, h("span", { text: "To" }), h("i", {})), body);
    const st = status("Try it: hold Left Alt and say a sentence.");
    main.append(
      title("Dictate into anything"), sub("Talk instead of type, in any app. Clean, punctuated text lands at your cursor."),
      h("div", { class: "wz-dict-top" }, isl.el, keyTile), mail,
      hint(key("Left Alt"), "hold, talk, let go ·", "say", chip("“comma”", "voice"), chip("“new line”", "voice")), st);
    let timer = 0;
    holdKey("AltLeft", "dictate", () => {
      isl.set(true); keyTile.classList.add("on"); body.textContent = ""; let i = 0;
      window.clearInterval(timer);
      timer = window.setInterval(() => { i = Math.min(DICTATION_PHRASE.length, i + 1); body.textContent = DICTATION_PHRASE.slice(0, i); if (i >= DICTATION_PHRASE.length) window.clearInterval(timer); }, 38);
      st.textContent = "Listening…"; st.classList.remove("ok");
    }, () => {
      isl.set(false); keyTile.classList.remove("on"); window.clearInterval(timer);
      body.textContent = DICTATION_PHRASE; st.textContent = "✓ That’s how it lands in the app you are using."; st.classList.add("ok");
    });
    onLeave(() => window.clearInterval(timer));
  };

  const pageCircle = () => {
    const isl = islandMock("circle");
    const st = status("Try it: tap Ctrl, then press it again and hold it. Draw a circle around the picture.");
    const trail = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    trail.setAttribute("class", "wz-circ-trail");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "wz-circ-svg"); svg.setAttribute("viewBox", "0 0 100 100"); svg.setAttribute("preserveAspectRatio", "none");
    svg.append(trail);
    const pic = h("div", { class: "wz-circ-pic", html:
      `<svg viewBox="0 0 120 90" preserveAspectRatio="xMidYMid slice"><defs><linearGradient id="wzSky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffb37a"/><stop offset=".55" stop-color="#ffd9a8"/><stop offset="1" stop-color="#bfe3ff"/></linearGradient></defs>
        <rect width="120" height="90" fill="url(#wzSky)"/><circle cx="92" cy="26" r="9" fill="#fff4cf" opacity=".9"/>
        <rect y="62" width="120" height="28" fill="#2f6fb5"/><path d="M0 70 Q15 66 30 70 T60 70 T90 70 T120 70 V90 H0Z" fill="#245a98"/>
        <path d="M22 90 L34 56 H72 L84 90Z" fill="#4b4f59"/>
        <path d="M48 56 L51 22 H61 L64 56Z" fill="#f4f4f6"/><path d="M49.4 44 H62.6 L63.4 50 H48.6Z M50.3 31 H61.7 L62.3 37 H49.7Z" fill="#d6453d"/>
        <rect x="50" y="15" width="12" height="7" fill="#ffe27a"/><path d="M48 15 H64 L56 8Z" fill="#d6453d"/></svg>` });
    const lines = h("div", { class: "wz-circ-lines" }, h("b", { style: "width:78%" }), h("b", { style: "width:92%" }), h("b", { style: "width:64%" }), h("b", { style: "width:84%" }), h("b", { style: "width:48%" }));
    const bubble = h("div", { class: "wz-circ-bubble" }, h("strong", { text: "Lyra" }), h("span", { text: "That’s a lighthouse on a rocky coast. It warns ships away from the rocks at night." }));
    const stage = h("div", { class: "wz-circ" }, isl.el, h("div", { class: "wz-circ-win" }, pic, lines), svg, bubble);

    let drawing = false, viaMouse = false, downAt = 0, lastTap = 0, pts: Array<[number, number]> = [];
    const reset = () => { pts = []; trail.setAttribute("points", ""); stage.classList.remove("drawing", "answered"); isl.set(false); };
    const pointAt = (e: PointerEvent): [number, number] => {
      const r = stage.getBoundingClientRect();
      return [Math.min(100, Math.max(0, ((e.clientX - r.left) / r.width) * 100)), Math.min(100, Math.max(0, ((e.clientY - r.top) / r.height) * 100))];
    };
    const begin = () => {
      drawing = true; reset(); stage.classList.add("drawing"); isl.set(true);
      st.textContent = "Listening… draw a circle around the picture, then let go."; st.classList.remove("ok");
    };
    const end = () => {
      if (!drawing) return;
      drawing = false; isl.set(false); stage.classList.remove("drawing");
      const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
      const big = pts.length > 8 && Math.max(...xs) - Math.min(...xs) > 14 && Math.max(...ys) - Math.min(...ys) > 20;
      if (big) {
        trail.setAttribute("points", [...pts, pts[0]].map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" "));
        stage.classList.add("answered");
        st.textContent = "✓ That’s Circle to ask. Lyra looks at what you circled and answers."; st.classList.add("ok");
      } else {
        reset(); st.textContent = "Draw a bigger circle around the picture. Tap Ctrl, press it again and hold."; st.classList.remove("ok");
      }
    };
    stage.addEventListener("pointermove", (e) => {
      if (!drawing) return;
      const p = pointAt(e); pts.push(p);
      trail.setAttribute("points", pts.map((q) => `${q[0].toFixed(1)},${q[1].toFixed(1)}`).join(" "));
    });
    // No keyboard handy? Pressing and dragging the mouse on the picture does the same.
    stage.addEventListener("pointerdown", (e) => { if (drawing) return; viaMouse = true; begin(); pts.push(pointAt(e)); });
    const up = () => { if (drawing && viaMouse) { viaMouse = false; end(); } };
    window.addEventListener("pointerup", up);
    const isCtrl = (e: KeyboardEvent) => e.code === "ControlLeft" || e.code === "ControlRight";
    const kd = (e: KeyboardEvent) => {
      if (!isCtrl(e) || e.repeat || STEPS[step] !== "circle" || drawing) return;
      const now = performance.now();
      if (now - lastTap < 700) { lastTap = 0; viaMouse = false; begin(); } else downAt = now;
    };
    const ku = (e: KeyboardEvent) => {
      if (!isCtrl(e) || STEPS[step] !== "circle") return;
      if (drawing && !viaMouse) { end(); return; }
      if (!drawing && performance.now() - downAt < 400) { lastTap = performance.now(); st.textContent = "Good. Now press Ctrl again and hold it."; st.classList.remove("ok"); }
    };
    const blur = () => { if (drawing) { drawing = false; reset(); } };
    window.addEventListener("keydown", kd, true); window.addEventListener("keyup", ku, true); window.addEventListener("blur", blur);
    onLeave(() => { window.removeEventListener("keydown", kd, true); window.removeEventListener("keyup", ku, true); window.removeEventListener("blur", blur); window.removeEventListener("pointerup", up); });

    const on = h("div", { class: "wz-row" }, h("div", { class: "wz-row-copy" }, h("strong", { text: "Circle to ask" }), h("small", { text: "Works anywhere on your screen. Switch it off if you never want it. You can change it any time in Settings → Features." })),
      toggle(choices.features["circle_ask"] !== false, (v) => { choices.features["circle_ask"] = v; }));
    main.append(
      title("Circle it, then ask"), sub("See something on your screen you want to know about? Circle it and Lyra tells you what it is."),
      stage,
      hint("Tap", key("Ctrl"), "then press", key("Ctrl"), "again and hold it while you circle. Let go to ask."), st,
      h("div", { class: "wz-list narrow" }, on));
  };

  const pageLook = () => {
    const grid = h("div", { class: "wz-themes" });
    let mochis: Character[] = [];
    const renderThemes = () => {
      mochis.forEach((c) => c.destroy()); mochis = []; watchers.forEach((w) => { if (!w.host.isConnected) watchers.delete(w); });
      clear(grid);
      for (const t of THEMES) {
        const sel = choices.theme === t.id;
        const pv = themePreview(t, choices.shape); mochis.push(pv.mochi);
        const b = h("button", { class: "wz-theme" + (sel ? " on" : ""), type: "button" },
          pv.el, h("strong", { text: t.name }), h("small", { text: t.note }));
        b.addEventListener("click", () => {
          choices.theme = t.id;
          ctx.theme.write(t.id);                                          // your real island changes right now
          try { window.localStorage.setItem("lyra-theme-preview", `${t.id}|${Date.now()}`); } catch { /* ignore */ }
          renderThemes();
        });
        grid.append(b);
      }
    };
    onLeave(() => { mochis.forEach((c) => c.destroy()); mochis = []; });
    renderThemes();

    const mats = h("div", { class: "wz-mats" });
    const renderMats = () => {
      clear(mats);
      for (const mt of MATERIALS) {
        const b = h("button", { class: "wz-mat" + (choices.material === mt.id ? " on" : ""), type: "button" },
          h("div", { class: "wz-mat-prev" }, islandScene({ shape: choices.position === "attached" ? "notch" : "island", material: mt.id, state: "rest" })), h("strong", { text: mt.name }));
        b.addEventListener("click", () => { choices.material = mt.id; writeExtras({ compactStyle: mt.id }); renderMats(); });
        mats.append(b);
      }
    };
    renderMats();
    main.append(title("Pick your Lyra look"), sub("The theme styles the expanded island and the tab dock. Your real island changes as you pick."), grid,
      h("div", { class: "wz-section", text: "Small island" }), h("p", { class: "wz-small tight", text: "Black, Frost or Glass for the small island, whatever the theme." }), mats);
  };

  const pageIsland = () => {
    const wrap = h("div", { class: "wz-two" });
    let mochis: Character[] = [];
    const mk = (kind: "floating" | "attached", name: string, note: string) => {
      const sel = choices.position === kind;
      const m = h("span", { class: "wz-posmochi" });
      const b = h("button", { class: "wz-pos" + (sel ? " on" : ""), type: "button" },
        h("div", { class: `wz-posdesk ${kind}` }, h("span", { class: "wz-posisl" }, m)),
        h("strong", { text: name }), h("small", { text: note }));
      m.style.width = "26px";
      const ch = createCharacter(m, { tight: true, sharp: 2 }); ch.setShape?.(choices.shape); ch.setEmote("idle"); mochis.push(ch);
      b.addEventListener("click", () => {
        choices.position = kind; ctx.position.write(kind);
        writeExtras({ shapeNotchless: kind === "attached" ? "notch" : "island" });
        renderAll();
      });
      return b;
    };
    const pick = (value: string, onPick: (v: string) => void, options: Array<[string, string]>): HTMLSelectElement => {
      const sel = h("select", { class: "wz-select" }) as HTMLSelectElement;
      for (const [v, label] of options) sel.append(h("option", { value: v, text: label }));
      sel.value = value;
      sel.addEventListener("change", () => onPick(sel.value));
      return sel;
    };
    const seg = h("div", { class: "wz-seg", role: "group" });
    const paintSeg = () => { for (const b of Array.from(seg.children) as HTMLElement[]) b.classList.toggle("on", b.dataset.id === choices.autoHide); };
    for (const m of AUTOHIDE_MODES) {
      const b = h("button", { class: "wz-seg-btn", type: "button", text: m.label });
      b.dataset.id = m.id;
      b.addEventListener("click", () => { choices.autoHide = m.id; paintSeg(); });
      seg.append(b);
    }
    paintSeg();
    // Which screen the island lives on: big choice cards right under Floating / Attached (the list grows by one card per extra monitor).
    const screens = h("div", { class: "wz-screens" });
    const screenOpts: Array<{ id: string; name: string; note: string }> = [
      { id: "primary", name: "Main display", note: "Always on your main screen." },
      { id: "cursor", name: "Follows the cursor", note: "Appears on the screen you are using." },
    ];
    const paintScreens = () => {
      clear(screens);
      for (const o of screenOpts) {
        const sel = choices.screen === o.id;
        const b = h("button", { class: "wz-screen" + (sel ? " on" : ""), type: "button" },
          h("span", { class: "wz-screen-ico", html: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>' }),
          h("strong", { text: o.name }), h("small", { text: o.note }));
        b.addEventListener("click", () => { choices.screen = o.id; paintScreens(); });
        screens.append(b);
      }
    };
    paintScreens();
    void (async () => {                                           // with two or more screens each one can be picked, like in Settings → Island
      try {
        const { availableMonitors } = await import("@tauri-apps/api/window");
        const mons = await availableMonitors();
        if (mons.length < 2) return;
        mons.forEach((m, i) => screenOpts.push({ id: `display:${i + 1}`, name: `Screen ${i + 1}${m.position.x === 0 && m.position.y === 0 ? " (main)" : ""}`, note: `${m.size.width}×${m.size.height}` }));
        paintScreens();
      } catch { /* not running inside Lyra: the two basic choices stay */ }
    })();
    const setRow = (name: string, note: string, control: HTMLElement): HTMLElement =>
      h("div", { class: "wz-row" }, h("div", { class: "wz-row-copy" }, h("strong", { text: name }), h("small", { text: note })), control);
    const behave = h("div", { class: "wz-list narrow" },
      setRow("Auto-hide", `${AUTOHIDE_HINT} When the island is hidden, rest the pointer on the top centre of the screen for a moment and it comes back.`, seg),
      setRow("Wake gesture", "How to call the hidden island from the top edge. Hold keeps the top of the screen clickable.",
        pick(choices.wake, (v) => { choices.wake = v === "instant" ? "instant" : "dwell"; }, [["dwell", "Hold the pointer on the top edge"], ["instant", "Instant (any hover)"]])));
    const renderAll = () => { mochis.forEach((c) => c.destroy()); mochis = []; clear(wrap); wrap.append(mk("floating", "Floating", "A small gap above the island."), mk("attached", "Attached", "Flush with the top edge, like a notch.")); };
    onLeave(() => { mochis.forEach((c) => c.destroy()); mochis = []; });
    renderAll();
    main.append(title("Where should it sit?"), sub("Floating keeps a little breathing room above the island. Attached glues it to the top of the screen."), wrap,
      h("div", { class: "wz-section", text: "Which screen?" }), screens, behave);
  };

  const pageReplies = () => {
    const wrap = h("div", { class: "wz-replies" });
    const paint = () => {
      clear(wrap);
      for (const r of REPLY_MODES) {
        const sel = choices.reply === r.id;
        const m = h("span", { class: "wz-isl-mochi" });
        const badge = h("span", { class: "wz-reply-badge" },
          r.id !== "speak" ? icon(ICON_WAVE) : null, r.id !== "mochi" ? icon(ICON_VOICE) : null);
        const sublt = h("span", { class: "wz-reply-sub", text: "Opening Spotify" });
        const isl = h("div", { class: "wz-isl reply" }, m, sublt, badge);
        const ch = spawn(m, 36); ch.setEmote("wink");
        const b = h("button", { class: "wz-reply" + (sel ? " on" : ""), type: "button" },
          h("div", { class: "wz-reply-stage" }, isl), h("strong", { text: r.name }), h("small", { text: r.note }));
        b.addEventListener("click", () => {
          choices.reply = r.id; voicePrefs.set("mochi_sound_mode", r.id);
          // Hear exactly what this choice does: the Mochi sound, Lyra's own voice, or the sound and then the voice.
          // (Voice goes through the backend's "Test voice", so it is the real voice, speed and engine.)
          const sayIt = () => { void fetch(`${lyra}/voice/test`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: "Opening Spotify" }) }).catch(() => undefined); };
          try { if (r.id !== "speak") playMochi("opened"); } catch { /* ignore */ }
          if (r.id === "speak") sayIt();
          else if (r.id === "both") window.setTimeout(sayIt, 650);
          paint();
          // the island of the card you clicked plays: it pops, and the subtitle and badge light up while it sounds
          const el = wrap.children[REPLY_MODES.findIndex((x) => x.id === r.id)]?.querySelector(".wz-isl");
          if (el) { el.classList.add("playing"); window.setTimeout(() => el.classList.remove("playing"), 1900); }
        });
        wrap.append(b);
      }
    };
    paint();
    main.append(title("How should short replies sound?"), sub("For quick answers like “Done” or “Opening Spotify”, Lyra can show a subtitle on the island with a Mochi sound instead of talking. Longer answers are always spoken."), wrap,
      h("p", { class: "wz-small", text: "Click one to hear it: the Mochi sound, Lyra’s voice, or both. You can change this any time in Settings → Voice." }));
  };

  const pageMochi = () => {
    // a big Mochi you can poke, and a row of every character
    const bigHost = h("div", { class: "wz-mochi-big" });
    const bubble = h("div", { class: "wz-bubble", text: "Poke me" });
    const poked = h("div", { class: "wz-status", text: "Tap your Mochi a few times. Each one gets angry in its own way." });
    let big: Character | null = null;
    const mount = () => {
      big?.destroy(); watchers.forEach((w) => { if (w.ch === big) watchers.delete(w); });
      clear(bigHost);
      const slot = h("div", { class: "wz-mochi-slot" });
      bigHost.append(slot);
      slot.style.width = "150px";
      big = createCharacter(slot, { tight: true, sharp: 2 });
      big.setShape?.(choices.shape);
      big.setEmote("idle");
      watchers.add({ host: slot, ch: big });
    };
    mount();
    onLeave(() => { big?.destroy(); watchers.forEach((w) => { if (w.ch === big) watchers.delete(w); }); });
    let lvl = 0, last = 0;
    bigHost.addEventListener("click", () => {
      const now = performance.now();
      lvl = now - last > 4200 ? 1 : Math.min(6, lvl + 1);
      last = now;
      big?.provoke?.(lvl);
      const line = RAGE_LINES[choices.shape]?.[lvl - 1];
      bubble.textContent = line || "…";
      bubble.classList.add("show");
      window.setTimeout(() => { if (performance.now() - last > 1300) bubble.classList.remove("show"); }, 1500);
      poked.textContent = lvl >= 6 ? "That’s as angry as it gets." : `Poked ${lvl}×`;
    });
    const grid = h("div", { class: "wz-mochi-grid" });
    for (const id of CHARACTER_IDS) {
      const host = h("div", { class: "wz-mochi-tile-host" });
      const t = h("button", { class: "wz-mochi-tile" + (id === choices.shape ? " on" : ""), type: "button", "aria-label": CHAR_DEFS[id].label }, host, h("span", { text: CHAR_DEFS[id].label }));
      host.style.width = "52px";
      const ch = createCharacter(host, { tight: true, sharp: 2 });
      ch.setShape?.(id); ch.setEmote("idle");
      onLeave(() => ch.destroy());
      new IntersectionObserver((e) => ch.setActive?.(e.some((x) => x.isIntersecting))).observe(host);
      t.addEventListener("click", () => {
        choices.shape = id;
        featPrefs.set("mascot_shape", id);                              // your real Mochi follows right away
        grid.querySelectorAll(".wz-mochi-tile").forEach((n) => n.classList.remove("on"));
        t.classList.add("on");
        lvl = 0; bubble.classList.remove("show"); poked.textContent = `${CHAR_DEFS[id].label}. Give it a poke.`;
        mount();
      });
      grid.append(t);
    }
    const sw = (id: string, name: string, note: string) => {
      const f = FEATURES.find((x) => x.id === id);
      if (!f) return null;
      return h("div", { class: "wz-row" }, h("div", { class: "wz-row-copy" }, h("strong", { text: name }), h("small", { text: note })),
        toggle(choices.features[id], (v) => { choices.features[id] = v; ctx.applyFeature(f, v, featPrefs); }));
    };
    main.append(title("Choose your Mochi"), sub("A little character that lives in the island and can hang out on your screen."),
      h("div", { class: "wz-mochi-stage" }, bubble, bigHost), poked, grid,
      h("div", { class: "wz-list narrow" }, sw("mascot", "Show it on my screen", "Next to the subtitles, following your cursor."), sw("mascot_stay", "Keep it on screen", "Otherwise it goes back to the island when idle.")));
  };

  const pageWheel = () => {
    const wf = FEATURES.find((f) => f.id === "tab_wheel");
    const done = { open: false, pick: false };
    const r1 = h("div", { class: "wz-task" }, h("span", { class: "wz-task-dot" }), h("span", { class: "wz-task-copy" }, h("strong", { text: "Hold Ctrl + CapsLock" }), h("small", { text: "A wheel of tabs appears right at your cursor." })));
    const r2 = h("div", { class: "wz-task" }, h("span", { class: "wz-task-dot" }), h("span", { class: "wz-task-copy" }, h("strong", { text: "Point at a tab and let go" }), h("small", { text: "The island opens that tab." })));
    const paint = () => {
      r1.classList.toggle("done", done.open); (r1.firstChild as HTMLElement).textContent = done.open ? "✓" : "";
      r2.classList.toggle("done", done.pick); (r2.firstChild as HTMLElement).textContent = done.pick ? "✓" : "";
    };
    void onTourSignal((s) => {
      if (STEPS[step] !== "wheel") return;
      if (s.kind === "wheel-open") done.open = true;
      if (s.kind === "wheel-pick") { done.open = true; done.pick = true; }
      paint();
    });
    main.append(title("Open any tab with the wheel"), sub("Hold Ctrl + CapsLock anywhere. Point at a group, then at a tab, and let go. You can also right-click the island."),
      h("div", { class: "wz-demo" }, wf ? featurePreview(wf) : null),
      h("div", { class: "wz-tasks" }, r1, r2), h("p", { class: "wz-small", text: "Optional: this works once Lyra is running. You can skip it." }));
  };

  /** How a message pop-up looks in the notch (Message pop-ups, on the helpers page). */
  const notifDemo = (): HTMLElement => {
    const apps = [
      { app: "WhatsApp", who: "Alex", text: "Lunch at 1?", color: "linear-gradient(135deg,#3fd16f,#13a04a)", letter: "W" },
      { app: "Discord", who: "Study group", text: "Anyone free to review chapter 3?", color: "linear-gradient(135deg,#7a8bff,#4a56d6)", letter: "D" },
      { app: "Telegram", who: "Sam", text: "See you at 6!", color: "linear-gradient(135deg,#4cc2ff,#1a8ad6)", letter: "T" },
    ];
    const toast = h("div", { class: "wz-toast" });
    const m = h("span", { class: "wz-isl-mochi" });
    const isl = h("div", { class: "wz-isl note" }, m, toast);
    spawn(m, 34);
    let i = 0;
    const show = () => {
      const a = apps[i % apps.length]; i++;
      isl.classList.remove("in"); void isl.offsetWidth;
      clear(toast);
      toast.append(h("span", { class: "wz-toast-ico", style: `background:${a.color}`, text: a.letter }), h("span", { class: "wz-toast-copy" }, h("b", { text: `${a.who} · ${a.app}` }), h("small", { text: a.text })));
      isl.classList.add("in");
    };
    show();
    const iv = window.setInterval(show, 3600);
    onLeave(() => window.clearInterval(iv));
    return h("div", { class: "wz-notif" }, h("div", { class: "wz-notif-desk" }, isl), h("div", { class: "wz-notif-copy" },
      h("strong", { text: "Message pop-ups" }), h("small", { text: "New Discord, WhatsApp and Telegram messages appear in the notch, then fold away." })));
  };

  const pageFeatures = (group: FeatureDef["group"]) => {
    const g = FEATURE_GROUPS.find((x) => x.id === group)!;
    const list = h("div", { class: "wz-fgrid" });
    for (const f of FEATURES.filter((x) => x.group === group && !x.soon && !OWN_PAGE_IDS.has(x.id))) {
      const on = choices.features[f.id];
      const c = h("div", { class: "wz-fcard" + (on ? "" : " off") },
        h("div", { class: "wz-fprev" }, featurePreview(f)),
        h("div", { class: "wz-fbody" },
          h("div", { class: "wz-fhead" }, h("strong", { text: f.name }),
            toggle(on, (v) => { choices.features[f.id] = v; c.classList.toggle("off", !v); })),
          h("span", { class: "wz-fblurb", text: f.blurb }),
          h("em", { class: "wz-ftip", text: `Try: ${f.tip}` })));
      list.append(c);
    }
    main.append(title(g.title), sub(`${g.sub} Switch off anything you don’t want. You can change it any time in Settings → Features.`),
      h("div", { class: "wz-scroll" }, group === "helpers" ? notifDemo() : null, list),
      h("p", { class: "wz-small", text: group === "helpers" ? "Background helpers start when Lyra restarts, which happens when you finish." : "" }));
  };

  // ── Screen recording: a preview of the red pill, then the few things worth setting up (recorder, quality, folder, OBS) ──
  const pageRecord = () => {
    // The preview: the island while you say "start / pause / stop recording".
    const seq: Array<{ say: string; label: string; cls: string; run: boolean }> = [
      { say: "Start recording", label: "Recording", cls: "", run: true },
      { say: "Pause recording", label: "Paused", cls: "paused", run: false },
      { say: "Stop recording", label: "Saved", cls: "done", run: false },
    ];
    const fmt = (n: number) => `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`;
    const tm = h("span", { class: "wz-rec-time", text: "0:00" });
    const lbl = h("span", { class: "wz-rec-lbl" });
    const isl = h("div", { class: "wz-rec-isl" }, h("i", { class: "wz-rec-dot" }), tm, lbl, h("i", { class: "wz-rec-pause" }), h("i", { class: "wz-rec-stop" }));
    const say = h("div", { class: "wz-rec-say" });
    let idx = 0, secs = 0, running = false;
    const apply = () => {
      const st = seq[idx];
      say.textContent = `Say “${st.say}”`;
      lbl.textContent = st.label;
      isl.className = `wz-rec-isl ${st.cls}`;
      running = st.run;
      if (idx === 0) { secs = 0; tm.textContent = fmt(0); }
    };
    apply();
    const tick = window.setInterval(() => { if (running) { secs++; tm.textContent = fmt(secs); } }, 1000);
    const cyc = window.setInterval(() => { idx = (idx + 1) % seq.length; apply(); }, 2800);
    onLeave(() => { window.clearInterval(tick); window.clearInterval(cyc); });
    const demo = h("div", { class: "wz-rec" }, h("div", { class: "wz-rec-desk" }, isl), say);

    // The setup. Recorder, quality and folder are saved the moment you change them (they live in Lyra's recording settings).
    const post = async (path: string, body: Record<string, unknown> = {}) => {
      try {
        const r = await fetch(`${lyra}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        return (await r.json()) as { ok?: boolean; message?: string; error?: string; folder?: string };
      } catch { return { ok: false, error: "Lyra isn’t running yet, so this can be set later in Settings → Recording." }; }
    };
    const row = (name: string, note: string, ...controls: Node[]) =>
      h("div", { class: "wz-row" }, h("div", { class: "wz-row-copy" }, h("strong", { text: name }), h("small", { text: note })), ...controls);
    const pickOf = (label: string, options: Array<[string, string]>, onPick: (v: string) => void): HTMLSelectElement => {
      const sel = h("select", { class: "wz-select", "aria-label": label }) as HTMLSelectElement;
      for (const [v, t] of options) sel.append(h("option", { value: v, text: t }));
      sel.addEventListener("change", () => onPick(sel.value));
      return sel;
    };

    const obsNote = status();
    const obsBtn = h("button", { class: "wz-testbtn", type: "button", text: "Check OBS" }) as HTMLButtonElement;
    obsBtn.addEventListener("click", async () => {
      obsBtn.disabled = true; obsNote.classList.remove("ok"); obsNote.textContent = "Connecting to OBS…";
      const r = await post("/record/obs-test");
      obsNote.textContent = r.message ?? r.error ?? (r.ok ? "Connected." : "Couldn’t connect.");
      obsNote.classList.toggle("ok", !!r.ok);
      obsBtn.disabled = false;
    });
    const obsRow = row("Connect OBS", "Lyra records through OBS Studio. In OBS open Tools → WebSocket Server Settings and tick “Enable WebSocket server”. Leave the port as it is.", obsBtn);

    const showObs = (v: string) => { obsRow.style.display = v === "obs" ? "" : "none"; obsNote.style.display = v === "obs" ? "" : "none"; };
    const backend = pickOf("Recorder", [["obs", "OBS Studio (recommended)"], ["lyra", "Built-in (experimental)"]], (v) => {
      showObs(v);
      void post("/settings/recordings", { record_backend: v });
    });
    const quality = pickOf("Quality", [["obs", "OBS settings"], ["high", "High · 1080p · 60 fps"], ["medium", "Medium · 720p · 30 fps"], ["low", "Low · 480p · 30 fps"]],
      (v) => void post("/settings/recordings", { obs_quality: v }));

    const where = h("small", { text: "Videos\\Lyra Recordings" });
    const change = h("button", { class: "wz-testbtn", type: "button", text: "Change…" }) as HTMLButtonElement;
    change.addEventListener("click", async () => {
      change.disabled = true;
      const r = await post("/record/folder", { action: "pick" });
      if (r.ok !== false && r.folder) where.textContent = r.folder;
      change.disabled = false;
    });

    void fetch(`${lyra}/settings/recordings`).then((r) => (r.ok ? r.json() : null)).then((j: Record<string, unknown> | null) => {
      if (!j) return;
      backend.value = j.record_backend === "lyra" ? "lyra" : "obs";
      showObs(backend.value);
      const q = String(j.obs_quality ?? "obs");
      quality.value = ["obs", "high", "medium", "low"].includes(q) ? q : "obs";
    }).catch(() => undefined);
    void fetch(`${lyra}/record/folder`).then((r) => (r.ok ? r.json() : null)).then((j: { folder?: string } | null) => { if (j?.folder) where.textContent = j.folder; }).catch(() => undefined);

    const list = h("div", { class: "wz-list narrow" },
      row("Screen recording", "Adds the Record tab to the island and the voice commands.", toggle(choices.features.record !== false, (v) => { choices.features.record = v; })),
      row("Recorder", "OBS gives the smoothest result. The built-in recorder needs no OBS but is experimental.", backend),
      obsRow,
      row("Quality", "OBS settings means Lyra leaves OBS’s own video settings alone.", quality),
      h("div", { class: "wz-row" }, h("div", { class: "wz-row-copy" }, h("strong", { text: "Save recordings in" }), where), change));
    main.append(title("Record your screen", "Record"), sub("One tap on the island or one phrase, and Lyra records your screen. Pause, resume and stop the same way."),
      h("div", { class: "wz-scroll" }, demo, list, obsNote),
      h("p", { class: "wz-small", text: "You can change all of this later in Settings → Recording." }));
  };

  const pageWhatsApp = () => {
    const box = h("div", { class: "wz-wa" });
    const stat = h("div", { class: "wz-wa-status", text: "Checking…" });
    let timer = 0, enabled = true, busy = false;
    const paint = (s: { connected: boolean; account: string; qr?: AppView; down?: boolean; off?: boolean }) => {
      clear(box);
      if (s.down) { box.append(h("p", { class: "wz-small", text: "Lyra isn’t running yet, so WhatsApp can’t be linked now. You can do it later in Settings → Apps." })); return; }
      if (s.off) { box.append(h("p", { class: "wz-small", text: "WhatsApp is off. Turn it on above to link your phone." })); return; }
      if (s.connected) { box.append(h("div", { class: "wz-wa-ok" }, h("span", { text: "✓" }), h("div", {}, h("strong", { text: "WhatsApp is connected" }), h("small", { text: s.account ? `Linked as ${s.account}. Your chats show up in the island’s Apps tab.` : "Your chats show up in the island’s Apps tab." })))); return; }
      const steps = ["Open WhatsApp on your phone", "Settings → Linked devices → Link a device", "Point the phone at this code"];
      box.append(h("div", { class: "wz-wa-qr" },
        s.qr?.image ? h("img", { src: s.qr.image, alt: "QR code to scan", width: 188, height: 188 }) : h("div", { class: "wz-wa-wait", text: s.qr?.text || "Getting the code…" }),
        h("div", { class: "wz-wa-steps" }, h("strong", { text: "Scan to link" }), h("ol", {}, ...steps.map((t) => h("li", { text: t }))), h("small", { text: "The code refreshes by itself. This turns to a tick when it works." }))));
    };
    const poll = async () => {
      if (busy) return; busy = true;
      try {
        const data = await fetchApps();
        if (!data) { paint({ connected: false, account: "", down: true }); stat.textContent = ""; return; }
        const wa = data.apps.find((a) => a.id === "whatsapp");
        if (!enabled || !wa) { paint({ connected: false, account: "", off: true }); return; }
        if (wa.connected) { paint({ connected: true, account: wa.account }); stat.textContent = "Connected"; return; }
        stat.textContent = "Waiting for you to scan…";
        const res = await appsPost<{ ok: boolean; view?: AppView }>("/apps/view", { id: "whatsapp", route: "" });
        paint({ connected: false, account: "", qr: res?.view });
      } finally { busy = false; }
    };
    const row = h("div", { class: "wz-row" }, h("div", { class: "wz-row-copy" }, h("strong", { text: "Use WhatsApp with Lyra" }), h("small", { text: "Read and answer your chats from the notch, with a pop-up for every new message." })),
      toggle(true, (v) => { enabled = v; void fetch(`${lyra}/plugins/toggle`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: "WhatsApp", enabled: v }) }).then(() => poll()).catch(() => undefined); }));
    void fetch(`${lyra}/plugins`).then((r) => (r.ok ? r.json() : null)).then((d) => {
      const wa = (d?.plugins as Array<{ name: string; enabled: boolean }> | undefined)?.find((p) => p.name === "WhatsApp");
      if (wa) { enabled = wa.enabled; const sw = row.querySelector<HTMLElement>(".wz-switch"); sw?.classList.toggle("on", wa.enabled); sw?.setAttribute("aria-checked", String(wa.enabled)); }
    }).catch(() => undefined).finally(() => void poll());
    timer = window.setInterval(() => void poll(), 2600);
    onLeave(() => window.clearInterval(timer));
    main.append(h("div", { class: "wz-wa-logo", text: "W" }), title("Link WhatsApp"), sub("Optional. Link your phone once and Lyra shows your chats and new messages in the island."),
      h("div", { class: "wz-list narrow" }, row), box, stat);
  };

  const pageModels = () => {
    interface Row { bar: HTMLElement; fill: HTMLElement; text: HTMLElement; btn: HTMLButtonElement; cancel?: HTMLButtonElement; pill: HTMLElement; note: HTMLElement }
    const mkRow = (name: string, desc: string, withCancel: boolean): { el: HTMLElement; r: Row } => {
      const pill = h("span", { class: "wz-pill", text: "Checking…" });
      const note = h("div", { class: "wz-model-note" });
      const fill = h("div", { class: "wz-bar-fill" });
      const bar = h("div", { class: "wz-bar" }, fill);
      const text = h("span", { class: "wz-model-text" });
      const btn = h("button", { class: "wz-testbtn", type: "button", text: "Download" }) as HTMLButtonElement;
      const cancel = withCancel ? h("button", { class: "wz-linkbtn", type: "button", text: "Cancel" }) as HTMLButtonElement : undefined;
      const el = h("div", { class: "wz-model" }, h("div", { class: "wz-model-head" }, h("div", { class: "wz-model-name" }, h("strong", { text: name }), h("small", { text: desc })), pill),
        h("div", { class: "wz-model-body" }, bar, text, btn, cancel ?? null), note);
      return { el, r: { bar, fill, text, btn, cancel, pill, note } };
    };
    const stt = mkRow(replay ? "Speech recognition" : "Speech recognition (required)", "Understands what you say. Runs on this PC, no audio is sent anywhere. About 700 MB, one time.", true);
    const tts = mkRow("Offline voice (optional)", "Lets Lyra speak without internet. Otherwise she uses Microsoft’s online voice. About 350 MB, one time.", false);
    const useOff = h("div", { class: "wz-row slim" }, h("div", { class: "wz-row-copy" }, h("strong", { text: "Speak with the offline voice" }), h("small", { text: "Available once it is downloaded." })),
      toggle(choices.ttsEngine === "offline", (v) => { choices.ttsEngine = v ? "offline" : "edge"; voicePrefs.set("tts_engine", choices.ttsEngine); }));
    useOff.style.display = "none";

    const paint = (r: Row, s: { ready: boolean; busy: boolean; pct: number; text: string; note?: string; none?: boolean }) => {
      r.pill.textContent = s.none ? "Lyra isn’t running" : s.ready ? "Ready ✓" : s.busy ? "Downloading…" : "Not downloaded";
      r.pill.classList.toggle("ok", s.ready); r.pill.classList.toggle("busy", s.busy);
      r.bar.style.display = s.busy ? "" : "none"; r.fill.style.width = `${Math.round(s.pct * 100)}%`;
      r.text.textContent = s.busy ? s.text : "";
      r.btn.style.display = !s.ready && !s.busy && !s.none ? "" : "none";
      if (r.cancel) r.cancel.style.display = s.busy ? "" : "none";
      r.note.textContent = s.note || "";
    };
    const getJson = async (path: string) => { try { const r = await fetch(`${lyra}${path}`); return r.ok ? await r.json() : null; } catch { return null; } };
    const post = async (path: string) => { try { await fetch(`${lyra}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); } catch { /* shown by the poll */ } };
    const refresh = async () => {
      const s = await getJson("/p1/voice/status");
      if (!s) { sttReady = false; paint(stt.r, { ready: false, busy: false, pct: 0, text: "", none: true }); }
      else {
        const p = s.parakeet || {};
        downloading.stt = !!s.downloading;
        // Ready when installed. If the speech package itself is missing the app falls back to Whisper, so never trap the person here.
        sttReady = !!p.installed || p.available === false;
        paint(stt.r, { ready: !!p.installed, busy: !!s.downloading, pct: s.progress || 0, text: p.bytes_total ? `${mb(p.bytes_done)} of ${mb(p.bytes_total)}${p.state === "verifying" ? " · verifying" : ""}` : "Starting…",
          note: s.error || p.error || (p.available === false ? "The speech package isn’t installed, so Whisper is used instead." : "") });
      }
      const o = await getJson("/voice/offline");
      if (!o) paint(tts.r, { ready: false, busy: false, pct: 0, text: "", none: true });
      else {
        downloading.tts = !!o.downloading;
        paint(tts.r, { ready: !!o.installed, busy: !!o.downloading, pct: o.total ? o.done / o.total : 0, text: `${mb(o.done || 0)} of ${mb(o.total || 0)}`, note: o.error ? `Download failed: ${o.error}` : "" });
        useOff.style.display = o.installed ? "" : "none";
      }
      next.disabled = !canContinue();
      gate.textContent = replay || sttReady ? "" : "Download the speech model to continue. It is the part that makes Lyra understand you well.";
    };
    const gate = h("p", { class: "wz-error" });
    stt.r.btn.addEventListener("click", async () => { stt.r.btn.disabled = true; await post("/p1/voice/download"); stt.r.btn.disabled = false; void refresh(); });
    stt.r.cancel?.addEventListener("click", async () => { await post("/p1/voice/cancel"); void refresh(); });
    tts.r.btn.addEventListener("click", async () => { tts.r.btn.disabled = true; await post("/voice/offline/download"); tts.r.btn.disabled = false; void refresh(); });
    void refresh();
    const iv = window.setInterval(() => void refresh(), 1000);
    onLeave(() => window.clearInterval(iv));
    main.append(title("Download the voice models"), sub(replay ? "Lyra listens and speaks on this PC. These are one-time downloads. Keep this window open while they run." : "Lyra listens on this PC, so speech recognition has to be downloaded once before anything else. Keep this window open while it runs."),
      h("div", { class: "wz-models" }, stt.el, tts.el, useOff), gate, h("p", { class: "wz-small", text: replay ? "The offline voice is optional; you can also download it later in Settings → Voice. Finishing the setup restarts Lyra and stops a running download." : "The offline voice is optional and can be downloaded later in Settings → Voice. Finishing the setup restarts Lyra and stops a running download." }));
  };

  const pageKeys = () => {
    const list = h("div", { class: "wz-providers" });
    const paintKeys = () => {
      clear(list);
      for (const p of PROVIDERS) {
        const local = p.id === "ollama";
        const used = choices.provider === p.id;
        const st = keyState[p.id];
        const input = h("input", { class: "wz-input", type: "password", placeholder: p.placeholder, autocomplete: "off", spellcheck: "false", value: choices.keys[p.key], "aria-label": `${p.name} API key` }) as HTMLInputElement;
        input.addEventListener("input", () => { choices.keys[p.key] = input.value; keyState[p.id] = { text: input.value ? "Not tested yet" : "", ok: null }; next.disabled = !canContinue(); syncKeysButton(); const s = card.querySelector(`[data-st="${p.id}"]`); if (s) s.textContent = keyState[p.id].text; });
        const open = h("button", { class: "wz-linkbtn", type: "button" }, local ? "Download Ollama" : `Get a ${p.name} key`, icon(ICON_LINK));
        open.addEventListener("click", () => { void Bridge.openUrl(p.url).catch(() => window.open(p.url, "_blank")); });
        const test = h("button", { class: "wz-testbtn", type: "button", text: "Test" }) as HTMLButtonElement;
        test.addEventListener("click", async () => {
          if (!local && !choices.keys[p.key].trim() && !hasSavedKey) { keyState[p.id] = { text: "Paste a key first", ok: false }; paintKeys(); return; }
          test.disabled = true; test.textContent = "Testing…";
          try {
            // The backend tests the saved key, so save what is typed first (blank fields keep the saved key).
            await fetch(`${lyra}/settings/llm`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: p.id, ...choices.keys }) });
            const r = await fetch(`${lyra}/settings/llm/test`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: p.id }) });
            const d = await r.json().catch(() => ({}));
            keyState[p.id] = { text: d.ok ? `Works${d.ms ? ` · ${d.ms} ms` : ""}` : String(d.message || "That did not work").slice(0, 90), ok: !!d.ok };
            if (d.ok) { hasSavedKey = true; if (!used) choices.provider = p.id; }
          } catch { keyState[p.id] = { text: "Could not reach Lyra. Is it running?", ok: false }; }
          paintKeys(); next.disabled = !canContinue(); syncKeysButton();
        });
        const stEl = h("span", { class: "wz-pstatus" + (st?.ok === true ? " ok" : st?.ok === false ? " bad" : ""), "data-st": p.id, text: st?.text || "" });
        const use = h("button", { class: "wz-use" + (used ? " on" : ""), type: "button", text: used ? "In use" : "Use this" });
        use.addEventListener("click", () => { choices.provider = p.id; paintKeys(); next.disabled = !canContinue(); syncKeysButton(); });
        list.append(h("div", { class: "wz-prov" + (used ? " on" : "") },
          h("div", { class: "wz-prov-head" }, h("span", { class: "wz-prov-mark", text: p.name.slice(0, 1) }),
            h("div", { class: "wz-prov-name" }, h("strong", { text: p.name }), h("small", { text: p.note })), stEl, use),
          h("div", { class: "wz-prov-body" }, local ? h("span", { class: "wz-prov-steps", text: p.steps.join("  ·  ") }) : input, open, test),
          local ? null : h("ol", { class: "wz-prov-steps list" }, ...p.steps.map((s) => h("li", { text: s })))));
      }
    };
    paintKeys();
    main.append(title("Connect Lyra"),
      sub(hasSavedKey ? "You already have a key saved. Add another or just continue." : "Optional. Lyra uses an AI provider for answers and chat. Pick one, open its page, paste the key, and press Test. Keys stay on this PC."),
      h("div", { class: "wz-scroll" }, list),
      h("p", { class: "wz-small", text: "Don’t want a key right now? Press Skip for now. You can add one any time in Settings → AI." }),
      h("div", { class: "wz-error", text: finishError }));
    syncKeysButton();
  };

  const pagePrivacy = () => {
    const sel = h("select", { class: "wz-select", "aria-label": "Microphone" }) as HTMLSelectElement;
    for (const name of ["Default Microphone", ...micList.filter((n) => n !== "Default Microphone")]) {
      const o = h("option", { value: name, text: name === "Default Microphone" ? "Default microphone" : name }) as HTMLOptionElement;
      if (name === choices.mic) o.selected = true;
      sel.append(o);
    }
    sel.addEventListener("change", () => { choices.mic = sel.value; });
    const row = (t: string, s: string, c: Node) => h("div", { class: "wz-row" }, h("div", { class: "wz-row-copy" }, h("strong", { text: t }), h("small", { text: s })), c);
    const slot = h("div", { class: "wz-hero-mochi small" });
    spawn(slot, 92);
    main.append(h("div", { class: "wz-center top" }, slot, title("Private by design"), sub("Everything runs on this PC. Your voice never leaves it.")),
      h("div", { class: "wz-list" },
        row("Start with Windows", "Lyra is ready when you sit down.", toggle(choices.autostart, (v) => { choices.autostart = v; })),
        row("Microphone", "Which one Lyra listens on when you hold Right Ctrl.", sel)),
      h("p", { class: "wz-small", text: "Change these any time in Settings." }));
  };

  const pageReady = () => {
    const live = FEATURES.filter((f) => !f.soon);
    const on = live.filter((f) => choices.features[f.id]).length;
    const row = (k: string, t: string) => h("div", { class: "wz-way" }, key(k), h("span", { text: t }));
    main.append(title("You’re all set"), sub(`${on} of ${live.length} features are on. Here is how to reach Lyra.`),
      h("div", { class: "wz-ways" },
        row("Right Ctrl", "Hold it, talk, let go."),
        row("Left Alt", "Hold it and dictate into any app."),
        row("Ctrl + CapsLock", "Open the tab wheel at your cursor."),
        row("Ctrl + Win", "Open Chat and type to Lyra."),
        row("Tray icon", "Settings, pause Lyra, or quit. Replay this tour from Settings → Features.")),
      h("div", { class: "wz-error", text: finishError }));
    next.textContent = replay ? "Save changes" : "Finish setup";
  };

  // ── navigation ──
  const canContinue = (): boolean => {
    if (STEPS[step] === "models") return replay || sttReady;
    return true;                                                            // the API key page can be skipped: keys can be added later in Settings → AI
  };
  const hasKeyChoice = (): boolean => hasSavedKey || choices.provider === "ollama" || PROVIDERS.some((p) => p.id !== "ollama" && choices.keys[p.key].trim());
  const syncKeysButton = () => { if (STEPS[step] === "keys") next.textContent = hasKeyChoice() ? "Continue" : "Skip for now"; };

  const renderDots = () => {
    clear(dots);
    for (let i = 0; i < STEPS.length; i++) dots.append(h("i", { class: i === step ? "on" : "" }));
  };

  const render = () => {
    cleanup.forEach((fn) => fn()); cleanup = [];
    clear(main);
    const id = STEPS[step];
    card.dataset.step = id;
    next.textContent = "Continue";
    back.style.visibility = step === 0 ? "hidden" : "visible";
    // First setup cannot be skipped or closed. Only the tour replayed from Settings has Close.
    skip.style.display = replay ? "" : "none";
    skip.style.visibility = id === "ready" ? "hidden" : "visible";
    if (id === "welcome") pageWelcome();
    else if (id === "talk") pageTalk();
    else if (id === "commands") pageCommands();
    else if (id === "dictate") pageDictate();
    else if (id === "circle") pageCircle();
    else if (id === "look") pageLook();
    else if (id === "island") pageIsland();
    else if (id === "replies") pageReplies();
    else if (id === "mochi") pageMochi();
    else if (id === "wheel") pageWheel();
    else if (id === "f-island") pageFeatures("island");
    else if (id === "f-daily") pageFeatures("daily");
    else if (id === "f-pc") pageFeatures("pc");
    else if (id === "record") pageRecord();
    else if (id === "f-helpers") pageFeatures("helpers");
    else if (id === "whatsapp") pageWhatsApp();
    else if (id === "models") pageModels();
    else if (id === "keys") pageKeys();
    else if (id === "privacy") pagePrivacy();
    else pageReady();
    next.disabled = !canContinue();
    renderDots();
  };

  const finish = async () => {
    const typedKey = PROVIDERS.some((p) => p.id !== "ollama" && choices.keys[p.key].trim());
    const useLocal = choices.provider === "ollama";
    if (!replay && !sttReady) { step = STEPS.indexOf("models"); render(); return; }
    if ((downloading.stt || downloading.tts) && !forceFinish) {
      forceFinish = true; step = STEPS.indexOf("ready"); finishError = "A voice model is still downloading. Finishing restarts Lyra and stops it. Press Finish again to continue anyway."; render(); return;
    }
    next.disabled = true; next.textContent = "Saving…";

    ctx.position.write(choices.position);
    ctx.theme.write(choices.theme);
    ctx.settings.islandAutoHide = choices.autoHide;
    ctx.settings.wakeMode = choices.wake;
    ctx.settings.screen = choices.screen;
    ctx.settings.autostart = choices.autostart;
    await ctx.saveSettings();

    if (typedKey || useLocal) {
      let ok = false;
      try {
        const r = await fetch(`${lyra}/settings/llm`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider: choices.provider, ...choices.keys }) });
        ok = r.ok;
      } catch { ok = false; }
      if (!ok) { step = STEPS.indexOf("keys"); finishError = "Lyra could not save the API key. Make sure Lyra is running and try again."; render(); return; }
    }

    // Feature switches: island tabs apply now, helpers when the backend restarts below.
    for (const f of FEATURES) if (!f.soon) ctx.applyFeature(f, choices.features[f.id], featPrefs);
    featPrefs.set("mascot_shape", choices.shape);
    // Talk is Right Ctrl only (there is no wake word any more).
    voicePrefs.set("voice_activation_mode", "ptt");
    voicePrefs.set("audio_input", choices.mic);
    voicePrefs.set("mochi_sound_mode", choices.reply);
    voicePrefs.set("tts_engine", choices.ttsEngine);
    const flushed = await Promise.all([featPrefs.flush(), privacyPrefs.flush(), voicePrefs.flush()]);
    if (flushed[2]) { try { await fetch(`${lyra}/backend/restart`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); } catch { /* ignore */ } }
    window.removeEventListener("mousemove", onMove);
    ctx.markDone();
    window.setTimeout(() => location.reload(), 120);
  };

  next.addEventListener("click", () => { if (step < STEPS.length - 1) { step++; render(); } else void finish(); });
  back.addEventListener("click", () => { if (step > 0) { step--; render(); } });
  skip.addEventListener("click", () => {
    if (replay) { ctx.markDone(); location.reload(); }                       // first setup has no skip button at all
  });

  clear(root);
  root.append(shell);
  render();
}
