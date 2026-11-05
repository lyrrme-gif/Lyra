"""Lyra — system-level actions: volume/media keys, lock, screenshot,
shutdown/restart, and system-status/current-window info helpers."""

import re
import time
import ctypes
import subprocess
from datetime import datetime
from pathlib import Path

import psutil
from PIL import ImageGrab
import pygetwindow as gw

from engine.core.config import POWER_ACTION_DELAY

# ---------------------------------------------------------------------------
# System controls (volume, lock, screenshot, etc.) — all key on VK_ media keys
# or pyautogui hotkeys. No app focus required.
# ---------------------------------------------------------------------------
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_NEXT = 0xB0
VK_MEDIA_PREV = 0xB1

# Volume / media control — these map to dedicated media keys (no app focus needed).
VOLUME_UP_PATTERN = re.compile(r"\b(?:turn\s+it\s+up|volume\s+up|louder|increase\s+(?:the\s+)?volume)\b", re.IGNORECASE)
VOLUME_DOWN_PATTERN = re.compile(r"\b(?:turn\s+it\s+down|volume\s+down|quieter|softer|decrease\s+(?:the\s+)?volume)\b", re.IGNORECASE)
MUTE_PATTERN = re.compile(r"\b(?:mute|unmute|silence)\b", re.IGNORECASE)
SET_VOLUME_PATTERN = re.compile(
    r"\b(?:set\s+(?:the\s+)?volume\s+(?:to\s+)?(\d{1,3})\s*%?|volume\s+(?:to\s+)?(\d{1,3})\s*%?)\b",
    re.IGNORECASE,
)
SET_APP_VOLUME_PATTERN = re.compile(
    # "set chrome volume to 20", "turn spotify down to 30", "make discord at 50 percent" ...
    r"\b(?:set|turn|put|make|change|lower|raise)\s+(?:the\s+)?(?P<app>[A-Za-z][\w .-]{1,24}?)\s+"
    r"(?:volume\s+)?(?:(?:up|down)\s+)?(?:to|at)\s+(?P<pct>\d{1,3})\s*(?:%|percent)?"
    # "chrome volume 20", "spotify volume at 40 percent"
    r"|\b(?P<app2>[A-Za-z][\w .-]{1,24}?)\s+volume\s+(?:(?:to|at)\s+)?(?P<pct2>\d{1,3})\s*(?:%|percent)?",
    re.IGNORECASE,
)
RECOGNIZE_MUSIC_PATTERN = re.compile(
    r"\b(?:"
    r"(?:what|whats|what's)\s+(?:is\s+)?(?:this\s+|the\s+)?(?:song|track|music|playing|play|played)|"
    r"(?:what|whats|what's)\s+(?:is\s+)?(?:the\s+)?(?:playing|play|played)\s+(?:song|track|music)|"
    r"(?:identify|recognize|name|find)\s+(?:this\s+|the\s+)?(?:song|track|music|playing)|"
    r"(?:which|what)\s+(?:song|track|music)\s+(?:is|was)\s+(?:this|playing|that)|"
    r"shazam"
    r")\b",
    re.IGNORECASE,
)
MUTE_APP_PATTERN = re.compile(
    r"\b(?:mute|unmute|silence)\s+(?P<app>[A-Za-z][\w .-]{1,30})",
    re.IGNORECASE,
)
CLIPBOARD_READ_PATTERN = re.compile(
    r"\b(?:what(?:'s|\s+is)\s+on\s+(?:my\s+)?(?:the\s+)?clipboard|read\s+(?:the\s+)?clipboard|clipboard\s+contents?)\b",
    re.IGNORECASE,
)
PAUSE_PLAY_PATTERN = re.compile(r"\b(?:pause|resume|play\s+pause)\b", re.IGNORECASE)
NEXT_TRACK_PATTERN = re.compile(r"\b(?:next\s+(?:track|song)|skip(?:\s+this)?)\b", re.IGNORECASE)
PREV_TRACK_PATTERN = re.compile(r"\b(?:previous\s+(?:track|song)|go\s+back\s+a\s+(?:track|song)|last\s+(?:track|song))\b", re.IGNORECASE)
LOCK_SCREEN_PATTERN = re.compile(r"\b(?:lock\s+(?:the\s+)?(?:computer|pc|screen))\b", re.IGNORECASE)
CAPTURE_CONTEXT_PATTERN = re.compile(
    r"\b(?:capture\s+(?:my\s+)?(?:the\s+)?screen|grab\s+(?:the\s+)?screen(?:shot)?\s+for\s+(?:context|me)|what(?:'?s|\s+is)\s+on\s+my\s+screen)\b",
    re.IGNORECASE,
)
SCREENSHOT_PATTERN = re.compile(r"\b(?:take\s+(?:a\s+)?screenshot|screenshot)\b", re.IGNORECASE)
SHUTDOWN_PATTERN = re.compile(r"\b(?P<verb>shut\s*down|restart|reboot)\s+(?:the\s+)?(?:computer|pc)\b", re.IGNORECASE)


