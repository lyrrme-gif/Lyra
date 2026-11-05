"""Lyra: keyboard shortcuts for screen recording (Settings -> Shortcuts).

    Start / stop recording      shortcut_record_combo   (built in: Ctrl+Alt+R)
    Pause / resume recording    shortcut_pause_combo    (built in: Ctrl+Alt+P)

They are registered with Windows itself (RegisterHotKey), the same safe way game mode's wake key is: NO keyboard
hook, nothing polled, nothing in the path of the keys you type or of a game's anti-cheat. One thread sleeps in
GetMessage and Windows wakes it only when one of the chords is pressed, whether the island is showing or hidden.
A shortcut is always on; removing its keys in Settings turns it off. If another program already owns a chord,
that one is skipped and Settings says so (status()["taken"]). A chord must be modifiers + one key (Win's
RegisterHotKey cannot do modifier-only or left/right-specific combos); anything else is reported as "unusable".
Windows only; anywhere else every function quietly does nothing."""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes

from engine.bridge import ui

log = ui.log

# name -> (settings key, built-in combo, Windows hotkey id)
KEYS = {"toggle": ("shortcut_record_combo", "ctrl+alt+r", 0x5252), "pause": ("shortcut_pause_combo", "ctrl+alt+p", 0x5250)}
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY, _WM_QUIT = 0x0312, 0x0012

_lock = threading.Lock()
_thread: threading.Thread | None = None
_tid = 0
_got: list[str] = []          # chords Windows gave us
_taken: list[str] = []        # chords another program already owns
_unusable: list[str] = []     # chords that cannot be a Windows hotkey (modifier-only, left/right-specific)
_armed: dict[str, str] = {}   # name -> combo text that was registered


def _pressed(name: str) -> None:
    """Runs in its own short thread (starting a recording takes a moment; the message loop must stay free)."""
    try:
        from engine.features import screen_record
        s = screen_record.toggle() if name == "toggle" else screen_record.toggle_pause()
        log.info("record hotkey %s -> recording=%s paused=%s", name, s.get("recording"), s.get("paused"))
    except Exception as e:  # noqa: BLE001
        log.warning("record hotkey %s failed: %s", name, e)


def combos() -> dict[str, str]:
    """name -> combo text from Settings ("" = the user removed that shortcut)."""
    out: dict[str, str] = {}
    for name, (key, default, _hid) in KEYS.items():
        try:
            from engine.core import settings_store
            raw = settings_store.get(key)
        except Exception:  # noqa: BLE001
            raw = None
        out[name] = default if raw is None else str(raw)
    return out


def _loop(ready: threading.Event, wanted: dict[str, str]) -> None:
    global _tid
    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    u.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    u.RegisterHotKey.restype = wintypes.BOOL
    u.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _tid = k.GetCurrentThreadId()
    from engine.core import hotkey_spec
    got, taken, unusable = [], [], []
    for name, text in wanted.items():
        hid = KEYS[name][2]
        spec = hotkey_spec.parse(text)
        if spec is None:
            continue                                    # no shortcut set: nothing to register
        args = spec.register_args()
        if args is None:
            unusable.append(name)
            log.warning("record hotkey %s (%s) cannot be a Windows hotkey: use modifiers plus one key", name, text)
            continue
        ctypes.set_last_error(0)
        if u.RegisterHotKey(None, hid, args[0] | _MOD_NOREPEAT, args[1]):
            got.append(name)
        else:
            taken.append(name)
            log.warning("record hotkey %s (%s) is taken by another program (error %s)", name, text, ctypes.get_last_error())
    _got[:], _taken[:], _unusable[:] = got, taken, unusable
    _armed.clear()
    _armed.update({n: wanted[n] for n in got})
    ready.set()
    if not got:
        return
    by_id = {hid: name for name, (_k, _d, hid) in KEYS.items()}
    msg = wintypes.MSG()
    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == _WM_HOTKEY and msg.wParam in by_id:
            threading.Thread(target=_pressed, args=(by_id[msg.wParam],), daemon=True, name="lyra-record-hotkey-act").start()
    for name in got:
        u.UnregisterHotKey(None, KEYS[name][2])


def start() -> None:
    global _thread
    if sys.platform != "win32":
        return
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        ready = threading.Event()
        _thread = threading.Thread(target=_loop, args=(ready, combos()), daemon=True, name="lyra-record-hotkeys")
        _thread.start()
    ready.wait(timeout=2.0)


def stop() -> None:
    global _thread, _tid
    with _lock:
        t, tid = _thread, _tid
        _thread, _tid = None, 0
    if t is not None and t.is_alive() and tid:
        try:
            ctypes.WinDLL("user32").PostThreadMessageW(tid, _WM_QUIT, 0, 0)
        except Exception:  # noqa: BLE001
            pass
        t.join(timeout=2.0)
    _got.clear()
    _taken.clear()
    _unusable.clear()
    _armed.clear()


def apply() -> None:
    """Make the registered chords match Settings (called at startup and whenever a recording shortcut is saved)."""
    try:
        stop()
        if any(combos().values()):                      # all removed -> no thread at all
            start()
    except Exception as e:  # noqa: BLE001
        log.warning("record hotkeys: %s", e)


def status() -> dict:
    want = combos()
    return {"keys": want, "defaults": {n: v[1] for n, v in KEYS.items()},
            "taken": [n for n in _taken], "unusable": [n for n in _unusable]}
