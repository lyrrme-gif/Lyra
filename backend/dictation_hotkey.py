"""Lyra — global dictation shortcut.

Hold the shortcut (or press it once to start and again to stop, Settings > Voice) and
Lyra types what you say into the focused window (dictation.py).

Same technique as ptt.py: a low-level Windows keyboard hook on its own thread that only
records what happened and hands the slow work to a dispatcher thread. Differences:
  * the shortcut's letter / Space key is swallowed, so the focused app never sees it
  * while a HOLD dictation runs, auto-repeats of the held modifiers are swallowed too
    (otherwise Windows would "re-press" Ctrl/Alt while Lyra is typing)
  * it counts the user's own key presses and mouse clicks (input_count()), which is how
    dictation.py knows the caret may have moved before it replaces text with the polished version
"""

from __future__ import annotations

import ctypes
import queue
import sys
import threading
import time
from ctypes import wintypes
from typing import NamedTuple

import dbg
import ui

log = ui.log

# Shortcuts offered in Settings > Voice: name -> (modifiers, virtual-key of the main key).
# "ctrl+alt" has no main key: hold LEFT Ctrl + LEFT Alt together (AltGr = Right Alt, used to type @ # { [ on
# many keyboards, never counts) for a moment and nothing else.
HOTKEYS: dict[str, tuple[frozenset, int | None]] = {
    "ctrl+alt": (frozenset({"ctrl", "alt"}), None),
    "ctrl+alt+d": (frozenset({"ctrl", "alt"}), 0x44),
    "ctrl+shift+d": (frozenset({"ctrl", "shift"}), 0x44),
    "ctrl+alt+space": (frozenset({"ctrl", "alt"}), 0x20),
    "ctrl+shift+space": (frozenset({"ctrl", "shift"}), 0x20),
}
_MOD_OF = {
    **dict.fromkeys((0x11, 0xA2, 0xA3), "ctrl"),
    **dict.fromkeys((0x12, 0xA4, 0xA5), "alt"),
    **dict.fromkeys((0x10, 0xA0, 0xA1), "shift"),
    **dict.fromkeys((0x5B, 0x5C), "win"),
}

# ---------------------------------------------------------------------------
# Phase 2: configurable key, gestures (hold / double-tap hands-free), Esc, "paste last"
# ---------------------------------------------------------------------------
_LCTRL, _RCTRL, _LALT, _RALT, _LSHIFT, _RSHIFT = 0xA2, 0xA3, 0xA4, 0xA5, 0xA0, 0xA1
_VK_ESC = 0x1B
_LEGACY_CHORD = frozenset({_LCTRL, _LALT})      # the "ctrl+alt" preset
_SIDED = {"lctrl": _LCTRL, "rctrl": _RCTRL, "lalt": _LALT, "lshift": _LSHIFT, "rshift": _RSHIFT}
_GENERIC_MODS = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift"}
_NAMED_KEYS = {"space": 0x20, "insert": 0x2D, "scrolllock": 0x91, "pause": 0x13}
_BARE_OK = frozenset({0x2D, 0x91, 0x13, *range(0x70, 0x88)})     # keys that may be used with no modifier: F1-F24, Insert, ScrollLock, Pause
_SOLO_OK = frozenset({_RCTRL, _RSHIFT, _LALT})                   # a single modifier that may be the whole shortcut (Left Alt is the default)


class HotkeySpec(NamedTuple):
    mods: frozenset            # generic modifiers that must be down: {"ctrl", "alt", "shift"}
    main: "int | None"         # virtual key of the main key (swallowed), None for a modifier chord
    chord: frozenset           # specific modifier keys held together (no main key), else empty


def _key_vk(name: str) -> "int | None":
    if len(name) == 1 and name.isalnum() and name.isascii():
        return ord(name.upper())
    if name.startswith("f") and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
        return 0x70 + int(name[1:]) - 1
    return _NAMED_KEYS.get(name)


