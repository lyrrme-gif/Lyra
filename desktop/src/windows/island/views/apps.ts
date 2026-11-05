// Apps: the island's tab for connected app plugins (WhatsApp chats). Setup and sign-in
// live in Settings → Apps; this tab only shows what an app has to say, drawn in the notch's own style from
// plain data the plugin sends (backend/plugins/apps.py).

import { visiblePoll, type VisiblePoll } from "../../../shared/core/visiblePoll";
import { h, svg } from "../../../shared/views/dom";
import { ICONS } from "../../../shared/views/icons";
import type { ViewHost } from "./views";
import { Bridge } from "../../../shared/core/bridge";
import { createCharacter, type Character } from "../../../shared/mascot/character";
import { getFeatures, onFeatures } from "../../../shared/core/featuresCache";
import {
  SETTINGS_GOTO_KEY, appsPost, appsUpload, fetchApps, mediaBlobUrl, peekMediaUrl,
  type AppAction, type AppInfo, type AppItem, type AppMedia, type AppMessage, type AppView,
} from "../../../shared/core/appsApi";

const iconPath = (name: string): string => (ICONS as Record<string, string>)[name] ?? ICONS.apps;

/** A steady colour per person, so a chat keeps its avatar colour everywhere. */
function hueOf(name: string): number {
  let n = 0;
  for (const ch of name) n = (n * 31 + ch.codePointAt(0)!) % 360;
  return n;
}
const initialOf = (name: string): string => (Array.from(name.trim())[0] ?? "?").toUpperCase();

function avatar(name: string, image = "", size = 34, square = false): HTMLElement {
  const el = h("span", { class: `ap-avatar${square ? " sq" : ""}`, style: `--s:${size}px;--hue:${hueOf(name)}` });
  if (image) el.append(h("img", { src: image, alt: "", loading: "lazy" }));
  else el.append(h("b", { text: initialOf(name) }));
  return el;
}

/** Dictation, not "talk to Lyra": Lyra types what you say into the focused box (the backend's dictation). */
function dictation(action: "toggle" | "stop" | "status"): Promise<boolean> {
  return fetch("http://127.0.0.1:8765/dictation/toggle", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) })
    .then((r) => r.json()).then((j: { active?: boolean }) => !!j.active).catch(() => false);
}
/** The mic in the chat bar and the search box: click, say it, and the words appear in that box. Click again (or stop talking) to finish. */
function micButton(target: HTMLInputElement): HTMLElement {
  const b = h("button", { class: "ap-micbtn", type: "button", title: "Dictate: writes what you say here", "aria-label": "Dictate into this box", "aria-pressed": "false" }, svg(ICONS.mic, 16, { stroke: 2 }));
  let on = false;
  let poll = 0;
  const set = (v: boolean) => {
    on = v;
    b.classList.toggle("on", v);
    b.setAttribute("aria-pressed", String(v));
    window.clearInterval(poll);
    // dictation also ends by itself after a few seconds of silence: follow it
    if (v) poll = window.setInterval(() => { void dictation("status").then((a) => { if (!a) set(false); }); }, 1200);
  };
  b.addEventListener("mousedown", (e) => e.preventDefault());          // the caret stays in the box: that is where the words go
  b.addEventListener("click", (e) => {
    e.stopPropagation();
    target.focus();
    void dictation("toggle").then(set);
  });
  target.addEventListener("blur", () => { if (on) { set(false); void dictation("stop"); } });
  return b;
}

function openSettings(): void {
  try { localStorage.setItem(SETTINGS_GOTO_KEY, "apps"); } catch { /* storage unavailable: Settings just opens */ }
  void Bridge.openSettingsWindow();
}

// ── The big chat ─────────────────────────────────────────────────────────────────────────────────────────
// Opening a conversation makes the island grow into a roomy chat (the island asks via appsChatActive()).
// A click on a message toast can open its chat directly (openAppsChat).

let chatOpen = false;
/** True while the Apps tab shows a conversation: the island then uses its big size and the tall native window. */
export function appsChatActive(): boolean { return chatOpen; }
/** Island size of the open conversation (the dock still fits below it inside the tall window). */
export const APPS_CHAT_W = 640;
export const APPS_CHAT_H = 490;

let pendingChat: { app: string; chat: string } | null = null;
let consumePendingChat: (() => void) | null = null;
/** Ask the Apps tab to open one conversation (the caller then switches the island to the Apps tab). */
export function openAppsChat(app: string, chat: string): void {
  pendingChat = { app, chat };
  consumePendingChat?.();
}

