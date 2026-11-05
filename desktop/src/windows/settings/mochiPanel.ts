// Settings → General → Mochi: everything about the little character, with live previews.
//
// Every preview is the real Mochi (the same canvas engine the island, the floating mascot and the
// subtitles use), so what you see here is what you get. Previews only animate while they are on screen.

import { h } from "../../shared/views/dom";
import { CHAR_DEFS } from "../../shared/mascot/characters";
import { createCharacter, type Character, type EmoteName } from "../../shared/mascot/character";
import { Sound } from "../../shared/core/sound";

type Kid = Node | string | null | undefined | false;

export interface MochiPrefs { data: Record<string, unknown>; set(key: string, value: unknown): void }

export interface MochiDeps {
  card(title: Kid, ...children: Kid[]): HTMLElement;
  item(label: string, hint: string | HTMLElement | null, ...controls: Kid[]): HTMLElement;
  toggle(on: boolean, onChange: (v: boolean) => void): HTMLElement;
  /** The "features" settings group (mascot_size, mascot_shape live here). */
  prefs: MochiPrefs;
  isOn(): boolean;
  setOn(on: boolean): void;
  isStay(): boolean;
  setStay(on: boolean): void;
}

/** Body width (px) of the Mochi on screen for each size = the mascot's SIZE_PX × the body/box ratio. */
const BODY = 0.684;
const SIZES: Array<{ id: string; name: string; px: number }> = [
  { id: "small", name: "Small", px: 64 },
  { id: "medium", name: "Medium", px: 84 },
  { id: "large", name: "Large", px: 112 },
];

/** Every preview follows the chosen character (cloud / fox / cat); the picker tiles pin their own. */
let currentShape = "cloud";
const followers = new Set<Character>();
function applyShape(name: string): void {
  currentShape = name;
  followers.forEach((c) => c.setShape?.(name));
  shapeListeners.forEach((fn) => fn(name));
}

/** Anything outside this panel that should wear the chosen character (the Settings sidebar avatar).
 *  Called right away with the current character, then again whenever it changes. */
const shapeListeners = new Set<(shape: string) => void>();
export function onMochiShape(fn: (shape: string) => void): void {
  shapeListeners.add(fn);
  fn(currentShape);
}

/** A real Mochi drawn into `host`, animating only while it is visible. */
function liveMochi(host: HTMLElement, bodyPx: number, emote: EmoteName = "idle", vibing = false, sharp = 2, pinnedShape?: string): Character {
  host.style.width = `${bodyPx}px`;
  const ch = createCharacter(host, { tight: true, sharp });
  ch.setShape?.(pinnedShape ?? currentShape);
  if (!pinnedShape) followers.add(ch);
  ch.setEmote(emote);
  if (vibing) ch.setVibing?.(true);
  new IntersectionObserver((entries) => ch.setActive?.(entries.some((e) => e.isIntersecting))).observe(host);
  return ch;
}

function searchable(el: HTMLElement, text: string): HTMLElement {
  el.classList.add("item");
  el.dataset.s = text.toLowerCase();
  return el;
}

// ── Hero: big live Mochi, on/off, "try a mood" ───────────────────────────────
function hero(d: MochiDeps): HTMLElement {
  const spot = h("div", { class: "mc-hero-mochi" });
  const stage = h("div", { class: "mc-stage" }, h("div", { class: "mc-floor" }), spot);
  const ch = liveMochi(spot, 150, "idle", false, 2);

  const status = h("span", { class: "mc-status" });
  const setSwitch = (el: HTMLElement, on: boolean) => { el.classList.toggle("on", on); el.setAttribute("aria-pressed", String(on)); };
  // "Show Mochi" turns the Mochi on or off. "Mochi on screen" lets it live out on the desktop; it needs the first one.
  const power = d.toggle(d.isOn(), (v) => { d.setOn(v); paint(); });
  const onScreen = d.toggle(d.isOn() && d.isStay(), (v) => { d.setStay(v); paint(); });
  const screenRow = h("div", { class: "mc-power" }, h("label", { text: "Mochi on screen" }), onScreen);
  const paint = () => {
    const on = d.isOn();
    status.textContent = on ? "On" : "Off";
    status.classList.toggle("on", on);
    stage.classList.toggle("off", !on);
    setSwitch(power, on);
    setSwitch(onScreen, on && d.isStay());
    onScreen.toggleAttribute("disabled", !on);
    screenRow.classList.toggle("disabled", !on);
    screenRow.title = on ? "" : "Turn on Show Mochi first";
  };

  const chips = h("div", { class: "mc-chips", role: "group", "aria-label": "Try a mood" });
  const tries: Array<{ label: string; emote: EmoteName }> = [
    { label: "Chilling", emote: "idle" }, { label: "Happy", emote: "happy" }, { label: "Thinking", emote: "think" },
    { label: "Listening", emote: "listen" }, { label: "Sleepy", emote: "sleep" }, { label: "Celebrate", emote: "celebrate" },
  ];
  const buttons = new Map<string, HTMLButtonElement>();
  let vibe = false;
  const music = h("button", { class: "mc-chip", type: "button", text: "Music" }) as HTMLButtonElement;
  music.addEventListener("click", () => {
    vibe = !vibe;
    ch.setVibing?.(vibe);
    music.classList.toggle("on", vibe);
  });
  for (const t of tries) {
    const b = h("button", { class: "mc-chip", type: "button", text: t.label }) as HTMLButtonElement;
    b.addEventListener("click", () => {
      ch.setEmote(t.emote);
      buttons.forEach((x, k) => x.classList.toggle("on", k === t.emote));
    });
    buttons.set(t.emote, b);
    chips.append(b);
  }
  buttons.get("idle")?.classList.add("on");
  chips.append(music);

  const copy = h("div", { class: "mc-copy" },
    h("div", { class: "mc-title" }, h("strong", { text: "Your Mochi" }), status),
    h("p", { text: "A little companion that lives in your notch, follows your cursor and reacts to what Lyra is doing." }),
    h("div", { class: "mc-power" }, h("label", { text: "Show Mochi" }), power),
    screenRow,
    h("div", { class: "mc-try" }, h("span", { text: "Try a mood" }), chips),
  );
  paint();
  return searchable(h("div", { class: "mc-hero" }, stage, copy), "mochi mascot show enable on off on screen desktop overlay preview try mood");
}

