// Phase 2 — "Writing style" panel: optional AI clean-up, and Natural / Polished per app.

import { segmented } from "../uiKit";
import { h, clear } from "../../views/dom";
import { PRIVACY_AI, api, loadSettings, saveSettings, statusLine, type P2Kit } from "./p2_common";

type Style = "natural" | "polished";
const STYLES: Array<[Style, string]> = [["natural", "Natural"], ["polished", "Polished"]];

export function stylesPanel(kit: P2Kit): HTMLElement {
  const { card, item, toggle } = kit;
  const body = h("div", {});
  const root = kit.panel(
    "Writing style",
    "",
    body,
  );

  void (async () => {
    const s = await loadSettings();
    const note = statusLine();
    let fallback: Style = s.p2_cleanup_style === "polished" ? "polished" : "natural";

    const ai = card(
      "AI clean-up",
      item("Clean up with AI", PRIVACY_AI,
        toggle(s.p2_ai_cleanup === true, (v) => void saveSettings(kit, { p2_ai_cleanup: v }, note))),
      item("Default style", "",
        segmented(STYLES, fallback, (v) => { fallback = v; void saveSettings(kit, { p2_cleanup_style: v }, note); })),
      note.el,
    );

    clear(body);
    body.append(ai);
    const r = await api("/p2/styles");
    if (r.ok) { fallback = r.data.default === "polished" ? "polished" : "natural"; }
  })();
  return root;
}
