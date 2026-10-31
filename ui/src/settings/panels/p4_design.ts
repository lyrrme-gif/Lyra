// Phase 4 · design-language integration for the Settings window.
// Everything here works from the DOM that main.ts already builds (panels are `.panel[data-title]`,
// rows are `.item[data-s]`, tabs are `button.tab`), so main.ts only needs: import + register().
//
// Adds:  the keyboard-shortcut list (Shortcuts) · privacy lines (AI, Voice, Screen, Privacy & Memory) · Ctrl+K palette ·
//        accessibility fixes (switch roles, landmarks, labels, arrow-key navigation in the sidebar).

import { buildShortcutList, resetAllShortcuts } from "../shortcutList";
import { closePalette, isPaletteOpen, openPalette, type PaletteEntry } from "../../design/palette";
const PRIVACY: Record<string, string> = {
  ai: "Your API keys stay on this device. Lyra sends only the text of your request to the provider you chose, and only when you use an AI feature.",
  voice: "Dictation audio is never saved. Only text can be sent to an AI model, and only if you turned on a feature that needs it.",
  screen: "Lyra reads your screen only when you ask (for example “what's on my screen?”) or when she is explaining something, and only text from it can reach an AI model.",
  "privacy & memory": "Lyra sends no telemetry. What it remembers is kept on this device; only text relevant to your request can go to an AI model, and only when you use one.",
};
const LOCK = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>';

/** Set an attribute only when it differs: observers watch these nodes, and a no-op write still queues a mutation. */
const setAttr = (el: Element | null | undefined, name: string, value: string) => { if (el && el.getAttribute(name) !== value) el.setAttribute(name, value); };

const panelOf = (root: ParentNode, title: string) => root.querySelector<HTMLElement>(`.panel[data-title="${title}"]`);
const tabs = (root: ParentNode) => Array.from(root.querySelectorAll<HTMLButtonElement>("button.tab"));
const tabLabel = (b: HTMLElement) => (b.querySelector(".tab-label")?.textContent ?? b.textContent ?? "").trim();

function goTab(root: ParentNode, title: string): void {
  const t = tabs(root).find((b) => tabLabel(b).toLowerCase() === title.toLowerCase());
  t?.click();
}

function addShortcuts(root: HTMLElement): void {
  const p = panelOf(root, "shortcuts");
  if (!p || p.querySelector(".p4-kbd-card")) return;
  const card = buildShortcutList();
  card.classList.add("p4-kbd-card");
  const sub = p.querySelector(":scope > .sub");
  if (sub) sub.after(card); else p.prepend(card);
}

// ── Privacy lines ────────────────────────────────────────────────────────────
function addPrivacy(root: HTMLElement): void {
  for (const [title, text] of Object.entries(PRIVACY)) {
    const p = panelOf(root, title);
    if (!p || p.querySelector(".p4-privacy")) continue;
    const line = document.createElement("p");
    line.className = "p4-privacy";
    const ic = document.createElement("span"); ic.innerHTML = LOCK; ic.setAttribute("aria-hidden", "true");
    const t = document.createElement("span"); t.textContent = text;
    line.append(ic, t);
    p.append(line);
  }
}

