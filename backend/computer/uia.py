"""Windows UI Automation helpers (pywinauto, backend="uia").

Everything here is best-effort: if pywinauto is missing, the OS isn't Windows,
or the app exposes a thin tree (common for Electron/WebView apps), functions
return None/False and the caller falls back to paste + vision.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

import ui

log = ui.log

_INPUT_TYPES = ("Edit", "Document")
_SCAN_BUDGET_S = 3.5          # Chromium trees can be huge; never hang the voice loop


def available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import pywinauto  # noqa: F401
        return True
    except Exception:
        return False


def foreground_window():
    """UIA wrapper for the currently focused top-level window, or None."""
    if not available():
        return None
    try:
        import ctypes
        from pywinauto import Desktop
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return None
        return Desktop(backend="uia").window(handle=hwnd).wrapper_object()
    except Exception as e:
        log.debug("uia.foreground_window failed: %s", e)
        return None


def window_title(win) -> str:
    try:
        return (win.window_text() or "").strip()
    except Exception:
        return ""


def _walk(win):
    """Descendants with a time budget."""
    t0 = time.time()
    try:
        for c in win.iter_descendants():
            if time.time() - t0 > _SCAN_BUDGET_S:
                log.debug("uia scan budget hit")
                return
            yield c
    except Exception as e:
        log.debug("uia walk failed: %s", e)


def find_input(win, hints: Iterable[str], exclude: Iterable[str] = ()) -> Optional[object]:
    """Best editable control whose name/automation-id matches a hint.

    A hint match is REQUIRED, so we never grab an unrelated field (e.g. the
    search box when we want the message box). Lower-on-screen wins ties
    (message boxes live at the bottom).
    """
    hints = [h.lower() for h in hints if h]
    bad = [x.lower() for x in exclude if x]
    best, best_score = None, 0.0
    for c in _walk(win):
        try:
            info = c.element_info
            if info.control_type not in _INPUT_TYPES or not info.enabled:
                continue
            r = info.rect
            if r is None or r.width() < 80 or r.height() < 14:
                continue
            name = (info.name or "").lower()
            aid = (info.automation_id or "").lower()
            if any(b in name or b in aid for b in bad):
                continue
            score = sum(2.0 for h in hints if h in name) + sum(1.0 for h in hints if h in aid)
            if score <= 0:
                continue
            score += r.top / 100000.0
            if score > best_score:
                best, best_score = c, score
        except Exception:
            continue
    return best


def focus(ctrl) -> bool:
    try:
        ctrl.set_focus()
        time.sleep(0.12)
        return True
    except Exception:
        try:
            ctrl.click_input()
            time.sleep(0.12)
            return True
        except Exception as e:
            log.debug("uia.focus failed: %s", e)
            return False


def set_text(ctrl, text: str) -> bool:
    """Set a field's value directly via ValuePattern. False if unsupported."""
    try:
        ctrl.iface_value.SetValue(text)
        return True
    except Exception:
        pass
    try:
        ctrl.set_edit_text(text)
        return True
    except Exception:
        return False


def read_text(ctrl) -> Optional[str]:
    try:
        return ctrl.iface_value.CurrentValue
    except Exception:
        pass
    try:
        return ctrl.get_value()
    except Exception:
        pass
    try:
        return ctrl.window_text()
    except Exception:
        return None


def visible_texts(win, limit: int = 80) -> list[str]:
    """Names of Text/Header controls (used to verify which chat is open)."""
    out: list[str] = []
    for c in _walk(win):
        try:
            if c.element_info.control_type in ("Text", "Header", "Hyperlink", "TitleBar"):
                n = (c.element_info.name or "").strip()
                if n:
                    out.append(n)
                    if len(out) >= limit:
                        break
        except Exception:
            continue
    return out


