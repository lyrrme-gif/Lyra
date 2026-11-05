// Phase 2 (dictation polish) — shared helpers for the four Dictation panels.
// The panels are built with the SAME card / item / panel helpers as every other Settings tab
// (passed in by main.ts), so Phase 4's restyling reaches them without any extra work.

import { h } from "../../../shared/views/dom";

export const LYRA = "http://127.0.0.1:8765";

type Kid = Node | string | null | undefined | false;

/** The building blocks main.ts already has; handed to us once (see the phase-2 hook in main.ts). */
export interface P2Kit {
  panel: (title: string, sub: string, ...cards: Kid[]) => HTMLElement;
  card: (title: Kid, ...children: Kid[]) => HTMLElement;
  item: (label: string, hint: string | HTMLElement | null, ...controls: Kid[]) => HTMLElement;
  toggle: (on: boolean, onChange: (v: boolean) => void) => HTMLElement;
  confirmButton: (label: string, confirmLabel: string, onConfirm: () => void, cls?: string) => HTMLElement;
  lyra: (path: string, body?: unknown) => Promise<{ ok: boolean; data: any; error: string }>;
  flashSaved: (ok?: boolean) => void;
  /** Cards built by main.ts that belong at the top of the Dictation tab (they use the Voice preferences group). */
  extra?: HTMLElement[];
  /** Builds the Statistics cards (Phase 3); shown as the "Statistics" section of the Dictation tab. */
  statistics?: () => HTMLElement[];
}

export interface P2Result {
  ok: boolean;
  data: any;
  error: string;
}

