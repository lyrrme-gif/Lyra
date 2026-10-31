"""Keyboard helpers. Unicode-safe via clipboard paste (Win32, no subprocess)."""
from __future__ import annotations

import sys
import time
from typing import Optional

import ui

log = ui.log
_WIN = sys.platform == "win32"
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _api():
    import ctypes
    from ctypes import wintypes  # noqa: F401
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k32.GlobalFree.argtypes = [ctypes.c_void_p]
    u32.OpenClipboard.argtypes = [ctypes.c_void_p]
    u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p
    u32.GetClipboardData.argtypes = [ctypes.c_uint]
    u32.GetClipboardData.restype = ctypes.c_void_p
    return ctypes, k32, u32


def _open_clip(u32, tries: int = 15) -> bool:
    for _ in range(tries):
        if u32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def get_clipboard() -> Optional[str]:
    """Clipboard text, or None if empty / not text / unavailable."""
    if not _WIN:
        return None
    try:
        ctypes, k32, u32 = _api()
        if not _open_clip(u32):
            return None
        try:
            h = u32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return None
            p = k32.GlobalLock(h)
            if not p:
                return None
            try:
                return ctypes.wstring_at(p)
            finally:
                k32.GlobalUnlock(h)
        finally:
            u32.CloseClipboard()
    except Exception as e:
        log.warning("get_clipboard failed: %s", e)
        return None


def set_clipboard(text: str) -> bool:
    """Put text on the clipboard exactly as given (quotes, newlines, emoji, Arabic)."""
    if not _WIN:
        return False
    try:
        ctypes, k32, u32 = _api()
        data = (text + "\0").encode("utf-16-le")
        if not _open_clip(u32):
            return False
        try:
            u32.EmptyClipboard()
            h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not h:
                return False
            p = k32.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            k32.GlobalUnlock(h)
            if not u32.SetClipboardData(CF_UNICODETEXT, h):
                k32.GlobalFree(h)
                return False
            return True
        finally:
            u32.CloseClipboard()
    except Exception as e:
        log.warning("set_clipboard failed: %s", e)
        return False


def press(*keys: str) -> None:
    import pyautogui
    if len(keys) == 1:
        pyautogui.press(keys[0])
    else:
        pyautogui.hotkey(*keys)


def paste_text(text: str, restore: bool = True) -> bool:
    """Paste `text` into the focused field, then restore the old clipboard.

    Pasted newlines stay newlines (typing "\\n" in a chat app would SEND).
    If the previous clipboard wasn't text (e.g. an image) it can't be restored.
    """
    old = get_clipboard() if restore else None
    if not set_clipboard(text):
        return False
    time.sleep(0.05)
    press("ctrl", "v")
    time.sleep(0.25)
    if restore and old is not None:
        set_clipboard(old)
    return True


def type_ascii(text: str, interval: float = 0.01) -> bool:
    """Plain typing — ASCII only (pyautogui silently DROPS other characters)."""
    if not text.isascii():
        return False
    import pyautogui
    pyautogui.write(text, interval=interval)
    return True


def enter_text(text: str) -> bool:
    """Best way to get text into the focused field."""
    if _WIN and paste_text(text):
        return True
    return type_ascii(text)


# ---------------------------------------------------------------------------
# Phase 4 (dictation): real Unicode typing, selection helpers, focus snapshot.
# Win32 SendInput; every event carries INPUT_TAG so Lyra can tell her own typing
# from the user's. All of it is best effort and returns False/None off Windows.
# ---------------------------------------------------------------------------
INPUT_TAG = 0x4C595241            # "LYRA" in dwExtraInfo of every key event Lyra injects
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_RETURN, VK_LEFT, VK_RIGHT, VK_C = 0x0D, 0x25, 0x27, 0x43
VK_DUMMY = 0xE8                   # unassigned key: stops a released Alt/Win from opening a menu
_MODIFIER_VKS = (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C)   # L/R Shift, Ctrl, Alt, Win
_ALT_WIN_VKS = (0xA4, 0xA5, 0x5B, 0x5C)
_input_cls = None


