// The launcher: ONE ring of group icons; click one and its tabs slide out beside it, on the same ring.
// Round white icons on a fully transparent background, drawn in its own transparent window
// (src/wheel + src-tauri/src/wheel.rs) that Rust opens in the middle of the active monitor. It
// looks the same whatever theme the island uses.
//
//   • The ring: the groups in destinations.ts (Talk, Media, Time, Info, Capture, Desk), small and close together, with a small
//     grey × in the middle that closes the launcher. A group with a single tab is that tab itself.
//   • Click a group: the ring does NOT change. The group turns black and its 2-4 tabs fan out in a small
//     arc around it, on its outer side. Click the group again (or press Backspace) to fold the tabs away.
//   • Hover or arrow keys light an icon (solid black) with a white pill showing its name.
//     Click / Enter opens the tab. Click anywhere else, right-click, or Esc closes everything.
//   • Info also holds Settings (wheel-only, not in destinations.ts): picking it opens the Settings window
//     (Island.onWheelPick), not an island tab.
//   • Tab / PageUp / PageDown / scrolling jump to the next / previous group.
//   • Hold mode (the global shortcut, Ctrl+CapsLock): stays up while the keys are down; letting go
//     on a tab opens it, letting go on a group opens that group.
//
// The launcher owns no navigation: it reports a choice through `cb.pick` (the same wheel_pick the
// old wheel used) and the island opens the tab the way it always did.

import { h, svg } from "./dom";
import { ICONS } from "./icons";
import { DESTINATIONS, PAGES, type Destination } from "../core/destinations";
import type { IslandViewName } from "../core/layout";
import {
  CAT_R, CLOSE_R, TAB_R, WHEEL_H, WHEEL_W,
  constellationHit, fitScale, neighbour, rootLayout, tabsAround, tooltipPlacement,
  type HitNode,
} from "../core/wheelGeometry";

export interface WheelCallbacks {
  /** A tab was chosen. */
  pick(view: IslandViewName): void;
  /** The launcher asked to be closed (Esc, click outside, the × in the middle). */
  dismiss(): void;
}

export interface WheelOpenOptions {
  /** Hold mode: opened by the held shortcut; the caller commits or keeps it open on release. */
  hold?: boolean;
  /** The tab on screen now (kept for the island's call; the launcher always opens with every group folded). */
  current?: IslandViewName;
  /** The tabs that are switched on (Settings → Features). Missing = all of them. */
  views?: IslandViewName[];
}

export interface WheelHost {
  el: HTMLElement;
  readonly isOpen: boolean;
  readonly holdMode: boolean;
  open(o?: WheelOpenOptions): void;
  /** Plays the closing animation (the window is hidden a moment later by Rust). */
  leave(): void;
  close(): void;
  /** Cursor position in window coordinates. */
  pointer(x: number, y: number): void;
  /** A quick tap of the shortcut: stay open and let the user click or scroll. */
  keepOpen(): void;
  /** Opens the lit tab (or category). Returns false when nothing is lit. */
  commit(): boolean;
}

interface Category {
  id: string;
  label: string;
  icon: keyof typeof ICONS;
  stroke?: number;
  dests: Destination[];
}

interface Node extends HitNode {
  el: HTMLElement;
  label: string;
  dest?: Destination;
  cat?: number;
}

/** The icon of each group (keyed by the id in PAGES). Chosen so none repeats the icon of a tab inside it. */
const CAT_ICON: Record<string, { icon: keyof typeof ICONS; stroke?: number }> = {
  talk: { icon: "grpTalk", stroke: 1.9 },
  media: { icon: "grpMedia", stroke: 1.9 },
  time: { icon: "grpTime", stroke: 1.9 },
  info: { icon: "grpInfo", stroke: 1.9 },
  capture: { icon: "grpCapture", stroke: 1.9 },
  desk: { icon: "grpDesk", stroke: 1.9 },
};

/** Settings sits in the Info group but is NOT an island tab (no entry in destinations.ts, so it is not on the dock,
 *  the scroller or the Features list): picking it makes the island open the Settings window (Island.onWheelPick). */
