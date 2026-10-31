// Peek tier for Music, Timer, Weather and Recognize: the tab first opens as a slightly bigger small island
// (sizes in core/layout.ts PEEK_SIZES) showing just what you glance at, like the previews in Settings →
// Features. A tap grows it to the full tab; the little arrow in the full tab shrinks it back.

import { visiblePoll, type VisiblePoll } from "../core/visiblePoll";
import "./peek.css";
import { h, svg } from "./dom";
import { ICONS } from "./icons";
import { State } from "../core/state";
import { nowPlaying, onNowPlaying } from "../core/nowPlaying";
import { SpotifyFeed } from "../spotify/feed";
import { recognizeMusic } from "../lyra/client";
import { getTimers, pickPrimary, clockText, labelText, colorFor, ringFraction } from "../island/timerChip";
import { getWeather, onWeather, refreshWeather, weatherGlyph } from "./weather";
import { dayKey, itemsOn } from "../core/calendarStore";
import { onSchedule, refreshSchedule, scheduleOn } from "../core/scheduleStore";
import { googleOn, refreshGoogle } from "../core/googleCalStore";
import type { ViewHost } from "./views";

/** Keeps a button's click from also counting as a tap on the peek (which would grow it). */
const keep = (b: HTMLElement) => { b.dataset.pkIgnore = "1"; return b; };

function round(icon: string, label: string, size: number, onClick: () => void): HTMLButtonElement {
  const b = keep(h("button", { class: "pk-btn", type: "button", title: label, "aria-label": label }, svg(icon, size))) as HTMLButtonElement;
  b.addEventListener("click", (e) => { e.stopPropagation(); onClick(); });
  return b;
}

// ── Music: art, title, equaliser, play/pause + next ──────────────────────────
export function peekMusic(): ViewHost {
  const art = h("div", { class: "pk-art" });
  const title = h("div", { class: "pk-t1" });
  const sub = h("div", { class: "pk-t2" });
  const eq = h("div", { class: "pk-eq" }, h("i"), h("i"), h("i"), h("i"));
  const playIcon = svg(ICONS.play, 17), pauseIcon = svg(ICONS.pause, 17);
  const toggle = round(ICONS.play, "Play / pause", 17, () => void SpotifyFeed.control("toggle"));
  const next = round(ICONS.next, "Next", 15, () => void SpotifyFeed.control("next"));
  const el = h("div", { class: "pk pk-music" }, art, h("div", { class: "pk-copy" }, title, sub), eq, toggle, next);
  const sync = () => {
    const live = nowPlaying.running && !!nowPlaying.title;
    const playing = live && nowPlaying.status === "playing";
    title.textContent = live ? nowPlaying.title : "Nothing playing";
    sub.textContent = live ? nowPlaying.artist : "Play something to see it here";
    art.style.backgroundImage = live && nowPlaying.artUrl ? `url("${nowPlaying.artUrl}")` : "";
    eq.classList.toggle("paused", !playing);
    el.classList.toggle("idle", !live);
    toggle.replaceChildren((playing ? pauseIcon : playIcon).cloneNode(true));
  };
  onNowPlaying(sync);
  sync();
  return { el, sync };
}

// ── Timer: draining ring + clock + label ─────────────────────────────────────
export function peekTimer(): ViewHost {
  const NS = "http://www.w3.org/2000/svg";
  const R = 11, C = 2 * Math.PI * R;
  const ring = document.createElementNS(NS, "svg");
  ring.setAttribute("viewBox", "0 0 28 28"); ring.setAttribute("class", "pk-ring");
  ring.innerHTML = `<circle cx="14" cy="14" r="${R}" class="bg"/><circle cx="14" cy="14" r="${R}" class="fg" stroke-dasharray="${C}" stroke-dashoffset="0"/>`;
  const fg = ring.querySelector(".fg") as SVGCircleElement;
  const clock = h("div", { class: "pk-clock" });
  const label = h("div", { class: "pk-t2" });
  const el = h("div", { class: "pk pk-timer" }, ring, h("div", { class: "pk-copy" }, clock, label));
  let last = "";
  const paint = (now: number) => {
    const it = pickPrimary(getTimers().items, now);
    const text = it ? `${clockText(it, now)}|${labelText(it)}|${it.state}` : "none";
    if (text === last) return;   // ring only moves when the clock text (1s) changes: no per-frame transition restarts
    last = text;
    if (it) {
      fg.style.stroke = colorFor(it);
      fg.setAttribute("stroke-dashoffset", String(C * (1 - ringFraction(it, now))));
    }
    el.classList.toggle("idle", !it);
    clock.textContent = it ? clockText(it, now) : "No timer";
    clock.style.color = it ? "" : "rgba(255,255,255,.85)";
    label.textContent = it ? `${labelText(it)}${it.state === "paused" ? " · paused" : ""}` : "Tap to set one";
  };
  return { el, sync: () => paint(Date.now()), tick: () => paint(Date.now()) };
}