def parse_hotkey(text: str, allow_chord: bool = True) -> "HotkeySpec | None":
    """"ctrl+alt+f9", "f9", "rctrl" -> HotkeySpec, or None if it is not a safe shortcut.
    Safe means: never Esc / Win / Right Alt (AltGr), no bare letters (they would be swallowed while you
    type), Shift alone never counts as a modifier for a letter, and a lone modifier only if it is Right
    Ctrl, Right Shift or Left Alt. Pure function: easy to test."""
    toks = [t.strip().lower() for t in str(text or "").replace(" ", "").split("+") if t.strip()]
    if not toks or len(set(toks)) != len(toks):
        return None
    mods: set[str] = set()
    sided: set[int] = set()
    mains: list[int] = []
    for t in toks:
        if t in _GENERIC_MODS:
            mods.add(_GENERIC_MODS[t])
        elif t in _SIDED:
            sided.add(_SIDED[t])
        else:
            vk = _key_vk(t)
            if vk is None or vk == _VK_ESC:
                return None
            mains.append(vk)
    if len(mains) > 1:
        return None
    if mains:
        if sided:
            return None
        main = mains[0]
        if not mods and main not in _BARE_OK:
            return None
        if mods == {"shift"} and main not in _BARE_OK:
            return None
        return HotkeySpec(frozenset(mods), main, frozenset())
    if not allow_chord:
        return None
    if mods == {"ctrl", "alt"} and not sided:
        return HotkeySpec(frozenset({"ctrl", "alt"}), None, _LEGACY_CHORD)
    if mods or not sided:
        return None
    if len(sided) == 1 and not (sided <= _SOLO_OK):
        return None
    return HotkeySpec(frozenset(), None, frozenset(sided))


class Gesture:
    """Hold-to-dictate key gestures, as a pure state machine (times are passed in).

        press                      -> start listening
        release after a long hold  -> stop (and type / paste)
        quick tap, then tap again  -> HANDS-FREE: keeps listening after the key is released
        tap while hands-free       -> stop and paste
        a single quick tap         -> nothing (the listening that started is thrown away)
        Esc                        -> cancel

    Methods return a list of actions for the caller: "start", "handsfree", "stop", "cancel".
    """
    TAP_MAX = 0.28            # a press shorter than this is a tap
    DOUBLE_WINDOW = 0.45      # the second tap must start within this long after the first one ended
    CHORD_TAP_MAX = 0.60      # a chord only counts after CHORD_DELAY (0.3 s), so its "tap" is a longer touch
    CHORD_WINDOW = 0.60

    def __init__(self, handsfree: bool = True) -> None:
        self.handsfree_enabled = handsfree
        self.tap_max, self.window = self.TAP_MAX, self.DOUBLE_WINDOW
        self.reset()

    def use_chord_timing(self, chord: bool) -> None:
        self.tap_max, self.window = ((self.CHORD_TAP_MAX, self.CHORD_WINDOW) if chord
                                     else (self.TAP_MAX, self.DOUBLE_WINDOW))

    def reset(self) -> None:
        self.state = "idle"        # idle | hold | wait2 | handsfree
        self.key_down = False
        self._ignore_release = False
        self._t_press = 0.0
        self._t_release = 0.0

    def on_press(self, t: float) -> list:
        self.key_down = True
        if self.state == "idle":
            self.state, self._t_press = "hold", t
            return ["start"]
        if self.state == "wait2":
            if t - self._t_release <= self.window:
                self.state = "handsfree"
                return ["handsfree"]
            self.state, self._t_press = "hold", t               # too late: a fresh dictation
            return ["cancel", "start"]
        if self.state == "handsfree":
            self.state, self._ignore_release = "idle", True
            return ["stop"]
        return []

    def on_release(self, t: float) -> list:
        self.key_down = False
        if self._ignore_release:
            self._ignore_release = False
            return []
        if self.state == "hold":
            if self.handsfree_enabled and t - self._t_press < self.tap_max:
                self.state, self._t_release = "wait2", t
                return []
            self.state = "idle"
            return ["stop"]
        return []

    def on_tick(self, t: float) -> list:
        if self.state == "wait2" and t - self._t_release > self.window:
            self.state = "idle"
            return ["cancel"]
        return []

    def on_cancel(self) -> list:
        if self.state == "idle":
            return []
        self.state = "idle"
        self._ignore_release = self.key_down
        return ["cancel"]

    @property
    def waiting(self) -> bool:
        return self.state == "wait2"


WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
_MOUSE_DOWN = frozenset({0x0201, 0x0204, 0x0207, 0x020B})   # left / right / middle / X button down
LLKHF_UP = 0x0080
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t),
    ]


_started = False
_hooks: list = []
_procs: list = []                    # hook callbacks must stay alive as long as the hooks
_thread: threading.Thread | None = None
_events: "queue.SimpleQueue[str]" = queue.SimpleQueue()
_dispatcher: threading.Thread | None = None

_down: set[int] = set()              # keys physically down (injected events are ignored)
_swallowed: set[int] = set()         # main keys whose key-down we swallowed (so we swallow the key-up too)
_hold_active = False                 # a hold-to-dictate session is running
_input_count = 0                     # user key presses (not modifiers, not the shortcut) + mouse clicks
_gesture = None                      # Gesture machine (created on first use)
_esc_swallowed = False               # we swallowed an Esc key-down (cancel)
_mouse_ok = False                    # the mouse hook is installed (clicks are counted)


def input_count() -> int:
    """How many key presses / mouse clicks the user made since Lyra started (compare two readings)."""
    return _input_count


def mouse_hook_ok() -> bool:
    """False if clicks cannot be counted: then dictation never replaces text by itself."""
    return _mouse_ok


_spec_cache: dict = {}


def _spec_for(text: str, allow_chord: bool = True):
    key = (text, allow_chord)
    if key not in _spec_cache:
        _spec_cache[key] = parse_hotkey(text, allow_chord)
    return _spec_cache[key]


def _pref(key: str):
    try:
        import dictation_clean
        return dictation_clean.pref(key)
    except Exception:
        return None


def _current():
    """HotkeySpec of the dictation key chosen in Settings > Dictation (Left Alt by default), or None
    if dictation is off. Runs inside the keyboard hook: no slow work here."""
    try:
        import settings_store
        if settings_store.get("dictation_enabled") is False:
            return None
    except Exception:
        pass
    spec = _spec_for(_pref("p2_dictation_key") or "lalt")
    return spec or _spec_for("lalt")


def _session_live() -> bool:
    mod = sys.modules.get("dictation")
    return bool(mod is not None and getattr(mod, "_session", None) is not None)


def _mods_down() -> set[str]:
    return {_MOD_OF[vk] for vk in _down if vk in _MOD_OF}


CHORD_DELAY = 0.20                   # a chord must stay down this long, with no other key, before dictation starts


def _chord_held(chord: frozenset = _LEGACY_CHORD) -> bool:
    """Exactly the chord's keys are down and nothing else (no AltGr, no Shift/Win, no letter)."""
    return bool(chord) and set(_down) == set(chord)


def _prune_stale(current_vk: int) -> None:
    """Forget keys the hook never saw released (e.g. after Win+L locked the screen)."""
    for vk in list(_down):
        if vk != current_vk and not ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000:
            _down.discard(vk)
            _swallowed.discard(vk)