def center(ctrl) -> Optional[tuple[int, int]]:
    try:
        r = ctrl.rectangle()
        return (r.left + r.right) // 2, (r.top + r.bottom) // 2
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Acting layer (used by guide_act.py): find controls by NAME, press them, never guess pixels.
#
# UNTESTED on a real Windows desktop in the build that produced this section (the tests drive
# guide_act through a fake backend). pywinauto's UIA wrappers are used as documented.
# ---------------------------------------------------------------------------
_ACTIONABLE = frozenset({"Button", "SplitButton", "Hyperlink", "MenuItem", "TabItem", "ListItem", "TreeItem",
                         "CheckBox", "RadioButton", "ComboBox", "DataItem"})
# Named things worth *pointing at* even though they cannot be pressed (labels, fields, images).
_LOOK_TYPES = _ACTIONABLE | {"Edit", "Text", "Image", "Header", "HeaderItem", "Group", "Document", "Slider",
                             "ProgressBar", "ToolBar", "TitleBar"}
_SKIP_CLASSES = frozenset({"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW", "NotifyIconOverflowWindow",
                           "Windows.UI.Core.CoreWindow"})
_MAX_NAME = 80
_MAX_TEXTS = 400


@dataclass
class Ctl:
    """One named control: what the planner sees and what the guide points at."""
    name: str
    type: str
    rect: tuple                 # (left, top, right, bottom) in physical screen pixels
    enabled: bool = True
    actionable: bool = True
    ref: object = field(default=None, repr=False)

    @property
    def center(self) -> tuple[int, int]:
        return (self.rect[0] + self.rect[2]) // 2, (self.rect[1] + self.rect[3]) // 2


@dataclass
class Scan:
    ctls: list
    texts: list                 # every visible name (used to spot payment / sign-in pages)
    has_password: bool = False
    truncated: bool = False


@dataclass
class WinInfo:
    title: str
    process: str                # lower-case exe name, "" when unknown
    pid: int
    elevated: "bool | None"     # True / False, None when it cannot be asked
    rect: tuple
    ref: object = field(default=None, repr=False)


def _clean(s) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f]", " ", str(s or ""))).strip()[:_MAX_NAME]


def process_name(pid: int) -> str:
    try:
        import psutil
        return psutil.Process(int(pid)).name().lower()
    except Exception:
        return ""


def is_elevated(pid: int) -> "bool | None":
    """True when the process runs as administrator. A standard process cannot open an elevated
    process's token, so 'opened the process but not its token' also counts as elevated."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32, adv = ctypes.windll.kernel32, ctypes.windll.advapi32
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(0x1000, False, int(pid))           # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            tok = wintypes.HANDLE()
            if not adv.OpenProcessToken(h, 0x0008, ctypes.byref(tok)):   # TOKEN_QUERY
                return True
            try:
                val, size = wintypes.DWORD(0), wintypes.DWORD(0)
                if not adv.GetTokenInformation(tok, 20, ctypes.byref(val), ctypes.sizeof(val), ctypes.byref(size)):
                    return None                                 # TokenElevation
                return bool(val.value)
            finally:
                k32.CloseHandle(tok)
        finally:
            k32.CloseHandle(h)
    except Exception as e:
        log.debug("uia.is_elevated failed: %s", e)
        return None


def _top_level_hwnds() -> list[int]:
    """Visible, un-minimised, un-cloaked top-level windows with a title, topmost first."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    out: list[int] = []
    cls = ctypes.create_unicode_buffer(256)

    def cb(hwnd, _lp):
        try:
            if not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd) or not u.GetWindowTextLengthW(hwnd):
                return True
            u.GetClassNameW(hwnd, cls, 256)
            if cls.value in _SKIP_CLASSES:
                return True
            cloaked = ctypes.c_int(0)
            try:
                ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            except Exception:
                pass
            if not cloaked.value:
                out.append(int(hwnd))
        except Exception:
            pass
        return True

    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    u.EnumWindows(proto(cb), 0)
    return out


