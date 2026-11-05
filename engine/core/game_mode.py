"""Lyra — game mode.

While a game is running, Lyra removes EVERY global key listener she owns, so nothing of hers sits in the
keyboard path of the game (or of its anti-cheat):

    Right Ctrl      push-to-talk + Ctrl+Win chat   ptt.py   a low-level keyboard hook  -> hook uninstalled, thread ended
    Ctrl tap + hold Circle to ask  circle_ask.py a key-state poller         -> poller thread ended
    Ctrl+CapsLock   tab wheel      tab_wheel.py  a key-state poller         -> poller thread ended

While game mode is on none of them exists: no hook, no polling. When the game closes, the ones that were
running come back. The only thing that keeps running is this module's watcher: every 2 seconds it asks Windows
which window is in front (and, every 6 seconds, which programs are running). It never reads the keyboard.

What counts as a game (Settings -> Features -> Game mode):
    * a program from the game list is running (editable: game_mode_apps).
    A full-screen program on its own never switches game mode on.

One safe way to wake Lyra meanwhile: Ctrl+Alt+L. It is registered with Windows itself (RegisterHotKey): no hook,
nothing polled; Windows just hands us a message when the keys are pressed. Press it once to talk, press it again
when you are done (the same as the mic button in the chat). The wake word "Hey Lyra" only uses the microphone,
not the keyboard, so it keeps working too.

The island shows a small "Game mode" badge while this is on (the "game_mode" event).
Windows only; anywhere else every function quietly does nothing.
"""

from __future__ import annotations

import ctypes
import re
import sys
import threading
from ctypes import wintypes
from typing import Callable

from engine.bridge import ui

log = ui.log

POLL_S = 2.0                 # how often the foreground window is checked
SCAN_EVERY = 3               # ... and every this many polls the running programs (psutil)
ENTER_POLLS_FULLSCREEN = 2   # a full-screen window must stay up this many polls (an Alt+Tab blip is not a game)
LEAVE_POLLS = 2              # nothing game-like for this many polls before the hooks come back
ANNOUNCE_EVERY = 15          # polls: repeat the "on" event so an island that started late still shows it

WAKE_LABEL = "Ctrl+Alt+L"
_MOD_ALT, _MOD_CONTROL, _MOD_NOREPEAT, _VK_L = 0x0001, 0x0002, 0x4000, 0x4C
_WM_HOTKEY, _WM_QUIT = 0x0312, 0x0012
_HOTKEY_ID = 0x4C59          # "LY"

# The key listeners game mode removes: module name -> the Settings feature that owns it (None = always on).
_COMPONENTS: tuple[tuple[str, str | None], ...] = (("ptt", None), ("circle_ask", "circle_ask"), ("tab_wheel", "tab_wheel"), ("hotkeys", None))

# Full-screen windows that are not games: never trigger game mode (lower-case file names).
_NOT_GAMES = frozenset({
    "explorer.exe", "applicationframehost.exe", "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
    "opera.exe", "vivaldi.exe", "vlc.exe", "mpv.exe", "wmplayer.exe", "powerpnt.exe", "spotify.exe",
    "teams.exe", "zoom.exe", "mstsc.exe", "code.exe", "searchhost.exe", "startmenuexperiencehost.exe",
})

_lock = threading.RLock()
_active = False
_reason = ""
_suspended: list[str] = []            # listeners game mode stopped (restarted when the game ends)
_deferred: set[str] = set()           # listeners that tried to start while a game was running
_push: Callable[[dict], None] | None = None
_watcher: threading.Thread | None = None
_stop_evt = threading.Event()
_wake_thread: threading.Thread | None = None
_wake_tid = 0
_wake_ok = False


# ── settings ─────────────────────────────────────────────────────────────────────────────────

def _setting(key: str, default):
    try:
        from engine.core import settings_store
        v = settings_store.get(key)
        return default if v is None else v
    except Exception:
        return default


def _enabled() -> bool:
    try:
        from engine.core import settings_store
        return settings_store.feature_on("game_mode")
    except Exception:
        return True


def game_list() -> set[str]:
    """Lower-case program names from the editable list ('Valorant' and 'valorant.exe' both work)."""
    raw = _setting("game_mode_apps", "")
    if isinstance(raw, (list, tuple)):
        raw = ",".join(str(x) for x in raw)
    out: set[str] = set()
    for part in re.split(r"[,;\n]", str(raw)):
        name = part.strip().lower()
        if name:
            out.add(name)
            if not name.endswith(".exe"):
                out.add(name + ".exe")
    return out


