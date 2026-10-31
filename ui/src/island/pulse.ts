// Island pulse: when something is ADDED (a to-do, a reminder, an event, a timer) the compact notch itself
// reacts. It swells with a spring, floods with the colour of the tab it belongs to (to-do green, calendar /
// reminder red, timer orange), an icon acts out what happened (tick draws, bell rings, calendar page drops,
// stopwatch sweeps), sparks burst out, shock rings ripple past the edge and a island goes
// back to sleep after a moment. A refused add shakes the whole island. Clicking it opens the tab it came from.
//
// Only transform / opacity are animated (plus one box-shadow glow on the island), so it stays smooth.
// The island (island.ts) owns the geometry and the timing; this file owns the look.

import { h } from "../views/dom";
import "./pulse.css";

export type PulseKind = "todo" | "reminder" | "alarm" | "event" | "timer" | "stopwatch" | "focus" | "fail" | "game" | "gameoff" | "song";

export interface PulseInfo {
  kind: PulseKind;
  title: string;
  sub: string;
  /** "r,g,b" of the main colour (used by the CSS as rgb(var(--pl-rgb))). */
  rgb: string;
  /** Spark colours for the burst. */
  sparks: string[];
  /** A mode switch (game mode...): the notch only swells a little, like a notification, instead of the wide "added" pill. */
  compact?: boolean;
  /** Song found: album cover, and where the Spotify button leads. */
  art?: string;
  url?: string;
}

export const PULSE_W = 350;
export const PULSE_H = 56;

const LOOK: Record<PulseKind, { rgb: string; title: string; sparks: string[] }> = {
  todo:      { rgb: "52,199,89",  title: "Added to your to-do", sparks: ["#34c759", "#a6f0b8", "#ffffff", "#ffd60a"] },
  reminder:  { rgb: "255,69,92",  title: "Reminder set",        sparks: ["#ff455c", "#ff9aa8", "#ffffff"] },
  alarm:     { rgb: "255,69,92",  title: "Alarm set",           sparks: ["#ff455c", "#ff9aa8", "#ffffff"] },
  event:     { rgb: "255,69,92",  title: "Added to calendar",   sparks: ["#ff455c", "#ffb3bd", "#ffffff"] },
  timer:     { rgb: "255,159,10", title: "Timer started",       sparks: ["#ff9f0a", "#ffd08a", "#ffffff"] },
  stopwatch: { rgb: "255,159,10", title: "Stopwatch started",   sparks: ["#ff9f0a", "#ffd08a", "#ffffff"] },
  focus:     { rgb: "255,159,10", title: "Focus started",       sparks: ["#ff9f0a", "#ffd08a", "#ffffff"] },
  fail:      { rgb: "255,59,48",  title: "Couldn't add that",   sparks: ["#ff3b30", "#ff8a80", "#ffffff"] },
  game:      { rgb: "48,209,88",  title: "Game mode on",        sparks: ["#30d158", "#9af0b3", "#ffffff"] },
  song:      { rgb: "30,215,96",  title: "Song found",          sparks: ["#ffffff", "#1ed760", "#ffffff"] },
  gameoff:   { rgb: "142,147,156", title: "Game mode off",      sparks: ["#8e939c", "#d1d4da", "#ffffff"] },
};

/** A song the recognizer just found: cover on the left, Spotify on the right. */
export function describeSong(s: { title?: string; artist?: string; artworkUrl?: string; spotifyUrl?: string }): PulseInfo {
  const look = LOOK.song;
  return {
    kind: "song", title: (s.title || "Song found").slice(0, 60), sub: (s.artist || "").slice(0, 60),
    rgb: look.rgb, sparks: look.sparks, art: s.artworkUrl || "", url: s.spotifyUrl || "",
  };
}