def _on_key(down: bool, vk: int, injected: bool) -> bool:
    """Runs INSIDE the keyboard hook, so it must stay instant. True = swallow this key event."""
    global _input_count, _esc_swallowed
    if not injected:
        dbg.key(down, vk)
    if injected:                                   # Lyra's own typing and other programs' input
        return False
    hk = _current()
    if down:
        _prune_stale(vk)
        was_down = vk in _down
        _down.add(vk)
        if vk == _VK_ESC and not was_down and _session_live() and _pref("p2_esc_cancels") and not _mods_down():
            _esc_swallowed = True                  # Esc = throw this dictation away
            _events.put(("cancel", time.monotonic()))
            return True
        if vk == _VK_ESC and _esc_swallowed:
            return True                            # auto-repeat of the swallowed Esc
        if hk and vk == hk[1]:
            if vk in _swallowed:                   # auto-repeat of the shortcut key
                return True
            if _mods_down() == hk[0]:
                _swallowed.add(vk)
                _events.put(("press", time.monotonic()))
                return True
            _input_count += 1
            return False
        if vk in _MOD_OF:
            if hk and hk.chord and not was_down and vk in hk.chord and _chord_held(hk.chord):
                _events.put(("chord", time.monotonic()))   # the chord just went down: confirm after CHORD_DELAY
            return bool(_hold_active and was_down)   # swallow a held modifier's auto-repeat
        _input_count += 1
        return False
    _down.discard(vk)
    if vk == _VK_ESC and _esc_swallowed:
        _esc_swallowed = False
        return True
    if vk in _swallowed:
        _swallowed.discard(vk)
        _events.put(("release", time.monotonic()))
        return True
    if vk in _MOD_OF and _hold_active:
        _events.put(("release", time.monotonic()))   # let go of Ctrl/Alt first: same as letting go of the shortcut
    return False


def _machine() -> Gesture:
    global _gesture
    if _gesture is None:
        _gesture = Gesture()
    return _gesture


def _run_actions(actions: list) -> None:
    """Carry out what the Gesture machine decided."""
    global _hold_active
    import dictation
    g = _machine()
    for act in actions:
        dbg.ev("hotkey", f"action -> {act}")
        if act == "start":
            if not dictation.start():
                g.reset()
            else:
                try:
                    import computer.keyboard as kb
                    kb.mark_alt_used()               # Alt alone would light up the menu bar of the window in front
                except Exception:
                    pass
        elif act == "handsfree":
            dictation.set_handsfree()
        elif act == "stop":
            dictation.stop()
        elif act == "cancel":
            dictation.cancel()
    _hold_active = g.state == "hold"


def _handle(kind: str, t: float = 0.0) -> None:
    """Dispatcher thread: start / stop / cancel dictation."""
    global _hold_active
    import dictation
    t = t or time.monotonic()
    if kind != "tick":
        dbg.ev("hotkey", f"event '{kind}' (state={_machine().state}, mode={_pref_setting('dictation_hotkey_mode')})")
    g = _machine()
    g.handsfree_enabled = bool(_pref("p2_handsfree") is not False)
    spec = _current()
    g.use_chord_timing(bool(spec and spec.chord))
    if kind == "cancel":
        acts = g.on_cancel()
        if dictation.is_active() and not acts:
            acts = ["cancel"]                        # e.g. a toggle-mode session: the machine is idle
        _run_actions(acts)
        return
    if kind == "tick":
        _run_actions(g.on_tick(t))
        return
    if kind == "chord":
        before = _input_count
        time.sleep(CHORD_DELAY)
        if not spec or not _chord_held(spec.chord) or _input_count != before or _hold_active:
            dbg.ev("hotkey", "chord ignored (released early, another key pressed, or already running)")
            return                                   # a shortcut like Ctrl+Alt+Del / Ctrl+Alt+T, or already running
        kind = "press"                               # keeps t = the moment the chord went down
    if not dictation.is_active() and g.state != "idle":
        g.reset()                                    # the session ended by itself (error, no model...)
        _hold_active = False
    if kind == "press":
        _run_actions(g.on_press(t))                  # always hold-to-dictate
    elif kind == "release":
        _run_actions(g.on_release(t))


def _pref_setting(key: str):
    try:
        import settings_store
        return settings_store.get(key)
    except Exception:
        return None


def _dispatch_loop() -> None:
    while True:
        try:
            item = _events.get(timeout=0.06) if _machine().waiting else _events.get()
        except queue.Empty:
            item = ("tick", time.monotonic())
        kind, t = item if isinstance(item, tuple) else (item, 0.0)
        if kind == "quit":
            return
        try:
            _handle(kind, t)
        except Exception as e:
            log.error("Dictation shortcut error: %s", e)


