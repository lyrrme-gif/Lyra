// Dock: three permanent destinations (Mochi · Music · Chat) plus state-driven
// temporary tabs that grow in beside them and leave when their context ends.
// Every other tab is on the tab wheel (hold Ctrl+CapsLock), not here.
//   [ Timer? ]  [ Mochi  Music  Chat ]  [ context? ]
// One sliding indicator per capsule; capsules grow/shrink so neighbours make room.

import { h, svg } from "./dom";
import { ICONS } from "./icons";
import { tourSignal } from "../core/tour";
import { State } from "../core/state";
import type { IslandViewName } from "../core/layout";
import { getTimers } from "../island/timerChip";
import type { ViewActions, ViewHost } from "./views";
import { buildDockScroller } from "./dockScroller";
import { destinationFor } from "../core/destinations";

type Def = { view: IslandViewName; label: string; icon: keyof typeof ICONS; evenodd?: boolean; stroke?: number };

const PERMANENT: Def[] = [
  { view: "overview", label: "Mochi", icon: "mochi", evenodd: true },
  { view: "music", label: "Music", icon: "noteFill" },
  { view: "prompt", label: "Chat", icon: "messageFill" },
];
const TIMER: Def = { view: "timer", label: "Timer", icon: "timerFill", evenodd: true };
// Study, Sounds, Controls and Translate are not tabs any more (no dock button); their views still open to show a result.
const CONTEXT: Partial<Record<IslandViewName, Def>> = {
  camera: { view: "camera", label: "Camera", icon: "cameraFill", evenodd: true },
  clipboard: { view: "clipboard", label: "Clipboard", icon: "clipboardFill", evenodd: true },
  recognition: { view: "recognition", label: "Recognize", icon: "waveform", stroke: 2.2 },
  weather: { view: "weather", label: "Weather", icon: "cloudFill" },
  calendar: { view: "calendar", label: "Calendar", icon: "calendarFill", evenodd: true },
  todo: { view: "todo", label: "To-do", icon: "listBullet" },
  stats: { view: "stats", label: "Stats", icon: "chartFill" },
  wallpaper: { view: "wallpaper", label: "Wallpaper", icon: "photo", stroke: 2 },
  record: { view: "record", label: "Record", icon: "record", evenodd: true },
};

function glyph(d: Def): SVGSVGElement {
  return svg(ICONS[d.icon], 16, d.stroke ? { stroke: d.stroke } : d.evenodd ? { evenodd: true } : {});
}

