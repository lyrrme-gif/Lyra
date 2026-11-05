// Scroller navigation: the alternative to the tab wheel. A capsule shows THREE tabs at a time (Mochi · Music ·
// Chat first, icons only like the normal dock); every other tab is reached by scrolling: mouse wheel over it, dragging, a
// touch swipe, or the left/right keys. It lists every tab the user has switched on (same list as the wheel),
// the active tab is highlighted; the strip always scrolls back to the first three. Chosen in Settings > Island > Tab navigation.

import { h, svg } from "../../../shared/views/dom";
import { ICONS } from "../../../shared/views/icons";
import { tourSignal } from "../../../shared/core/tour";
import { State } from "../../../shared/core/state";
import { DESTINATIONS, type Destination } from "../../../shared/core/destinations";
import { getTimers } from "../timerChip";
import type { ViewActions } from "./views";

const VISIBLE = 3;

export interface Scroller {
  el: HTMLElement;
  sync(): void;
  /** Scrolls back to the three main tabs (Mochi · Music · Chat). */
  reset(smooth?: boolean): void;
}

function glyph(d: Destination): SVGSVGElement {
  return svg(ICONS[d.icon], 11, d.stroke ? { stroke: d.stroke } : d.evenodd ? { evenodd: true } : {});
}

export function buildDockScroller(actions: ViewActions): Scroller {
  const track = h("div", { class: "ds-track" });
  const el = h("div", { class: "dock-group dock-scroll", role: "tablist", "aria-label": "Tabs" }, track);

  let tabs: Array<{ dest: Destination; btn: HTMLButtonElement }> = [];
  let key = "";
  let slotW = 26;

  const maxFirst = () => Math.max(0, tabs.length - VISIBLE);
  const first = () => Math.min(maxFirst(), Math.max(0, Math.round(track.scrollLeft / slotW)));
  const goTo = (i: number, smooth = true) => {
    const c = Math.min(maxFirst(), Math.max(0, i));
    track.scrollTo({ left: c * slotW, behavior: smooth ? "smooth" : "auto" });
  };
  const edges = () => {
    const f = track.scrollLeft / slotW;
    el.classList.toggle("can-left", f > 0.05);
    el.classList.toggle("can-right", f < maxFirst() - 0.05);
  };

  const build = () => {
    const FIRST = ["overview", "music", "prompt"];                   // the three that are showing at rest
    const rank = (d: Destination) => { const i = FIRST.indexOf(d.view); return i < 0 ? FIRST.length : i; };
    const list = DESTINATIONS.filter((d) => State.viewAllowed(d.view)).sort((a, b) => rank(a) - rank(b));   // stable: the rest keep group order
    const k = list.map((d) => d.view).join("|");
    if (k === key) return;
    key = k;
    tabs = list.map((dest) => {
      const btn = h("button", {
        class: "ds-tab", type: "button", role: "tab", title: dest.label, "aria-label": dest.label, "data-view": dest.view,
        onclick: (e: Event) => {
          e.stopPropagation();
          if (dragged) return;                       // that was a drag, not a click
          actions.blip(); tourSignal("dock-pick", dest.view); actions.setView(dest.view);
        },
      }, glyph(dest)) as HTMLButtonElement;
      return { dest, btn };
    });
    track.replaceChildren(...tabs.map((t) => t.btn));
        el.style.setProperty("--ds-w", `${slotW}px`);
    edges();
  };

  // Wheel over the capsule scrolls it (one step per notch) instead of reaching the page behind.
  let wheelLock = -1000;
  el.addEventListener("wheel", (e: WheelEvent) => {
    if (tabs.length <= VISIBLE) return;
    e.preventDefault(); e.stopPropagation();
    const now = performance.now();
    if (now - wheelLock < 120) return;
    wheelLock = now;
    const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
    if (d) goTo(first() + (d > 0 ? 1 : -1));
  }, { passive: false });

  el.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") { e.preventDefault(); goTo(first() + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); goTo(first() - 1); }
  });

  // Mouse drag (touch already scrolls natively): drag, then snap to the nearest slot.
  let down = false, dragged = false, x0 = 0, s0 = 0;
  track.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "touch" || e.button !== 0) return;
    down = true; dragged = false; x0 = e.clientX; s0 = track.scrollLeft;
  });
  window.addEventListener("pointermove", (e) => {
    if (!down) return;
    const dx = e.clientX - x0;
    if (!dragged && Math.abs(dx) < 5) return;
    dragged = true;
    track.classList.add("dragging");
    track.scrollLeft = s0 - dx;
  });
  const endDrag = () => {
    if (!down) return;
    down = false;
    track.classList.remove("dragging");
    if (dragged) { goTo(Math.round(track.scrollLeft / slotW)); window.setTimeout(() => { dragged = false; }, 0); }
  };
  window.addEventListener("pointerup", endDrag);
  window.addEventListener("pointercancel", endDrag);

  // After a touch swipe / smooth scroll settles, keep it snapped and refresh the arrows.
  let settle = 0;
  track.addEventListener("scroll", () => {
    edges();
    window.clearTimeout(settle);
    settle = window.setTimeout(() => { if (!down && Math.abs(track.scrollLeft - first() * slotW) > 1) goTo(first()); }, 120);
  }, { passive: true });

  // It always comes back to the three main tabs (Mochi, Music, Chat): when the tab changes, when the island
  // opens or closes, and a few seconds after you stop browsing. The open tab shows beside it (see dock.ts).
  let lastView = "", lastExpanded = false, idle = 0;
  const armIdle = () => {
    window.clearTimeout(idle);
    if (first() === 0) return;
    idle = window.setTimeout(() => { if (el.matches(":hover")) armIdle(); else goTo(0); }, 4000);
  };
  track.addEventListener("scroll", armIdle, { passive: true });
  return {
    el,
    reset(smooth = true) { window.clearTimeout(idle); goTo(0, smooth); },
    sync() {
      build();
      const view = State.view === "empty" ? "overview" : State.view;
      const expanded = State.mode === "expanded";
      const timerLive = State.featureOn("timer") && getTimers().items.length > 0;
      for (const t of tabs) {
        t.btn.classList.toggle("on", t.dest.view === view);
        t.btn.setAttribute("aria-selected", String(t.dest.view === view));
        t.btn.classList.toggle("live", t.dest.view === "timer" && timerLive);   // a small dot: a timer is running
      }
      if (view !== lastView || expanded !== lastExpanded) { lastView = view; lastExpanded = expanded; this.reset(expanded && lastView !== ""); }
    },
  };
}