def _input_type():
    """The Win32 INPUT structure (built once; needs ctypes.wintypes)."""
    global _input_cls
    if _input_cls is None:
        import ctypes
        from ctypes import wintypes

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class MOUSEINPUT(ctypes.Structure):      # only here so the union has the right size
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class _Union(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _Union)]

        _input_cls = INPUT
    return _input_cls


def _send(events: list) -> bool:
    """SendInput for [(vk, scan, flags), ...] key events."""
    if not _WIN or not events:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        INPUT = _input_type()
        arr = (INPUT * len(events))()
        for i, (vk, scan, flags) in enumerate(events):
            arr[i].type = 1                       # INPUT_KEYBOARD
            ki = arr[i].u.ki
            ki.wVk, ki.wScan, ki.dwFlags, ki.dwExtraInfo = vk, scan, flags, INPUT_TAG
        send = ctypes.windll.user32.SendInput
        send.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
        send.restype = wintypes.UINT
        return send(len(events), ctypes.addressof(arr), ctypes.sizeof(INPUT)) == len(events)
    except Exception as e:
        log.warning("SendInput failed: %s", e)
        return False


def _tap(vk: int, flags: int = 0) -> list:
    return [(vk, 0, flags), (vk, 0, flags | KEYEVENTF_KEYUP)]


