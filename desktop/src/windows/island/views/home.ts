// The Mochi tab (this used to be Home): the Mochi, up close, plus the two things worth a glance.
//
//   ┌ Mochi ──────────┐ ┌ Weather ─────┐
//   │ 6:46 PM  Sat 17 │ └──────────────┘
//   │      (◕ ◕)      │ ┌ To-do ───────┐
//   │  ‹  Crown  ›    │ │ ○ item       │
//   │  · · ● · · · ·  │ │ ○ item  …    │
//   └─────────────────┘ └──────────────┘
//
// The Mochi is the real one (same engine as the notch and the screen mascot): its eyes follow
// the cursor, it grooves while music plays, and the arrows switch the character (Cloud, Fox, Cat). The
// pick is saved to the same backend setting as Settings → Mochi, so the notch and the screen Mochi follow.
// Drag the Mochi out of this card and it hops onto the screen, where it stays; drag it back onto the island and it
// flies home (the screen half lives in src/mascot/engine.ts, the two windows talk through src/mascot/carry.ts).
// Every other tab (Music, Calendar, Timer, Stats, ...) is on the tab wheel.

import { visiblePoll, type VisiblePoll } from "../../../shared/core/visiblePoll";
import { h, svg } from "../../../shared/views/dom";
import { ICONS } from "../../../shared/views/icons";
import type { ViewActions, ViewHost } from "./views";
import type { IslandViewName } from "../../../shared/core/layout";
import { State } from "../../../shared/core/state";
import { getWeather, onWeather, refreshWeather, weatherGlyph } from "./weather";
import { getTodos, onTodos, refreshTodos, toggleTodo } from "../../../shared/core/todoStore";
import { nowPlaying, onNowPlaying } from "../../../shared/core/nowPlaying";
import { Sound } from "../../../shared/core/sound";
import { isReallyVisible } from "../../../shared/core/visibility";
import { getFeatures, postFeatures } from "../../../shared/core/featuresCache";
import { createCharacter, gazeToward, type Character, type EmoteName } from "../../../shared/mascot/character";
import { AWAY_TIMEOUT_MS, onAway } from "../../../shared/mascot/dock";
import { carry, emitCarryStart, onCarryState } from "../../../shared/mascot/carry";
import { CHAR_DEFS, CHARACTER_IDS, RAGE_LINES } from "../../../shared/mascot/characters";
import "./mochiPlay.css";

const MOCHI_PX = 112;     // body width of the Mochi on this tab

/** The pressed item grows to fill the island while the destination view settles in. */
function growFrom(item: HTMLElement) {
  const isl = item.closest<HTMLElement>("#island");
  if (!isl) return;
  const root = item.closest<HTMLElement>(".hx-view");
  root?.classList.add("grow");
  window.setTimeout(() => root?.classList.remove("grow"), 700);
  const ir = isl.getBoundingClientRect();
  const r = item.getBoundingClientRect();
  const ghost = h("div", { class: "widget-ghost" });
  ghost.style.cssText = `left:${r.left - ir.left}px;top:${r.top - ir.top}px;width:${r.width}px;height:${r.height}px;border-radius:${getComputedStyle(item).borderRadius}`;
  isl.append(ghost);
  requestAnimationFrame(() => requestAnimationFrame(() => {
    ghost.classList.add("go");
    ghost.style.cssText = "left:0;top:0;width:100%;height:100%;border-radius:inherit";
  }));
  window.setTimeout(() => ghost.remove(), 560);
}

/** Rounded-square colour tile with a white glyph (iOS app-icon style). */
function tile(from: string, to: string, glyph: SVGSVGElement, cls = ""): HTMLElement {
  const t = h("span", { class: `hx-tile-ico ${cls}`.trim() }, glyph);
  t.style.background = `linear-gradient(160deg, ${from}, ${to})`;
  return t;
}