// ── Accessibility ────────────────────────────────────────────────────────────
function a11y(root: HTMLElement): void {
  const fix = () => {
    const side = root.querySelector(".sidebar");
    setAttr(side, "aria-label", "Settings sections"); setAttr(side, "role", "navigation");
    const content = root.querySelector(".content");
    setAttr(content, "role", "main"); setAttr(content, "tabindex", "-1");
    setAttr(root.querySelector("input.search"), "aria-label", "Search settings");
    root.querySelectorAll(".strip, .saved").forEach((e) => setAttr(e, "aria-live", "polite"));
    setAttr(root.querySelector(".nav-arrows button:first-child"), "aria-label", "Back");
    setAttr(root.querySelector(".nav-arrows button:last-child"), "aria-label", "Forward");
    tabs(root).forEach((b) => { const on = b.classList.contains("on"); if (on) setAttr(b, "aria-current", "page"); else if (b.hasAttribute("aria-current")) b.removeAttribute("aria-current"); });
    root.querySelectorAll<HTMLButtonElement>("button.switch").forEach((s) => {
      setAttr(s, "role", "switch");
      setAttr(s, "aria-checked", String(s.classList.contains("on") || s.getAttribute("aria-pressed") === "true"));
      if (!s.getAttribute("aria-label") && !s.getAttribute("aria-labelledby")) {
        const label = s.closest(".row")?.querySelector("label")?.textContent?.trim();
        if (label) s.setAttribute("aria-label", label);
      }
    });
    root.querySelectorAll<HTMLElement>("button:not([aria-label]):not([title])").forEach((b) => {
      if (!(b.textContent ?? "").trim()) b.setAttribute("aria-label", "Button");
    });
  };
  fix();
  let queued = false;
  new MutationObserver(() => { if (queued) return; queued = true; requestAnimationFrame(() => { queued = false; fix(); }); })
    .observe(root, { subtree: true, childList: true, attributes: true, attributeFilter: ["class", "aria-pressed"] });

  // Up / Down move through the sidebar like a list.
  root.querySelector(".sidebar")?.addEventListener("keydown", (e) => {
    const k = (e as KeyboardEvent).key;
    if (k !== "ArrowDown" && k !== "ArrowUp") return;
    const list = tabs(root).filter((b) => !b.disabled);
    const i = list.indexOf(document.activeElement as HTMLButtonElement);
    if (i < 0) return;
    e.preventDefault();
    list[(i + (k === "ArrowDown" ? 1 : list.length - 1)) % list.length].focus();
  });
}

// ── Palette ──────────────────────────────────────────────────────────────────
function paletteEntries(root: HTMLElement): PaletteEntry[] {
  window.dispatchEvent(new Event("lyra-settings-build-all"));   // every tab must be built to be searchable
  const out: PaletteEntry[] = [];
  const seen = new Set<string>();
  for (const b of tabs(root)) { const l = tabLabel(b); out.push({ title: l, sub: "Settings page", run: () => b.click() }); }
  root.querySelectorAll<HTMLElement>(".panel[data-title]").forEach((p) => {
    const page = tabs(root).find((b) => tabLabel(b).toLowerCase() === p.dataset.title);
    const pageName = page ? tabLabel(page) : (p.dataset.title ?? "");
    p.querySelectorAll<HTMLElement>(".item").forEach((it) => {
      const label = it.querySelector(".row > label")?.textContent?.trim();
      if (!label) return;
      const key = `${pageName}|${label}`;
      if (seen.has(key)) return; seen.add(key);
      out.push({
        title: label, sub: pageName, keywords: it.dataset.s,
        run: () => { page?.click(); window.setTimeout(() => { it.scrollIntoView({ block: "center" }); it.classList.add("p4-flash"); window.setTimeout(() => it.classList.remove("p4-flash"), 1400); }, 60); },
      });
    });
  });
  const act = (title: string, keywords: string, run: () => void) => out.push({ title, sub: "Action", keywords, run });
  act("Reset keyboard shortcuts to defaults", "shortcuts hotkeys keys reset", () => { void resetAllShortcuts(); goTab(root, "Shortcuts"); });
  return out;
}

// ── Entry point ──────────────────────────────────────────────────────────────
let started = false;

export function register(): void {
  const root = document.getElementById("settings-root");
  if (!root) return;

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") {
      if (!root.querySelector(".shell")) return;
      e.preventDefault();
      if (isPaletteOpen()) closePalette(); else openPalette(() => paletteEntries(root));
    }
  });

  const start = () => {
    if (started) return;
    started = true;
    addShortcuts(root);
    addPrivacy(root);
    a11y(root);
    const wrap = root.querySelector(".sidebar input.search")?.parentElement;
    if (wrap && !wrap.querySelector(".p4-kbd-hint")) {
      const hint = document.createElement("span");
      hint.className = "p4-kbd-hint"; hint.setAttribute("aria-hidden", "true");
      for (const k of ["Ctrl", "K"]) { const kb = document.createElement("kbd"); kb.className = "p4-key"; kb.textContent = k; hint.append(kb); }
      wrap.append(hint);
    }
  };
  if (root.querySelector(".shell")) start();
  else new MutationObserver((_m, obs) => { if (root.querySelector(".shell")) { obs.disconnect(); start(); } }).observe(root, { childList: true, subtree: true });
}

