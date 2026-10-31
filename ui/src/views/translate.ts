// Translate tab: type or paste, the language is detected, pick the target. Small like the other
// utility tabs. The last target language is remembered.

import "./translate.css";
import { h, svg } from "./dom";
import { ICONS } from "./icons";
import { Bridge } from "../core/bridge";
import type { ViewHost } from "./views";

const BASE = "http://127.0.0.1:8765";
const LANGS = ["English", "French", "Spanish", "Arabic", "German", "Italian", "Portuguese", "Dutch", "Russian", "Turkish", "Japanese", "Korean", "Chinese", "Hindi"];
const KEY = "lyra-translate-to";

function savedTarget(): string {
  try { const v = localStorage.getItem(KEY); if (v && LANGS.includes(v)) return v; } catch { /* storage unavailable */ }
  return "English";
}

/** A translation Lyra made by voice ("translate hello to French"): the tab shows it when it opens. */
export interface TranslatePrefill { src: string; dst: string; text: string; result: string }
let prefill: TranslatePrefill | null = null;

// ── Voice result = the Settings → Features preview, 1:1 ─────────────────────────────────────────
// A voice translation shows the same black pill as the preview (settings/featurePreviews.ts, `translate`):
// source, arrow, translation on one row. Every preview measure is multiplied by PILL_SCALE
// (island px = preview px × 2), so the layout, spacing and type keep the preview's proportions.
const PILL_SCALE = 2;
const PILL = {
  minW: 112 * PILL_SCALE, minH: 24 * PILL_SCALE, maxW: 440,
  padX: 9 * PILL_SCALE, padY: 6 * PILL_SCALE, gap: 6 * PILL_SCALE,
  arrow: 10 * PILL_SCALE, font: 10 * PILL_SCALE, radius: 13 * PILL_SCALE, maxLines: 4,
};
let voiceMode = false;
let voiceSrc = "", voiceRes = "";
let resizeCb: (() => void) | null = null;

/** True while a voice translation is shown as the preview's pill (the island sizes itself to it). */
export function translatePillActive(): boolean { return voiceMode; }

let measureCtx: CanvasRenderingContext2D | null = null;
function textWidth(t: string): number {
  measureCtx ??= document.createElement("canvas").getContext("2d");
  if (!measureCtx) return t.length * PILL.font * 0.6;
  const fam = getComputedStyle(document.documentElement).getPropertyValue("--font").trim() || "system-ui, sans-serif";
  measureCtx.font = `700 ${PILL.font}px ${fam}`;
  return measureCtx.measureText(t).width;
}
/** Short text fits the preview's single row; longer text drops the source and wraps the translation. */
function pillLayout(): { wrap: boolean; w: number; h: number } {
  const inner = PILL.maxW - 2 * PILL.padX;
  const one = textWidth(voiceSrc) + PILL.gap + PILL.arrow + PILL.gap + textWidth(voiceRes);
  if (voiceSrc && one <= inner) return { wrap: false, w: Math.max(PILL.minW, Math.ceil(one + 2 * PILL.padX)), h: PILL.minH };
  const lines = Math.min(PILL.maxLines, Math.max(1, Math.ceil(textWidth(voiceRes) / (inner * 0.92))));
  return { wrap: true, w: PILL.maxW, h: Math.max(PILL.minH, Math.round(lines * PILL.font * 1.25 + 2 * PILL.padY)) };
}
/** Island size for the pill (same corner radius as the preview's island, scaled). */
export function translatePillSize(): { w: number; h: number; r: number } {
  const { w, h } = pillLayout();
  return { w, h, r: Math.min(PILL.radius, Math.round(h / 2) + 2) };
}

export function setTranslatePrefill(p: TranslatePrefill): void {
  prefill = p;
  voiceMode = true;                   // set now so the island's first size already is the pill
  voiceSrc = p.text.trim(); voiceRes = p.result.trim();
  resizeCb?.();                       // the tab may already be open on a bigger card
}

