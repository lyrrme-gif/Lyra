// "Added" / "couldn't add" animation: a small pill pops in at the top of a tab, with an icon that acts out
// what happened (a bell rings for a reminder, a stopwatch hand sweeps for a timer, a calendar page drops
// for an event, a tick draws for a to-do), then fades. A failed add shows a red pill with a shaking cross.
// Used when Lyra adds something by voice and when you add a to-do / event by typing. Only
// transform/opacity are animated, so it stays at 60 fps.

import { h } from "./dom";
import "./addedFx.css";

export type FxKind = "todo" | "reminder" | "alarm" | "timer" | "stopwatch" | "focus" | "event";
export type FxStatus = "ok" | "fail";

const KIND_COLOR: Record<FxKind, string> = {
  todo: "#34c759", reminder: "#ff455c", alarm: "#ff455c", event: "#ff455c",
  timer: "#ff9f0a", stopwatch: "#ff9f0a", focus: "#ff9f0a",
};
const VIEW_KIND: Record<string, FxKind> = { todo: "todo", calendar: "event", timer: "timer" };
const FAIL_COLOR = "#e5484d";
const NS = "http://www.w3.org/2000/svg";

/** One stroked/filled SVG shape; `a` is a plain attribute map. */
function shape(tag: string, a: Record<string, string>): SVGElement {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(a)) el.setAttribute(k, v);
  return el;
}
function glyph(...parts: SVGElement[]): SVGSVGElement {
  const s = document.createElementNS(NS, "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("width", "14"); s.setAttribute("height", "14");
  s.setAttribute("aria-hidden", "true");
  s.append(...parts);
  return s;
}
const ST = { fill: "none", stroke: "currentColor", "stroke-width": "2.4", "stroke-linecap": "round", "stroke-linejoin": "round" };

/** The badge icon for a kind (and for a failure). Each part carries a class the CSS animates. */
function icon(kind: FxKind, status: FxStatus): SVGSVGElement {
  if (status === "fail") {
    return glyph(shape("path", { ...ST, class: "fx-x", d: "M7 7l10 10M17 7 7 17", "stroke-width": "3" }));
  }
  switch (kind) {
    case "reminder":
    case "alarm":   // a bell that swings from its top
      return glyph(
        shape("path", { ...ST, class: "fx-bell", d: "M6 16v-4.5a6 6 0 0 1 12 0V16l1.6 2H4.4zM10 20.5a2 2 0 0 0 4 0" }),
      );
    case "timer":
    case "stopwatch":
    case "focus":   // a stopwatch whose hand sweeps once round
      return glyph(
        shape("circle", { ...ST, cx: "12", cy: "13.5", r: "7" }),
        shape("path", { ...ST, d: "M10 3h4M12 3v3.5" }),
        shape("path", { ...ST, class: "fx-hand", d: "M12 13.5V9.6" }),
      );
    case "event":   // a calendar whose tick draws in
      return glyph(
        shape("rect", { ...ST, x: "4", y: "5.5", width: "16", height: "14.5", rx: "3" }),
        shape("path", { ...ST, d: "M8 3.5v4M16 3.5v4M4 10h16" }),
        shape("path", { ...ST, class: "fx-draw", d: "M9 15l2.2 2.2L15.4 13" }),
      );
    default:        // to-do: a tick that draws itself
      return glyph(shape("path", { ...ST, class: "fx-draw", d: "M5 12.5 9.5 17 19 7.5", "stroke-width": "3.4" }));
  }
}

interface Last { t: number; specific: boolean }
const lastAt = new WeakMap<HTMLElement, Last>();

export interface FxOptions {
  /** What was added: picks the icon animation. Default = the tab's own kind. */
  kind?: FxKind;
  /** "fail" = it could not be added (red pill, shaking cross). */
  status?: FxStatus;
  /** The event came from the backend (knows the exact kind / outcome), not from a list refresh. */
  specific?: boolean;
}

/** Show the pill inside `host` (a tab's root element). A second call within 1.2 s is ignored, except that a
 *  specific one (the backend's "added" event) replaces a generic one (a list refresh), so a voice reminder
 *  rings its bell instead of showing the generic calendar tick, and a failure is never swallowed. */
export function fxAdded(host: HTMLElement | undefined | null, text: string, view = "todo", opts: FxOptions = {}): void {
  if (!host) return;
  const status: FxStatus = opts.status === "fail" ? "fail" : "ok";
  const kind: FxKind = opts.kind ?? VIEW_KIND[view] ?? "todo";
  const specific = !!opts.specific || status === "fail";
  const now = Date.now();
  const prev = lastAt.get(host);
  if (prev && now - prev.t < 1200 && (!specific || prev.specific)) return;
  lastAt.set(host, { t: now, specific });
  if (getComputedStyle(host).position === "static") host.style.position = "relative";
  host.querySelectorAll(":scope > .fx-added").forEach((n) => n.remove());
  const pill = h("div", { class: `fx-added fx-${status} fx-k-${kind}`, role: status === "fail" ? "alert" : "status" },
    h("span", { class: "fx-tick" }, icon(kind, status)),
    h("span", { class: "fx-txt", text: text || (status === "fail" ? "Couldn't add that" : "Added") }));
  pill.style.setProperty("--fx", status === "fail" ? FAIL_COLOR : KIND_COLOR[kind]);
  pill.addEventListener("animationend", (e) => { if ((e as AnimationEvent).animationName.startsWith("fx-pop")) pill.remove(); });
  host.append(pill);
  window.setTimeout(() => pill.remove(), status === "fail" ? 3200 : 2600);   // safety net (animations off / hidden window)
}

/** Event kinds the backend may send ("" / unknown = the tab's default). */
export function asFxKind(k: string): FxKind | undefined {
  return (["todo", "reminder", "alarm", "timer", "stopwatch", "focus", "event"] as const).find((x) => x === k);
}
