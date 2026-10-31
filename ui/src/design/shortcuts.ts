// Shortcut model for the recorder: pure logic, no DOM and no imports, so it is unit-tested offline
// (backend/tests/test_p4_shortcuts.py runs it with Node). Combos are canonical lower-case strings
// such as "ctrl+alt+d" or "ctrl+meta"; modifiers always come first in the order ctrl, alt, shift, meta.

export type Mod = "ctrl" | "alt" | "shift" | "meta";
const MOD_ORDER: Mod[] = ["ctrl", "alt", "shift", "meta"];

export interface ShortcutDef { id: string; label: string; hint: string; def: string; scope: "lyra" | "dictation" }
export interface ShortcutPrefs { [id: string]: { combo: string; enabled: boolean } }

/** Every key Lyra listens for today. `def` is the built-in default. */
export const SHORTCUTS: ShortcutDef[] = [
  { id: "talk", label: "Talk to Lyra", hint: "Hold while you speak, release when you are done.", def: "rctrl", scope: "lyra" },
  { id: "dictate", label: "Dictate anywhere", hint: "Hold it and talk: your words are pasted into the app in front when you let go.", def: "lalt", scope: "dictation" },
  { id: "typing", label: "Open chat tab", hint: "Opens the Chat tab on the island so you can type, from any program.", def: "ctrl+meta", scope: "lyra" },
  { id: "wheel", label: "Tab wheel", hint: "Hold to open the wheel at your cursor, let go on a tab to open it.", def: "ctrl+capslock", scope: "lyra" },
  { id: "circle", label: "Circle to ask", hint: "Hold and circle something on screen to ask about it.", def: "lctrl", scope: "lyra" },
  { id: "record", label: "Start / stop recording", hint: "Starts a screen recording, or stops and saves the one that is running.", def: "ctrl+alt+r", scope: "lyra" },
  { id: "pause", label: "Pause / resume recording", hint: "Pauses the recording that is running, or carries on.", def: "ctrl+alt+p", scope: "lyra" },
];

/** Combos Windows (or nearly every app) already owns. Warn, never silently allow. */
export const RESERVED: Record<string, string> = {
  "ctrl+alt+delete": "Windows security screen",
  "alt+tab": "Windows task switcher",
  "alt+f4": "Close window",
  "meta+l": "Lock Windows",
  "meta+d": "Show desktop",
  "meta+tab": "Windows task view",
  "ctrl+shift+escape": "Task Manager",
  "ctrl+c": "Copy", "ctrl+v": "Paste", "ctrl+x": "Cut", "ctrl+z": "Undo", "ctrl+a": "Select all",
  "ctrl+s": "Save", "ctrl+w": "Close tab", "ctrl+f": "Find", "ctrl+p": "Print",
};

const KEY_NAMES: Record<string, string> = {
  " ": "space", spacebar: "space", esc: "escape", del: "delete", os: "meta", win: "meta", windows: "meta", command: "meta", control: "ctrl", option: "alt",
  arrowup: "up", arrowdown: "down", arrowleft: "left", arrowright: "right",
};
const SHOWN: Record<string, string> = {
  ctrl: "Ctrl", alt: "Alt", shift: "Shift", meta: "Win", lctrl: "Left Ctrl", rctrl: "Right Ctrl", lalt: "Left Alt", lshift: "Left Shift", rshift: "Right Shift", space: "Space", capslock: "CapsLock", escape: "Esc", enter: "Enter", tab: "Tab",
  up: "↑", down: "↓", left: "←", right: "→", delete: "Del", backspace: "Backspace",
};

export function normKey(k: string): string {
  const s = k.length === 1 ? k.toLowerCase() : k.toLowerCase().replace(/\s+/g, "");
  return KEY_NAMES[s] ?? s;
}
const isMod = (k: string): k is Mod => k === "ctrl" || k === "alt" || k === "shift" || k === "meta";

/** Canonicalise any user/legacy spelling ("Ctrl + Win", "ctrl+ALT+d") to "ctrl+meta" / "ctrl+alt+d". */
export function canon(combo: string): string {
  const parts = combo.split("+").map((p) => normKey(p.trim())).filter(Boolean);
  const mods = MOD_ORDER.filter((m) => parts.includes(m));
  const keys = parts.filter((p) => !isMod(p));
  return [...mods, ...keys.slice(0, 1)].join("+");
}

/** "ctrl+alt+d" → ["Ctrl", "Alt", "D"] for the KeyCap display. */
export function formatCombo(combo: string): string[] {
  return canon(combo).split("+").filter(Boolean).map((p) => SHOWN[p] ?? (p.length === 1 ? p.toUpperCase() : p.charAt(0).toUpperCase() + p.slice(1)));
}

