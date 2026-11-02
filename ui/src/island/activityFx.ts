// Activity FX: the light show around the notch. One small DOM layer that sits on top of the island and is
// driven by a kind ("message", "call", "success" ...). The REAL island (island.ts) and the Settings →
// Live Activities previews (settings/activityPreviews.ts) both use this same module and the same CSS
// (activityFx.css), so what you preview is exactly what the notch does.
//
//   - edge light: a conic gradient that travels round the rim of the notch, plus a blurred twin as its glow
//   - shockwaves: rings that leave the notch (ringing phones, listening, fresh arrivals)
//   - sheen: a diagonal glint that crosses the glass
//   - particles: sparks / confetti thrown from the icon
//   - host moves: the notch itself buzzes (call), throbs (reminder) or shakes (fail) via the individual
//     `rotate` / `scale` / `translate` properties, so it never fights the island's own `transform`.
//
// Pure DOM + CSS, no timers inside the layer except the one that clears a one-shot. Nothing runs while it is hidden.

import "./activityFx.css";
import { isEco, onEcoChange } from "../core/eco";
import { readExtras, type LiveActivity } from "../core/state";

export type FxKind =
  | "message" | "call" | "reminder" | "timer" | "music"
  | "volume" | "brightness" | "devices" | "disconnect"
  | "download" | "success" | "fail" | "listening" | "recognition" | "found";

export interface FxOpts {
  /** Main colour (any CSS colour). Defaults to the kind's own palette. */
  color?: string;
  /** 0‥1: how strongly volume / brightness glow. */
  level?: number;
  /** Where the sparks start, from the left edge of the notch, in px (the icon sits there). */
  originX?: number;
  /** Which Live Activity this light belongs to (decides whether its glow switch is on). Defaults to the kind's own. */
  act?: LiveActivity;
}

/** The Live Activity each kind of light belongs to. `success` / `fail` default to Downloads; the timer passes `act`. */
const KIND_ACTIVITY: Record<FxKind, LiveActivity> = {
  message: "notifications", call: "calls", reminder: "reminders", timer: "timer", music: "music",
  volume: "volume", brightness: "brightness", devices: "devices", disconnect: "devices",
  download: "downloads", success: "downloads", fail: "downloads", listening: "listening",
  recognition: "recognition", found: "recognition",
};

/** False when the user switched this activity's glow off (Settings → Live Activities). */
export function glowEnabled(kind: FxKind, act?: LiveActivity): boolean {
  return readExtras().glow?.[act ?? KIND_ACTIVITY[kind]] !== false;
}

export interface Fx {
  el: HTMLElement;
  /** Starts (or restarts) `kind` on the host. One-shot kinds clear themselves. */
  show(kind: FxKind, opts?: FxOpts): void;
  hide(): void;
  kind(): FxKind | null;
  /** Called whenever the layer goes dark (a one-shot ended, or hide()). */
  onHide?: () => void;
}

/** Kinds that keep going until `hide()`; everything else plays once. */
export const PERSISTENT: ReadonlySet<FxKind> = new Set<FxKind>(["call", "reminder", "timer", "music", "download", "listening", "recognition"]);

/** How long a one-shot stays on before the layer clears itself (ms). Matches the longest animation in the CSS. */
const ONE_SHOT_MS: Partial<Record<FxKind, number>> = {
  message: 1900, volume: 1500, brightness: 1500, devices: 1900, disconnect: 1300,
  success: 2300, fail: 1400, found: 2800,
};

const CONFETTI = ["#ff375f", "#ff9f0a", "#ffd60a", "#30d158", "#64d2ff", "#0a84ff", "#bf5af2"];
const PARTICLES: Partial<Record<FxKind, { n: number; palette?: string[]; spread: number }>> = {
  message: { n: 8, spread: 1 },
  reminder: { n: 6, spread: 1 },
  devices: { n: 7, spread: 1 },
  success: { n: 16, palette: CONFETTI, spread: 1.5 },
  found: { n: 16, palette: ["#ff375f", "#ff9f0a", "#30d158", "#64d2ff", "#bf5af2", "#ffffff"], spread: 1.5 },
  fail: { n: 4, spread: 0.6 },
};

