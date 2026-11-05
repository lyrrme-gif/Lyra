// Island views — DOM ports of IslandViewContent.swift. Paddings, font sizes,
// colours and wording are copied from the Swift views so both platforms read
// identically.

import { buildHome } from "./home";
import { buildWeather } from "./weather";
import { buildCalendar } from "./calendar";
import { buildTodo } from "./todo";
import { buildStats } from "./stats";
import { buildControls } from "./controls";
import { buildWallpaper } from "./wallpaper";
import { h } from "../../../shared/views/dom";
import { State, type IslandTheme, type IslandPosition } from "../../../shared/core/state";
import { washRGBA, type IslandViewName, type Wash } from "../../../shared/core/layout";
import { buildPrompt } from "./chat";
import { buildChoose, buildUpload, buildUploading } from "./upload";
import { buildMusic } from "./music";
import { buildRecognition } from "./recognition";
import { buildClipboard } from "./clipboard";
import { buildTimer } from "./timer";
import { buildCamera } from "./camera";
import { buildApps } from "./apps";

import { buildVolume } from "./volume";
import { buildTranslate } from "./translate";
import { buildRecord } from "./record";
import { peekMusic, peekTimer, peekWeather, peekRecognition, peekStats, peekCalendar, withPeek } from "./peek";

export interface ViewActions {
  setView(v: IslandViewName): void;
  collapse(): void;
  setFocus(id: string): void;
  openUrl(url: string): void;
  toggleSound(): void;
  setVolume(v: number): void;
  setAutoClose(seconds: number): void;
  setTheme(theme: IslandTheme): void;
  setIslandPosition(position: IslandPosition): void;
  /** true = small peek, false = the full tab (Music, Timer, Weather, Recognize). */
  setPeek(on: boolean): void;
  openSettingsWindow(): void;
  blip(): void;
}

export interface ViewHost {
  el: HTMLElement;
  sync(): void;
  /** Called when the view becomes active, for views with a text field. */
  focus?(): void;
  /** Called every frame while the view is on screen. */
  tick?(nowMs: number): void;
  /**
   * Called when the view really becomes visible (selected AND island open) or stops
   * being. For views that talk to something outside the island, like the Music tab.
   */
  setActive?(on: boolean): void;
}

// ── Shared pieces ─────────────────────────────────────────────────────────────

function card(wash: Wash, ...children: (Node | string)[]): HTMLElement {
  const el = h("div", { class: wash ? "card wash" : "card" }, ...children);
  if (wash) el.style.setProperty("--wash", washRGBA(wash));
  return el;
}

function btn(
  label: string,
  kind: "primary" | "secondary",
  onClick: () => void,
  kbd?: string,
): HTMLElement {
  return h(
    "button",
    { class: `btn ${kind}`, onclick: onClick },
    h("span", { text: label }),
    kbd ? h("span", { class: "kbd", text: kbd }) : null,
  );
}

// ── Header ────────────────────────────────────────────────────────────────────

export { buildTabDock } from "./dock";


// ── Empty ─────────────────────────────────────────────────────────────────────

function buildEmpty(actions: ViewActions): ViewHost {
  const body = h(
    "div",
    { class: "stack", style: "padding:0 16px 0 16px;flex-direction:row;align-items:center;gap:16px" },
    h(
      "div",
      { style: "display:flex;flex-direction:column;gap:5px" },
      h("div", { class: "title", text: "Nothing running right now." }),
      h("div", { class: "sub", text: "Drop a file or window, or ask me anything." }),
    ),
    h("div", { class: "grow" }),
    btn("Ask Lyra", "primary", () => actions.setView("prompt")),
  );
  return { el: h("div", { class: "view" }, card(null, body)), sync() {} };
}

// ── Note ──────────────────────────────────────────────────────────────────────

function buildNote(): ViewHost {
  const title = h("div", { class: "title" });
  const el = h("div", { class: "view" }, card(null, h("div", { class: "stack", style: "padding:0 16px 0 16px" }, title)));
  return {
    el,
    sync() {
      title.textContent = State.noteMessage ?? "";
    },
  };
}

// ── Placeholders filled in later stages ───────────────────────────────────────

function buildPlaceholder(title: string, sub: string): ViewHost {
  const body = h(
    "div",
    { class: "stack", style: "padding:0 16px 0 16px" },
    h("div", { class: "title", text: title }),
    h("div", { class: "sub", text: sub }),
  );
  return { el: h("div", { class: "view" }, card(null, body)), sync() {} };
}

// ── Registry ──────────────────────────────────────────────────────────────────

export function buildViews(
  actions: ViewActions,
  onChatHeightChange: () => void,
): Map<IslandViewName, ViewHost> {
  const map = new Map<IslandViewName, ViewHost>();
  map.set("overview", buildHome(actions));
  map.set("weather", withPeek(buildWeather(), peekWeather(), actions.setPeek));
  map.set("calendar", withPeek(buildCalendar(), peekCalendar(), actions.setPeek));
  map.set("todo", buildTodo());
  map.set("stats", withPeek(buildStats(), peekStats(), actions.setPeek));
  map.set("controls", buildControls());
  map.set("wallpaper", buildWallpaper());
  map.set("empty", buildEmpty(actions));
  map.set("note", buildNote());
  map.set("prompt", buildPrompt(onChatHeightChange));
  map.set("music", withPeek(buildMusic(), peekMusic(), actions.setPeek));
  map.set("volume", buildVolume());
  map.set("recognition", withPeek(buildRecognition(), peekRecognition(), actions.setPeek));
  map.set("translate", buildTranslate(onChatHeightChange));
  map.set("record", buildRecord());
  map.set("clipboard", buildClipboard());
  map.set("timer", withPeek(buildTimer(), peekTimer(), actions.setPeek));
  map.set("camera", buildCamera());
  map.set("apps", buildApps(onChatHeightChange));
  map.set("upload", buildUpload());
  map.set("uploading", buildUploading());
  map.set("choose", buildChoose(actions));
  // Screen Watch and Search tabs removed — search results appear in chat.
  map.set("mail", buildPlaceholder("Sending by email isn't in this version.", ""));
  map.set("searching", buildPlaceholder("Lyra is searching…", ""));
  map.set("result", buildPlaceholder("Search results appear in chat.", "Ask Lyra anything and web results show up there."));
  map.set("screen", buildPlaceholder("Screen Watch has been removed.", "Ask about the screen from chat if needed."));
  map.set("settings", buildPlaceholder("Settings live in the Settings window.", "Use the gear icon to open them."));
  return map;
}