/** Call a /p2/ endpoint. Never throws. */
export async function api(path: string, body?: unknown): Promise<P2Result> {
  try {
    const r = await fetch(`${LYRA}${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    return { ok: r.ok && data.ok !== false, data, error: r.ok ? String(data.error ?? "") : String(data.error || r.statusText) };
  } catch {
    return { ok: false, data: {}, error: "Could not reach Lyra — is the backend running?" };
  }
}

/** A line under a control that says what went wrong (or nothing). */
export function statusLine(): { el: HTMLElement; show: (msg: string, bad?: boolean) => void } {
  const el = h("div", { class: "hint", style: "min-height:1.2em" });
  return {
    el,
    show(msg, bad = false) {
      el.textContent = msg;
      el.style.color = bad ? "#f4505e" : "";
    },
  };
}

export function button(label: string, onClick: () => void, cls = ""): HTMLButtonElement {
  const b = h("button", { type: "button", class: cls, text: label }) as HTMLButtonElement;
  b.addEventListener("click", onClick);
  return b;
}

export function textInput(value: string, placeholder: string, width = "10rem"): HTMLInputElement {
  return h("input", { type: "text", value, placeholder, spellcheck: "false", style: `width:${width}` }) as HTMLInputElement;
}

export function select(options: Array<[string, string]>, current: string, onPick: (v: string) => void): HTMLSelectElement {
  const el = h("select", {}, ...options.map(([v, label]) => h("option", { value: v, text: label }))) as HTMLSelectElement;
  el.value = current;
  el.addEventListener("change", () => onPick(el.value));
  return el;
}

/** Saves some /p2/settings keys; shows the server's complaint if it refuses. Returns the saved values. */
export async function saveSettings(kit: P2Kit, updates: Record<string, unknown>, note?: { show: (m: string, bad?: boolean) => void }): Promise<Record<string, any> | null> {
  const r = await api("/p2/settings", updates);
  if (r.ok) {
    kit.flashSaved(true);
    note?.show("");
    return r.data.settings as Record<string, any>;
  }
  kit.flashSaved(false);
  note?.show(r.error, true);
  return null;
}

export async function loadSettings(): Promise<Record<string, any>> {
  const r = await api("/p2/settings");
  return (r.ok ? r.data.settings : {}) as Record<string, any>;
}

// ── Recording a key combination ────────────────────────────────────────────────────────────

const SIDED: Record<string, string> = {
  ControlLeft: "lctrl", ControlRight: "rctrl", AltLeft: "lalt", ShiftLeft: "lshift", ShiftRight: "rshift",
};

/** "KeyD" -> "d", "Digit5" -> "5", "F9" -> "f9", "Space" -> "space"; null = not a key Lyra accepts. */
export function mainKeyName(code: string): string | null {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3).toLowerCase();
  if (/^Digit\d$/.test(code)) return code.slice(5);
  if (/^F([1-9]|1\d|2[0-4])$/.test(code)) return code.toLowerCase();
  return ({ Space: "space", Insert: "insert", ScrollLock: "scrolllock", Pause: "pause" } as Record<string, string>)[code] ?? null;
}

/**
 * Turns the keys pressed while recording into Lyra's text form ("ctrl+alt+f9", "rctrl", "ctrl+alt").
 * `mods` = codes of modifier keys seen, `main` = the first normal key's code (or null).
 */
export function comboText(mods: Set<string>, main: string | null): string | null {
  if (main) {
    const key = mainKeyName(main);
    if (!key) return null;
    const parts: string[] = [];
    if (mods.has("ControlLeft") || mods.has("ControlRight")) parts.push("ctrl");
    if (mods.has("AltLeft")) parts.push("alt");
    if (mods.has("ShiftLeft") || mods.has("ShiftRight")) parts.push("shift");
    return [...parts, key].join("+");
  }
  const sided = [...mods].map((c) => SIDED[c]).filter(Boolean).sort();
  if (!sided.length) return null;
  if (sided.length === 2 && sided.includes("lctrl") && sided.includes("lalt")) return "ctrl+alt";
  return sided.join("+");
}

const KEY_NAMES: Record<string, string> = { lctrl: "Left Ctrl", rctrl: "Right Ctrl", lalt: "Left Alt", lshift: "Left Shift", rshift: "Right Shift", ctrl: "Ctrl", alt: "Alt", shift: "Shift", space: "Space" };
/** "rctrl" -> "Right Ctrl", "ctrl+alt+d" -> "Ctrl + Alt + D". */
export const prettyKey = (t: string): string => t.split("+").map((k) => KEY_NAMES[k] ?? k.toUpperCase()).join(" + ");

/**
 * A key button bound to one Lyra setting ("p2_talk_key" or "p2_dictation_key"). The Voice / Dictation tab and the
 * Shortcuts tab both build one, so the key can be changed in either place and they always show the same thing.
 */
export function settingKeyButton(prefKey: "p2_talk_key" | "p2_dictation_key", def: string, flash?: (ok?: boolean) => void): HTMLButtonElement {
  const b = keyRecorder(def, prettyKey(def), async (combo) => {
    const r = await api("/p2/settings", { [prefKey]: combo || def });
    flash?.(r.ok);
    if (r.ok) window.dispatchEvent(new CustomEvent("lyra-key-changed", { detail: { prefKey, value: combo || def } }));
    else b.title = r.error;
    return r.ok;
  });
  const show = (v: string) => { b.textContent = prettyKey(v); };
  void loadSettings().then((s) => show(String(s[prefKey] || def)));
  window.addEventListener("lyra-key-changed", (e) => {
    const d = (e as CustomEvent<{ prefKey: string; value: string }>).detail;
    if (d?.prefKey === prefKey && b.textContent !== "Press the keys\u2026") show(d.value);
  });
  return b;
}

/** A button that records the next key combination; calls onCombo with its text form. */
export function keyRecorder(current: string, emptyLabel: string, onCombo: (combo: string) => Promise<boolean> | boolean | void): HTMLButtonElement {
  const b = h("button", { type: "button", class: "keycap" }) as HTMLButtonElement;
  const label = (t: string) => { b.textContent = t.startsWith("Press the keys") ? t : t ? prettyKey(t) : emptyLabel; };
  label(current);
  let stop = () => {};
  b.addEventListener("click", () => {
    stop();
    label("Press the keys… (Esc cancels)");
    const mods = new Set<string>();
    let main: string | null = null;
    const down = (e: KeyboardEvent) => {
      e.preventDefault();
      if (e.code === "Escape") { finish(null); return; }
      if (SIDED[e.code] || e.code === "AltRight" || e.code.startsWith("Meta")) mods.add(e.code);
      else if (!main) main = e.code;
    };
    const up = (e: KeyboardEvent) => {
      e.preventDefault();
      if (!mods.size && !main) return;
      finish(comboText(mods, main) ?? "");
    };
    const finish = (combo: string | null) => {
      stop();
      if (combo === null) label(current);
      else if (combo === "") { label(current); b.title = "That key cannot be used"; }
      else {
        const before = current;
        current = combo;
        label(combo);
        void Promise.resolve(onCombo(combo)).then((ok) => { if (ok === false) { current = before; label(before); } });
      }
    };
    stop = () => { window.removeEventListener("keydown", down, true); window.removeEventListener("keyup", up, true); stop = () => {}; };
    window.addEventListener("keydown", down, true);
    window.addEventListener("keyup", up, true);
  });
  return b;
}

export function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export const PRIVACY_HISTORY =
  "Kept as text on this PC only, never uploaded. Audio is never saved. Old entries delete themselves after the time you choose.";
export const PRIVACY_AI =
  "When ON, the text you dictated (never audio) is sent to the AI model you chose under AI to tidy it up. When OFF, nothing is sent.";