def target_window() -> "WinInfo | None":
    """The app window the user means: the focused one, or the topmost real window when Lyra's own
    island/chat has the focus. None when UI Automation isn't available."""
    if not available():
        return None
    try:
        import ctypes
        import os
        from pywinauto import Desktop
        u = ctypes.windll.user32
        order = [int(u.GetForegroundWindow() or 0)] + _top_level_hwnds()
        seen = set()
        for hwnd in order:
            if not hwnd or hwnd in seen:
                continue
            seen.add(hwnd)
            pid = ctypes.c_ulong(0)
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            proc = process_name(pid.value)
            if pid.value == os.getpid() or "lyra" in proc:
                continue                                        # Lyra's own windows are never the target
            wrapper = Desktop(backend="uia").window(handle=hwnd).wrapper_object()
            r = wrapper.rectangle()
            return WinInfo(window_title(wrapper), proc, int(pid.value), is_elevated(pid.value),
                           (r.left, r.top, r.right, r.bottom), wrapper)
    except Exception as e:
        log.debug("uia.target_window failed: %s", e)
    return None


def scan_controls(win: "WinInfo", limit: int = 160) -> Scan:
    """Named controls of `win` (time-boxed). Only visible controls inside the window are kept."""
    ctls: list[Ctl] = []
    texts: list[str] = []
    has_pw = False
    wl, wt, wr, wb = win.rect
    n_seen = 0
    for c in _walk(win.ref):
        n_seen += 1
        try:
            info = c.element_info
            ctype = info.control_type
            name = _clean(info.name)
            if name and len(texts) < _MAX_TEXTS:
                texts.append(name)
            if ctype == "Edit" and not has_pw:
                try:
                    has_pw = bool(info.element.CurrentIsPassword)
                except Exception:
                    pass
            if not name or ctype not in _LOOK_TYPES or len(ctls) >= limit:
                continue
            if not info.visible:
                continue
            r = info.rect
            if r is None or r.width() < 4 or r.height() < 4:
                continue
            rect = (r.left, r.top, r.right, r.bottom)
            cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
            if not (wl <= cx <= wr and wt <= cy <= wb):
                continue
            enabled = bool(info.enabled)
            ctls.append(Ctl(name, ctype, rect, enabled, ctype in _ACTIONABLE and enabled, c))
        except Exception:
            continue
    ctls.sort(key=lambda k: (k.rect[1] // 24, k.rect[0]))       # reading order: rows top to bottom, then left to right
    return Scan(ctls, texts, has_pw, truncated=len(ctls) >= limit)


def control_state(ctl: Ctl):
    """Selected / toggled state of a control (None when it has none): lets a click be verified even
    when the picture barely changes."""
    try:
        if ctl.type == "CheckBox":
            return ("toggle", ctl.ref.get_toggle_state())
        if ctl.type in ("TabItem", "ListItem", "RadioButton", "TreeItem", "DataItem"):
            return ("selected", bool(ctl.ref.is_selected()))
    except Exception:
        pass
    return None


def press(ctl: Ctl, physical: bool = False) -> bool:
    """Press a control through its UI Automation pattern (no mouse needed). With `physical` (or when
    no pattern works) it clicks the control's centre with the real mouse instead."""
    ref = ctl.ref
    if not physical:
        if ctl.type in ("TabItem", "ListItem", "TreeItem", "RadioButton", "DataItem"):
            order = ("select", "invoke", "toggle", "expand")
        elif ctl.type == "CheckBox":
            order = ("toggle", "invoke", "select")
        else:
            order = ("invoke", "select", "toggle", "expand")
        for name in order:
            try:
                getattr(ref, name)()
                return True
            except Exception:
                continue
    try:
        from computer.mouse import ensure_dpi_aware
        ensure_dpi_aware()
        ref.click_input()
        return True
    except Exception as e:
        log.debug("uia.press failed: %s", e)
        return False
