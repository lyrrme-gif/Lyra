// Settings → Shortcuts → Keyboard shortcuts: Every row is editable: click its key, press the new
// keys, and Lyra's key listener uses them straight away. A row warns about clashes (with another Lyra shortcut or
// a well-known Windows one), and has its own "Reset" once it differs from the built-in key.
//
// Where each key lives (the list hides this): talk / dictate in the voice settings (p2_*_key), the rest in the
// features settings (shortcut_*_combo). Removing the keys of a row (Backspace while recording) switches that
// shortcut off.

import { getFeatures, postFeatures } from "../core/featuresCache";
import { Recorder, SHORTCUTS, canon, findConflicts, formatCombo, invalidReason, type RecResult, type ShortcutPrefs } from "../design/shortcuts";
import { api, loadSettings } from "./panels/p2_common";
import { Row } from "./uiKit";

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, cls = "", text?: string): HTMLElementTagNameMap[K] => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};

const SIDED_CODES: Record<string, string> = { ControlLeft: "lctrl", ControlRight: "rctrl", AltLeft: "lalt", ShiftLeft: "lshift", ShiftRight: "rshift" };
const MODS = new Set(["ctrl", "alt", "shift", "meta"]);
/** Features-group setting for each row that is not a voice key. */
const FEATURE_KEY: Record<string, string> = {
  typing: "shortcut_typing_combo", wheel: "shortcut_wheel_combo", circle: "shortcut_circle_combo",
  record: "shortcut_record_combo", pause: "shortcut_pause_combo",
};
const VOICE_KEY: Record<string, "p2_talk_key" | "p2_dictation_key"> = { talk: "p2_talk_key", dictate: "p2_dictation_key" };
/** Windows registers these two itself: they need modifiers + one key, never a left/right-specific modifier. */
const NEEDS_KEY = new Set(["record", "pause"]);

/** Why this row cannot take the combo, or null. */
function rowProblem(id: string, combo: string): string | null {
  if (!combo) return null;                                    // "no shortcut" is always allowed
  const bad = invalidReason(combo);
  if (bad) return bad;
  if (NEEDS_KEY.has(id)) {
    const parts = canon(combo).split("+");
    const keys = parts.filter((p) => !MODS.has(p));
    if (keys.length !== 1 || parts.length < 2 || /^[lr](ctrl|alt|shift)$/.test(keys[0])) return "Use Ctrl, Alt, Shift or Win together with one key, for example Ctrl + Alt + R";
  }
  return null;
}

