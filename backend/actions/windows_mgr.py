"""Lyra — window management (minimize, maximize, close, switch, snap)."""

from __future__ import annotations

import time
import re

import ui

log = ui.log

try:
    import pygetwindow as gw
except Exception:
    gw = None

try:
    import pyautogui
except Exception:
    pyautogui = None


def _active():
    if not gw:
        return None
    try:
        return gw.getActiveWindow()
    except Exception:
        return None


def list_windows(limit: int = 12) -> list[str]:
    if not gw:
        return []
    titles = []
    try:
        for w in gw.getAllWindows():
            t = (w.title or "").strip()
            if t and getattr(w, "width", 0) > 80:
                titles.append(t)
    except Exception as e:
        log.warning("list_windows: %s", e)
    # de-dupe preserve order
    seen = set()
    out = []
    for t in titles:
        if t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= limit:
            break
    return out


def minimize_active() -> str:
    w = _active()
    if not w:
        return "No active window to minimize."
    try:
        w.minimize()
        return f"Minimized “{w.title}”."
    except Exception as e:
        return f"Couldn't minimize — {e}"


def maximize_active() -> str:
    w = _active()
    if not w:
        return "No active window to maximize."
    try:
        if getattr(w, "isMinimized", False):
            w.restore()
        w.maximize()
        return f"Maximized “{w.title}”."
    except Exception as e:
        return f"Couldn't maximize — {e}"


def restore_active() -> str:
    w = _active()
    if not w:
        return "No active window."
    try:
        w.restore()
        return f"Restored “{w.title}”."
    except Exception as e:
        return f"Couldn't restore — {e}"


def close_active() -> str:
    w = _active()
    if not w:
        return "No active window to close."
    title = w.title or "window"
    try:
        w.close()
        return f"Closed “{title}”."
    except Exception as e:
        return f"Couldn't close — {e}"


def switch_to(title_substring: str) -> str:
    from actions.apps import focus_window, _find_window
    if focus_window(title_substring):
        return f"Switched to “{title_substring}”."
    # Fuzzy over titles
    titles = list_windows(30)
    low = title_substring.lower()
    for t in titles:
        if low in t.lower():
            if focus_window(t):
                return f"Switched to “{t}”."
    return f"Couldn't find a window matching “{title_substring}”."


def snap_active(side: str) -> str:
    """Snap left/right/up (maximize) using Windows Win+Arrow."""
    if not pyautogui:
        return "Can't snap — pyautogui missing."
    side = (side or "").lower()
    key = {"left": "left", "right": "right", "up": "up", "down": "down"}.get(side)
    if not key:
        return "Say snap left, right, up, or down."
    try:
        pyautogui.hotkey("win", key)
        time.sleep(0.15)
        return f"Snapped {side}."
    except Exception as e:
        return f"Couldn't snap — {e}"


def next_window() -> str:
    if not pyautogui:
        return "Can't switch — pyautogui missing."
    try:
        pyautogui.hotkey("alt", "tab")
        return "Switched window."
    except Exception as e:
        return f"Couldn't switch — {e}"


# Voice patterns
MINIMIZE_PATTERN = re.compile(r"\b(?:minimize(?:\s+(?:the\s+)?window)?|minimise)\b", re.I)
MAXIMIZE_PATTERN = re.compile(r"\b(?:maximize(?:\s+(?:the\s+)?window)?|maximise|fullscreen)\b", re.I)
RESTORE_PATTERN = re.compile(r"\b(?:restore(?:\s+(?:the\s+)?window)?)\b", re.I)
CLOSE_WINDOW_PATTERN = re.compile(
    r"\b(?:close(?:\s+(?:the\s+)?(?:window|app))?|close\s+this)\b", re.I
)
SWITCH_TO_PATTERN = re.compile(
    r"\b(?:switch\s+to|focus|go\s+to\s+window)\s+(?P<title>.+)$", re.I
)
SNAP_PATTERN = re.compile(r"\b(?:snap|dock)\s+(?P<side>left|right|up|down)\b", re.I)
LIST_WINDOWS_PATTERN = re.compile(
    r"\b(?:list\s+windows|what\s+windows\s+are\s+open|show\s+windows)\b", re.I
)
NEXT_WINDOW_PATTERN = re.compile(r"\b(?:next\s+window|switch\s+window)\b", re.I)
