"""Windows Downloads folder watcher for Lyra's Dynamic Island.

Uses only the standard library so it works in the normal Lyra venv. Browser
partial files (.crdownload/.part/.tmp) are treated as active downloads; when
the partial disappears and a final file appears, Lyra announces completion.
"""
from __future__ import annotations

import os
import threading
import time
import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Callable

Publisher = Callable[[dict], None]

_PARTIAL = {".crdownload", ".part", ".tmp", ".download"}
_IGNORE = {"desktop.ini"}
_INTERVAL = 0.35
_IDLE_INTERVAL = 1.0   # light mode: while nothing is downloading, look at the folder once a second


def _light() -> bool:
    try:
        import settings_store
        return settings_store.feature_on("light_mode")
    except Exception:
        return False
_HEARTBEAT = 1.5
_MIN_GAP = 0.9  # never flood the island: at most ~1 progress update per second per file

# Time of the last progress event this watcher published (the browser watcher backs off while it is fresh).
LAST_PROGRESS_TS = 0.0

_stop = threading.Event()
_thread: threading.Thread | None = None


def _downloads_dir() -> Path:
    """Resolve the real Windows Downloads known folder, including redirected/OneDrive paths."""
    if os.name == "nt":
        try:
            # FOLDERID_Downloads = {374DE290-123F-4565-9164-39C4925E467B}
            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                            ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]
            g = GUID(0x374DE290, 0x123F, 0x4565,
                     (wintypes.BYTE * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
            ptr = ctypes.c_wchar_p()
            hr = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(ptr))
            if hr == 0 and ptr.value:
                folder = Path(ptr.value)
                try:
                    ctypes.windll.ole32.CoTaskMemFree(ptr)
                except Exception:
                    pass
                if folder.exists():
                    return folder
        except Exception:
            pass
    # Fallbacks for older Windows setups / non-Windows development.
    candidates = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            raw,_=winreg.QueryValueEx(k,"{374DE290-123F-4565-9164-39C4925E467B}")
            if raw: candidates.append(Path(os.path.expandvars(str(raw))))
    except Exception: pass
    candidates += [
        Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Downloads",
        Path(os.environ.get("USERPROFILE", str(Path.home()))) / "OneDrive" / "Downloads",
        Path.home() / "Downloads",
    ]
    for folder in candidates:
        if folder.exists():
            return folder
    return candidates[0]


def _snapshot(folder: Path) -> dict[str, tuple[int, int, bool]]:
    out={}
    try:
        for p in folder.rglob("*"):
            if not p.is_file() or p.name.lower() in _IGNORE: continue
            try:
                st=p.stat(); key=str(p.relative_to(folder)); out[key]=(int(st.st_size),int(st.st_mtime_ns),p.suffix.lower() in _PARTIAL)
            except OSError: pass
    except OSError: pass
    return out

def _pretty_name(name: str) -> str:
    if name.lower().endswith(".crdownload"):
        return name[:-11] or name
    if name.lower().endswith(".part"):
        return name[:-5] or name
    return name


def _is_temp_name(name: str) -> bool:
    return Path(name).name.lower().startswith("unconfirmed ")


def _publish(pub: Publisher, *, phase: str, name: str, size: int = 0, subtitle: str = "",
             progress: float | None = None, title: str | None = None) -> None:
    ev = {
        "type": "activity",
        "kind": "download",
        "phase": phase,
        "title": (title or Path(_pretty_name(name)).name)[:80],
        "subtitle": subtitle[:120] if subtitle else None,
        "duration_ms": 2200 if phase == "success" else 5000,
        "ts": int(time.time() * 1000),
    }
    if progress is not None:
        ev["progress"] = round(progress, 1)
    pub(ev)
    if phase == "success":                     # the Mochi enjoys a finished download
        try:
            import ui_bridge
            ui_bridge.push_mascot_emote("munch", 2500)
        except Exception:
            pass


def start(publisher: Publisher) -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    folder = _downloads_dir()
    # Baseline existing files so Lyra never announces everything already in Downloads.
    previous = _snapshot(folder)
    partial_names: set[str] = {n for n, (_, _, partial) in previous.items() if partial}
    stable: dict[str, int] = {}
    last_emit: dict[str, float] = {}
    speeds: dict[str, tuple[int, float, float]] = {}  # name -> (size, time, smoothed bytes/s)
    fresh: dict[str, tuple[int, int]] = {}  # newly appeared final files -> (last size, stable passes)
    announced: set[str] = set()
    gone: dict[str, float] = {}  # temp files that disappeared -> when (finished or cancelled?)

    light = _light()

    def run() -> None:
        global LAST_PROGRESS_TS
        nonlocal previous, partial_names, folder
        while not _stop.wait(_INTERVAL if (partial_names or fresh or gone or not light) else _IDLE_INTERVAL):
            # Re-resolve in case the Downloads known folder is created/redirected after Lyra starts.
            resolved = _downloads_dir()
            if resolved != folder:
                folder = resolved
                previous = _snapshot(folder)
                partial_names = {n for n, (_, _, partial) in previous.items() if partial}
                stable.clear()
                last_emit.clear()
                continue
            current = _snapshot(folder)
            # Partial files: announce once, then refresh only when size changes.
            for name, (size, _, is_partial) in current.items():
                if not is_partial:
                    continue
                old = previous.get(name)
                now = time.monotonic()
                # Show immediately, then keep the Island alive while a long download continues.
                # This also catches a download that was already in progress when Lyra started.
                gap = now - last_emit.get(name, 0.0)
                if name not in partial_names or (old and old[0] != size and gap >= _MIN_GAP) or gap >= _HEARTBEAT:
                    # Smoothed speed from the previous emitted sample.
                    prev = speeds.get(name)
                    speed = prev[2] if prev else 0.0
                    if prev and now > prev[1] and size >= prev[0]:
                        inst = (size - prev[0]) / (now - prev[1])
                        speed = inst if not speed else speed * 0.6 + inst * 0.4
                    speeds[name] = (size, now, speed)
                    # Chromium's temp file is called "Unconfirmed N": borrow the real name/total
                    # from the browser history watcher, or fall back to a neutral title.
                    title, total = None, 0
                    if _is_temp_name(name):
                        try:
                            import browser_downloads_watch as _bw
                            hit = _bw.resolve_active(size)
                        except Exception:
                            hit = None
                        title, total = (hit if hit else ("Downloading a file", 0))
                    try:
                        from browser_downloads_watch import _fmt_size, _fmt_eta
                        parts = [f"{_fmt_size(size)} of {_fmt_size(total)}" if total else _fmt_size(size)]
                        if speed > 1024:
                            parts.append(f"{_fmt_size(speed)}/s")
                            if total and total > size:
                                parts.append(_fmt_eta((total - size) / speed))
                        subtitle = " · ".join(parts) if size else "Starting…"
                    except Exception:
                        subtitle = f"Downloading · {size / (1024 * 1024):.1f} MB"
                    _publish(publisher, phase="start", name=name, title=title, subtitle=subtitle,
                             progress=(min(100.0, size * 100 / total) if total else None))
                    LAST_PROGRESS_TS = now
                    last_emit[name] = now
                partial_names.add(name)
                stable[name] = 0

            # A partial disappearing means the browser likely renamed it to the final file.
            for old_name in list(partial_names):
                if old_name in current:
                    continue
                partial_names.discard(old_name)
                last_emit.pop(old_name, None)
                speeds.pop(old_name, None)
                base = _pretty_name(old_name)
                # Find the likely completed file by basename, otherwise wait for a new file.
                candidate = next((n for n in current if n == base or Path(n).stem == Path(base).stem), None)
                if candidate:
                    announced.add(candidate)
                    fresh.pop(candidate, None)
                    _publish(publisher, phase="success", name=candidate, subtitle="Saved to Downloads", progress=100)
                else:
                    # Temp file gone and no matching final file (yet): finished under a new
                    # name, or cancelled. Decide below once we have waited a moment.
                    gone[old_name] = time.monotonic()

            # Cancelled downloads: the temp file vanished and no finished file showed up.
            for g_name, g_at in list(gone.items()):
                if time.monotonic() - g_at < 3.0:
                    continue
                gone.pop(g_name, None)
                _publish(publisher, phase="fail", name=g_name, subtitle="Download canceled")

            # New finished files (a renamed temp file, or a direct download with no partial).
            # The old check compared against `previous`, which already contained the file on
            # the next pass, so completion was never reported. Track fresh files separately and
            # announce once their size has stopped changing.
            for name, (size, _, is_partial) in current.items():
                if not is_partial and name not in previous and name not in fresh and name not in announced:
                    fresh[name] = (size, 0)
            for name in list(fresh):
                if name not in current:
                    fresh.pop(name, None)
                    continue
                last_size, count = fresh[name]
                size = current[name][0]
                count = count + 1 if size == last_size else 0
                fresh[name] = (size, count)
                if count >= 2:
                    fresh.pop(name, None)
                    announced.add(name)
                    gone.clear()
                    try:
                        from browser_downloads_watch import _fmt_size
                        sub_txt = "Saved to Downloads" + (f" · {_fmt_size(size)}" if size else "")
                    except Exception:
                        sub_txt = "Download complete"
                    _publish(publisher, phase="success", name=name, subtitle=sub_txt, progress=100)
            if len(announced) > 200:
                announced.clear()

            previous = current

    _thread = threading.Thread(target=run, name="lyra-downloads-watch", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