const MAX_PARTICLES = 16;

function mixWhite(c: string, pct = 55): string {
  return `color-mix(in srgb, ${c} ${100 - pct}%, #fff)`;
}

export function buildFx(host: HTMLElement): Fx {
  const mk = (cls: string, tag = "b") => { const e = document.createElement(tag); e.className = cls; return e; };
  const el = mk("afx", "i");
  el.setAttribute("aria-hidden", "true");
  el.dataset.r = "act-fx";
  const clip = mk("afx-clip");
  clip.append(mk("afx-sheen"));
  const parts: HTMLElement[] = [];
  for (let i = 0; i < MAX_PARTICLES; i++) parts.push(mk("afx-p", "s"));
  el.append(mk("afx-glow"), mk("afx-edge"), mk("afx-wave w1"), mk("afx-wave w2"), mk("afx-wave w3"), clip, ...parts);
  host.append(el);

  let current: FxKind | null = null;
  let timer: number | null = null;

  const clearTimer = () => { if (timer != null) { window.clearTimeout(timer); timer = null; } };

  const api: Fx = {
    el,
    kind: () => current,
    show(kind, o = {}) {
      clearTimer();
      // Eco mode (light CPU): no light show at all. The sounds are not part of this and keep playing.
      // The per-activity glow switch does the same: that activity gets no light (its sound is not affected).
      if (isEco() || !glowEnabled(kind, o.act)) { current = null; el.classList.remove("on"); host.classList.remove("afx-host"); delete host.dataset.afx; return; }
      current = kind;
      // restart every animation: drop the class, force a reflow, put it back
      el.classList.remove("on");
      host.classList.remove("afx-host");
      void el.offsetWidth;
      el.dataset.fx = kind;
      host.dataset.afx = kind;
      if (o.color) {
        el.style.setProperty("--fx", o.color);
        el.style.setProperty("--fx2", mixWhite(o.color));
      } else {
        el.style.removeProperty("--fx");
        el.style.removeProperty("--fx2");
      }
      el.style.setProperty("--afx-int", String(o.level == null ? 1 : Math.max(0.25, Math.min(1, o.level))));
      el.style.setProperty("--ox", `${o.originX ?? 24}px`);
      const spec = PARTICLES[kind];
      parts.forEach((p, i) => {
        if (!spec || i >= spec.n) { p.style.display = "none"; return; }
        p.style.display = "block";
        const a = (Math.PI * 2 * i) / spec.n + Math.random() * 0.7;
        const dist = (46 + Math.random() * 72) * spec.spread;
        p.style.setProperty("--dx", `${Math.round(Math.cos(a) * dist * 1.5)}px`);
        p.style.setProperty("--dy", `${Math.round(Math.sin(a) * dist * 0.55 - 8)}px`);
        p.style.setProperty("--d", `${Math.round(Math.random() * 140)}ms`);
        p.style.setProperty("--ps", `${(0.7 + Math.random() * 0.9).toFixed(2)}`);
        if (spec.palette) p.style.setProperty("--pc", spec.palette[i % spec.palette.length]);
        else p.style.removeProperty("--pc");
      });
      host.classList.add("afx-host");
      el.classList.add("on");
      const ms = ONE_SHOT_MS[kind];
      if (!PERSISTENT.has(kind) && ms) timer = window.setTimeout(() => api.hide(), ms);
    },
    hide() {
      clearTimer();
      const was = current;
      current = null;
      el.classList.remove("on");
      host.classList.remove("afx-host");
      delete host.dataset.afx;
      if (was) api.onHide?.();
    },
  };
  // Eco mode switched on while a light was showing: switch it off right away.
  onEcoChange((on) => { if (on) { clearTimer(); current = null; el.classList.remove("on"); host.classList.remove("afx-host"); delete host.dataset.afx; } });
  return api;
}

/** Colour of a chat app's notification (the same brand colours as toast.ts). */
export const APP_FX_COLOR: Record<string, string> = {
  discord: "#5865f2",
  whatsapp: "#25d366",
  telegram: "#26a5e4",
  reminder: "#ff455c",
};