// ── Voice message player ─────────────────────────────────────────────────────────────────────────────────
// One <audio> per message, kept across repaints (a refresh must not cut off what is playing); only one at a time.

interface Player {
  audio: HTMLAudioElement | null;
  total: number;
  ui: { btn: HTMLElement; fill: HTMLElement; time: HTMLElement } | null;
}
const players = new Map<string, Player>();
let playingId = "";
const fileSize = (n: number): string => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
const clock = (n: number): string => `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, "0")}`;

function refreshPlayer(p: Player): void {
  if (!p.ui) return;
  const a = p.audio;
  const dur = a && isFinite(a.duration) && a.duration > 0 ? a.duration : p.total;
  const pos = a ? a.currentTime : 0;
  const playing = !!a && !a.paused && !a.ended;
  const bars = p.ui.fill.children;                  // the waveform: bars up to the playhead light up
  const lit = dur > 0 ? Math.round(Math.min(1, pos / dur) * bars.length) : 0;
  for (let i = 0; i < bars.length; i++) bars[i].classList.toggle("on", i < lit);
  p.ui.time.textContent = playing || pos > 0.1 ? `${clock(pos)} / ${clock(dur)}` : clock(dur);
  p.ui.btn.replaceChildren(svg(playing ? ICONS.pause : ICONS.play, 13));
  p.ui.btn.setAttribute("aria-label", playing ? "Pause" : "Play");
}

function stopAllAudio(): void {
  for (const p of players.values()) { p.audio?.pause(); if (p.audio) p.audio.currentTime = 0; refreshPlayer(p); }
  playingId = "";
}