def _send_media_key(vk_code: int):
    """Send a media key to the OS — works regardless of which app is focused."""
    ctypes.windll.user32.keybd_event(vk_code, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk_code, 0, 2, 0)  # KEYEVENTF_KEYUP


def adjust_volume(direction: str, steps: int = 5):
    """Press volume up/down N times. Each step is ~2% on Windows."""
    vk = VK_VOLUME_UP if direction == "up" else VK_VOLUME_DOWN
    for _ in range(max(1, steps)):
        _send_media_key(vk)
        time.sleep(0.05)


def set_volume_level(percent: int) -> tuple[bool, str]:
    """Set master volume to approximately `percent` (0–100).

    Tries Windows Core Audio via pycaw if installed; otherwise approximates
    with media keys (down to near-zero, then up).
    """
    percent = max(0, min(100, int(percent)))
    # Preferred: pycaw. Works with old pycaw (device.Activate) and new pycaw (device.EndpointVolume).
    try:
        from ctypes import POINTER, cast
        import comtypes
        comtypes.CoInitialize()
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        dev = AudioUtilities.GetSpeakers()
        ep = getattr(dev, "EndpointVolume", None)
        if ep is None:
            iface = dev.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
            ep = cast(iface, POINTER(IAudioEndpointVolume))
        ep.SetMasterVolumeLevelScalar(percent / 100.0, None)
        return True, f"Volume set to {percent}%."
    except Exception:
        pass
    # Fallback: media-key approximation (~2% per step)
    adjust_volume("down", steps=55)
    time.sleep(0.15)
    if percent > 0:
        adjust_volume("up", steps=max(1, int(round(percent / 2))))
    return True, f"Volume set to about {percent}%."


def get_clipboard_text() -> str:
    """Return current clipboard text, or empty string."""
    try:
        import subprocess
        # PowerShell is reliable on Windows without extra deps
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            return (r.stdout or "").strip()
    except Exception:
        pass
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        text = root.clipboard_get()
        root.destroy()
        return str(text)
    except Exception:
        return ""


def set_clipboard_text(text: str) -> bool:
    try:
        import subprocess
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Set-Clipboard -Value {repr(text)}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def take_screenshot() -> str:
    """Persist a screenshot through the shared screen_capture service."""
    try:
        from engine.features import screen_capture
        out_dir = Path.home() / "Pictures" / "Screenshots"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"screenshot_{datetime.now():%Y%m%d_%H%M%S}.png"
        result = screen_capture.capture(
            mode="full",
            save_path=out_path,
            keep_context=True,
        )
        if result.get("ok") and result.get("path"):
            return str(result["path"])
        return f"failed: {result.get('message') or 'unknown error'}"
    except Exception as e:
        return f"failed: {e}"


def lock_screen():
    ctypes.windll.user32.LockWorkStation()


def system_power_action(verb: str):
    """verb is 'shutdown', 'restart', or 'reboot' (already whitespace-stripped
    by the caller). Schedules a delayed shutdown/restart via the Windows
    `shutdown` utility rather than acting instantly, so a misheard command
    can still be cancelled with system_power_cancel()."""
    flag = "/r" if verb in ("restart", "reboot") else "/s"
    try:
        subprocess.run(["shutdown", flag, "/t", str(POWER_ACTION_DELAY)], check=True)
        action_word = "Restarting" if flag == "/r" else "Shutting down"
        return True, f"{action_word} in {POWER_ACTION_DELAY} seconds, sir. Say 'cancel shutdown' to stop it."
    except Exception as e:
        return False, str(e)


def system_power_cancel():
    try:
        subprocess.run(["shutdown", "/a"], check=True)
        return True, "Cancelled, sir."
    except Exception as e:
        return False, str(e)


CANCEL_SHUTDOWN_PATTERN = re.compile(r"\b(?:cancel\s+(?:the\s+)?shutdown|abort\s+shutdown|stop\s+shutdown)\b", re.IGNORECASE)



# ---------------------------------------------------------------------------
# System info helpers (CPU, RAM, battery) — for "how's my computer doing?"
# ---------------------------------------------------------------------------
def get_system_status() -> str:
    parts = []
    try:
        cpu = psutil.cpu_percent(interval=0.5)
        parts.append(f"CPU at {cpu}%")
    except Exception:
        pass
    try:
        ram = psutil.virtual_memory()
        parts.append(f"RAM at {ram.percent}%")
    except Exception:
        pass
    try:
        bat = psutil.sensors_battery()
        if bat is not None:
            plugged = "plugged in" if bat.power_plugged else "on battery"
            parts.append(f"battery {bat.percent}% and {plugged}")
    except Exception:
        pass
    return "Your machine is " + ", ".join(parts) + "." if parts else "I couldn't read system stats."


def get_current_window() -> str:
    """Return the title of the currently focused window — useful for 'what am I looking at?'."""
    try:
        win = gw.getActiveWindow()
        if win and win.title:
            return f"You're looking at {win.title}."
    except Exception:
        pass
    return "I couldn't tell which window is in focus."