def _install_hooks() -> bool:
    """Install the keyboard and mouse hooks on the thread that pumps their messages."""
    global _mouse_ok
    import ptt
    user32, kernel32 = ptt._configure_winapi()     # same Win32 signatures as push-to-talk
    handle = {"kb": None, "ms": None}

    def kb_proc(nCode, wParam, lParam):
        if nCode >= 0:
            try:
                kb = ctypes.cast(lParam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                wp = int(wParam)
                is_up = bool(kb.flags & LLKHF_UP) or wp in (WM_KEYUP, WM_SYSKEYUP)
                is_down = wp in (WM_KEYDOWN, WM_SYSKEYDOWN) and not is_up
                if (is_down or is_up) and _on_key(is_down, int(kb.vkCode), bool(kb.flags & LLKHF_INJECTED)):
                    return 1
            except Exception:
                pass
        return user32.CallNextHookEx(handle["kb"], nCode, wParam, lParam)

    def ms_proc(nCode, wParam, lParam):
        global _input_count
        if nCode >= 0 and int(wParam) in _MOUSE_DOWN:
            try:
                ms = ctypes.cast(lParam, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                dbg.click(int(wParam), int(ms.pt.x), int(ms.pt.y), bool(ms.flags & LLMHF_INJECTED))
                if not ms.flags & LLMHF_INJECTED:
                    _input_count += 1
            except Exception:
                pass
        return user32.CallNextHookEx(handle["ms"], nCode, wParam, lParam)

    proto = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    kb_cb, ms_cb = proto(kb_proc), proto(ms_proc)
    _procs[:] = [kb_cb, ms_cb]
    module = kernel32.GetModuleHandleW(None)
    ctypes.set_last_error(0)
    handle["kb"] = user32.SetWindowsHookExW(WH_KEYBOARD_LL, ctypes.cast(kb_cb, ctypes.c_void_p), module, 0)
    if not handle["kb"]:
        log.warning("Dictation shortcut: keyboard hook failed (Win32 error %s)", ctypes.get_last_error())
        _procs.clear()
        return False
    handle["ms"] = user32.SetWindowsHookExW(WH_MOUSE_LL, ctypes.cast(ms_cb, ctypes.c_void_p), module, 0)
    if not handle["ms"]:       # only used to notice clicks: dictation still works, it just never auto-replaces
        log.warning("Dictation shortcut: mouse hook failed (Win32 error %s)", ctypes.get_last_error())
    _hooks[:] = [h for h in (handle["kb"], handle["ms"]) if h]
    _mouse_ok = bool(handle["ms"])
    return True


def start() -> bool:
    """Arm the global dictation shortcut. Safe to call once."""
    global _started, _thread, _dispatcher
    if _started:
        return True
    try:
        import dictation_clean  # noqa: F401  # warm up: the hook reads Phase 2 prefs on key presses and must not import then
        _machine()
    except Exception as e:
        log.warning("Dictation shortcut: Phase 2 preferences unavailable (%s) - using defaults", e)
    ready = threading.Event()

    def runner():
        global _started
        ok = _install_hooks()
        ready.ok = ok  # type: ignore[attr-defined]
        ready.set()
        if not ok:
            return
        _started = True
        user32 = ctypes.windll.user32
        msg = wintypes.MSG()
        while _started:
            if user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    if _dispatcher is None or not _dispatcher.is_alive():
        _dispatcher = threading.Thread(target=_dispatch_loop, daemon=True, name="lyra-dictation-dispatch")
        _dispatcher.start()
    _thread = threading.Thread(target=runner, daemon=True, name="lyra-dictation-hook")
    _thread.start()
    ready.wait(timeout=2.0)
    if not getattr(ready, "ok", False):
        log.warning("Dictation shortcut could not be armed")
        return False
    log.info("Dictation shortcut armed (%s)", __file__)
    return True


def stop() -> None:
    global _started
    _events.put(("quit", 0.0))
    _started = False
    try:
        user32 = ctypes.windll.user32
        for h in _hooks:
            user32.UnhookWindowsHookEx(h)
    except Exception:
        pass
    _hooks.clear()
    _procs.clear()