def send_unicode(text: str) -> bool:
    """Type `text` as real Unicode characters (no clipboard, any language, emoji).

    "\\n" is Shift+Enter: a plain Enter would SEND the message in a chat box, while
    Shift+Enter is a new line in chat apps, text boxes and editors alike."""
    if not _WIN:
        return False
    ok, events = True, []
    for ch in text:
        if ch == "\r":
            continue
        if ch == "\n":
            events += [(VK_SHIFT, 0, 0)] + _tap(VK_RETURN) + [(VK_SHIFT, 0, KEYEVENTF_KEYUP)]
        else:
            data = ch.encode("utf-16-le")
            for i in range(0, len(data), 2):      # a character outside the BMP is two UTF-16 units
                unit = int.from_bytes(data[i:i + 2], "little")
                events += [(0, unit, KEYEVENTF_UNICODE), (0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
        if len(events) >= 200:
            ok = _send(events) and ok
            events = []
            time.sleep(0.004)
    return (_send(events) and ok) if events else ok


def held_modifiers() -> list:
    """Virtual-key codes of the Shift / Ctrl / Alt / Win keys that are down right now."""
    if not _WIN:
        return []
    import ctypes
    get = ctypes.windll.user32.GetAsyncKeyState
    return [vk for vk in _MODIFIER_VKS if get(vk) & 0x8000]


def release_modifiers() -> bool:
    """Let go of any modifier the user is physically holding (hold-to-dictate shortcuts),
    so typed characters are not read as Ctrl+letter / Alt+letter. True if any was held."""
    down = held_modifiers()
    if not down:
        return False
    events = []
    if any(vk in _ALT_WIN_VKS for vk in down):
        events += _tap(VK_DUMMY)                  # a key between Alt/Win down and up: no menu / Start
    events += [(vk, 0, KEYEVENTF_KEYUP) for vk in down]
    _send(events)
    return True


def mark_alt_used() -> bool:
    """While Alt/Win is held, tap an unassigned key so letting go of Alt does not open the app's menu
    bar (Notepad's "Edit" lit up after every dictation). True if a key was sent."""
    if any(vk in _ALT_WIN_VKS for vk in held_modifiers()):
        return _send(_tap(VK_DUMMY))
    return False


def select_back(count: int) -> bool:
    """Select the `count` characters before the caret (Shift+Left, `count` times)."""
    if count <= 0:
        return False
    ok = True
    events = [(VK_SHIFT, 0, 0)]
    for _ in range(count):
        events += _tap(VK_LEFT, KEYEVENTF_EXTENDEDKEY)
        if len(events) >= 400:
            ok = _send(events) and ok
            events = []
            time.sleep(0.004)
    events.append((VK_SHIFT, 0, KEYEVENTF_KEYUP))
    return _send(events) and ok


def collapse_selection_right() -> bool:
    """Put the caret back at the END of the selection (what select_back() started from)."""
    return _send(_tap(VK_RIGHT, KEYEVENTF_EXTENDEDKEY))


def read_selection(timeout: float = 0.6) -> Optional[str]:
    """Text of the current selection (Ctrl+C), or None if nothing was copied.
    The clipboard text from before is put back; an image / file clipboard cannot be restored."""
    old = get_clipboard()
    probe = f"lyra-selection-probe-{time.time_ns()}"
    if not set_clipboard(probe):
        return None
    _send([(VK_CONTROL, 0, 0)] + _tap(VK_C) + [(VK_CONTROL, 0, KEYEVENTF_KEYUP)])
    got = None
    deadline = time.time() + timeout
    while time.time() < deadline and got is None:
        time.sleep(0.03)
        cur = get_clipboard()
        if cur is not None and cur != probe:
            got = cur
    if old is not None:
        set_clipboard(old)
    elif got is None:
        set_clipboard("")                         # do not leave the probe text behind
    return got


def focus_snapshot() -> Optional[dict]:
    """Which window, control and caret has the keyboard right now:
    {"hwnd", "pid", "focus", "caret_hwnd", "caret": (l, t, r, b)}. Apps that draw their own
    caret (browsers, Electron) report an empty caret rectangle, so it is only one clue."""
    if not _WIN:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.windll.user32

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                        ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                        ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                        ("rcCaret", wintypes.RECT)]

        u32.GetForegroundWindow.restype = wintypes.HWND
        u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u32.GetWindowThreadProcessId.restype = wintypes.DWORD
        u32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
        hwnd = u32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD(0)
        tid = u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        u32.GetGUIThreadInfo(tid, ctypes.byref(info))
        r = info.rcCaret
        return {"hwnd": int(hwnd), "pid": int(pid.value), "focus": int(info.hwndFocus or 0),
                "caret_hwnd": int(info.hwndCaret or 0), "caret": (r.left, r.top, r.right, r.bottom)}
    except Exception as e:
        log.debug("focus_snapshot failed: %s", e)
        return None


def process_name(pid: int) -> str:
    """Lower-case executable name of a process ("notepad.exe"), "" if unknown."""
    try:
        import psutil
        return psutil.Process(int(pid)).name().lower()
    except Exception:
        return ""


def focus_window(hwnd: int) -> bool:
    """Bring a window (by handle) to the foreground. True if it ended up there."""
    if not _WIN or not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        u32.IsWindow.argtypes = u32.IsIconic.argtypes = u32.BringWindowToTop.argtypes = [wintypes.HWND]
        u32.SetForegroundWindow.argtypes = [wintypes.HWND]
        u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u32.GetForegroundWindow.restype = wintypes.HWND
        u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u32.GetWindowThreadProcessId.restype = wintypes.DWORD
        if not u32.IsWindow(hwnd):
            return False
        if u32.IsIconic(hwnd):
            u32.ShowWindow(hwnd, 9)               # SW_RESTORE
        fg = u32.GetForegroundWindow()
        if fg == hwnd:
            return True
        me = k32.GetCurrentThreadId()
        fg_tid = u32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = bool(fg_tid and fg_tid != me and u32.AttachThreadInput(me, fg_tid, True))
        try:                                       # attaching lets a background process take focus
            u32.BringWindowToTop(hwnd)
            u32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                u32.AttachThreadInput(me, fg_tid, False)
        time.sleep(0.15)
        return u32.GetForegroundWindow() == hwnd
    except Exception as e:
        log.debug("focus_window failed: %s", e)
        return False