// ── Size: three live tiles at their true relative sizes ──────────────────────
function sizeTiles(d: MochiDeps): HTMLElement {
  const current = String(d.prefs.data.mascot_size || "medium");
  const wrap = h("div", { class: "mtiles", role: "radiogroup", "aria-label": "Mochi size" });
  const tiles = new Map<string, HTMLButtonElement>();
  for (const s of SIZES) {
    const mochi = h("span", { class: "mt-mochi" });
    const scene = h("span", { class: "mt-scene" }, h("span", { class: "mt-ground" }), mochi);
    const b = h("button", { class: "mtile", type: "button", role: "radio" },
      scene,
      h("span", { class: "mt-label", text: s.name }),
      h("span", { class: "mt-sub", text: `${s.px} px` }),
    ) as HTMLButtonElement;
    liveMochi(mochi, Math.round(s.px * BODY), "idle", false, 2);
    b.addEventListener("click", () => {
      d.prefs.set("mascot_size", s.id);
      tiles.forEach((t, k) => { t.classList.toggle("on", k === s.id); t.setAttribute("aria-checked", String(k === s.id)); });
    });
    tiles.set(s.id, b);
    wrap.append(b);
  }
  tiles.forEach((t, k) => { t.classList.toggle("on", k === current); t.setAttribute("aria-checked", String(k === current)); });
  return wrap;
}

function characterTiles(d: MochiDeps): HTMLElement {
  const wrap = h("div", { class: "mc-chars", role: "radiogroup", "aria-label": "Character" });
  const tiles = new Map<string, HTMLButtonElement>();
  const OPTIONS = Object.entries(CHAR_DEFS).map(([id, c]) => ({ id, name: c.label, sub: c.blurb }));
  for (const o of OPTIONS) {
    const host = h("span", { class: "mt-mochi" });
    liveMochi(host, 64, "idle", false, 2, o.id);
    const b = h("button", { class: "mtile", type: "button", role: "radio" },
      h("span", { class: "mt-scene" }, h("span", { class: "mt-ground" }), host),
      h("span", { class: "mt-label", text: o.name }),
      h("span", { class: "mt-sub", text: o.sub }),
    ) as HTMLButtonElement;
    b.addEventListener("click", () => {
      d.prefs.set("mascot_shape", o.id);
      Sound.setCharacter(o.id);
      applyShape(o.id);
      tiles.forEach((t, k) => { t.classList.toggle("on", k === o.id); t.setAttribute("aria-checked", String(k === o.id)); });
    });
    tiles.set(o.id, b);
    wrap.append(b);
  }
  tiles.forEach((t, k) => { t.classList.toggle("on", k === currentShape); t.setAttribute("aria-checked", String(k === currentShape)); });
  return wrap;
}

export function mochiCards(d: MochiDeps): HTMLElement[] {
  currentShape = String(d.prefs.data.mascot_shape || "cloud");
  if (!CHAR_DEFS[currentShape]) currentShape = "cloud";      // an old saved character that no longer exists
  shapeListeners.forEach((fn) => fn(currentShape));
  followers.clear();
  // Everything is a card of General now, so the titles say what they are about. Two cards instead of seven: the
  // mood gallery, the music preview and the "ways to play" tips were information only (no setting in them) and are gone;
  // "Try a mood" and the Music chip in the first card still let you see the reactions.
  const character = searchable(characterTiles(d), "character cloud fox cat kitten who look sounds");   // full-width tile row, as before
  return [
    d.card("Mochi", hero(d)),
    d.card("Mochi appearance",
      character,
      d.item("Size", "How big the Mochi is when it is on your screen.", sizeTiles(d)),
    ),
  ];
}