# ── detection ────────────────────────────────────────────────────────────────────────────────

_user32 = _kernel32 = _shell32 = None


def _winapi() -> bool:
    """Private (WinDLL) copies of the few Win32 calls we need, with pointer-safe signatures."""
    global _user32, _kernel32, _shell32
    if _user32 is not None:
        return True
    if sys.platform != "win32":
        return False
    try:
        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        s = ctypes.WinDLL("shell32", use_last_error=True)
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        s.SHQueryUserNotificationState.argtypes = [ctypes.POINTER(ctypes.c_int)]
        s.SHQueryUserNotificationState.restype = ctypes.c_long
        _user32, _kernel32, _shell32 = u, k, s
        return True
    except Exception as e:
        log.warning("game mode: Win32 not available (%s)", e)
        return False


def _foreground_exe() -> str:
    """File name (lower case) of the program that owns the window in front, or ''."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return ""
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    h = _kernel32.OpenProcess(0x1000, False, pid.value)      # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        n = wintypes.DWORD(520)
        if not _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return ""
        return buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
    finally:
        _kernel32.CloseHandle(h)


def _fullscreen_busy() -> bool:
    """Windows' own answer to 'is a full-screen app in front?' (exclusive D3D or borderless full screen)."""
    state = ctypes.c_int(0)
    if _shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
        return False
    return state.value in (2, 3)      # QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN


def _running_game(games: set[str]) -> str:
    """Name of a listed program that is running now, or ''."""
    if not games:
        return ""
    import psutil
    for p in psutil.process_iter(["name"]):
        n = (p.info.get("name") or "").lower()
        if n in games:
            return n
    return ""


# ── the listeners ────────────────────────────────────────────────────────────────────────────

def is_active() -> bool:
    return _active


def defer(name: str) -> None:
    """A listener asked to start while a game is running: it is started when the game ends."""
    with _lock:
        _deferred.add(name)
    log.info("game mode: %s will start when the game ends", name)


# Component name -> the module that holds it. The name itself stays the key the listeners and defer() use.
_MODULE_PATHS = {"ptt": "engine.voice.ptt", "circle_ask": "engine.features.circle_ask", "tab_wheel": "engine.features.tab_wheel", "hotkeys": "engine.core.hotkeys"}


def _module(name: str):
    try:
        import importlib
        return importlib.import_module(_MODULE_PATHS.get(name, name))
    except Exception:
        return None


def _announce() -> None:
    fn = _push
    if fn is None:
        return
    try:
        fn({"type": "game_mode", "on": _active, "app": _reason, "wake": WAKE_LABEL if (_active and _wake_ok) else ""})
    except Exception as e:
        log.debug("game mode: could not tell the island (%s)", e)


def enter(reason: str = "game") -> None:
    """Remove every global key listener and arm the safe wake key."""
    global _active, _reason, _suspended
    with _lock:
        if _active:
            return
        stopped: list[str] = []
        for name, _feature in _COMPONENTS:
            mod = _module(name)
            try:
                if mod is not None and mod.is_running():
                    mod.stop()
                    stopped.append(name)
            except Exception as e:
                log.warning("game mode: could not stop %s (%s)", name, e)
        _suspended, _active, _reason = stopped, True, reason
        _wake_start()
    log.info("game mode ON (%s): key listeners removed (%s); wake key %s", reason,
             ", ".join(stopped) or "none were running", WAKE_LABEL if _wake_ok else "unavailable")
    _announce()


def leave() -> None:
    """The game is over: bring back the listeners that were running."""
    global _active, _reason, _suspended
    with _lock:
        if not _active:
            return
        _wake_stop()
        _active, _reason = False, ""
        todo = list(dict.fromkeys(_suspended + sorted(_deferred)))
        _suspended = []
        _deferred.clear()
    for name, feature in _COMPONENTS:
        if name not in todo:
            continue
        try:
            if feature:
                from engine.core import settings_store
                if not settings_store.feature_on(feature):
                    continue                     # switched off in Settings while the game ran
            mod = _module(name)
            if mod is not None:
                mod.start()
        except Exception as e:
            log.warning("game mode: could not restart %s (%s)", name, e)
    log.info("game mode OFF: key listeners are back (%s)", ", ".join(todo) or "none")
    _announce()