const SETTINGS_DEST: Destination = {
  view: "settings", label: "Settings", icon: "settingsCog",
  page: PAGES.findIndex((p) => p.id === "info"),
  from: "#9aa1ad", to: "#4b505a", evenodd: true,
};

const ICON = 24;
const FOLD_MS = 130;          // tabs fading out before they are removed
const SCROLL_PAUSE = 380;     // one category per scroll gesture, even with trackpad inertia

const destGlyph = (d: Destination) =>
  svg(ICONS[d.icon], ICON - 2, d.stroke ? { stroke: d.stroke } : d.evenodd ? { evenodd: true } : {});
const catGlyph = (c: Category, size = ICON + 1) =>
  svg(ICONS[c.icon], size, c.stroke ? { stroke: c.stroke } : {});

export function buildWheel(cb: WheelCallbacks): WheelHost {
  // ── Static parts ────────────────────────────────────────────────────────────
  // `field` is the WHEEL_W × WHEEL_H design space, centred in the window and scaled to fit it.
  const tip = h("div", { class: "c-tip", "aria-hidden": "true" });
  const field = h("div", { class: "c-field" }, tip);
  const el = h("div", { id: "lyra-wheel", "aria-hidden": "true" }, field);
  field.style.width = `${WHEEL_W}px`;
  field.style.height = `${WHEEL_H}px`;

  // ── State ───────────────────────────────────────────────────────────────────
  let cats: Category[] = [];
  let sel = -1;                         // -1 = every group folded, otherwise the open group
  let groupNodes: Node[] = [];          // the ring: built once per open, kept while groups open and fold
  let tabNodes: Node[] = [];            // the open group's tabs
  let closeNode: Node | null = null;
  let nodes: Node[] = [];               // everything that can be pointed at right now
  let open = false;
  let hold = false;
  let hl = -1;
  let scale = 1;
  let lastPx = -1e9;
  let lastPy = -1e9;
  let lastScroll = 0;

  const fit = () => {
    scale = fitScale(window.innerWidth, window.innerHeight);
    field.style.transform = `translate(-50%, -50%) scale(${scale.toFixed(4)})`;
  };

  // ── Drawing ─────────────────────────────────────────────────────────────────
  const showTip = (i: number) => {
    const n = nodes[i];
    if (!n || n.kind === "close" || (n.kind === "cat" && n.cat === sel)) { tip.classList.remove("on"); return; }   // no label on the ×, nor on the group that is already open
    tip.textContent = n.label;
    const w = tip.offsetWidth;
    const hgt = tip.offsetHeight;
    const p = tooltipPlacement(n, nodes, w, hgt);
    tip.dataset.side = p.side;
    tip.style.left = `${WHEEL_W / 2 + p.x}px`;
    tip.style.top = `${WHEEL_H / 2 + p.y}px`;
    // The notch points at the icon, which may be off-centre when the pill was pushed back inside the window.
    const along = p.side === "above" || p.side === "below" ? n.x - p.x : n.y - p.y;
    const lim = p.side === "above" || p.side === "below" ? w : hgt;
    tip.style.setProperty("--notch", `${Math.round(Math.max(10, Math.min(lim - 10, along)))}px`);
    tip.classList.add("on");
  };

  const setHl = (i: number) => {
    if (i === hl) return;
    hl = i;
    nodes.forEach((n, k) => n.el.classList.toggle("lit", k === i));
    showTip(i);
  };

  const mk = (cls: string, label: string, glyph: SVGElement, n: Omit<Node, "el" | "label">, i: number): Node => {
    const e = h("div", { class: `c-node ${cls}`, role: "menuitem", "aria-label": label, tabindex: -1 });
    e.append(glyph);
    e.style.setProperty("--i", String(i));
    e.style.left = `${WHEEL_W / 2 + n.x - n.r}px`;
    e.style.top = `${WHEEL_H / 2 + n.y - n.r}px`;
    e.style.width = e.style.height = `${n.r * 2}px`;
    return { ...n, el: e, label };
  };

  const settle = () => { nodes = [...groupNodes, ...(closeNode ? [closeNode] : []), ...tabNodes]; };

  /** Builds the ring of groups (once per open). A group with one tab is that tab itself. */
  const buildRing = () => {
    groupNodes.forEach((n) => n.el.remove());
    closeNode?.el.remove();
    const spots = rootLayout(cats.length);
    groupNodes = cats.map((c, k) => {
      if (c.dests.length === 1) {
        const d = c.dests[0];
        return mk("", d.label, destGlyph(d), { kind: "dest", x: spots[k].x, y: spots[k].y, r: CAT_R, dest: d }, k);
      }
      return mk("c-cat", c.label, catGlyph(c), { kind: "cat", x: spots[k].x, y: spots[k].y, r: CAT_R, cat: k }, k);
    });
    closeNode = mk("c-close", "Close", svg(ICONS.xmark, 17), { kind: "close", x: 0, y: 0, r: CLOSE_R }, cats.length);
    [...groupNodes, closeNode].forEach((n) => field.insertBefore(n.el, tip));
  };

  /** Folds the open group's tabs away (they fade, then leave the DOM). */
  const dropTabs = (instant = false) => {
    const old = tabNodes.map((n) => n.el);
    tabNodes = [];
    if (instant) { old.forEach((e) => e.remove()); return; }
    old.forEach((e) => e.classList.add("c-out"));
    window.setTimeout(() => old.forEach((e) => e.remove()), FOLD_MS);
  };

  /** Opens group `v` (or folds everything with -1): the ring stays, only the tabs beside the group change. */
  const select = (v: number, instant = false) => {
    if (v === sel && !instant) return;
    sel = v;
    hl = -1;
    tip.classList.remove("on");
    dropTabs(instant);
    groupNodes.forEach((n, k) => n.el.classList.toggle("c-sel", k === sel));
    if (sel >= 0 && cats[sel].dests.length > 1) {
      const g = groupNodes[sel];
      const spots = tabsAround(g, cats[sel].dests.length);
      tabNodes = cats[sel].dests.map((d, k) => {
        const n = mk("c-tab", d.label, destGlyph(d), { kind: "dest", x: spots[k].x, y: spots[k].y, r: TAB_R, dest: d }, k);
        n.el.style.setProperty("--fx", `${g.x - n.x}px`);       // the tab starts on the group and slides out
        n.el.style.setProperty("--fy", `${g.y - n.y}px`);
        return n;
      });
      tabNodes.forEach((n) => field.insertBefore(n.el, tip));
    }
    settle();
  };

  /** Groups: next / previous (keys, scrolling). With every group folded, the first or last one opens. */
  const stepCat = (dir: 1 | -1) => {
    if (cats.length < 2) return;
    if (sel < 0) { select(dir > 0 ? 0 : cats.length - 1); return; }
    select((sel + dir + cats.length) % cats.length);
  };

  /** Backspace: fold the open group; with nothing open, close the launcher. */
  const back = () => { if (sel >= 0 && cats.length > 1) select(-1); else cb.dismiss(); };

  const choose = (i: number) => {
    const n = nodes[i];
    if (!n) return;
    if (n.kind === "dest" && n.dest) cb.pick(n.dest.view);
    else if (n.kind === "cat" && n.cat != null) select(n.cat === sel ? -1 : n.cat);   // click the open group again to fold it
    else cb.dismiss();
  };

  // ── Input ───────────────────────────────────────────────────────────────────
  const hitAt = (x: number, y: number) =>
    constellationHit((x - window.innerWidth / 2) / scale, (y - window.innerHeight / 2) / scale, nodes);

  el.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!open) return;
    const hit = hitAt(e.clientX, e.clientY);
    if (hit.kind === "node") choose(hit.index);
    else cb.dismiss();                    // anywhere that is not an icon
  });
  el.addEventListener("contextmenu", (e) => { e.preventDefault(); e.stopPropagation(); cb.dismiss(); });
  el.addEventListener("pointerdown", (e) => e.stopPropagation());
  el.addEventListener("pointermove", (e) => host.pointer(e.clientX, e.clientY));
  window.addEventListener("resize", () => { if (open) fit(); });

  // Scrolling inside a category moves to the next one; it never scrolls anything underneath.
  el.addEventListener("wheel", (e) => {
    if (!open) return;
    e.preventDefault();
    e.stopPropagation();
    const now = performance.now();
    if (now - lastScroll > SCROLL_PAUSE && Math.abs(e.deltaY || e.deltaX) >= 8) {
      lastScroll = now;
      stepCat((e.deltaY || e.deltaX) > 0 ? 1 : -1);
    }
  }, { passive: false });

  const arrow = (dir: "left" | "right" | "up" | "down") => {
    if (!nodes.length) return;
    const to = neighbour(hl >= 0 ? nodes[hl] : null, nodes, dir, hl);
    if (to >= 0) setHl(to);
    else if (hl < 0) setHl(nodes.findIndex((n) => n.kind !== "close" && n.kind !== "center"));
  };

  // Keyboard, while the launcher is up (capture, so Esc closes it and not the whole island).
  window.addEventListener("keydown", (e) => {
    if (!open) return;
    const swallow = () => { e.preventDefault(); e.stopImmediatePropagation(); };
    switch (e.key) {
      case "Escape": swallow(); cb.dismiss(); break;
      case "Backspace": swallow(); back(); break;
      case "ArrowRight": swallow(); arrow("right"); break;
      case "ArrowLeft": swallow(); arrow("left"); break;
      case "ArrowDown": swallow(); arrow("down"); break;
      case "ArrowUp": swallow(); arrow("up"); break;
      case "Tab": swallow(); stepCat(e.shiftKey ? -1 : 1); break;
      case "PageDown": swallow(); stepCat(1); break;
      case "PageUp": swallow(); stepCat(-1); break;
      case "Enter": case " ": swallow(); if (hl >= 0) choose(hl); else cb.dismiss(); break;
    }
  }, true);

  const host: WheelHost = {
    el,
    get isOpen() { return open; },
    get holdMode() { return hold; },
    open(o = {}) {
      hold = !!o.hold;
      hl = -1;
      lastPx = lastPy = -1e9;
      tip.classList.remove("on");
      // The registry's groups, each with the tabs that are switched on; empty groups are left out.
      const on = [...DESTINATIONS.filter((d) => !o.views || o.views.includes(d.view)), SETTINGS_DEST];   // Settings is always there (last tab of Info)
      cats = PAGES.map((p, i) => ({
        id: p.id, label: p.label, icon: CAT_ICON[p.id]?.icon ?? "ellipsis", stroke: CAT_ICON[p.id]?.stroke,
        dests: on.filter((d) => d.page === i),
      })).filter((c) => c.dests.length > 0);
      // Tabs on a page the groups do not cover (a future entry) are not lost: they get a group of their own.
      const extra = on.filter((d) => d.page < 0 || d.page >= PAGES.length);
      if (extra.length) cats.push({ id: "more", label: "More", icon: "ellipsis", dests: extra });
      fit();
      sel = -1;
      dropTabs(true);
      buildRing();
      settle();
      if (cats.length === 1 && cats[0].dests.length > 1) select(0, true);   // a single group: its tabs are already out
      open = true;
      // The window is hidden and shown again rather than rebuilt: restart the animations each time.
      el.classList.remove("open", "closing");
      field.classList.remove("enter");
      void el.offsetWidth;
      el.classList.add("open");
      field.classList.add("enter");
      el.setAttribute("aria-hidden", "false");
    },
    leave() {
      if (!open) return;
      open = false;
      hold = false;
      el.classList.add("closing");
      el.setAttribute("aria-hidden", "true");
    },
    close() {
      if (!open) return;
      open = false;
      hold = false;
      el.classList.remove("open", "closing");
      el.setAttribute("aria-hidden", "true");
    },
    pointer(x, y) {
      if (!open) return;
      // The highlight follows the pointer only while it moves, so an arrow-key choice
      // is not snapped back by the cursor feed repeating a resting position.
      if (Math.hypot(x - lastPx, y - lastPy) < 3) return;
      lastPx = x;
      lastPy = y;
      const hit = hitAt(x, y);
      setHl(hit.kind === "node" ? hit.index : -1);
    },
    keepOpen() {
      hold = false;
    },
    commit() {
      if (!open || hl < 0) return false;
      const n = nodes[hl];
      if (!n || n.kind === "close") return false;                           // let go on the ×: just close
      hold = false;                                                         // a group opens and stays up
      choose(hl);
      return true;
    },
  };
  return host;
}