/** Average colour of the cover, pushed to a vivid mid-tone, as "r,g,b". Null when the image can't be read (CORS). */
export function artColor(url: string): Promise<string | null> {
  return new Promise((resolve) => {
    if (!url) return resolve(null);
    const img = new Image();
    img.crossOrigin = "anonymous";
    const done = (v: string | null) => { img.onload = img.onerror = null; resolve(v); };
    img.onerror = () => done(null);
    img.onload = () => {
      try {
        const c = document.createElement("canvas");
        c.width = c.height = 16;
        const g = c.getContext("2d", { willReadFrequently: true });
        if (!g) return done(null);
        g.drawImage(img, 0, 0, 16, 16);
        const d = g.getImageData(0, 0, 16, 16).data;
        let r = 0, gg = 0, b = 0, wsum = 0;
        for (let i = 0; i < d.length; i += 4) {
          const mx = Math.max(d[i], d[i + 1], d[i + 2]), mn = Math.min(d[i], d[i + 1], d[i + 2]);
          const w = 0.15 + (mx - mn) / 255;               // colourful pixels count more than grey ones
          r += d[i] * w; gg += d[i + 1] * w; b += d[i + 2] * w; wsum += w;
        }
        if (!wsum) return done(null);
        let R = r / wsum, G = gg / wsum, B = b / wsum;
        // Lift towards a vivid mid-tone so the flood is never muddy or near-black.
        const mx = Math.max(R, G, B), mn = Math.min(R, G, B);
        const l = (mx + mn) / 510;
        const k = l < 0.38 ? 0.38 / Math.max(l, 0.05) : l > 0.62 ? 0.62 / l : 1;
        R = Math.min(255, R * k); G = Math.min(255, G * k); B = Math.min(255, B * k);
        done(`${Math.round(R)},${Math.round(G)},${Math.round(B)}`);
      } catch { done(null); }
    };
    img.src = url;
  });
}

/** A mode switching on / off (game mode today). */
export function describeMode(on: boolean, wake: string): PulseInfo {
  const k: PulseKind = on ? "game" : "gameoff";
  const look = LOOK[k];
  const sub = on ? (wake ? `${wake} wakes Lyra` : "Say \u201cHey Lyra\u201d to wake her") : "Lyra's shortcuts are back";
  return { kind: k, title: look.title, sub, rgb: look.rgb, sparks: look.sparks, compact: true };
}

/** The tab's default kind when the backend does not say which. */
const VIEW_KIND: Record<string, PulseKind> = { todo: "todo", calendar: "event", timer: "timer" };

export function describePulse(view: string, kind: string, status: "ok" | "fail", text: string): PulseInfo {
  const known = (Object.keys(LOOK) as PulseKind[]).find((k) => k === kind && k !== "fail");
  const k: PulseKind = status === "fail" ? "fail" : known ?? VIEW_KIND[view] ?? "todo";
  const look = LOOK[k];
  return { kind: k, title: look.title, sub: text.trim(), rgb: look.rgb, sparks: look.sparks };
}