# ── the safe wake key ────────────────────────────────────────────────────────────────────────

def _wake_pressed() -> None:
    """Ctrl+Alt+L: start listening, or finish the current recording."""
    try:
        from engine.voice import ptt
        listening = ptt.toggle_listen()
        log.info("game mode: wake key - %s", "listening" if listening else "finishing")
    except Exception as e:
        log.warning("game mode: wake key failed (%s)", e)


def _wake_loop(ready: threading.Event) -> None:
    global _wake_tid, _wake_ok
    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    u.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    u.RegisterHotKey.restype = wintypes.BOOL
    u.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _wake_tid = k.GetCurrentThreadId()
    ctypes.set_last_error(0)
    _wake_ok = bool(u.RegisterHotKey(None, _HOTKEY_ID, _MOD_CONTROL | _MOD_ALT | _MOD_NOREPEAT, _VK_L))
    if not _wake_ok:
        log.warning("game mode: %s is taken by another program (error %s); say 'Hey Lyra' instead",
                    WAKE_LABEL, ctypes.get_last_error())
    ready.set()
    if not _wake_ok:
        return
    msg = wintypes.MSG()
    while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == _WM_HOTKEY and msg.wParam == _HOTKEY_ID:
            threading.Thread(target=_wake_pressed, daemon=True, name="lyra-game-wake").start()
    u.UnregisterHotKey(None, _HOTKEY_ID)


def _wake_start() -> None:
    global _wake_thread, _wake_ok
    _wake_ok = False
    if not _winapi():
        return
    ready = threading.Event()
    _wake_thread = threading.Thread(target=_wake_loop, args=(ready,), daemon=True, name="lyra-game-hotkey")
    _wake_thread.start()
    ready.wait(timeout=2.0)


def _wake_stop() -> None:
    global _wake_thread, _wake_tid, _wake_ok
    t, tid = _wake_thread, _wake_tid
    if t is not None and t.is_alive() and tid:
        try:
            ctypes.WinDLL("user32").PostThreadMessageW(tid, _WM_QUIT, 0, 0)
        except Exception:
            pass
        t.join(timeout=2.0)
    _wake_thread, _wake_tid, _wake_ok = None, 0, False


# ── the watcher ──────────────────────────────────────────────────────────────────────────────

def _watch() -> None:
    hits = misses = tick = 0
    listed = ""
    warned = False
    while not _stop_evt.wait(POLL_S):
        try:
            if not _enabled():
                if _active:
                    leave()
                hits = misses = 0
                continue
            if tick % SCAN_EVERY == 0:
                listed = _running_game(game_list())
            tick += 1
            full = ""   # full-screen programs are never treated as games (the setting was removed); only the game list counts
            reason = listed or full
            if reason:
                hits, misses = hits + 1, 0
                if not _active and hits >= (1 if listed else ENTER_POLLS_FULLSCREEN):
                    enter(reason)
            else:
                hits, misses = 0, misses + 1
                if _active and misses >= LEAVE_POLLS:
                    leave()
            if _active and tick % ANNOUNCE_EVERY == 0:
                _announce()
        except Exception as e:
            if not warned:
                log.warning("game mode watcher: %s", e)
                warned = True


def start(push: Callable[[dict], None] | None = None) -> bool:
    """Start watching for games (once). `push` sends an event to the island."""
    global _push, _watcher
    if push is not None:
        _push = push
    if _watcher is not None and _watcher.is_alive():
        return True
    if not _winapi():
        return False
    _stop_evt.clear()
    _watcher = threading.Thread(target=_watch, daemon=True, name="lyra-game-mode")
    _watcher.start()
    log.info("game mode: watching for games (wake key %s)", WAKE_LABEL)
    return True


def stop() -> None:
    """Stop watching; if game mode is on, bring the listeners back first."""
    global _watcher
    _stop_evt.set()
    w, _watcher = _watcher, None
    if w is not None and w is not threading.current_thread():
        w.join(timeout=POLL_S + 1)
    leave()


def snapshot() -> dict:
    return {"on": _active, "app": _reason, "wake": WAKE_LABEL if (_active and _wake_ok) else ""}