// ── Weather: glyph, temperature, condition ───────────────────────────────────
export function peekWeather(): ViewHost {
  const glyph = h("div", { class: "pk-glyph" });
  const temp = h("div", { class: "pk-temp" });
  const t1 = h("div", { class: "pk-t1" });
  const t2 = h("div", { class: "pk-t2" });
  const el = h("div", { class: "pk pk-weather" }, glyph, temp, h("div", { class: "pk-copy" }, t1, t2));
  const sync = () => {
    const w = getWeather();
    el.classList.toggle("idle", !w.ok);
    glyph.replaceChildren(w.ok ? weatherGlyph(w.kind || "clear", !!w.night, 34) : svg(ICONS.cloudFill, 28));
    temp.textContent = w.ok && w.temp != null ? `${Math.round(w.temp)}°` : "–";
    t1.textContent = w.ok ? (w.city || "Weather") : "Weather";
    t2.textContent = w.ok ? [w.condition, w.hi != null && w.lo != null ? `H:${Math.round(w.hi)}° L:${Math.round(w.lo)}°` : ""].filter(Boolean).join(" · ") : "Fetching…";
  };
  onWeather(sync);
  sync();
  return { el, sync, setActive(on) { if (on) void refreshWeather(); } };
}

// ── Recognize: the orb listens right here; tapping anywhere else grows the island ──
export function peekRecognition(): ViewHost {
  const orb = keep(h("button", { class: "pk-orb", type: "button", title: "Identify music", "aria-label": "Identify music" },
    h("i", { class: "pk-orb-ring" }), svg(ICONS.waveform, 18, { stroke: 2.2 }))) as HTMLButtonElement;
  const t1 = h("div", { class: "pk-t1", text: "Identify music" });
  const t2 = h("div", { class: "pk-t2", text: "Tap the button to listen" });
  const el = h("div", { class: "pk pk-reco" }, orb, h("div", { class: "pk-copy" }, t1, t2));
  let busy = false;
  const listening = () => {
    busy = true; State.recognitionBusy = true;
    orb.classList.add("busy"); orb.disabled = true;
    t1.textContent = "Listening…"; t2.textContent = "Checking a short sample of your system audio";
  };
  const done = (r: { ok?: boolean; title?: string; artist?: string } | null) => {
    busy = false; State.recognitionBusy = false;
    orb.classList.remove("busy"); orb.disabled = false;
    if (r?.ok) { t1.textContent = r.title || "Unknown title"; t2.textContent = r.artist || "Unknown artist"; }
    else { t1.textContent = "No match found"; t2.textContent = "Tap the button to try again"; }
  };
  orb.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (busy) return;
    listening();
    done(await recognizeMusic(true));
  });
  // Started by voice ("what song is this?"): show it here too.
  window.addEventListener("lyra:music-recognition-activity", (ev) => {
    const d = (ev as CustomEvent<{ kind?: string; phase?: string }>).detail || {};
    if (d.kind === "music_recognition" && d.phase === "start" && !busy) listening();
  });
  window.addEventListener("lyra:music-recognition-result", (ev) => { const d = (ev as CustomEvent).detail; if (d) done(d); });
  return { el, sync() {}, setActive(on) { if (on && !busy) { t1.textContent = "Identify music"; t2.textContent = "Tap the button to listen"; } } };
}

// ── Stats: four bars (CPU, RAM, GPU, NET), like the Settings preview ──────────
export function peekStats(): ViewHost {
  const BASE = "http://127.0.0.1:8765";
  const cols = ["CPU", "RAM", "GPU", "NET"].map((label) => {
    const val = h("div", { class: "pk-sv", text: "–" });
    const fill = h("i");
    const name = h("em", { text: label });
    return { el: h("div", { class: "pk-sc" }, val, h("div", { class: "pk-st" }, fill), name), val, fill };
  });
  const el = h("div", { class: "pk pk-stats" }, ...cols.map((c) => c.el));
  const set = (i: number, pct: number | null, text: string) => {
    cols[i].fill.style.transform = `scaleY(${pct == null ? 0.04 : Math.max(0.04, Math.min(1, pct / 100))})`;
    cols[i].val.textContent = text;
  };
  const rate = (bps: number) => (bps >= 1048576 ? `${(bps / 1048576).toFixed(1)} MB/s` : `${Math.round(bps / 1024)} KB/s`);
  type Snap = { ok?: boolean; cpu?: { pct: number } | null; ram?: { pct: number } | null; gpu?: { pct: number } | null; net?: { down_bps: number; up_bps: number } | null };
  const paint = (s: Snap) => {
    set(0, s.cpu ? s.cpu.pct : null, s.cpu ? `${Math.round(s.cpu.pct)}%` : "–");
    set(1, s.ram ? s.ram.pct : null, s.ram ? `${Math.round(s.ram.pct)}%` : "–");
    set(2, s.gpu ? s.gpu.pct : null, s.gpu ? `${Math.round(s.gpu.pct)}%` : "–");
    const bps = s.net ? s.net.down_bps + s.net.up_bps : 0;
    set(3, s.net ? Math.min(100, (bps / (4 * 1048576)) * 100) : null, s.net ? rate(bps) : "–");   // full bar = 4 MB/s
  };
  let timer: VisiblePoll | null = null;
  const poll = async () => {
    try {
      const d = (await (await fetch(`${BASE}/stats`)).json()) as Snap;
      if (d && d.ok !== false) paint(d);
    } catch { /* backend not reachable: keep the last values */ }
  };
  return {
    el,
    sync() {},
    setActive(on) {
      timer?.stop(); timer = null;
      if (on) timer = visiblePoll(() => poll(), { ms: 1500 });   // runs at once; cleared while the window is hidden
    },
  };
}

