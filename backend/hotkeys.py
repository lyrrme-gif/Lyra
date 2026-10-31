"""Lyra - keyboard shortcuts that run an action (Settings -> Shortcuts). See hotkey_defs.py for the list.

Registered with Windows itself (RegisterHotKey), like the recording shortcuts and game mode's wake key: NO keyboard
hook, nothing polled, nothing in the path of the keys you type or of a game's anti-cheat. One thread sleeps in
GetMessage and Windows wakes it only when one of YOUR combinations is pressed. Each shortcut has its own combo in
Settings (shortcut_<id>_combo; removing the keys switches it off). A combo another program already owns is skipped
and reported (status()["taken"]); one Windows cannot register (no modifier, or a left/right-specific one) is
reported as unusable. Windows only; anywhere else every function quietly does nothing."""
from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes

import hotkey_defs as defs
import hotkey_spec
import ui

log = ui.log

_MOD_NOREPEAT = 0x4000
_WM_HOTKEY, _WM_QUIT = 0x0312, 0x0012
_ID_BASE = 0x4C00                       # Windows hotkey ids: _ID_BASE + position in defs.ACTIONS

_lock = threading.Lock()
_thread: threading.Thread | None = None
_tid = 0
_got: list[str] = []
_taken: list[str] = []
_unusable: list[str] = []


def combos() -> dict[str, str]:
    """action -> combo text from Settings (\"\" = the user switched it off)."""
    out: dict[str, str] = {}
    for a, (_label, default) in defs.ACTIONS.items():
        try:
            import settings_store
            raw = settings_store.get(defs.key_of(a))
        except Exception:  # noqa: BLE001
            raw = None
        out[a] = default if raw is None else str(raw)
    return out


def active_map() -> dict[str, tuple[int, int]]:
    """action -> (modifiers, vk) for every shortcut that is set and can be a Windows hotkey."""
    out: dict[str, tuple[int, int]] = {}
    for a, text in combos().items():
        spec = hotkey_spec.parse(text) if text else None
        args = spec.register_args() if spec else None
        if args:
            out[a] = args
    return out


def _media_key(vk: int) -> None:
    u = ctypes.WinDLL("user32")
    u.keybd_event(vk, 0, 0, 0)
    u.keybd_event(vk, 0, 2, 0)          # key up


def run(action: str) -> None:
    """Do what `action` says (in its own short thread, so the message loop stays free)."""
    try:
        if action == "media_play":
            _media_key(0xB3)
        elif action == "media_next":
            _media_key(0xB0)
        elif action == "media_prev":
            _media_key(0xB1)
        elif action == "volume_mute":
            _media_key(0xAD)
        elif action in defs.OPEN_VIEW:
            import ui_bridge
            ui_bridge.push_open_view(defs.OPEN_VIEW[action])
        else:
            log.warning("hotkey: unknown action %r", action)
    except Exception as e:  # noqa: BLE001
        log.warning("hotkey %s failed: %s", action, e)


def _loop(ready: threading.Event, wanted: dict[str, str]) -> None:
    global _tid
    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    u.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    u.RegisterHotKey.restype = wintypes.BOOL
    u.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _tid = k.GetCurrentThreadId()
    ids = {a: _ID_BASE + i for i, a in enumerate(defs.ACTIONS)}
    got, taken, unusable = [], [], []
    for action, text in wanted.items():
        if not text:
            continue
        spec = hotkey_spec.parse(text)
        args = spec.register_args() if spec else None
        if args is None:
            unusable.append(action)
            log.warning("hotkey %s (%s) cannot be a Windows hotkey: use modifiers plus one key", action, text)
            continue
        ctypes.set_last_error(0)
        if u.RegisterHotKey(None, ids[action], args[0] | _MOD_NOREPEAT, args[1]):
            got.append(action)
        else:
            taken.append(action)
            log.warning("hotkey %s (%s) is taken by another program (error %s)", action, text, ctypes.get_last_error())
    _got[:], _taken[:], _unusable[:] = got, taken, unusable
    ready.set()
    if not got:
        return
    by_id = {ids[a]: a for a in got}
    msg = wintypes.MSG()
    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == _WM_HOTKEY and msg.wParam in by_id:
            threading.Thread(target=run, args=(by_id[msg.wParam],), daemon=True, name="lyra-hotkey-act").start()
    for a in got:
        u.UnregisterHotKey(None, ids[a])


def start() -> None:
    global _thread
    if sys.platform != "win32":
        return
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        ready = threading.Event()
        _thread = threading.Thread(target=_loop, args=(ready, combos()), daemon=True, name="lyra-hotkeys")
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


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def apply() -> None:
    """Make the registered shortcuts match Settings (called at startup and whenever one is saved)."""
    try:
        stop()
        if any(combos().values()):
            start()
    except Exception as e:  # noqa: BLE001
        log.warning("hotkeys: %s", e)


def status() -> dict:
    return {"keys": combos(), "defaults": {a: v[1] for a, v in defs.ACTIONS.items()},
            "taken": list(_taken), "unusable": list(_unusable)}