export function buildHome(actions: ViewActions): ViewHost {
  const go = (view: IslandViewName) => (e: Event) => {
    e.stopPropagation();
    actions.blip();
    growFrom(e.currentTarget as HTMLElement);
    actions.setView(view);
  };
  const opener = (cls: string, view: IslandViewName, ...kids: Array<Node | null>) =>
    h("button", { class: cls, type: "button", onclick: go(view) }, ...kids) as HTMLButtonElement;

  // ── Mochi ───────────────────────────────────────────────────────────────────
  const clock = h("div", { class: "hx-clock" });
  const date = h("span", { class: "hx-eyebrow" });

  const host = h("div", { class: "hx-mochi-host" });
  host.style.width = `${MOCHI_PX}px`;
  // Empty while the Mochi is out on the screen (or in the cursor's hand): it lights up when the Mochi is held above it.
  const nest = h("div", { class: "hx-nest", "aria-hidden": "true" }, h("span", { text: "Drag Mochi back here" }));
  const spot = h("button", { class: "hx-mochi-spot", type: "button", title: "Say hi, or drag me out onto the screen", "aria-label": "Mochi" }, nest, host) as HTMLButtonElement;
  const mochi: Character = createCharacter(host, { tight: true, sharp: 2 });
  mochi.setActive?.(false);   // drawn only while this tab is on screen

  let shape: string = CHARACTER_IDS[0];
  let waveTimer = 0;
  let sleepTimer = 0;
  let clicks = 0, lastClick = 0, dizzyUntil = 0, asleep = false;

  /** A little word that pops up over the Mochi, and floating glyphs (hearts, stars ...). */
  const say = (text: string) => {
    const b = h("span", { class: "hx-say", text });
    spot.append(b);
    window.setTimeout(() => b.remove(), 1700);
  };
  const burst = (glyph: string, n: number) => {
    for (let i = 0; i < n; i++) {
      const f = h("span", { class: "hx-fx", text: glyph });
      f.style.setProperty("--x", `${22 + Math.random() * 56}%`);
      f.style.setProperty("--dx", `${(Math.random() - 0.5) * 46}px`);
      f.style.animationDelay = `${i * 110}ms`;
      spot.append(f);
      window.setTimeout(() => f.remove(), 1500 + i * 110);
    }
  };
  /** Falls asleep when left alone for a while; a click wakes it up. */
  const armSleep = () => {
    window.clearTimeout(sleepTimer);
    sleepTimer = window.setTimeout(() => {
      if (!isReallyVisible()) return;
      asleep = true;
      mochi.setEmote("sleep");
      burst("\u{1F4A4}", 2);
    }, 28_000);
  };

  // Clicks: 1 wave (or dance while music plays) -> 2 happy -> 3 love -> 4 startled -> 5 quick clicks = dizzy.
  const PLAY: EmoteName[] = ["wave", "happy", "love", "surprised"];
  spot.addEventListener("click", (e) => {
    e.stopPropagation();
    const now = performance.now();
    if (away || carried || now < noClickUntil) return;   // out on the screen, or the click that ended a drag
    actions.blip();
    if (now < dizzyUntil) return;                     // still seeing stars: ignore the poking
    window.clearTimeout(waveTimer);
    armSleep();
    if (asleep) {                                     // woken up
      asleep = false; clicks = 0; lastClick = now;
      mochi.setEmote("surprised"); say("Huh? I'm up!");
      waveTimer = window.setTimeout(() => mochi.setEmote("idle"), 1500);
      return;
    }
    clicks = now - lastClick < 4200 ? clicks + 1 : 1;
    lastClick = now;
    if (CHAR_DEFS[shape]) {                           // nobody gets dizzy any more: each poke = angrier, in the Mochi's own way
      const lvl = Math.min(6, clicks);
      mochi.provoke?.(lvl);
      const line = RAGE_LINES[shape]?.[lvl - 1];
      if (line) say(line);
      if (lvl >= 3) burst("\u{1F4A2}", lvl >= 5 ? 3 : 1);
      return;
    }
    if (clicks >= 5) {
      clicks = 0; dizzyUntil = now + 3600;
      mochi.setEmote("dizzy"); say("Whoa... so dizzy!"); burst("\u{1F4AB}", 5);
      waveTimer = window.setTimeout(() => {
        mochi.setEmote("tired"); say("Phew...");
        waveTimer = window.setTimeout(() => mochi.setEmote("idle"), 1700);
      }, 3600);
      return;
    }
    const playing = nowPlaying.running && nowPlaying.status === "playing";
    const emote: EmoteName = playing && clicks === 1 ? "dance" : (PLAY[clicks - 1] ?? "wave");
    mochi.setEmote(emote);
    if (emote === "happy" || emote === "love") burst("\u{1F497}", emote === "love" ? 4 : 2);
    if (emote === "surprised") say("Hey!");
    waveTimer = window.setTimeout(() => mochi.setEmote("idle"), 1500);
  });

  // ── Drag the Mochi out onto the screen (and back) ───────────────────────────
  // Pulling it out hands it to the screen Mochi (the overlay window): it hops out of this card into the cursor's
  // hand, and where the button is let go it stays (or flies back in when let go over the island). While it is
  // out this card shows its empty nest.
  let mascotOn = false;                  // Settings → Mochi → Lyra mascot (the overlay window exists only then)
  let tabOn = false;                     // this tab is on screen
  let away = false;                      // the Mochi is out of the island (on the screen, or in the cursor's hand)
  let carried = false;                   // pulled out of this card and still held
  let awayTimer = 0, carryGuard = 0, noClickUntil = 0;
  let picking: { x: number; y: number } | null = null;

  const paintAway = () => {
    spot.classList.toggle("hx-away", away && !carried);
    spot.classList.toggle("hx-carried", carried);
    if (!away && !carried) spot.classList.remove("hx-nest-hot");
  };
  const endCarry = () => {
    carried = false;
    carry.active = false;
    window.clearTimeout(carryGuard);
    noClickUntil = performance.now() + 400;
    paintAway();
  };
  void onAway((a) => {                   // same heartbeat the notch Mochi follows: it times out if the overlay goes quiet
    window.clearTimeout(awayTimer);
    away = a;
    paintAway();
    if (a) awayTimer = window.setTimeout(() => { away = false; paintAway(); }, AWAY_TIMEOUT_MS);
  });
  void onCarryState((st) => {
    if (typeof st.over === "boolean") spot.classList.toggle("hx-nest-hot", st.over && (away || carried));
    if (!st.end) return;
    const home = st.end === "island";
    if (home) { away = false; window.clearTimeout(awayTimer); }
    endCarry();
    if (home && tabOn) {                 // it flew back into this card: a little landing
      host.classList.remove("hx-land");
      void host.offsetWidth;
      host.classList.add("hx-land");
      window.setTimeout(() => host.classList.remove("hx-land"), 650);
      mochi.setEmote("happy");
      waveTimer = window.setTimeout(() => mochi.setEmote("idle"), 1500);
      armSleep();
    }
  });

  /** Where the Mochi sits in this card (island-window coordinates), for the screen Mochi to land on. */
  carry.spot = () => {
    if (!tabOn) return null;
    const r = host.getBoundingClientRect();
    return r.width < 4 ? null : { cx: r.left + r.width / 2, cy: r.top + r.height / 2, body: r.width * (82 / 84) };
  };

  const startCarry = (e: PointerEvent) => {
    if (!mascotOn) { say("Turn on the Lyra mascot to drag me out"); noClickUntil = performance.now() + 600; return; }
    const r = host.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    window.clearTimeout(waveTimer);
    window.clearTimeout(sleepTimer);
    asleep = false; clicks = 0;
    carried = true;
    carry.active = true;                                   // the island stays open while the Mochi is in the hand
    noClickUntil = performance.now() + 1500;
    window.clearTimeout(carryGuard);
    carryGuard = window.setTimeout(endCarry, 30_000);      // the screen Mochi never answered: take the nest back
    paintAway();
    actions.blip();
    emitCarryStart({ fx: cx, fy: cy, gx: e.clientX - cx, gy: e.clientY - cy, body: r.width * (82 / 84), winW: window.innerWidth });
  };
  spot.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || away || carried) return;
    picking = { x: e.clientX, y: e.clientY };
    try { spot.setPointerCapture(e.pointerId); } catch { /* the moves still arrive while over it */ }
  });
  spot.addEventListener("pointermove", (e) => {
    if (!picking || carried) return;
    if (Math.hypot(e.clientX - picking.x, e.clientY - picking.y) < 8) return;   // a click, not a drag
    picking = null;
    try { spot.releasePointerCapture(e.pointerId); } catch { /* already released */ }
    startCarry(e);
  });
  const dropPick = () => { picking = null; };
  spot.addEventListener("pointerup", dropPick);
  spot.addEventListener("pointercancel", dropPick);

  const skinName = h("div", { class: "hx-skin-name" });
  const dots = CHARACTER_IDS.map((id) =>
    h("button", { class: "hx-skin-dot", type: "button", title: CHAR_DEFS[id].label, "aria-label": CHAR_DEFS[id].label,
      onclick: (e: Event) => { e.stopPropagation(); setShape(id); } }) as HTMLButtonElement);
  const arrow = (label: string, path: string, dir: 1 | -1) =>
    h("button", { class: "hx-skin-arrow", type: "button", title: label, "aria-label": label,
      onclick: (e: Event) => {
        e.stopPropagation();
        const i = Math.max(0, (CHARACTER_IDS as readonly string[]).indexOf(shape));
        setShape(CHARACTER_IDS[(i + dir + CHARACTER_IDS.length) % CHARACTER_IDS.length]);
      } }, svg(path, 12, { stroke: 2.6 })) as HTMLButtonElement;
  const skinRow = h("div", { class: "hx-skin-row" }, arrow("Previous character", ICONS.chevronLeft, -1), skinName, arrow("Next character", ICONS.chevronRight, 1));
  const dotRow = h("div", { class: "hx-skin-dots" }, ...dots);

  const mochiCard = h("div", { class: "hx-card hx-mochi" },
    h("div", { class: "hx-mochi-head" }, clock, date), spot, skinRow, dotRow);

  const paintSkin = () => {
    skinName.textContent = CHAR_DEFS[shape]?.label ?? shape;
    dots.forEach((d, i) => d.classList.toggle("on", CHARACTER_IDS[i] === shape));
  };
  const setShape = (id: string) => {
    shape = id;
    mochi.setShape?.(id);
    Sound.setCharacter(id);          // and its own sounds
    paintSkin();
    actions.blip();
    // Same setting as Settings → Mochi → Character: the notch and the screen Mochi pick it up from there.
    void postFeatures({ mascot_shape: id });   // backend not up: the character still shows here until the next load
  };
  const loadPrefs = async () => {
    try {
      const p = (await getFeatures()) as { mascot_shape?: string; feature_mascot?: boolean } | null;
      if (!p) return;
      mascotOn = p.feature_mascot === true;
      shape = (CHARACTER_IDS as readonly string[]).includes(p.mascot_shape ?? "") ? String(p.mascot_shape) : "cloud";
      mochi.setShape?.(shape);   // the character chosen in Settings → Mochi
      paintSkin();
      paintVibe();
    } catch { /* backend not up yet: keep what is showing */ }
  };

  // A little groove while a track is playing.
  const paintVibe = () => mochi.setVibing?.(nowPlaying.running && nowPlaying.status === "playing");
  onNowPlaying(paintVibe);

  // The eyes follow the cursor (same window coordinates the island is fed).
  const aim = () => {
    const m = State.mouse;
    if (m.x < -5000 || m.y < -5000) { mochi.look(0, 0); return; }   // "no cursor" (a game is in front)
    const [gx, gy] = gazeToward(host, m.x, m.y);
    mochi.look(gx, gy);
  };

  // ── Weather ─────────────────────────────────────────────────────────────────
  const wxCity = h("span", { class: "hx-eyebrow", text: "Weather" });
  const wxGlyph = h("div", { class: "hx-wx-glyph" });
  const wxTemp = h("div", { class: "hx-temp" });
  const wxCond = h("div", { class: "hx-sub" });
  const weather = opener("hx-card hx-weather", "weather",
    wxGlyph, wxTemp, h("div", { class: "hx-wx-text" }, wxCity, wxCond));

  // ── To-do: the actual list, tick things off right here ──────────────────────
  const todoCount = h("span", { class: "hx-count" });
  const todoHead = opener("hx-todo-head", "todo",
    tile("#34c759", "#1f9d43", svg(ICONS.check, 12, { stroke: 3 }), "xs"),
    h("span", { class: "hx-todo-name", text: "To-do" }), todoCount);
  const todoList = h("div", { class: "hx-todo-list" });
  const todo = h("div", { class: "hx-card hx-todo" }, todoHead, todoList);

  const el = h("div", { class: "view hx-view" }, mochiCard, h("div", { class: "hx-col" }, weather, todo));

  // ── Painters ────────────────────────────────────────────────────────────────
  const paintClock = () => {
    const now = new Date();
    const parts = new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit" }).formatToParts(now);
    const time = parts.filter((p) => p.type !== "dayPeriod").map((p) => p.value).join("").trim();
    const period = parts.find((p) => p.type === "dayPeriod")?.value;
    const key = `${time}|${period ?? ""}`;
    if (clock.dataset.k !== key) {
      clock.dataset.k = key;
      clock.replaceChildren(time, ...(period ? [h("small", { text: period })] : []));
    }
    date.textContent = now.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
  };

  const paintWeather = () => {
    const w = getWeather();
    if (!w.ok) {
      wxTemp.textContent = "--°"; wxGlyph.replaceChildren(); wxCond.textContent = "Unavailable"; wxCity.textContent = "Weather";
      return;
    }
    wxTemp.textContent = `${w.temp}°`;
    wxGlyph.replaceChildren(weatherGlyph(w.kind ?? "cloud", !!w.night, 30));
    wxCond.textContent = w.condition ?? "";
    wxCity.textContent = w.city || "Weather";
  };
  onWeather(paintWeather);

  const SHOWN = 4;
  let lastTodoSig: string | null = null;
  const paintTodo = () => {
    const open = getTodos().filter((t) => !t.done);
    todoCount.textContent = open.length ? String(open.length) : "";
    // Every /todos or /timers poll repainted this list from scratch ("All clear" was destroyed and recreated each
    // time, which can flicker). Same visible content = nothing to do.
    const sig = `${open.length}|${open.slice(0, SHOWN).map((t) => t.text).join("\u0001")}`;
    if (sig === lastTodoSig) return;
    lastTodoSig = sig;
    todoList.replaceChildren();
    if (!open.length) {
      todoList.append(h("div", { class: "hx-todo-none", text: "All clear" }));
      return;
    }
    for (const t of open.slice(0, SHOWN)) {
      const row = h("div", { class: "hx-todo-item" },
        h("button", { class: "hx-check", type: "button", "aria-label": `Done: ${t.text}`, title: "Mark done",
          onclick: (e: Event) => { e.stopPropagation(); actions.blip(); void toggleTodo(t.text, true); } }, svg(ICONS.check, 9, { stroke: 3.4 })),
        h("span", { class: "hx-todo-text", text: t.text }));
      todoList.append(row);
    }
    if (open.length > SHOWN) todoList.append(h("div", { class: "hx-more", text: `+${open.length - SHOWN} more` }));
  };
  onTodos(paintTodo);

  let timer: VisiblePoll | null = null;
  let tick: number | null = null;
  const refresh = () => {
    void refreshWeather();
    void refreshTodos();
  };

  return {
    el,
    sync() {
      // Hide whatever the user switched off in Settings → Features.
      const show = (node: HTMLElement, view: IslandViewName) => { node.hidden = !State.viewAllowed(view); };
      show(weather, "weather"); show(todo, "todo");
      paintClock();
      paintWeather();
      paintTodo();
    },
    setActive(on) {
      tabOn = on;
      timer?.stop(); timer = null;
      if (tick != null) { window.clearInterval(tick); tick = null; }
      mochi.setActive?.(on);
      if (on) {
        void loadPrefs();
        refresh(); paintClock(); paintVibe(); aim();
        asleep = false; clicks = 0; armSleep();
        timer = visiblePoll(() => refresh(), { ms: 6000, runOnShow: false });   // cleared while hidden
        let n = 0;
        tick = window.setInterval(() => { if (!isReallyVisible()) return; aim(); if (++n % 4 === 0) paintClock(); }, 120);
      } else {
        window.clearTimeout(waveTimer);
        window.clearTimeout(sleepTimer);
        mochi.setEmote("idle");
      }
    },
  };
}