export function buildTabDock(actions: ViewActions): ViewHost {
  const tabBtn = (d: Def, onPick: () => void) =>
    h("button", {
      class: "dock-tab", type: "button", title: d.label, "aria-label": d.label, "data-view": d.view,
      onclick: (e: Event) => { e.stopPropagation(); actions.blip(); tourSignal("dock-pick", d.view); onPick(); },
    }, glyph(d)) as HTMLButtonElement;

  const group = (cls: string, ...tabs: HTMLElement[]) => {
    const ind = h("i", { class: "dock-ind" });
    return { el: h("div", { class: `dock-group ${cls}` }, ind, ...tabs), ind };
  };

  const timerTab = tabBtn(TIMER, () => actions.setView("timer"));
  const left = group("temp temp-left", timerTab);

  const permTabs = PERMANENT.map((d) => tabBtn(d, () => actions.setView(d.view)));
  const main = group("main", ...permTabs);

  // The context tab is a way out: pressing it closes that experience (back to Home).
  const ctxTab = tabBtn(CONTEXT.clipboard!, () => actions.setView("overview"));
  const right = group("temp temp-right", ctxTab);

  // Alternative navigation: a capsule with three tabs at a time that scrolls through the rest (Settings > Island).
  const scroller = buildDockScroller(actions);
  const el = h("div", { id: "lyra-dock", "aria-label": "Lyra navigation" }, left.el, main.el, right.el, scroller.el);

  let lastCtx: IslandViewName | null = null;
  let wasVisible = false;

  const lastX = new WeakMap<HTMLElement, number>();
  /** Slides the pill to the active tab; it stretches in flight like liquid, then settles. */
  const moveIndicator = (g: { el: HTMLElement; ind: HTMLElement }, tab: HTMLElement | null, animate: boolean) => {
    if (!tab || !tab.offsetWidth) { g.ind.style.opacity = "0"; return; }
    const to = tab.offsetLeft;
    const from = lastX.get(g.ind);
    g.ind.style.width = `${tab.offsetWidth}px`;
    g.ind.style.transform = `translateX(${to}px)`;
    g.ind.style.opacity = "1";
    if (animate && from != null && Math.abs(from - to) > 1 && typeof g.ind.animate === "function") {
      const mid = (from + to) / 2;
      g.ind.animate([
        { transform: `translateX(${from}px) scale(1, 1)` },
        { transform: `translateX(${mid}px) scale(1.28, .9)`, offset: 0.42 },
        { transform: `translateX(${to}px) scale(1, 1)` },
      ], { duration: 520, easing: "cubic-bezier(.3,1.25,.55,1)" });
    }
    lastX.set(g.ind, to);
  };

  return {
    el,
    sync() {
      const view = State.view === "empty" ? "overview" : State.view;
      const visible = State.mode === "expanded";
      el.classList.toggle("visible", visible);

      // "scroller": the three-at-a-time capsule replaces the Mochi/Music/Chat capsule. The open tab still sits
      // alone beside it (timer on the left, any other open tab on the right), exactly like in wheel mode.
      const nav = State.extras.navStyle === "wheel" ? "wheel" : "scroller";   // "both" shows the scroller too; the wheel opens from its own gestures
      el.dataset.nav = nav;
      // "Tab wheel" = no bar under the island. "Scroller" / "Both" = the scroller bar, while the island is open.
      const show = nav === "scroller" && visible;
      el.style.opacity = show ? "1" : "0";
      el.style.pointerEvents = show ? "auto" : "none";

      // Tabs the user switched off in Settings → Features are taken out of the dock.
      permTabs.forEach((b) => { b.hidden = !State.viewAllowed(b.dataset.view as IslandViewName); });

      // Timer tab lives while a timer exists, or while its experience is open.
      const timerOn = State.featureOn("timer") && (getTimers().items.length > 0 || view === "timer");
      left.el.classList.toggle("on", timerOn);
      timerTab.classList.toggle("on", view === "timer");

      permTabs.forEach((b) => b.classList.toggle("on", b.dataset.view === view));

      // Context tab: present only while that experience is open.
      const ctxDef = (v: IslandViewName): Def | undefined => {
        if (CONTEXT[v]) return CONTEXT[v];
        if (nav !== "scroller" || v === "timer" || PERMANENT.some((p) => p.view === v)) return undefined;
        const d = destinationFor(v);           // scroller mode: every other tab (Apps...) gets its own capsule too
        return d ? { view: d.view, label: d.label, icon: d.icon, evenodd: d.evenodd, stroke: d.stroke } : undefined;
      };
      const def = ctxDef(view);
      const ctx = def ? view : null;
      if (ctx && def && ctx !== lastCtx) {
        lastCtx = ctx;
        ctxTab.replaceChildren(glyph(def));
        ctxTab.title = def.label;
        ctxTab.setAttribute("aria-label", `Close ${def.label}`);
        ctxTab.dataset.view = ctx;
      }
      right.el.classList.toggle("on", !!ctx);
      ctxTab.classList.toggle("on", !!ctx);

      const animate = visible && wasVisible;
      const activePerm = permTabs.find((b) => b.classList.contains("on")) ?? null;
      moveIndicator(main, activePerm, animate);
      wasVisible = visible;
      if (nav === "scroller") scroller.sync();
    },
  };
}
