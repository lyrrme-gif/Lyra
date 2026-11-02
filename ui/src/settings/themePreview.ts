// One island theme as a little card picture: the island on the blue desktop, with the real Mochi and a bar in the
// theme's accent colour. The setup ("Pick your Lyra look") and Settings -> General -> Island theme both use it, so
// the two always look the same.

import "./themePreview.css";
import { h } from "../views/dom";
import { createCharacter, type Character } from "../mascot/character";

export interface ThemeLook { id: string; name: string; note: string; bg: string; accent: string }

export const THEME_LOOKS: ThemeLook[] = [
  { id: "pure-black", name: "Black", note: "Absolute black", bg: "#000", accent: "#fff" },
  { id: "midnight", name: "Midnight", note: "Deep Apple glass", bg: "linear-gradient(135deg,#17191e,#08090c)", accent: "#9aaeff" },
  { id: "aurora", name: "Aurora", note: "Cool luminous colour", bg: "linear-gradient(135deg,#101827,#151125)", accent: "#9ff2da" },
  { id: "dynamic", name: "Dynamic", note: "Adapts to what is playing", bg: "linear-gradient(135deg,#151722,#252a40)", accent: "#c1ccff" },
  { id: "frost", name: "Frost", note: "Black fading into glass", bg: "linear-gradient(180deg,#000 0%,#000 46%,rgba(70,58,48,.78) 100%)", accent: "#3b82f6" },
];

/** The picture. `mochi` is the character the caller should destroy when the picture goes away. */
export function themePreview(t: Pick<ThemeLook, "bg" | "accent">, shape: string): { el: HTMLElement; mochi: Character } {
  const m = h("span", { class: "tp-mochi" });
  m.style.width = "26px";
  const isl = h("div", { class: "tp-isl", style: `background:${t.bg};--acc:${t.accent}` }, m, h("i", { class: "tp-bar" }));
  const el = h("div", { class: "tp-stage" }, isl);
  const ch = createCharacter(m, { tight: true, sharp: 2 });
  ch.setShape?.(shape);
  ch.setEmote("idle");
  return { el, mochi: ch };
}