/** Why a combo cannot be used, or null when it is fine. */
export function invalidReason(combo: string): string | null {
  const c = canon(combo);
  if (!c) return "Press a key combination";
  const parts = c.split("+");
  const mods = parts.filter(isMod);
  const keys = parts.filter((p) => !isMod(p));
  if (keys.length === 1 && mods.length === 0 && /^(lctrl|rctrl|lalt|lshift|rshift)$/.test(keys[0])) return null;   // one specific modifier (Right Ctrl, Left Alt ...)
  if (keys.length === 0 && mods.length < 2) return "Hold at least two modifier keys, or add a letter";
  if (keys.length === 1 && mods.length === 0 && !/^f([1-9]|1\d|2[0-4])$/.test(keys[0]) && keys[0] !== "capslock") return "Add Ctrl, Alt, Shift or Win so normal typing is not caught";
  if (keys.length === 1 && keys[0] === "escape") return "Esc is used to cancel";
  return null;
}

export interface Conflict { kind: "lyra" | "system"; with: string; label: string }

/** Other enabled Lyra shortcuts and well-known system combos that share `combo`. */
export function findConflicts(prefs: ShortcutPrefs, id: string, combo: string): Conflict[] {
  const c = canon(combo);
  const out: Conflict[] = [];
  if (!c) return out;
  for (const d of SHORTCUTS) {
    if (d.id === id) continue;
    const p = prefs[d.id] ?? { combo: d.def, enabled: true };
    if (p.enabled && canon(p.combo) === c) out.push({ kind: "lyra", with: d.id, label: d.label });
    // Holding a shorter chord that is the start of a longer one also fires both (e.g. Ctrl+Alt vs Ctrl+Alt+D).
    else if (p.enabled && canon(p.combo) !== c && (canon(p.combo).startsWith(c + "+") || c.startsWith(canon(p.combo) + "+"))
      && !(c === canon(SHORTCUTS.find((x) => x.id === id)?.def ?? "") && canon(p.combo) === canon(d.def))) {   // the shipped pair is not a warning
      out.push({ kind: "lyra", with: d.id, label: `${d.label} (overlaps)` });
    }
  }
  if (RESERVED[c]) out.push({ kind: "system", with: c, label: RESERVED[c] });
  return out;
}

export function defaultPrefs(): ShortcutPrefs {
  const p: ShortcutPrefs = {};
  for (const d of SHORTCUTS) p[d.id] = { combo: d.def, enabled: true };
  return p;
}

export function sanitizePrefs(raw: unknown): ShortcutPrefs {
  const base = defaultPrefs();
  if (!raw || typeof raw !== "object") return base;
  for (const d of SHORTCUTS) {
    const r = (raw as Record<string, unknown>)[d.id] as { combo?: unknown; enabled?: unknown } | undefined;
    if (!r || typeof r !== "object") continue;
    if (typeof r.combo === "string" && (r.combo === "" || !invalidReason(r.combo))) base[d.id].combo = canon(r.combo);   // "" = the user removed it
    // "Switch to typing" used to default to Ctrl+T (browsers and editors use it for "new tab"); a saved Ctrl+T is that old default.
    if (d.id === "typing" && base[d.id].combo === "ctrl+t") base[d.id].combo = d.def;
    if (typeof r.enabled === "boolean") base[d.id].enabled = r.enabled;
  }
  return base;
}

// ── Recorder state machine ────────────────────────────────────────────────────
// Feed it keydown/keyup events. Esc cancels, Backspace/Delete removes (back to "no shortcut"),
// a normal key finishes the combo, and two or more modifiers released together finish a modifier chord.

export interface KeyEv { key: string; ctrlKey?: boolean; altKey?: boolean; shiftKey?: boolean; metaKey?: boolean }
export type RecResult =
  | { state: "recording"; preview: string }
  | { state: "done"; combo: string }
  | { state: "removed" }
  | { state: "cancelled" }
  | { state: "invalid"; reason: string; preview: string };

export class Recorder {
  private held = new Set<Mod>();
  private peak = new Set<Mod>();

  private mods(e: KeyEv): Mod[] {
    const m: Mod[] = [];
    if (e.ctrlKey) m.push("ctrl"); if (e.altKey) m.push("alt"); if (e.shiftKey) m.push("shift"); if (e.metaKey) m.push("meta");
    return m;
  }

  down(e: KeyEv): RecResult {
    const k = normKey(e.key);
    if (k === "escape") return { state: "cancelled" };
    const mods = this.mods(e);
    if (isMod(k)) {
      if (!mods.includes(k)) mods.push(k);
      this.held = new Set(mods); mods.forEach((m) => this.peak.add(m));
      return { state: "recording", preview: canon(mods.join("+")) };
    }
    if ((k === "backspace" || k === "delete") && mods.length === 0) return { state: "removed" };
    const combo = canon([...mods, k].join("+"));
    const bad = invalidReason(combo);
    return bad ? { state: "invalid", reason: bad, preview: combo } : { state: "done", combo };
  }

  up(e: KeyEv): RecResult | null {
    const k = normKey(e.key);
    if (!isMod(k)) return null;
    this.held.delete(k);
    if (this.held.size === 0 && this.peak.size >= 2) {
      const combo = canon([...this.peak].join("+"));
      this.peak.clear();
      return { state: "done", combo };
    }
    if (this.held.size === 0) this.peak.clear();
    return { state: "recording", preview: canon([...this.held].join("+")) };
  }
}
