"""Lyra — per-app volume: read and set each app's own Windows volume (what the Volume Mixer
shows), and tell the island when one changes so it can show it in the volume HUD.

Uses pycaw, the same library as Spotify ducking in voice.py (already in requirements.txt).
Lyra's own ducking calls quiet() so lowering Spotify to speak never pops the HUD up.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import ui

log = ui.log

POLL_S = 1.5          # was 0.5: re-reading every app's audio session twice a second used ~12% of a core
CHANGE = 0.01            # smallest level change worth showing (1 %)
_quiet_until = 0.0
_tls = threading.local()
_started = False
_warned: list = []   # the first failure is logged as a warning, later ones quietly

_NAMES = {
    "spotify.exe": "Spotify", "chrome.exe": "Chrome", "msedge.exe": "Edge", "firefox.exe": "Firefox",
    "brave.exe": "Brave", "opera.exe": "Opera", "vlc.exe": "VLC", "discord.exe": "Discord",
    "telegram.exe": "Telegram", "whatsapp.exe": "WhatsApp", "teams.exe": "Teams", "zoom.exe": "Zoom",
}


def _friendly(exe: str) -> str:
    known = _NAMES.get(exe.lower())
    if known:
        return known
    base = exe[:-4] if exe.lower().endswith(".exe") else exe
    return (base[:1].upper() + base[1:])[:24]


def quiet(seconds: float = 1.5) -> None:
    """Don't announce changes for a moment (Lyra is ducking/restoring music on purpose)."""
    global _quiet_until
    _quiet_until = max(_quiet_until, time.time() + seconds)


def _com() -> None:
    """Each thread that talks to Core Audio needs COM initialised once."""
    if getattr(_tls, "ok", False):
        return
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass
    _tls.ok = True


def _sessions() -> dict[str, dict]:
    """exe (lower case) -> {exe, name, level, muted, active, _vols}. One entry per app:
    a browser with several audio sessions is one app (level = the loudest one)."""
    _com()
    try:
        from pycaw.pycaw import AudioUtilities
    except ImportError as e:
        import sys
        # Say which Python the backend is really using: the usual cause is pycaw installed
        # into a different Python (or a different venv) than the one running Lyra.
        raise RuntimeError(
            f'Could not load pycaw ({e}). The backend runs on: {sys.executable} - install with: '
            f'"{sys.executable}" -m pip install pycaw comtypes'
        ) from None
    out: dict[str, dict] = {}
    for s in AudioUtilities.GetAllSessions():
        try:
            proc = s.Process
            if proc is None:                       # "System sounds"
                continue
            state = int(s.State)                   # 0 inactive, 1 active, 2 expired
            if state == 2:
                continue
            exe = proc.name()
            vol = s.SimpleAudioVolume
            level, muted = float(vol.GetMasterVolume()), bool(vol.GetMute())
        except Exception:
            continue
        cur = out.get(exe.lower())
        if cur is None:
            out[exe.lower()] = {"exe": exe, "name": _friendly(exe), "level": level, "muted": muted,
                                "active": state == 1, "_vols": [vol]}
        else:
            cur["level"] = max(cur["level"], level)
            cur["muted"] = cur["muted"] and muted
            cur["active"] = cur["active"] or state == 1
            cur["_vols"].append(vol)
    return out



_icon_cache: dict[str, str | None] = {}


def _query_image_path(pid: int) -> str | None:
    """Exe path of a process even when psutil is denied (Store apps, elevated apps)."""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                       wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value
        finally:
            k32.CloseHandle(h)
    except Exception:
        pass
    return None


def _exe_icon_b64(exe_name: str) -> str | None:
    """Extract a small PNG (base64) from the running process exe. Cached per exe name."""
    key = (exe_name or "").lower()
    if key in _icon_cache:
        return _icon_cache[key]
    try:
        import psutil
        from PIL import Image
        import base64
        import io
        import ctypes
        from ctypes import wintypes

        path = None
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                if (proc.info.get("name") or "").lower() != key:
                    continue
                path = proc.info.get("exe") or _query_image_path(proc.pid)
                if path:
                    break
            except Exception:
                continue
        if not path:
            return None  # not cached: the process may just not be visible yet

        # Extract icon via Shell32
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        gdi32 = ctypes.windll.gdi32
        large = ctypes.c_void_p()
        small = ctypes.c_void_p()
        count = shell32.ExtractIconExW(path, 0, ctypes.byref(large), ctypes.byref(small), 1)
        handle = large.value or small.value
        if not handle or count < 1:
            _icon_cache[key] = None
            return None

        import app_icons
        b64 = app_icons.hicon_to_data_url(handle, 32)
        if large.value:
            user32.DestroyIcon(large)
        if small.value:
            user32.DestroyIcon(small)
        _icon_cache[key] = b64
        return b64
    except Exception:
        _icon_cache[key] = None
        return None