// ── Calendar: the week, with the selected day's events under it ──────────────
export function peekCalendar(): ViewHost {
  const days = h("div", { class: "pk-days" });
  const list = h("div", { class: "pk-evs" });
  const el = h("div", { class: "pk pk-cal" }, days, list);
  let sel = new Date();
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString();
  const linesOn = (d: Date) => {
    const k = dayKey(d);
    return [
      ...itemsOn(k).map((i) => ({ time: i.time || "", title: i.title })),
      ...scheduleOn(k).map((l) => ({ time: l.time || "", title: l.title })),
      ...googleOn(k).map((l) => ({ time: l.time || "", title: l.title })),
    ].sort((a, b) => a.time.localeCompare(b.time));
  };
  const paint = () => {
    const today = new Date();
    const start = new Date(today); start.setDate(today.getDate() - today.getDay());
    days.replaceChildren(...[0, 1, 2, 3, 4, 5, 6].map((i) => {
      const d = new Date(start); d.setDate(start.getDate() + i);
      const b = keep(h("button", { class: `pk-day${same(d, sel) ? " sel" : ""}${same(d, today) ? " today" : ""}`, type: "button", "aria-label": d.toDateString() },
        h("span", { text: "SMTWTFS"[i] }), h("b", { text: String(d.getDate()) })));
      b.addEventListener("click", (e) => { e.stopPropagation(); sel = d; paint(); });
      return b;
    }));
    const ls = linesOn(sel);
    const rows = ls.slice(0, 2).map((l, i) => h("div", { class: "pk-ev" }, h("i", { class: i ? "alt" : "" }), h("span", { class: "pk-evt", text: l.time }), h("span", { class: "pk-evn", text: l.title })));
    if (ls.length > 2) rows.push(h("div", { class: "pk-t2", text: `+${ls.length - 2} more` }));
    list.replaceChildren(...(rows.length ? rows : [h("div", { class: "pk-t2", text: same(sel, today) ? "Nothing planned today" : "Nothing planned" })]));
  };
  onSchedule(paint);
  paint();
  return { el, sync: paint, setActive(on) { if (on) { sel = new Date(); void refreshSchedule(); void refreshGoogle(); paint(); } } };
}

// ── Wrapper: the same .view element, with the peek as an extra child ─────────
export function withPeek(full: ViewHost, peek: ViewHost, setPeek: (on: boolean) => void): ViewHost {
  const el = full.el;
  const peekEl = h("div", { class: "pk-peek" }, peek.el);
  peekEl.addEventListener("click", (e) => {
    if ((e.target as HTMLElement).closest("[data-pk-ignore]")) return;
    // The same click that opened the island lands here a moment later (mousedown opens, click follows).
    if (performance.now() - State.openedAt < 450) return;
    setPeek(false);
  });
  const shrink = h("button", { class: "pk-shrink", type: "button", title: "Smaller", "aria-label": "Make smaller" }, svg(ICONS.arrowUp, 12, { stroke: 2.4 }));
  shrink.addEventListener("click", (e) => { e.stopPropagation(); setPeek(true); });
  el.append(peekEl, shrink);

  let active = false, shown: boolean | null = null;
  const apply = () => {
    const p = State.peek;
    el.classList.toggle("pk-is-peek", p);
    if (p !== shown) { shown = p; full.setActive?.(active && !p); }
  };
  apply();
  return {
    el,
    sync() { apply(); full.sync(); peek.sync(); },
    focus: full.focus,
    tick(now) { peek.tick?.(now); if (!State.peek) full.tick?.(now); },
    setActive(on) { active = on; peek.setActive?.(on); full.setActive?.(on && !State.peek); },
  };
}
