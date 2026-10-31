"""Mouse helpers with explicit DPI handling."""
from __future__ import annotations

import sys
import time

import ui

log = ui.log
_dpi_done = False


def ensure_dpi_aware() -> None:
    """Make this process per-monitor DPI aware so screen coords are physical px.

    Harmless if Qt/Tauri already set it (the call just fails and we move on).
    """
    global _dpi_done
    if _dpi_done or sys.platform != "win32":
        return
    _dpi_done = True
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception as e:
        log.debug("DPI awareness not set: %s", e)


def click(x: int, y: int, button: str = "left", double: bool = False) -> bool:
    import pyautogui
    ensure_dpi_aware()
    try:
        w, h = pyautogui.size()
        x = max(2, min(int(x), w - 2))
        y = max(2, min(int(y), h - 2))
        pyautogui.moveTo(x, y, duration=0.12)
        time.sleep(0.1)
        if double:
            pyautogui.doubleClick(x, y, button=button)
        else:
            pyautogui.click(x, y, button=button)
        return True
    except Exception as e:
        log.error("mouse.click failed: %s", e)
        return False


def position() -> tuple[int, int] | None:
    try:
        import pyautogui
        p = pyautogui.position()
        return int(p.x), int(p.y)
    except Exception:
        return None


def glide(x: int, y: int, duration: float = 0.35) -> bool:
    """Move the real pointer smoothly (the visible 'Lyra cursor'). Never clicks."""
    import pyautogui
    ensure_dpi_aware()
    try:
        w, h = pyautogui.size()
        pyautogui.moveTo(max(2, min(int(x), w - 2)), max(2, min(int(y), h - 2)), duration=duration)
        return True
    except Exception as e:
        log.error("mouse.glide failed: %s", e)
        return False


def wheel(x: int, y: int, notches: int) -> bool:
    """Scroll with the mouse wheel at (x, y). Positive scrolls up, negative down."""
    import pyautogui
    ensure_dpi_aware()
    try:
        w, h = pyautogui.size()
        pyautogui.moveTo(max(2, min(int(x), w - 2)), max(2, min(int(y), h - 2)), duration=0.12)
        time.sleep(0.05)
        pyautogui.scroll(int(notches) * 120)
        return True
    except Exception as e:
        log.error("mouse.wheel failed: %s", e)
        return False