export function buildShortcutList(): HTMLElement {
  const values: Record<string, string> = {};
  for (const d of SHORTCUTS) values[d.id] = d.def;
  const prefs = (): ShortcutPrefs => Object.fromEntries(SHORTCUTS.map((d) => [d.id, { combo: values[d.id], enabled: true }]));

  // Same look as before: one card, one row per shortcut, the keys as a keycap button on the right.
  const card = el("section", "card");
  card.dataset.s = "keyboard shortcuts hotkeys keys talk dictate chat tab wheel circle recording record pause";
  card.append(el("h3", "", "Keyboard shortcuts"));
  const list = el("div", "group");
  card.append(list);

  const paints = new Map<string, () => void>();
  const repaintAll = () => paints.forEach((fn) => fn());

  // Where each value is stored: read once when the list is built, written on every change.
  async function save(id: string, combo: string): Promise<{ ok: boolean; error?: string }> {
    const voice = VOICE_KEY[id];
    if (voice) {
      const r = await api("/p2/settings", { [voice]: combo || SHORTCUTS.find((d) => d.id === id)!.def });
      if (r.ok) window.dispatchEvent(new CustomEvent("lyra-key-changed", { detail: { prefKey: voice, value: combo || SHORTCUTS.find((d) => d.id === id)!.def } }));
      return { ok: !!r.ok, error: r.error };
    }
    const ok = await postFeatures({ [FEATURE_KEY[id]]: combo });
    return { ok, error: ok ? undefined : "Lyra could not save this. Is she running?" };
  }

  for (const d of SHORTCUTS) {
    const press = el("button", "keycap") as HTMLButtonElement;      // click it, press the keys
    press.type = "button";
    const reset = el("button", "sc-reset", "Reset") as HTMLButtonElement;
    reset.type = "button";
    reset.title = `Back to ${formatCombo(d.def).join(" + ")}`;
    const row = Row(d.label, `${d.hint} Built in: ${formatCombo(d.def).join(" + ")}. Click the key to change it.`, press, reset);
    const msg = el("div", "p4-rec-msg");
    msg.setAttribute("aria-live", "polite");
    row.append(msg);
    row.dataset.s += " shortcut keyboard hotkey key";
    list.append(row);

    let recorder: Recorder | null = null;
    let codes = new Set<string>();

    const showKeys = (combo: string, preview = false) => {
      press.textContent = combo ? formatCombo(combo).join(" + ") + (preview ? " …" : "") : preview ? "Press the keys… (Esc cancels)" : "Not set";
    };
    const note = (kind: "" | "warn" | "err" | "ok", t: string) => { msg.className = `p4-rec-msg ${kind}`.trim(); msg.textContent = t; };
    const warnings = () => {
      const c = values[d.id];
      if (!c) { note("", ""); return; }
      const hits = findConflicts(prefs(), d.id, c);
      if (!hits.length) { note("", ""); return; }
      note("warn", hits.map((x) => (x.kind === "system" ? `Windows already uses this: ${x.label}` : `Clashes with “${x.label}”`)).join(" · "));
    };
    const paint = () => {
      const c = values[d.id];
      press.classList.toggle("recording", !!recorder);
      reset.hidden = canon(c) === canon(d.def);
      if (!recorder) showKeys(c);
      press.setAttribute("aria-label", `${d.label}: ${c ? formatCombo(c).join(" + ") : "off"}. Press Enter to change.`);
      if (!recorder) warnings();
    };
    paints.set(d.id, paint);

    const stop = () => { recorder = null; codes = new Set(); };
    const commit = async (combo: string) => {
      const before = values[d.id];
      const problem = rowProblem(d.id, combo);
      if (problem) { note("err", problem); return; }
      const twin = combo ? findConflicts(prefs(), d.id, combo).find((x) => x.kind === "lyra" && !x.label.endsWith("(overlaps)")) : undefined;
      if (twin) { note("err", `Already used by “${twin.label}”. Change that one first.`); return; }
      stop();
      values[d.id] = combo;
      paint();
      const r = await save(d.id, combo);
      if (!r.ok) { values[d.id] = before; paint(); note("err", r.error || "That key could not be saved."); return; }
      repaintAll();                                              // other rows may now clash with (or no longer clash with) this one
      if (combo === "" ) note("ok", "Switched off.");
    };
    const finish = (r: RecResult) => {
      if (r.state === "recording") { showKeys(r.preview, true); return; }
      if (r.state === "invalid") { note("err", r.reason); return; }
      if (r.state === "cancelled") { stop(); paint(); return; }
      if (r.state === "removed") { void commit(""); return; }
      void commit(r.combo);
    };

    press.addEventListener("click", () => {
      if (recorder) return;
      recorder = new Recorder(); codes = new Set();
      press.classList.add("recording"); note("", "");
      showKeys("", true);
    });
    press.addEventListener("keydown", (e) => {
      if (!recorder) return;
      e.preventDefault(); e.stopPropagation();
      if (SIDED_CODES[e.code] || e.code.startsWith("Meta") || e.code === "AltRight") codes.add(e.code); else codes.add("key");
      finish(recorder.down(e));
    });
    press.addEventListener("keyup", (e) => {
      if (!recorder) return;
      e.preventDefault();
      const r = recorder.up(e);
      if (!r) return;
      // One specific modifier on its own (Left Ctrl, Right Ctrl, Left Alt ...), like the Talk key.
      if (r.state === "recording" && !r.preview && codes.size === 1) {
        const side = SIDED_CODES[[...codes][0]];
        if (side) { finish({ state: "done", combo: side }); return; }
      }
      finish(r);
    });
    press.addEventListener("blur", () => { if (recorder) { stop(); paint(); } });
    reset.addEventListener("click", () => { void commit(d.def); });
    paint();
  }

  const resetAll = el("button", "", "Reset to defaults") as HTMLButtonElement;
  resetAll.type = "button";
  resetAll.addEventListener("click", async () => {
    resetAll.disabled = true;
    for (const d of SHORTCUTS) { values[d.id] = d.def; await save(d.id, d.def); }
    repaintAll();
    resetAll.disabled = false;
  });
  list.append(Row("Reset", "Puts every shortcut back to how Lyra shipped.", resetAll));

  // Fill in what is saved.
  void Promise.all([getFeatures(), loadSettings()]).then(([f, v]) => {
    for (const d of SHORTCUTS) {
      const voice = VOICE_KEY[d.id];
      const raw = voice ? v?.[voice] : (f as Record<string, unknown> | null)?.[FEATURE_KEY[d.id]];
      if (typeof raw === "string") values[d.id] = raw === "" ? "" : canon(raw);
    }
    repaintAll();
  });
  window.addEventListener("lyra-shortcuts-reset", () => { for (const d of SHORTCUTS) values[d.id] = d.def; repaintAll(); });
  // The Voice / Dictation tabs can change the same keys: follow them.
  window.addEventListener("lyra-key-changed", (e) => {
    const det = (e as CustomEvent<{ prefKey: string; value: string }>).detail;
    const id = Object.entries(VOICE_KEY).find(([, k]) => k === det?.prefKey)?.[0];
    if (id && values[id] !== det.value) { values[id] = det.value; repaintAll(); }
  });
  return card;
}

/** Puts every shortcut back to its built-in key (the palette action calls this). */
export async function resetAllShortcuts(): Promise<void> {
  for (const d of SHORTCUTS) {
    const voice = VOICE_KEY[d.id];
    if (voice) await api("/p2/settings", { [voice]: d.def }); else await postFeatures({ [FEATURE_KEY[d.id]]: d.def });
  }
  window.dispatchEvent(new Event("lyra-shortcuts-reset"));
}
