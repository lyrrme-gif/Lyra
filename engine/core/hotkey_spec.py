"""Lyra: one way to read a key combo such as "ctrl+capslock", "lctrl" or "ctrl+alt+r".

The Settings window stores every shortcut as a canonical text (modifiers first, in the order ctrl, alt, shift, meta,
then at most one key: see ui/src/design/shortcuts.ts). The key listeners (tab wheel, circle to ask, screen recording)
turn that text into a `Spec` here, so they all agree on what a combo means and none of them hard-codes a key.

    spec = parse("ctrl+capslock")
    spec.all_down(down)        every part of the combo is held     (`down(vk)` = GetAsyncKeyState-style callback)
    spec.any_down(down)        at least one part is held
    spec.extra_mods_down(down) a modifier family that is NOT part of the combo is held (Shift while Ctrl+CapsLock)
    spec.other_vks             every other key on the keyboard (a different key pressed = "typing a shortcut")
    spec.register_args()       (modifier flags, virtual key) for RegisterHotKey, or None when the combo is not
                               a plain "modifiers + one key" (modifier-only and left/right-specific combos cannot be)

Pure Python, no Windows calls: it is unit-tested everywhere (tests/test_hotkey_spec.py).
"""

from __future__ import annotations

from dataclasses import dataclass

# Modifier families: the generic name -> the left and right virtual keys.
FAMILIES: dict[str, tuple[int, ...]] = {
    "ctrl": (0xA2, 0xA3), "alt": (0xA4, 0xA5), "shift": (0xA0, 0xA1), "meta": (0x5B, 0x5C),
}
# One specific modifier on its own (Left Ctrl, Right Ctrl ...): the family it belongs to and its key.
SIDED: dict[str, tuple[str, int]] = {
    "lctrl": ("ctrl", 0xA2), "rctrl": ("ctrl", 0xA3), "lalt": ("alt", 0xA4), "ralt": ("alt", 0xA5),
    "lshift": ("shift", 0xA0), "rshift": ("shift", 0xA1),
}
NAMED = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "backspace": 0x08, "escape": 0x1B, "capslock": 0x14,
    "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
}
_GENERIC_MODS = (0x10, 0x11, 0x12)        # the generic Shift / Ctrl / Alt keys: down whenever either side is
_DUMMY = 0xE8                              # the key Windows uses to cancel the Alt / Win menu
_MOD_FLAGS = {"alt": 0x0001, "ctrl": 0x0002, "shift": 0x0004, "meta": 0x0008}   # RegisterHotKey MOD_*


def vk_for_key(name: str) -> int | None:
    """Virtual-key code of a non-modifier key name ("r", "5", "f9", "capslock"), or None when unknown."""
    k = name.strip().lower()
    if len(k) == 1 and k.isalnum() and k.isascii():
        return ord(k.upper())
    if k in NAMED:
        return NAMED[k]
    if k.startswith("f") and k[1:].isdigit() and 1 <= int(k[1:]) <= 24:
        return 0x70 + int(k[1:]) - 1
    return None


@dataclass(frozen=True)
class Spec:
    text: str
    groups: tuple[tuple[int, ...], ...]     # each group: the combo needs ANY key of it (Ctrl = left or right)
    families: frozenset[str]                # modifier families the combo mentions
    key_vk: int                             # the non-modifier key (0 = none)
    registrable: bool                       # plain modifiers + one key: RegisterHotKey can take it

    @property
    def keys(self) -> frozenset[int]:
        return frozenset(v for g in self.groups for v in g)

    @property
    def other_vks(self) -> tuple[int, ...]:
        """Every key that is NOT part of the combo (and not a generic modifier): pressing one means the user is typing."""
        mine = self.keys | set(_GENERIC_MODS) | {_DUMMY}
        return tuple(v for v in range(0x08, 0xFF) if v not in mine)

    def all_down(self, down) -> bool:
        return all(any(down(v) for v in g) for g in self.groups)

    def any_down(self, down) -> bool:
        return any(down(v) for g in self.groups for v in g)

    def extra_mods_down(self, down) -> bool:
        """A modifier family the combo does not mention is held (so Ctrl+CapsLock does not fire for Ctrl+Shift+CapsLock)."""
        return any(down(v) for fam, vks in FAMILIES.items() if fam not in self.families for v in vks)

    def register_args(self) -> tuple[int, int] | None:
        if not self.registrable:
            return None
        flags = 0
        for fam in self.families:
            flags |= _MOD_FLAGS[fam]
        return flags, self.key_vk


def parse(combo: str | None) -> Spec | None:
    """"ctrl+alt+r" -> Spec. None for an empty text ("no shortcut") or anything that is not a usable combo."""
    text = str(combo or "").lower().replace(" ", "").strip("+")
    if not text:
        return None
    parts = [p for p in text.split("+") if p]
    if len(parts) == 1 and parts[0] in SIDED:
        fam, vk = SIDED[parts[0]]
        return Spec(parts[0], ((vk,),), frozenset({fam}), 0, False)
    mods = [p for p in parts if p in FAMILIES]
    keys = [p for p in parts if p not in FAMILIES]
    if len(keys) > 1 or len(set(mods)) != len(mods) or any(k in SIDED for k in keys):
        return None
    groups: list[tuple[int, ...]] = [FAMILIES[m] for m in mods]
    key_vk = 0
    if keys:
        key_vk = vk_for_key(keys[0]) or 0
        if not key_vk:
            return None
        groups.append((key_vk,))
    elif len(mods) < 2:
        return None                        # one generic modifier alone would fire on every keystroke
    registrable = bool(key_vk) and bool(mods)
    return Spec(text, tuple(groups), frozenset(mods), key_vk, registrable)