export function buildTranslate(onResize?: () => void): ViewHost {
  resizeCb = onResize ?? null;
  const from = h("span", { class: "tr-from", text: "Detect language" });
  const select = h("select", { class: "tr-to", "aria-label": "Translate into" }, ...LANGS.map((l) => h("option", { value: l, text: l }))) as HTMLSelectElement;
  select.value = savedTarget();
  const input = h("textarea", { class: "tr-in", rows: 2, placeholder: "Type or paste text to translate", spellcheck: "false" }) as HTMLTextAreaElement;
  const out = h("div", { class: "tr-out" });
  const copy = h("button", { class: "tr-copy", type: "button", title: "Copy", "aria-label": "Copy translation" }, svg(ICONS.doc, 14)) as HTMLButtonElement;
  const paste = h("button", { class: "tr-paste", type: "button", text: "Paste" }) as HTMLButtonElement;
  const row = h("div", { class: "tr-row" }, from, svg(ICONS.chevronRight, 12, { stroke: 2.4 }), select, paste);
  const srcEl = h("span", { class: "tr-src" });
  const arrow = svg(ICONS.chevronRight, 13, { stroke: 2.4 });
  arrow.classList.add("tr-arrow");
  const result = h("div", { class: "tr-res" }, srcEl, arrow, out, copy);
  const card = h("div", { class: "card tr-card" }, row, input, result);
  // The preview's pill: [source] → [translation]. Same arrow glyph as the preview.
  const pSrc = h("span", { class: "tp-tx" });
  const pRes = h("span", { class: "tp-tx tp-res" });
  const pArrow = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  pArrow.setAttribute("class", "tp-arr");
  pArrow.setAttribute("viewBox", "0 0 24 24");
  pArrow.setAttribute("aria-hidden", "true");
  pArrow.innerHTML = '<path d="M5 12h14m-5-5l5 5-5 5" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>';
  const pill = h("div", { class: "tp", title: "Click to edit" }, pSrc, pArrow, pRes);
  const el = h("div", { class: "view" }, card, pill);

  /** Paint the pill from the current voice result (restarts the translation's fade-in). */
  const paintPill = () => {
    const { wrap } = pillLayout();
    pill.classList.toggle("wrap", wrap);
    pSrc.textContent = wrap ? "" : voiceSrc;
    pRes.textContent = voiceRes;
    pSrc.dir = "auto"; pRes.dir = "auto";
    pRes.style.animation = "none"; void pRes.offsetWidth; pRes.style.animation = "";   // replay tp-in
  };
  const setVoice = (on: boolean, resize = true) => {
    voiceMode = on;
    el.classList.toggle("tr-voice", on);
    if (on) paintPill();
    if (resize) resizeCb?.();
  };

  let seq = 0, timer = 0;
  /** Short phrases read as the preview's pill: "Hello → Bonjour". Longer text just shows the translation. */
  const show = (text: string, kind: "" | "busy" | "err" = "", source = "") => {
    const pill = kind === "" && !!source && source.length + text.length <= 30 && !source.includes("\n");   // "Hello → Bonjour" fits on one row
    result.classList.toggle("pill", pill);
    srcEl.textContent = pill ? source : "";
    out.textContent = text;
    out.className = `tr-out${kind ? ` ${kind}` : ""}`;
    result.classList.toggle("has", !!text && kind === "");
    out.dir = "auto";
  };
  show("Translation appears here");
  out.classList.add("hint");

  async function run() {
    const text = input.value.trim();
    const mine = ++seq;
    if (!text) { show("Translation appears here"); out.classList.add("hint"); from.textContent = "Detect language"; return; }
    show("Translating…", "busy");
    try {
      const r = await fetch(`${BASE}/translate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, to: select.value }) });
      const d = (await r.json()) as { ok?: boolean; src?: string; text?: string; error?: string };
      if (mine !== seq) return;   // a newer request is already running
      if (d.ok && d.text) { show(d.text, "", text); from.textContent = d.src ? d.src : "Detected"; }
      else show(d.error ? `Couldn't translate: ${d.error}` : "Couldn't translate that.", "err");
    } catch {
      if (mine === seq) show("Lyra's backend isn't running.", "err");
    }
  }

  input.addEventListener("input", () => { window.clearTimeout(timer); timer = window.setTimeout(() => void run(), 600); });
  input.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); window.clearTimeout(timer); void run(); }
  });
  select.addEventListener("change", () => {
    try { localStorage.setItem(KEY, select.value); } catch { /* ignore */ }
    if (input.value.trim()) void run();
  });
  copy.addEventListener("click", async (e) => {
    e.stopPropagation();
    const t = out.textContent || "";
    if (!t || out.classList.contains("hint") || out.classList.contains("err") || out.classList.contains("busy")) return;
    try { await navigator.clipboard.writeText(t); copy.classList.add("ok"); window.setTimeout(() => copy.classList.remove("ok"), 900); } catch { /* clipboard blocked */ }
  });
  paste.addEventListener("click", async (e) => {
    e.stopPropagation();
    try { input.value = await navigator.clipboard.readText(); window.clearTimeout(timer); void run(); } catch { input.focus(); }
  });
  el.addEventListener("click", (e) => {
    if ((e.target as HTMLElement).closest("button, select, a")) return;
    if (voiceMode) { setVoice(false); void Bridge.focusWindow(true); window.setTimeout(() => input.focus(), 120); return; }   // pill -> the editable card
    void Bridge.focusWindow(true);
    window.setTimeout(() => input.focus(), 0);
  });

  function applyPrefill() {
    if (!prefill) return;
    const p = prefill; prefill = null;
    input.value = p.text;
    if (LANGS.includes(p.dst)) { select.value = p.dst; try { localStorage.setItem(KEY, p.dst); } catch { /* ignore */ } }
    from.textContent = p.src || "Detected";
    seq++;   // a voice result replaces whatever was in flight
    show(p.result, "", p.text);        // the card behind the pill stays filled, for when it is clicked
    setVoice(true, false);             // geometry was already requested by setTranslatePrefill
  }

  return {
    el,
    sync() { applyPrefill(); },
    // Closed or left: the next manual visit is the normal card again.
    setActive(on: boolean) { if (!on && voiceMode) setVoice(false, false); },
    focus() { if (voiceMode) return; void Bridge.focusWindow(true); input.focus(); },
  };
}