def list_apps() -> list[dict]:
    """Apps that have an audio session right now, playing ones first."""
    apps = []
    for a in _sessions().values():
        row = {k: v for k, v in a.items() if k != "_vols"}
        row["icon"] = _exe_icon_b64(a.get("exe") or "")
        apps.append(row)
    apps.sort(key=lambda a: (not a["active"], a["name"].lower()))
    return apps


def set_app(exe: str, level: float) -> float:
    """Set one app's volume (0.0 - 1.0). Only apps that currently have a session are accepted."""
    level = max(0.0, min(1.0, float(level)))
    app = _sessions().get(str(exe).lower())
    if app is None:
        raise ValueError("that app is not making sound right now")
    for vol in app["_vols"]:
        vol.SetMasterVolume(level, None)
        if level > 0:
            vol.SetMute(0, None)
    return level


def mute_app(exe: str, muted: bool | None = None) -> bool:
    """Mute / unmute one app. muted=None toggles. Returns the new muted state."""
    app = _sessions().get(str(exe).lower())
    if app is None:
        raise ValueError("that app is not making sound right now")
    if muted is None:
        muted = not bool(app["muted"])
    muted = bool(muted)
    for vol in app["_vols"]:
        vol.SetMute(1 if muted else 0, None)
    return muted


def resolve_app(query: str) -> dict | None:
    """Fuzzy-match a spoken / typed name to a current audio session."""
    q = (query or "").strip().lower()
    if not q:
        return None
    sessions = _sessions()
    apps = list(sessions.values())
    for a in apps:
        if a["exe"].lower() == q or a["name"].lower() == q:
            return a
    hits = [a for a in apps if q in a["name"].lower() or q in a["exe"].lower()]
    if hits:
        hits.sort(key=lambda a: (not a["active"], a["name"].lower()))
        return hits[0]
    aliases = {
        "browser": ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"),
        "edge": ("msedge.exe",),
        "vs code": ("code.exe",),
        "vscode": ("code.exe",),
        "code": ("code.exe",),
    }
    for key, exes in aliases.items():
        if key in q or q in key:
            for e in exes:
                if e in sessions:
                    return sessions[e]
    return None


def set_app_by_name(name: str, level: float) -> tuple[str, float]:
    app = resolve_app(name)
    if app is None:
        raise ValueError(f"no audio session for '{name}'")
    v = set_app(app["exe"], level)
    return app["name"], v


def mute_app_by_name(name: str, muted: bool | None = None) -> tuple[str, bool]:
    app = resolve_app(name)
    if app is None:
        raise ValueError(f"no audio session for '{name}'")
    m = mute_app(app["exe"], muted)
    return app["name"], m


def start(push: Callable[[dict], None]) -> None:
    """Watch every app's volume and push {"type":"appvolume",...} when one changes.
    The first reading is only a baseline, and apps appearing later don't announce themselves."""
    global _started
    if _started:
        return
    _started = True

    def loop() -> None:
        prev: dict[str, tuple[float, bool]] | None = None
        while True:
            try:
                sessions = _sessions()
                cur = {k: (a["level"], a["muted"]) for k, a in sessions.items()}
                if prev is not None and time.time() >= _quiet_until:
                    for key, (level, muted) in cur.items():
                        old = prev.get(key)
                        if old and (abs(old[0] - level) >= CHANGE or old[1] != muted):
                            push({"type": "appvolume", "app": sessions[key]["name"], "level": level,
                                  "muted": muted, "ts": int(time.time() * 1000)})
                prev = cur
            except Exception as e:  # pycaw missing or Core Audio hiccup: try again next tick
                if not _warned:
                    _warned.append(1)
                    log.warning("app volume watch: %s", e)
                else:
                    log.debug("app volume watch: %s", e)
            time.sleep(POLL_S)

    threading.Thread(target=loop, daemon=True, name="lyra-app-volume").start()
