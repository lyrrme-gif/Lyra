// Run by test_p4_shortcuts.py:  node --experimental-strip-types p4_shortcuts_check.mjs
import assert from "node:assert/strict";
import * as S from "../../ui/src/design/shortcuts.ts";

// canonical spelling
assert.equal(S.canon("Ctrl + Win"), "ctrl+meta");
assert.equal(S.canon("ALT+ctrl+D"), "ctrl+alt+d");
assert.equal(S.canon("Control+Shift+Space"), "ctrl+shift+space");
assert.deepEqual(S.formatCombo("ctrl+alt+space"), ["Ctrl", "Alt", "Space"]);
assert.deepEqual(S.formatCombo("ctrl+meta"), ["Ctrl", "Win"]);

// validity
assert.equal(S.invalidReason("ctrl+alt+d"), null);
assert.equal(S.invalidReason("ctrl+meta"), null);
assert.equal(S.invalidReason("f9"), null);
assert.match(S.invalidReason("a"), /Ctrl, Alt, Shift or Win/);
assert.match(S.invalidReason("ctrl"), /two modifier/);
assert.match(S.invalidReason("ctrl+escape"), /cancel/);
assert.ok(S.invalidReason(""));

// the shipped defaults are valid and raise no warnings (Ctrl+Alt+R and Ctrl+Alt+P ship together)
const d = S.defaultPrefs();
for (const def of S.SHORTCUTS) {
  assert.equal(S.invalidReason(def.def), null, def.id);
  assert.deepEqual(S.findConflicts(d, def.id, def.def), [], `default ${def.id}`);
}
// but a user choice that starts another key's chord is flagged
const ov = S.findConflicts(d, "typing", "lalt");
assert.ok(ov.some((c) => c.with === "dictate"), "same combo as dictate");
const ov2 = S.findConflicts(d, "typing", "ctrl+alt");
assert.ok(ov2.some((c) => c.with === "record" && /overlaps/.test(c.label)), "prefix of the recording shortcut");

// conflicts: another Lyra key, a disabled key is ignored, system combos are flagged
const p = S.defaultPrefs();
assert.deepEqual(S.findConflicts(p, "typing", "rctrl").map((c) => c.with), ["talk"]);
p.talk.enabled = false;
assert.equal(S.findConflicts(p, "typing", "rctrl").length, 0);
assert.equal(S.findConflicts(p, "typing", "alt+f4")[0].kind, "system");
assert.equal(S.findConflicts(p, "typing", "ctrl+t").length, 0, "own combo is not a conflict");

// sanitising stored data
const bad = S.sanitizePrefs({ wheel: { combo: "x", enabled: "yes" }, talk: { combo: "ctrl+alt+m", enabled: false }, nope: 1 });
assert.equal(bad.wheel.combo, "ctrl+capslock");
assert.equal(bad.wheel.enabled, true);
assert.deepEqual(bad.talk, { combo: "ctrl+alt+m", enabled: false });
assert.deepEqual(S.sanitizePrefs(null), S.defaultPrefs());
assert.equal(S.sanitizePrefs({ typing: { combo: "", enabled: true } }).typing.combo, "", "a removed shortcut stays removed");

// recorder: normal combo
let r = new S.Recorder();
assert.deepEqual(r.down({ key: "Control", ctrlKey: true }), { state: "recording", preview: "ctrl" });
assert.deepEqual(r.down({ key: "d", ctrlKey: true }), { state: "done", combo: "ctrl+d" });
// recorder: Esc cancels, Backspace removes, plain letter is rejected
r = new S.Recorder();
assert.equal(r.down({ key: "Escape" }).state, "cancelled");
assert.equal(r.down({ key: "Backspace" }).state, "removed");
assert.equal(r.down({ key: "a" }).state, "invalid");
// recorder: modifier chord finishes when the keys are released
r = new S.Recorder();
r.down({ key: "Control", ctrlKey: true });
r.down({ key: "Alt", ctrlKey: true, altKey: true });
assert.equal(r.up({ key: "Alt", ctrlKey: true }).state, "recording");
assert.deepEqual(r.up({ key: "Control" }), { state: "done", combo: "ctrl+alt" });
// recorder: a single modifier released does not finish
r = new S.Recorder();
r.down({ key: "Shift", shiftKey: true });
assert.equal(r.up({ key: "Shift" }).state, "recording");
console.log("ok");