const NS = "http://www.w3.org/2000/svg";
function shape(tag: string, a: Record<string, string>): SVGElement {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(a)) el.setAttribute(k, v);
  return el;
}
const ST = { fill: "none", stroke: "currentColor", "stroke-width": "2.5", "stroke-linecap": "round", "stroke-linejoin": "round" };
function glyph(...parts: SVGElement[]): SVGSVGElement {
  const s = document.createElementNS(NS, "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("width", "20"); s.setAttribute("height", "20");
  s.setAttribute("aria-hidden", "true");
  s.append(...parts);
  return s;
}

/** The icon for a kind; every part carries a class that pulse.css animates. */
function icon(kind: PulseKind): SVGSVGElement {
  switch (kind) {
    case "reminder":
    case "alarm":
      return glyph(
        shape("path", { ...ST, class: "pl-bell", d: "M6 16v-4.5a6 6 0 0 1 12 0V16l1.6 2H4.4zM10 20.5a2 2 0 0 0 4 0" }),
        shape("path", { ...ST, class: "pl-wave pl-wave-l", d: "M2.5 9.5a9 9 0 0 1 2.2-4.2", "stroke-width": "2" }),
        shape("path", { ...ST, class: "pl-wave pl-wave-r", d: "M21.5 9.5a9 9 0 0 0-2.2-4.2", "stroke-width": "2" }),
      );
    case "event":
      return eventIcon();
    case "timer":
    case "stopwatch":
    case "focus":
      return glyph(
        shape("circle", { ...ST, cx: "12", cy: "13.5", r: "7" }),
        shape("path", { ...ST, d: "M10 3h4M12 3v3.5" }),
        shape("path", { ...ST, class: "pl-hand", d: "M12 13.5V9.4" }),
      );
    case "game":
    case "gameoff":
      return padIcon(kind === "gameoff");
    case "fail":
      return glyph(shape("path", { ...ST, class: "pl-draw", pathLength: "1", d: "M7 7l10 10M17 7 7 17", "stroke-width": "3.2" }));
    default:
      return glyph(shape("path", { ...ST, class: "pl-draw", pathLength: "1", d: "M5 12.5 9.5 17 19 7.5", "stroke-width": "3.6" }));
  }
}
function padIcon(off: boolean): SVGSVGElement {
  const pad = shape("g", { class: "pl-pad" });
  pad.append(
    shape("path", { ...ST, "stroke-width": "2.2", d: "M7.2 7.5h9.6a4.2 4.2 0 0 1 4.1 3.4l.7 4.3a2.5 2.5 0 0 1-4.5 1.8L15.8 14H8.2l-1.9 3a2.5 2.5 0 0 1-4.5-1.8l.7-4.3a4.2 4.2 0 0 1 4.1-3.4z" }),
    shape("path", { ...ST, "stroke-width": "2", d: "M8 9.8v3.4M6.3 11.5h3.4" }),
    shape("path", { ...ST, "stroke-width": "2.6", d: "M15.6 10.6h.01M17.8 12.6h.01" }),
  );
  return glyph(pad, ...(off ? [shape("path", { ...ST, class: "pl-draw", pathLength: "1", d: "M4 20 20 4", "stroke-width": "3" })] : []));
}
function eventIcon(): SVGSVGElement {
  const page = shape("g", { class: "pl-page" });
  page.append(
    shape("rect", { ...ST, x: "4", y: "5.5", width: "16", height: "14.5", rx: "3" }),
    shape("path", { ...ST, d: "M8 3.5v4M16 3.5v4M4 10h16" }),
  );
  return glyph(page, shape("path", { ...ST, class: "pl-draw", pathLength: "1", d: "M9 15l2.2 2.2L15.4 13", "stroke-width": "2.8" }));
}

const SPARKS = 12;

export interface Pulse {
  /** Overlay that covers the notch (goes inside #island). */
  el: HTMLElement;
  /** Shock rings behind the island (goes inside #island too). */
  aura: HTMLElement;
  /** Show (or restart) the animation. `ms` is how long the bar takes to drain. */
  play(info: PulseInfo, ms: number, count: number): void;
  /** Same kind arrived again while showing: the "×2" badge pops and the colour flashes again. */
  bump(info: PulseInfo, ms: number, count: number): void;
  /** Change the colour while it is showing (the cover colour arrives a moment after the pulse starts). */
  recolor(rgb: string): void;
  stop(): void;
}

export function buildPulse(): Pulse {
  const flood = h("i", { class: "pl-flood" });
  const tile = h("div", { class: "pl-tile" });
  const sparks = h("div", { class: "pl-sparks" });
  const sparkEls: HTMLElement[] = [];
  for (let i = 0; i < SPARKS; i++) {
    const s = h("i");
    const ang = (Math.PI * 2 * i) / SPARKS + (i % 2 ? 0.2 : -0.1);
    const dist = 34 + (i % 3) * 14;
    s.style.setProperty("--dx", `${Math.round(Math.cos(ang) * dist)}px`);
    s.style.setProperty("--dy", `${Math.round(Math.sin(ang) * dist * 0.8)}px`);
    s.style.setProperty("--rot", `${(i * 97) % 360}deg`);
    s.style.setProperty("--sd", `${0.05 + (i % 4) * 0.03}s`);
    sparks.append(s);
    sparkEls.push(s);
  }
  const title = h("div", { class: "pl-title" });
  const sub = h("div", { class: "pl-sub" });
  const col = h("div", { class: "pl-col" }, title, sub);
  const badge = h("span", { class: "pl-count" });
  const spot = h("div", { class: "pl-spot" });
  spot.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 1.5C6.2 1.5 1.5 6.2 1.5 12S6.2 22.5 12 22.5 22.5 17.8 22.5 12 17.8 1.5 12 1.5zm4.86 15.12a.66.66 0 0 1-.9.22c-2.47-1.51-5.58-1.85-9.24-1.01a.66.66 0 1 1-.29-1.28c4.01-.91 7.42-.52 10.21 1.17a.66.66 0 0 1 .22.9zm1.3-2.89a.82.82 0 0 1-1.13.27c-2.83-1.74-7.14-2.24-10.48-1.23a.82.82 0 1 1-.48-1.57c3.8-1.16 8.54-.6 11.82 1.42a.82.82 0 0 1 .27 1.11zm.11-3.01c-3.39-2.01-8.98-2.2-12.21-1.21a.99.99 0 1 1-.57-1.89c3.7-1.13 9.86-.91 13.74 1.4a.99.99 0 0 1-.96 1.7z"/></svg>';
  const el = h("div", { id: "island-pulse", "aria-hidden": "true" }, flood, sparks, tile, col, badge, spot);
  const aura = h("div", { id: "island-aura", "aria-hidden": "true" }, h("i"), h("i"), h("i"));

  const setTile = (info: PulseInfo) => {
    if (info.kind !== "song") { tile.replaceChildren(icon(info.kind)); return; }
    if (info.art) {
      const img = document.createElement("img");
      img.alt = ""; img.decoding = "async";
      img.onload = () => img.classList.add("ready");
      img.src = info.art;
      tile.replaceChildren(img);
    } else {
      tile.replaceChildren(glyph(shape("path", { fill: "currentColor", d: "M9 17.5V6.6l10-2v10.9a2.7 2.7 0 1 1-1.6-2.5V7.6L10.6 9v8.5A2.7 2.7 0 1 1 9 17.5z" })));
    }
  };

  const fill = (info: PulseInfo, ms: number, count: number) => {
    el.dataset.spot = info.url ? "1" : "0";
    el.style.setProperty("--pl-rgb", info.rgb);
    el.style.setProperty("--pl-ms", `${ms}ms`);
    el.dataset.kind = info.kind;
    title.textContent = info.title;
    sub.textContent = info.sub;
    sub.style.display = info.sub ? "block" : "none";
    badge.textContent = count > 1 ? `×${count}` : "";
    sparkEls.forEach((s, i) => { s.style.background = info.sparks[i % info.sparks.length]; });
  };
  /** Restart every CSS animation under `.on` from the beginning. */
  const restart = () => { el.classList.remove("on"); void el.offsetWidth; el.classList.add("on"); };

  return {
    el,
    aura,
    play(info, ms, count) {
      fill(info, ms, count);
      setTile(info);
      restart();
    },
    bump(info, ms, count) {
      fill(info, ms, count);
      setTile(info);
      restart();
      badge.classList.remove("pop"); void badge.offsetWidth; badge.classList.add("pop");
    },
    recolor(rgb) { el.style.setProperty("--pl-rgb", rgb); },
    stop() { el.classList.remove("on"); },
  };
}