export function buildApps(onResize?: () => void): ViewHost {
  let apps: AppInfo[] = [];          // every app plugin; only the connected ones get a screen here
  let enabled = true;
  let selected = "";
  let stack: AppView[] = [];         // screens we came from, for the back button
  let current: AppView | null = null;
  let active = false;
  let timer: VisiblePoll | null = null;
  let note = "";                     // one line of feedback ("Not sent", an error)
  let draft = "";                    // what is typed in the reply box, kept across refreshes
  let loaded = false;
  let suggestions: string[] = [];    // reply ideas for the open chat: only ever fill the reply box
  let lightbox = "";                 // blob: URL of the photo shown large, "" = none
  // A file picked with + : kept by the app until Send is pressed. Nothing goes out when it is picked.
  let attach: { token: string; name: string; kind: "image" | "file"; size: number; preview: string; busy: boolean; route: string } | null = null;
  const clearAttach = (): void => { if (attach?.preview) URL.revokeObjectURL(attach.preview); attach = null; };
  let wasBig = false;
  let chatFilter: "all" | "unread" | "group" = "all";   // the chat list's filter chips and search box
  let chatQuery = "";
  // The real Mochi (the same character as the notch) at the top left of the chat list: made once, moved into each repaint.
  let mochi: Character | null = null;
  const mochiHost = h("span", { class: "ap-mochi", "aria-hidden": "true" });
  function ensureMochi(): void {
    if (mochi) return;
    mochi = createCharacter(mochiHost, { tight: true, sharp: 2 });
    mochi.setEmote("idle");
    const readShape = async (): Promise<void> => {
      const f = (await getFeatures()) as { mascot_shape?: string } | null;
      mochi?.setShape?.(f?.mascot_shape ?? "cloud");
    };
    void readShape();
    onFeatures(() => void readShape());
  }
  const seenMsgs = new Map<string, Set<string>>();   // per chat: the messages already drawn, so only new ones animate

  const top = h("div", { class: "ap-top" });
  const body = h("div", { class: "ap-body" });
  const foot = h("div", { class: "ap-foot" });
  const el = h("div", { class: "view apps-view" }, top, body, foot);

  const connected = () => apps.filter((a) => a.connected);
  const app = () => connected().find((a) => a.id === selected) ?? null;

  async function loadApps(): Promise<void> {
    const data = await fetchApps();
    apps = data?.apps ?? [];
    enabled = data ? data.enabled : true;
    loaded = true;
    const ok = connected();
    if (!ok.some((a) => a.id === selected)) { selected = ok[0]?.id ?? ""; stack = []; current = null; draft = ""; }
  }

  async function loadView(route = current?.route ?? ""): Promise<void> {
    const a = app();
    if (!a) return;
    const res = await appsPost<{ ok: boolean; view?: AppView; error?: string }>("/apps/view", { id: a.id, route });
    if (!res) { note = "Lyra's backend did not answer."; return; }
    if (res.ok && res.view) {
      current = res.view; note = res.view.notice ?? "";
      takeServerDraft();
    } else note = res.error ?? "";
  }

  /** A reply the backend wrote ("draft a reply to Alex") arrives once, in the reply box. It is never sent by itself. */
  function takeServerDraft(): boolean {
    const d = current?.compose?.draft ?? "";
    if (!d || draft.trim()) return false;
    draft = d;
    note = "Draft reply: not sent. Edit it and press Send.";
    return true;
  }

  async function openChat(a: AppInfo, chat: string): Promise<void> {
    const res = await appsPost<{ ok: boolean; message?: string; view?: AppView }>("/apps/action", { id: a.id, name: "open", params: { chat } });
    if (res?.view) { stack = []; current = res.view; draft = ""; suggestions = []; clearAttach(); takeServerDraft(); }
  }

  async function takePendingChat(): Promise<void> {
    const want = pendingChat;
    if (!want || !active) return;
    pendingChat = null;
    await loadApps();
    const a = connected().find((x) => x.id === want.app);
    if (!a) { paint(); return; }
    selected = a.id;
    await openChat(a, want.chat);
    paint();
  }

  async function select(id: string): Promise<void> {
    selected = id; stack = []; current = null; note = ""; draft = ""; suggestions = []; clearAttach();
    paint();
    await loadView("");
    paint();
  }

  async function runAction(a: AppInfo, act: AppAction): Promise<void> {
    const res = await appsPost<{ ok: boolean; message?: string; view?: AppView }>("/apps/action", { id: a.id, name: act.name, params: act.params });
    if (!res) { note = "Lyra's backend did not answer."; paint(); return; }
    note = res.ok ? res.message ?? "" : res.message || "That did not work.";
    if (res.view) { if (current) stack.push(current); current = res.view; draft = ""; suggestions = []; clearAttach(); takeServerDraft(); }
    paint();
  }

  // ── Drawing ────────────────────────────────────────────────────────────────

  const style = (a: AppInfo) => `--accent:${a.accent || "#2fd16f"}`;

  function paintTop(a: AppInfo | null): void {
    top.replaceChildren();
    top.classList.toggle("thread", !!a && current?.type === "thread");
    if (!a) return;
    const ok = connected();
    const left = h("div", { class: "ap-left" });
    if (current?.type === "thread") {
      // Like Messages: a blue "Chats" button on the left, the contact's picture with their name centred.
      const name = current.title || a.title;
      top.append(
        h("button", { class: "ap-back", type: "button", "aria-label": "Back to chats", onclick: (e: Event) => {
          e.stopPropagation(); current = stack.pop() ?? null; draft = ""; suggestions = []; clearAttach(); lightbox = ""; paint(); void loadView().then(paint);
        } }, svg(ICONS.chevronLeft, 17, { stroke: 2.7 })),
        h("div", { class: "ap-who" }, avatar(name, "", 36), h("span", { class: "ap-whoname", text: name })),
        h("span", { class: "ap-whospace" }));
      return;
    }
    if (stack.length) {
      left.append(h("button", { class: "ap-iconbtn", type: "button", "aria-label": "Back", onclick: (e: Event) => {
        e.stopPropagation(); current = stack.pop() ?? null; draft = ""; suggestions = []; clearAttach(); lightbox = ""; paint(); void loadView().then(paint);
      } }, svg(ICONS.chevronLeft, 14, { stroke: 2.4 })));
      left.append(avatar(current?.title || a.title, "", 24), h("span", { class: "ap-title", text: current?.title || a.title }));
    } else if (ok.length > 1) {
      for (const x of ok) {
        left.append(h("button", { class: `ap-pill${x.id === a.id ? " on" : ""}`, type: "button", style: style(x),
          onclick: (e: Event) => { e.stopPropagation(); void select(x.id); } },
          svg(iconPath(x.icon), 12), h("span", { text: x.title })));
      }
    } else if (!(a.id === "whatsapp" && current?.type === "list")) {
      left.append(h("span", { class: "ap-dot", style: style(a) }), h("span", { class: "ap-title", text: a.title }));
    }
    if (!stack.length && current?.chip && a.id !== "whatsapp") left.append(h("span", { class: "ap-chip", style: style(a), text: current.chip }));
    if (left.childNodes.length) top.append(left);          // no gear here: the "Open Settings" link still shows when an app needs setting up
  }

  function emptyState(): HTMLElement {
    const msg = !enabled ? "WhatsApp is switched off."
      : apps.length ? "Nothing is connected yet." : "No apps installed.";
    const sub = !enabled ? "Turn it on in Settings → Features."
      : apps.length ? `Connect ${apps.map((x) => x.title).join(" or ")} in Settings. It takes a minute.` : "WhatsApp ships with Lyra.";
    return h("div", { class: "ap-empty" },
      h("span", { class: "ap-emptyicon" }, svg(ICONS.apps, 22)),
      h("div", { class: "ap-emptytitle", text: msg }),
      h("div", { class: "ap-emptysub", text: sub }),
      h("button", { class: "ap-primary", type: "button", text: "Open Settings", onclick: (e: Event) => { e.stopPropagation(); openSettings(); } }));
  }

  function renderView(a: AppInfo, v: AppView): HTMLElement {
    if (v.type === "message") return h("div", { class: "ap-msg", text: v.text ?? "" });
    if (v.type === "qr") return h("div", { class: "ap-msg", text: "Finish setting this app up in Settings → Apps." });
    if (v.type === "thread") return renderThread(a, v);
    if (a.id === "whatsapp") return renderChats(a, v);
    const list = h("div", { class: "ap-list" });
    if (v.actions?.length) {
      list.append(h("div", { class: "ap-actions" }, ...v.actions.map((b) =>
        h("button", { class: "ap-primary small", type: "button", text: b.label, onclick: (e: Event) => { e.stopPropagation(); void runAction(a, b); } },
          svg(ICONS.play, 11)))));
    }
    if (!v.items?.length) list.append(h("div", { class: "ap-msg", text: v.empty || "Nothing here yet." }));
    const art = a.id !== "whatsapp" && v.items?.some((i) => i.image);
    for (const it of v.items ?? []) {
      const unread = !!it.badge;
      const row = h("div", { class: `ap-row${it.action ? " click" : ""}${unread ? " unread" : ""}` },
        avatar(it.title, it.image, 34, !!art),
        h("div", { class: "ap-rowtext" },
          h("div", { class: "ap-rowhead" }, h("b", { text: it.title }), it.time ? h("time", { text: it.time }) : null),
          it.subtitle ? h("span", { class: "ap-sub", text: it.subtitle }) : null),
        unread ? h("em", { class: "ap-badge", text: it.badge }) : null);
      if (it.action) row.addEventListener("click", (e) => { e.stopPropagation(); void runAction(a, it.action as AppAction); });
      list.append(row);
    }
    return list;
  }

  // ── WhatsApp's chat list: Mochi + search pill, filter chips, rows ──────────────────────────────────────

  function renderChats(a: AppInfo, v: AppView): HTMLElement {
    const items = v.items ?? [];
    const isGroup = (it: AppItem): boolean => String(it.action?.params?.chat ?? it.id ?? "").endsWith("@g.us");
    const unreadN = items.filter((i) => !!i.badge).length;

    const find = h("input", { class: "ap-in ap-find", type: "text", placeholder: "Search chats", spellcheck: "false", autocomplete: "off", value: chatQuery }) as HTMLInputElement;
    find.addEventListener("click", (e) => e.stopPropagation());
    find.addEventListener("keydown", (e) => e.stopPropagation());
    ensureMochi();
    const search = h("div", { class: "ap-searchrow" },
      mochiHost,
      h("div", { class: "ap-find-pill" }, svg(ICONS.search, 15, { stroke: 2 }), find, micButton(find)));

    const defs: Array<["all" | "unread" | "group", string]> = [["all", "All"], ["unread", unreadN ? `Unread ${unreadN}` : "Unread"], ["group", "Groups"]];
    const chipEls = defs.map(([k, label]) => {
      const b = h("button", { class: `ap-fchip${chatFilter === k ? " on" : ""}`, type: "button", text: label });
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        chatFilter = k;
        chipEls.forEach((c, i) => c.classList.toggle("on", defs[i][0] === k));
        paintRows();
      });
      return b;
    });
    const chips = h("div", { class: "ap-chips" }, ...chipEls);

    const rows = h("div", { class: "ap-list" });
    const first = (items[0]?.title ?? "").split(" ")[0];
    const paintRows = (): void => {
      rows.replaceChildren();
      const q = chatQuery.trim().toLowerCase();
      const shown = items.filter((it) =>
        (chatFilter === "all" || (chatFilter === "unread" ? !!it.badge : isGroup(it)))
        && (!q || it.title.toLowerCase().includes(q) || it.subtitle.toLowerCase().includes(q)));
      if (!shown.length) rows.append(h("div", { class: "ap-msg", text: items.length ? "No chats match." : v.empty || "No chats yet. New messages show up here." }));
      for (const it of shown) {
        const unread = !!it.badge;
        const row = h("div", { class: `ap-row${it.action ? " click" : ""}${unread ? " unread" : ""}` },
          avatar(it.title, it.image, 40),
          h("div", { class: "ap-rowtext" }, h("b", { text: it.title }), it.subtitle ? h("span", { class: "ap-sub", text: it.subtitle }) : null),
          h("div", { class: "ap-rowend" }, it.time ? h("time", { text: it.time }) : null, unread ? h("em", { class: "ap-badge", text: it.badge }) : null));
        if (it.action) row.addEventListener("click", (e) => { e.stopPropagation(); void runAction(a, it.action as AppAction); });
        rows.append(row);
      }
      if (shown.length && first) rows.append(h("div", { class: "ap-hint" }, svg(ICONS.mic, 13, { stroke: 2 }), h("span", { text: `Say \u201Cdraft a reply to ${first}\u201D` })));
    };
    find.addEventListener("input", () => { chatQuery = find.value; paintRows(); });
    paintRows();
    return h("div", { class: "ap-chats" }, search, chips, rows);
  }

  // ── Photos and voice messages ──────────────────────────────────────────────────────────────────────────

  const chatOf = (v: AppView): string => String(v.compose?.params?.chat ?? "");

  function mediaNote(text: string, action?: HTMLElement | null): HTMLElement {
    return h("div", { class: "ap-mnote" }, svg(ICONS.photo, 13), h("span", { text }), action ?? null);
  }

  /** "Load" / "Try again": fetches this one photo or voice message now (also when automatic loading is off). */
  function loadButton(label: string, msg: AppMessage, v: AppView, a: AppInfo): HTMLElement {
    return h("button", { class: "ap-link", type: "button", text: label, onclick: (e: Event) => {
      e.stopPropagation();
      void appsPost("/apps/action", { id: a.id, name: "load_media", params: { chat: chatOf(v), id: msg.id } }).then(() => refreshThread(true));
    } });
  }

  function renderImage(m: AppMedia, msg: AppMessage, v: AppView, a: AppInfo): HTMLElement {
    const ratio = m.w > 0 && m.h > 0 ? `aspect-ratio:${m.w}/${m.h};` : "aspect-ratio:4/3;";
    if (m.state === "loading") return h("div", { class: "ap-photo loading", style: ratio }, h("span", { text: "Loading photo…" }));
    if (m.state === "viewonce") return mediaNote("View-once photo: open it on your phone");
    if (m.state === "off") return mediaNote("Photo", loadButton("Load", msg, v, a));
    if (m.state === "expired") return mediaNote("Photo no longer on this PC", loadButton("Load again", msg, v, a));
    if (m.state === "failed" || !m.url) return mediaNote(m.note ? `Photo not loaded (${m.note})` : "Photo not loaded", loadButton("Try again", msg, v, a));
    const box = h("div", { class: "ap-photo", style: ratio });
    const show = (url: string) => {
      const img = h("img", { src: url, alt: "Photo", draggable: "false" });
      box.classList.add("ready");
      box.append(img);
      box.addEventListener("click", (e) => { e.stopPropagation(); lightbox = url; paintLightbox(); });
    };
    const known = peekMediaUrl(m.url);
    if (known) show(known);
    else {
      box.classList.add("loading");
      void mediaBlobUrl(m.url).then((url) => {
        box.classList.remove("loading");
        if (url) show(url); else box.replaceChildren(h("span", { text: "Photo could not be loaded" }));
      });
    }
    return box;
  }

  async function togglePlay(id: string, m: AppMedia): Promise<void> {
    const p = players.get(id);
    if (!p) return;
    if (!p.audio) {
      const url = await mediaBlobUrl(m.url);
      if (!url) { note = "That voice message could not be played."; foot.textContent = note; return; }
      const audio = new Audio(url);
      audio.preload = "auto";
      for (const ev of ["timeupdate", "play", "pause", "ended", "loadedmetadata"]) audio.addEventListener(ev, () => {
        if (ev === "ended") { audio.currentTime = 0; if (playingId === id) playingId = ""; }
        refreshPlayer(p);
      });
      p.audio = audio;
    }
    if (playingId && playingId !== id) players.get(playingId)?.audio?.pause();
    if (p.audio.paused) { playingId = id; await p.audio.play().catch(() => { note = "That voice message could not be played."; foot.textContent = note; }); }
    else p.audio.pause();
  }

  function renderVoice(msg: AppMessage, m: AppMedia, v: AppView, a: AppInfo): HTMLElement {
    const box = h("div", { class: "ap-voicebox" });
    if (m.state !== "ready") {
      const text = m.state === "loading" ? "Voice message loading…"
        : m.state === "viewonce" ? "View-once voice message: listen on your phone"
        : m.state === "off" ? "Voice message"
        : m.state === "expired" ? "Voice message no longer on this PC"
        : m.note ? `Voice message not loaded (${m.note})` : "Voice message not loaded";
      const again = m.state === "off" ? "Load" : m.state === "expired" ? "Load again" : m.state === "failed" ? "Try again" : "";
      box.append(h("div", { class: "ap-mnote" }, svg(ICONS.waveform, 13), h("span", { text }), again ? loadButton(again, msg, v, a) : null));
      return box;
    }
    const id = `${chatOf(v)}|${msg.id}`;
    let p = players.get(id);
    if (!p) { p = { audio: null, total: m.seconds, ui: null }; players.set(id, p); }
    const btn = h("button", { class: "ap-play", type: "button", "aria-label": "Play" }, svg(ICONS.play, 13));
    // A waveform drawn from the message id (WhatsApp's own is not sent to linked devices): the same message always looks the same.
    let seed = 0;
    for (const ch of id) seed = (seed * 31 + ch.charCodeAt(0)) >>> 0;
    const bars = Array.from({ length: 32 }, () => {
      seed = (seed * 1664525 + 1013904223) >>> 0;
      return h("i", { style: `height:${24 + (seed >>> 16) % 70}%` });
    });
    const track = h("div", { class: "ap-track", role: "slider", "aria-label": "Position" }, ...bars);
    const fill = track;
    const time = h("span", { class: "ap-vtime", text: clock(m.seconds) });
    p.ui = { btn, fill, time };
    btn.addEventListener("click", (e) => { e.stopPropagation(); void togglePlay(id, m); });
    track.addEventListener("click", (e) => {
      e.stopPropagation();
      const player = players.get(id);
      if (!player?.audio) { void togglePlay(id, m); return; }
      const r = track.getBoundingClientRect();
      const dur = isFinite(player.audio.duration) && player.audio.duration > 0 ? player.audio.duration : player.total;
      if (r.width > 0 && dur > 0) player.audio.currentTime = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * dur;
      refreshPlayer(player);
    });
    box.append(h("div", { class: "ap-voice" }, btn, track, time));
    refreshPlayer(p);
    return box;
  }

  function paintLightbox(): void {
    el.querySelector(".ap-lightbox")?.remove();
    if (!lightbox) return;
    const close = () => { lightbox = ""; paintLightbox(); };
    el.append(h("div", { class: "ap-lightbox", onclick: (e: Event) => { e.stopPropagation(); close(); } },
      h("img", { src: lightbox, alt: "Photo" }),
      h("button", { class: "ap-iconbtn light", type: "button", "aria-label": "Close photo", onclick: (e: Event) => { e.stopPropagation(); close(); } }, svg(ICONS.xmark, 14, { stroke: 2.4 }))));
  }

  // ── A conversation ─────────────────────────────────────────────────────────────────────────────────────

  function renderThread(a: AppInfo, v: AppView): HTMLElement {
    const msgs = h("div", { class: "ap-thread" });
    const list = v.messages ?? [];
    const seen = seenMsgs.get(v.route) ?? new Set<string>();
    const firstDraw = !seenMsgs.has(v.route);
    seenMsgs.set(v.route, seen);
    let prev = "";
    let prevHour = "";
    list.forEach((m, i) => {
      const who = m.mine ? "me" : m.from || "them";
      const media = m.media;
      const next = list[i + 1];
      const hour = m.time.slice(0, 2);
      // A centred time like Messages shows it, whenever the hour changes (and at the top of the chat).
      if (m.time && hour !== prevHour) { msgs.append(h("div", { class: "ap-stamp", text: m.time })); prev = ""; }
      prevHour = hour;
      const endOfRun = !next || next.mine !== m.mine || (!m.mine && next.from !== m.from) || next.time.slice(0, 2) !== hour;
      const bare = media?.kind === "image" && media.state === "ready" && !m.text;      // a photo alone has no bubble
      const parts: Array<HTMLElement | null> = [
        !m.mine && m.from && who !== prev ? h("small", { text: m.from }) : null,
        media?.kind === "image" ? renderImage(media, m, v, a) : null,
        media && media.kind !== "image" ? renderVoice(m, media, v, a) : null,
        m.text ? h("span", { class: "ap-text", text: m.text }) : null,
      ];
      msgs.append(h("div", { class: `ap-m${m.mine ? " mine" : ""}${who === prev ? " cont" : ""}`, title: m.time },
        h("div", { class: `ap-bubble${media ? " media" : ""}${bare ? " bare" : ""}${endOfRun && !bare ? " tail" : ""}${!firstDraw && m.id && !seen.has(m.id) ? " fresh" : ""}` }, ...parts)));
      if (m.id) seen.add(m.id);
      prev = who;
    });
    if (!v.messages?.length) msgs.append(h("div", { class: "ap-msg", text: "No messages yet." }));
    const box = h("div", { class: "ap-threadbox" }, msgs);
    if (v.compose) {
      const c = v.compose;
      const mine = attach && attach.route === v.route ? attach : null;
      const input = h("input", { class: "ap-in", type: "text", placeholder: mine ? "Add a caption" : c.placeholder || "Message", maxlength: "1000", value: draft, autocomplete: "off" }) as HTMLInputElement;
      const canSend = () => !!input.value.trim() || (!!mine && !mine.busy);
      const send = h("button", { class: `ap-send${draft.trim() || (mine && !mine.busy) ? " ready" : ""}`, type: "button", "aria-label": "Send" }, svg(ICONS.arrowUp, 15, { stroke: 2.6 }));
      // Sending only ever happens here, from the user's own press of Send (or Enter). Suggestions and a picked file
      // only fill the box: nothing leaves this PC until then.
      const go = () => {
        if (mine?.busy) { foot.textContent = note = "Still preparing the file…"; return; }
        const text = input.value.trim();
        if (!text && !mine) return;
        const token = mine?.token ?? "";
        input.value = ""; draft = ""; suggestions = []; clearAttach(); send.classList.remove("ready");
        void appsPost<{ ok: boolean; message?: string }>("/apps/action", { id: a.id, name: c.action, params: { ...c.params, text, ...(token ? { attach: token } : {}) } })
          .then((r) => { note = r && !r.ok ? r.message ?? "Not sent." : ""; foot.textContent = note; window.setTimeout(() => void refreshThread(true), 700); });
        paint();
      };
      input.addEventListener("input", () => { draft = input.value; send.classList.toggle("ready", canSend()); });
      input.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") go(); });
      input.addEventListener("click", (e) => e.stopPropagation());
      send.addEventListener("click", (e) => { e.stopPropagation(); go(); });
      const row = h("div", { class: "ap-composewrap" });
      if (mine) {
        row.append(h("div", { class: "ap-attach" },
          mine.kind === "image" && mine.preview ? h("img", { src: mine.preview, alt: "" }) : h("span", { class: "ap-atticon" }, svg(ICONS.folder, 16)),
          h("span", { class: "ap-attname", text: mine.name }),
          h("span", { class: "ap-attsize", text: mine.busy ? "Preparing…" : fileSize(mine.size) }),
          h("button", { class: "ap-attx", type: "button", "aria-label": "Remove the file", onclick: (e: Event) => { e.stopPropagation(); clearAttach(); paint(); } }, svg(ICONS.xmark, 11, { stroke: 2.6 }))));
      }
      if (suggestions.length) {
        row.append(h("div", { class: "ap-sugs" }, ...suggestions.map((t) => h("button", { class: "ap-sug", type: "button", text: t, onclick: (e: Event) => {
          e.stopPropagation();
          input.value = t; draft = t; send.classList.add("ready"); input.focus();
          foot.textContent = note = "Draft reply: not sent. Edit it and press Send.";
        } }))));
      }
      const compose = h("div", { class: "ap-compose" });
      if (c.attach) {
        // The round + on the left, like Siri's: picks a photo or file. It is only attached, never sent, until Send.
        const picker = h("input", { type: "file", class: "ap-file", tabindex: "-1" }) as HTMLInputElement;
        picker.addEventListener("click", (e) => e.stopPropagation());
        picker.addEventListener("change", () => {
          const f = picker.files?.[0];
          picker.value = "";
          if (!f) return;
          clearAttach();
          const image = /^image\/(jpeg|png|webp)$/.test(f.type);
          const mineNow = { token: "", name: f.name, kind: (image ? "image" : "file") as "image" | "file", size: f.size,
            preview: image ? URL.createObjectURL(f) : "", busy: true, route: v.route };
          attach = mineNow;
          note = ""; paint();
          void appsUpload(a.id, f).then((r) => {
            if (attach !== mineNow) return;                       // removed or replaced meanwhile
            if (r.ok && r.token) { mineNow.token = r.token; mineNow.busy = false; mineNow.kind = r.kind ?? mineNow.kind; note = ""; }
            else { clearAttach(); note = r.error ?? "Could not attach that file."; }
            paint();
          });
        });
        compose.append(h("button", { class: "ap-plus", type: "button", title: "Attach a photo or file", "aria-label": "Attach a photo or file",
          onclick: (e: Event) => { e.stopPropagation(); picker.click(); } }, svg(ICONS.plus, 17, { stroke: 2 })), picker);
      }
      const pill = h("div", { class: "ap-pillin" }, input);
      if (c.suggest) {
        const sg = c.suggest;
        pill.append(h("button", { class: "ap-sugbtn", type: "button", title: "Suggest a reply (nothing is sent)", "aria-label": "Suggest a reply", onclick: (e: Event) => {
          e.stopPropagation();
          foot.textContent = "Thinking of a reply…";
          void appsPost<{ ok: boolean; message?: string; suggestions?: string[] }>("/apps/action", { id: a.id, name: sg.name, params: sg.params }).then((r) => {
            suggestions = r?.ok ? r.suggestions ?? [] : [];
            note = r ? r.message ?? "" : "Lyra's backend did not answer.";
            paint();
          });
        } }, svg(ICONS.star, 15)));
      }
      pill.append(micButton(input), send);
      compose.append(pill);
      row.append(compose);
      box.append(row);
    }
    return box;
  }

  /** The island grows while a conversation is open and shrinks back to the list (see appsChatActive()). */
  function syncBig(): void {
    const big = active && current?.type === "thread";
    el.classList.toggle("big", big);
    if (big === wasBig && chatOpen === big) return;
    wasBig = big; chatOpen = big;
    onResize?.();
  }

  function paint(): void {
    const a = app();
    el.style.cssText = a ? style(a) : "";
    paintTop(a);
    foot.textContent = note;
    if (!loaded) { body.replaceChildren(h("div", { class: "ap-msg", text: "Loading…" })); return; }
    if (!a) { body.replaceChildren(emptyState()); syncBig(); return; }
    // A repaint must not steal the cursor from the reply box nor jump the conversation back to the bottom.
    const typing = document.activeElement as HTMLInputElement | null;
    const hadInput = !!typing && typing.classList?.contains("ap-in");
    const caret = hadInput ? typing!.selectionStart : null;
    const oldThread = body.querySelector<HTMLElement>(".ap-thread");
    const sameChat = !!oldThread && current?.type === "thread" && oldThread.dataset.route === current.route;
    const wasAtBottom = !oldThread || oldThread.scrollHeight - oldThread.scrollTop - oldThread.clientHeight < 40;
    const oldTop = oldThread?.scrollTop ?? 0;
    body.replaceChildren(current ? renderView(a, current) : h("div", { class: "ap-msg", text: "Loading…" }));
    const thread = body.querySelector<HTMLElement>(".ap-thread");
    if (thread && current) {
      thread.dataset.route = current.route;
      queueMicrotask(() => { thread.scrollTop = sameChat && !wasAtBottom ? oldTop : thread.scrollHeight; });
    }
    if (hadInput) {
      const input = body.querySelector<HTMLInputElement>(".ap-in");
      if (input) { input.focus(); if (caret != null) input.setSelectionRange(caret, caret); }
    }
    paintLightbox();
    mochi?.setActive?.(active && body.contains(mochiHost));   // it only draws while the chat list shows it
    syncBig();
  }

  async function refreshThread(force = false): Promise<void> {
    const a = app();
    if (!active || !a || current?.type !== "thread") return;
    const before = JSON.stringify(current.messages);
    const draftBefore = draft;
    await loadView();
    if (current?.type === "thread" && (force || draft !== draftBefore || JSON.stringify(current.messages) !== before)) paint();
  }

  async function tick(): Promise<void> {
    if (!active) return;
    const before = JSON.stringify(connected().map((x) => x.id));
    await loadApps();
    if (JSON.stringify(connected().map((x) => x.id)) !== before) { await loadView(current?.route ?? ""); paint(); return; }
    if (current?.type === "thread") { await refreshThread(); return; }
    if (current?.type === "list" && !current.route) {
      const prev = JSON.stringify(current.items);
      await loadView("");
      if (JSON.stringify(current?.items) !== prev && document.activeElement?.tagName !== "INPUT") paint();
    }
  }

  return {
    el,
    sync() { paint(); },
    setActive(on) {
      active = on;
      if (on) { chatQuery = ""; chatFilter = "all"; }
      timer?.stop(); timer = null;
      consumePendingChat = on ? () => void takePendingChat() : null;
      if (!on) { stopAllAudio(); lightbox = ""; paintLightbox(); mochi?.setActive?.(false); syncBig(); return; }
      void (async () => {
        await loadApps();
        if (selected) await loadView(current?.route ?? "");
        paint();
        await takePendingChat();       // a clicked WhatsApp toast opens its chat
      })();
      timer = visiblePoll(() => tick(), { ms: 3000, runOnShow: false });   // cleared while hidden
    },
  };
}
