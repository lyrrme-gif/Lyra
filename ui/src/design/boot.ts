// Runs first in every window (linked from each HTML file). It only sets attributes
// and CSS variables on <html>: theme, density, motion, and the accent hue that
// drives the tonal palette in tokens.css. It never touches the page's DOM.
//
// The look is fixed: the Settings window is white with the blue accent and Cozy spacing, the island is always
// dark. Nothing here is a preference any more. Only reduced motion still varies, and it follows the Windows
// "show animations" setting by itself.

import { hueOf } from "./color";

const ACCENT = "#3b8cff";   // blue
const root = document.documentElement;
const isSettings = /settings/i.test(location.pathname) || document.body?.classList.contains("settings-window");
const reduceQuery = window.matchMedia?.("(prefers-reduced-motion: reduce)");

function applyAll(): void {
  if (isSettings && root.style.getPropertyValue("--accent") !== ACCENT) root.style.setProperty("--accent", ACCENT);
  root.dataset.dTheme = isSettings ? "light" : "dark";   // only the Settings window is light: the island is a dark notch
  root.dataset.dDensity = "cozy";
  if (reduceQuery?.matches) root.dataset.dMotion = "reduce"; else delete root.dataset.dMotion;
  const hue = String(hueOf(ACCENT));
  if (hue !== root.style.getPropertyValue("--p4-h")) root.style.setProperty("--p4-h", hue);
}

applyAll();
reduceQuery?.addEventListener?.("change", applyAll);
