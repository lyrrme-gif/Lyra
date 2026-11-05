"""Lyra — screen recording through OBS Studio (the island's Record tab, "Hey Lyra, start recording", the keyboard shortcuts).

Lyra does not capture the screen itself. It tells OBS to start / pause / resume / stop recording over OBS's built-in
WebSocket server (OBS 28 or newer: Tools -> WebSocket Server Settings -> Enable; see obs_control.py), so a recording has
exactly OBS's capture, encoder and sound settings. Before each recording Lyra points OBS's recording folder at Lyra's own
(Settings -> Recordings -> Save location, default Videos\\Lyra Recordings), so the finished file lands there; if OBS is too
old for that, the file is moved there when the recording stops.

This module also keeps the list of recordings with their thumbnails (made with ffmpeg: `pip install imageio-ffmpeg`).
While nothing is recorded it does nothing: no timers, no threads, no polling.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from engine.features.obs_control import ObsClient, ObsError, ObsRefused

_lock = threading.RLock()
_client: ObsClient | None = None
_last = ""                            # the last finished recording (full path)
_path = ""                            # the file being recorded, when OBS told us (it does so when it stops)
_error = ""
_saving = 0                           # finished recordings that are still being moved into place
_stopping = False
_live = False                         # what OBS last said: a recording is running
_paused = False
_secs = 0
_size = 0
_obs_msg = ""                         # why OBS cannot be reached right now ("" = fine)
_next_try = 0.0                       # do not knock on a closed OBS more than every few seconds
_mux_thread = None
_launching = False                    # Lyra is opening OBS in the background right now (no polling meanwhile)
_launch_lock = threading.Lock()
_OBS_WAIT_S = 25                      # how long to wait for a freshly opened OBS to start its WebSocket server


def _log():
    try:
        from engine.bridge import ui
        return ui.log
    except Exception:  # noqa: BLE001
        return logging.getLogger("lyra.record")


def _ffmpeg() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


_folder_warned = ""


def default_dir() -> Path:
    """Where recordings go until the user picks another folder: Videos\\Lyra Recordings."""
    return Path.home() / "Videos" / "Lyra Recordings"


def _custom_dir() -> Path | None:
    """The folder chosen in Settings -> Recording -> Save location, or None for the default."""
    raw = str(_pref("record_folder", "") or "").strip()
    return Path(os.path.expandvars(os.path.expanduser(raw))) if raw else None


_dir_cache: tuple = ("", 0.0, None)


def out_dir() -> Path:
    """The recordings folder: the saved custom one when it can be used, else the default.

    A custom folder that has gone away (an unplugged drive, a deleted folder that cannot be made again)
    never stops a recording: Lyra records to the default folder and says so in Settings.

    The answer is kept for 5 seconds (and dropped when the folder setting changes): status() and the recordings
    list ask for it all the time, and each fresh answer costs a mkdir and a write-access check on the disk."""
    global _folder_warned, _dir_cache
    c = _custom_dir()
    key = str(c) if c is not None else ""
    now = time.monotonic()
    if _dir_cache[2] is not None and _dir_cache[0] == key and now < _dir_cache[1]:
        return _dir_cache[2]
    found = None
    if c is not None:
        try:
            c.mkdir(parents=True, exist_ok=True)
            if c.is_dir() and os.access(c, os.W_OK):
                found = c
        except OSError:
            pass
        if found is None and _folder_warned != str(c):
            _folder_warned = str(c)
            _log().warning("recordings folder %s is not available - saving to %s instead", c, default_dir())
    if found is None:
        found = default_dir()
        found.mkdir(parents=True, exist_ok=True)
    _dir_cache = (key, now + 5.0, found)
    return found


def _pref(key: str, default):
    try:
        from engine.core import settings_store
        v = settings_store.get(key)
        return default if v is None else v
    except Exception:  # noqa: BLE001
        return default


def folder_info() -> dict:
    """{folder (in use now), default, custom (a folder was chosen), wanted (the chosen one), fallback (it cannot be used right now)}."""
    c = _custom_dir()
    cur = out_dir()
    return {"ok": True, "folder": str(cur), "default": str(default_dir()), "custom": c is not None,
            "wanted": str(c) if c else "", "fallback": c is not None and cur != c}


def _busy() -> bool:
    return _recording_dir_busy()


def set_folder(path: str) -> dict:
    """Save a new recordings folder ("" = back to the default). Takes effect on the next recording.

    Refused while a recording is running or still being saved (its files are in the old folder), and when the
    folder cannot be created or written to. Recordings already made stay where they are."""
    raw = str(path or "").strip().strip('"')
    if raw:
        p = Path(os.path.expandvars(os.path.expanduser(raw)))
        if not p.is_absolute():
            return {"ok": False, "error": "Type or choose a full folder path, like D:\\Videos\\Lyra."}
        p = Path(os.path.normpath(str(p)))
    if _busy():
        return {"ok": False, "error": "A recording is running or still being saved. Stop it first, then change the folder."}
    if raw:
        if p.exists() and not p.is_dir():
            return {"ok": False, "error": "That path is a file, not a folder."}
        try:
            p.mkdir(parents=True, exist_ok=True)
            probe = p / f".lyra-write-test-{os.getpid()}"
            probe.write_bytes(b"ok")
            probe.unlink()
        except OSError as e:
            return {"ok": False, "error": f"Lyra can't save into that folder ({e.strerror or e})."}
        raw = "" if str(p).rstrip("\\/").lower() == str(default_dir()).rstrip("\\/").lower() else str(p)
    try:
        from engine.core import settings_store
        settings_store.set_many({"record_folder": raw})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Couldn't save the choice: {e}"}
    global _folder_warned, _dir_cache
    _folder_warned = ""
    _dir_cache = ("", 0.0, None)                           # the folder answer is worked out again
    return folder_info()


def pick_folder() -> dict:
    """Ask Windows for a folder (the same dialog the Files tab uses). {ok, path} - path is "" when the dialog was cancelled."""
    start = str(out_dir()).replace("'", "''")
    # The hidden top-most owner window makes Windows bring the folder window to the front (a window opened
    # from a background process can otherwise appear behind the app, as if nothing happened).
    script = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
              "Add-Type -AssemblyName System.Windows.Forms; "
              "[System.Windows.Forms.Application]::EnableVisualStyles(); "
              "$o = New-Object System.Windows.Forms.Form; $o.TopMost = $true; $o.ShowInTaskbar = $false; "
              "$o.StartPosition = 'CenterScreen'; $o.Opacity = 0; $o.Show(); $o.Activate(); "
              "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
              "$d.Description = 'Choose where Lyra saves screen recordings'; "
              f"$d.SelectedPath = '{start}'; $d.ShowNewFolderButton = $true; "
              "$ok = $d.ShowDialog($o); $o.Close(); "
              "if ($ok -eq 'OK') { Write-Output $d.SelectedPath }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script], capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=300, creationflags=0x08000000 if sys.platform == "win32" else 0)
        lines = (r.stdout or "").strip().splitlines()
        return {"ok": True, "path": lines[-1].strip() if lines else ""}
    except Exception as e:  # noqa: BLE001
        _log().warning("recordings folder picker: %s", e)
        return {"ok": False, "path": "", "error": "Couldn't open the folder window."}


_EXTS = (".mp4", ".mkv")

THUMB_W = 480                                                          # width of a recording's preview picture
_DUR = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")        # ffmpeg's banner: length, picture size, frame rate
_DIM = re.compile(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})")
_FPS = re.compile(r"(\d+(?:\.\d+)?)\s*fps")


def _resolve(name: str) -> Path | None:
    """A recording by file name: only an .mp4 / .mkv directly inside the recordings folder (no paths, no '..')."""
    n = str(name or "")
    if not n or n != os.path.basename(n) or not n.lower().endswith(_EXTS):
        return None
    p = out_dir() / n
    return p if p.is_file() else None


def _thumb_dir() -> Path:
    d = out_dir() / ".thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _thumb_path(p: Path) -> Path:
    return _thumb_dir() / (p.name + ".jpg")


def _low() -> int:
    """Process flags for helper ffmpeg runs (probes, previews): no window, and low priority."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


def _probe(exe: str, p: Path) -> dict:
    """Length and picture size of a video, read from ffmpeg's own banner (no ffprobe needed)."""
    out: dict = {}
    try:
        r = subprocess.run([exe, "-hide_banner", "-i", str(p)], capture_output=True, timeout=10, creationflags=_low())
        text = (r.stderr or b"").decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return out
    m = _DUR.search(text)
    if m:
        out["duration"] = round(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)), 1)
    m = _DIM.search(text)
    if m:
        out["width"], out["height"] = int(m.group(1)), int(m.group(2))
    m = _FPS.search(text)
    if m:
        out["fps"] = round(float(m.group(1)))
    return out


_meta_mem: dict = {}


def _meta(p: Path, make: bool = True) -> dict:
    """{duration, width, height, fps, thumb} for a finished recording. Made once, then read from the cache (in memory,
    then the .json next to the preview). `thumb` is False when it could not be made (no ffmpeg, damaged file): the list
    shows a plain tile. make=False only reads what is cached and never starts ffmpeg (used while recording)."""
    try:
        st = p.stat()
        side = _thumb_dir() / (p.name + ".json")
        jpg = _thumb_path(p)
        stamp = [st.st_size, int(st.st_mtime)]
        hit = _meta_mem.get(str(p))
        if hit and hit[0] == stamp and (not hit[1].get("thumb") or jpg.is_file()):
            return dict(hit[1])
        if side.is_file() and jpg.is_file():
            try:
                cached = json.loads(side.read_text("utf-8"))
                if cached.get("stamp") == stamp:
                    info = {**{k: v for k, v in cached.items() if k != "stamp"}, "thumb": True}
                    _meta_mem[str(p)] = (stamp, info)
                    return dict(info)
            except (OSError, ValueError):
                pass
        if not make:
            return {"thumb": False, "pending": True}
        exe = _ffmpeg()
        if not exe or st.st_size == 0:
            return {"thumb": False}
        info = _probe(exe, p)
        at = 1.0 if info.get("duration", 0) > 2 else 0.0
        r = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(at), "-i", str(p),
                            "-frames:v", "1", "-vf", f"scale={THUMB_W}:-2", "-q:v", "4", str(jpg)],
                           capture_output=True, timeout=20, creationflags=_low())
        if r.returncode != 0 or not jpg.is_file():
            return {**info, "thumb": False}
        side.write_text(json.dumps({**info, "stamp": stamp}), "utf-8")
        _meta_mem[str(p)] = (stamp, {**info, "thumb": True})
        return {**info, "thumb": True}
    except Exception:  # noqa: BLE001
        return {"thumb": False}


def thumbnail(name: str) -> bytes | None:
    """The JPEG preview of a recording (made on demand), or None."""
    p = _resolve(name)
    if p is None:
        return None
    with _lock:
        if _active() and os.path.basename(_path) == p.name:
            return None
    if not _meta(p, make=not _recording_dir_busy()).get("thumb"):
        return None
    try:
        return _thumb_path(p).read_bytes()
    except OSError:
        return None


def delete_recording(name: str) -> dict:
    p = _resolve(name)
    if p is None:
        return {"ok": False, "error": "That recording no longer exists."}
    with _lock:
        if _active() and os.path.basename(_path) == p.name:
            return {"ok": False, "error": "Stop the recording before deleting it."}
    try:
        p.unlink()
    except OSError as e:
        return {"ok": False, "error": f"Couldn't delete it: {str(e)[:100]}"}
    _meta_mem.pop(str(p), None)
    for extra in (_thumb_path(p), _thumb_dir() / (p.name + ".json")):
        try:
            extra.unlink()
        except OSError:
            pass
    return {"ok": True}


def open_recording(name: str, reveal: bool = False) -> dict:
    """Play it in the default video app, or (reveal) show it selected in Explorer."""
    p = _resolve(name)
    if p is None:
        return {"ok": False, "error": "That recording no longer exists."}
    try:
        if reveal:
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            os.startfile(str(p))   # type: ignore[attr-defined]
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Couldn't open it: {str(e)[:100]}"}


_PREFIX = r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:please\s+|can\s+you\s+|could\s+you\s+)?"


_START = re.compile(_PREFIX + r"(?:start|begin)\s+(?:a\s+|the\s+|my\s+)?(?:screen\s+)?recording(?:\s+(?:of\s+)?(?:my\s+)?(?:screen|desktop))?\s*[.!?]*$"
                    r"|" + _PREFIX + r"(?:record|capture)\s+(?:my\s+|the\s+)?(?:screen|desktop)\s*[.!?]*$", re.I)


_STOP = re.compile(_PREFIX + r"(?:stop|end|finish)\s+(?:the\s+|my\s+)?(?:screen\s+)?(?:recording|capture)\s*[.!?]*$", re.I)


_PAUSE = re.compile(_PREFIX + r"pause\s+(?:the\s+|my\s+)?(?:screen\s+)?(?:recording|capture)\s*[.!?]*$", re.I)


_RESUME = re.compile(_PREFIX + r"(?:resume|continue|unpause)\s+(?:the\s+|my\s+)?(?:screen\s+)?(?:recording|capture)\s*[.!?]*$", re.I)


def try_command(command: str) -> str | None:
    """Voice / typed commands. None = not about recording."""
    text = (command or "").strip()
    if not text:
        return None
    if _STOP.match(text):
        s = status()
        if not s["recording"]:
            return "I'm not recording right now."
        s = stop()
        if s["saving"]:
            return "Stopped. I'm still saving it, it will be in your Videos folder in a moment."
        if s["saved"]:
            return s["error"] if s["error"].startswith("Saved") else "Recording saved to your Videos folder."
        return s["error"] or "I stopped, but there is no file."
    if _PAUSE.match(text):
        if not status()["recording"]:
            return "I'm not recording right now."
        s = pause()
        return "Recording paused. Say resume recording to carry on." if s["paused"] else (s["error"] or "I couldn't pause it.")
    if _RESUME.match(text):
        s = status()
        if not s["recording"]:
            return "I'm not recording right now."
        if not s["paused"]:
            return "The recording isn't paused."
        s = resume()
        return "Recording again." if s["recording"] and not s["paused"] else (s["error"] or "I couldn't resume it.")
    if _START.match(text):
        try:
            from engine.bridge import ui_bridge
            ui_bridge.push_open_view("record")
        except Exception:  # noqa: BLE001
            pass
        s = start()
        if s["error"]:
            return s["error"]
        if s["audio"]:
            return f"Recording your screen with {s['audio']}. Say stop recording when you're done."
        if s["audio_warning"]:
            return f"Recording your screen, but without sound: {s['audio_warning']}. Say stop recording when you're done."
        return "Recording your screen. Say stop recording when you're done."
    return None



# ── Which recorder: OBS (default) or the built-in one (wgc_recorder.py, experimental) ──────────────────

def _builtin() -> bool:
    return str(_pref("record_backend", "obs") or "obs") == "lyra"


def _wgc():
    from engine.features import wgc_recorder
    return wgc_recorder


# ── Talking to OBS ────────────────────────────────────────────────────────────────────────────────────

def _obs() -> ObsClient:
    host, port, pw = str(_pref("obs_host", "127.0.0.1") or "127.0.0.1"), int(_pref("obs_port", 4455) or 4455), str(_pref("obs_password", "") or "")
    global _client
    with _lock:
        if _client is None or not _client.same_as(host, port, pw):
            if _client is not None:
                _client.close()
            _client = ObsClient(host, port, pw)
        return _client


def _ask(kind: str, data: dict | None = None) -> dict:
    return _obs().request(kind, data)


def obs_test() -> dict:
    """Settings -> Recordings -> "Test connection": is OBS reachable with these settings?"""
    global _obs_msg, _next_try
    try:
        v = _ask("GetVersion")
        _obs_msg, _next_try = "", 0.0
        return {"ok": True, "message": f"Connected to OBS {v.get('obsVersion', '?')} (WebSocket {v.get('obsWebSocketVersion', '?')})."}
    except ObsError as e:
        _obs_msg = str(e)
        return {"ok": False, "message": str(e)[:200].rstrip(".") + "."}


def _is_unreachable(e: Exception) -> bool:
    """True when OBS did not answer at all (closed, or its WebSocket is off) - as opposed to a wrong password etc."""
    return str(e).startswith("OBS isn't running")


# ── Opening OBS by itself, hidden in the tray ────────────────────────────────

def _exe_candidates() -> list[Path]:
    """Where obs64.exe may be: the path in Settings, the installer's registry entry, then the usual folders."""
    out: list[Path] = []

    def add_dir(d: str | Path) -> None:
        d = Path(str(d))
        out.append(d / "bin" / "64bit" / "obs64.exe")

    custom = str(_pref("obs_path", "") or "").strip().strip('"')
    if custom:
        p = Path(custom)
        out.append(p) if p.suffix.lower() == ".exe" else add_dir(p)
    if os.name == "nt":
        try:
            import winreg
            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                    try:
                        with winreg.OpenKey(root, r"SOFTWARE\OBS Studio", 0, winreg.KEY_READ | view) as k:
                            add_dir(winreg.QueryValueEx(k, "")[0])
                    except OSError:
                        continue
        except Exception:  # noqa: BLE001
            pass
        for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            base = os.environ.get(env)
            if base:
                add_dir(Path(base) / "obs-studio")
                add_dir(Path(base) / "Steam" / "steamapps" / "common" / "OBS Studio")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            add_dir(Path(local) / "Programs" / "obs-studio")
    return out


def _obs_exe() -> Path | None:
    for p in _exe_candidates():
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def _obs_running() -> bool:
    """Is an OBS process already there (even if its WebSocket does not answer)? Opening a second one would only
    make OBS ask 'already running'."""
    try:
        import psutil  # type: ignore
        return any((p.info.get("name") or "").lower() in ("obs64.exe", "obs.exe", "obs") for p in psutil.process_iter(["name"]))
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        return False
    if os.name != "nt":
        return False
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq obs64.exe", "/NH"], capture_output=True, text=True, timeout=5,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "obs64.exe" in (r.stdout or "").lower()
    except Exception:  # noqa: BLE001
        return False


def _launch_obs(exe: Path):
    """Start OBS minimized to the system tray, detached from Lyra (it keeps running when Lyra closes)."""
    kw: dict = {"cwd": str(exe.parent), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        si = subprocess.STARTUPINFO()                                     # type: ignore[attr-defined]
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW                      # type: ignore[attr-defined]
        si.wShowWindow = 7                                                 # SW_SHOWMINNOACTIVE: no flash, no focus stolen
        kw["startupinfo"] = si
        kw["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    # --minimize-to-tray: no window, just the tray icon. --disable-shutdown-check: no "OBS crashed, safe mode?" box.
    return subprocess.Popen([str(exe), "--minimize-to-tray", "--disable-shutdown-check"], **kw)


def _hide_obs_windows(pid: int) -> int:
    """Fallback if OBS still shows its main window: hide it (OBS keeps running; its tray icon opens it again).
    Returns how many windows were hidden."""
    if os.name != "nt":
        return 0
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32                                      # type: ignore[attr-defined]
        hidden = [0]

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _lp):
            if not user32.IsWindowVisible(hwnd):
                return True
            owner = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value != pid:
                return True
            buf = ctypes.create_unicode_buffer(300)
            user32.GetWindowTextW(hwnd, buf, 300)
            if buf.value.startswith("OBS ") and "Profile:" in buf.value:     # the main window, not a dialog
                user32.ShowWindow(hwnd, 0)                                   # SW_HIDE
                hidden[0] += 1
            return True

        user32.EnumWindows(cb, 0)
        return hidden[0]
    except Exception:  # noqa: BLE001
        return 0


def _ensure_obs() -> str | None:
    """OBS is not answering and a recording was asked for. If allowed, open OBS in the background (tray only) and wait
    until its WebSocket answers. Returns "" when OBS is ready, None when this does not apply (the original error is
    shown), or a message to show instead."""
    global _launching, _obs_msg, _next_try
    if not _pref("obs_autostart", True):
        return None
    if str(_pref("obs_host", "127.0.0.1") or "127.0.0.1").strip().lower() not in ("127.0.0.1", "localhost", "::1"):
        return None                                                        # OBS on another PC: nothing to open here
    with _launch_lock:                                                     # a second request waits for the first launch
        try:
            _ask("GetVersion")
            return ""                                                      # the other request already opened it
        except ObsError as e:
            if not _is_unreachable(e):
                return None
        if _obs_running():
            return ("OBS is open but its WebSocket server isn't answering. "
                    "In OBS: Tools → WebSocket Server Settings → Enable.")
        exe = _obs_exe()
        if exe is None:
            _log().info("OBS is closed and Lyra could not find obs64.exe to open it (Settings -> Recordings -> OBS location)")
            return None                                                    # the usual "OBS isn't running ..." message is shown
        _launching = True
        _obs_msg = "Opening OBS in the background…"
        try:
            try:
                proc = _launch_obs(exe)
            except OSError as e:
                return f"I couldn't open OBS: {str(e)[:100]}"
            deadline, hide_at = time.monotonic() + _OBS_WAIT_S, time.monotonic() + 4
            while True:
                try:
                    _ask("GetVersion")
                    break
                except ObsError as e:
                    if not _is_unreachable(e):
                        return str(e)                                      # e.g. wrong password: no point waiting
                if proc.poll() is not None:
                    return "OBS closed right after opening."
                if time.monotonic() >= deadline:
                    return "OBS is taking too long to open. Try again in a moment."
                if time.monotonic() >= hide_at:                            # still showing a window? send it to the tray
                    _hide_obs_windows(proc.pid)
                    hide_at += 1.0
                time.sleep(0.5)
            for _ in range(6):                                             # the window may show up just after the WebSocket
                if _hide_obs_windows(proc.pid) == 0:
                    break
                time.sleep(0.4)
            _log().info("OBS opened in the background (tray): %s", exe)
            return ""
        finally:
            _launching = False
            _obs_msg, _next_try = "", 0.0


def _active() -> bool:
    return _live


def _recording_dir_busy() -> bool:
    return _live or _saving > 0 or _stopping


def _push() -> None:
    """Tell the island (it shows a red pill while recording). Never lets a UI problem break recording."""
    try:
        from engine.bridge import ui_bridge
        with _lock:
            rec, paused, secs = _live, _paused, _secs
        began = int((time.time() - secs) * 1000)               # the pill shows now - began = recorded time (OBS does not count a pause)
        if paused:
            ui_bridge.push_record(True, began, paused=True, elapsed_ms=int(secs * 1000))
        else:
            ui_bridge.push_record(rec, began if rec else 0)
    except Exception:  # noqa: BLE001
        pass


def _refresh() -> None:
    """Ask OBS what it is doing (cheap: one small message on a connection that stays open) and keep the island's pill in step,
    also when the recording was started or stopped in OBS itself."""
    global _live, _paused, _secs, _size, _obs_msg, _next_try, _error
    if _launching or time.monotonic() < _next_try:
        return
    try:
        r = _ask("GetRecordStatus")
        now_live, now_paused = bool(r.get("outputActive")), bool(r.get("outputPaused"))
        with _lock:
            changed = (now_live != _live) or (now_paused != _paused)
            _live, _paused = now_live, now_paused and now_live
            _secs = int((r.get("outputDuration") or 0) / 1000) if now_live else 0
            _size = int(r.get("outputBytes") or 0) if now_live else 0
            _obs_msg = ""
        if changed:
            _push()
    except ObsError as e:
        with _lock:
            was = _live
            _live = _paused = False
            _secs = _size = 0
            _obs_msg = str(e)
            _next_try = time.monotonic() + 3.0
            if was:
                _error = "Lost the connection to OBS while recording. OBS may still be recording."
        if was:
            _push()


def _status() -> dict:
    return {
        "ok": True, "recording": _live, "paused": _live and _paused, "stopping": _stopping,
        "seconds": _secs if _live else 0, "path": _path if _live else _last,
        "saved": (not _live) and bool(_last) and os.path.exists(_last),
        "error": _error, "folder": str(out_dir()), "capture": "OBS", "audio": "", "audio_warning": "",
        "saving": _saving > 0 or _stopping, "size": _size if _live else 0, "notice": "" if _live else _obs_msg,
        "health": {}, "obs_ok": not _obs_msg, "obs_message": _obs_msg,
    }


def status() -> dict:
    """{recording, paused, stopping, seconds, path, saved, error, folder, capture, audio, saving, size, notice, health, obs_ok,
    obs_message}. Asks OBS (at most every few seconds when OBS is not there)."""
    if _builtin():
        return _wgc().status()
    _refresh()
    with _lock:
        return _status()


# Recording quality (Settings -> Recordings -> Quality): picture height and frame rate. "obs" = do not touch OBS's settings.
QUALITY = {"high": (1080, 60), "medium": (720, 30), "low": (480, 30)}


def _apply_quality() -> None:
    """Before a recording starts: set OBS's output (scaled) size and frame rate to the chosen preset.

    The canvas (what OBS captures) is left alone; only the size of the recorded picture and the fps change, and the
    picture keeps the canvas's shape. It never makes a picture bigger than the canvas. OBS refuses to change video
    settings while something else is using them (streaming, virtual camera ...): then the recording simply starts with
    OBS's current settings. A quality change takes effect in OBS itself too (it is OBS's global video setting)."""
    q = str(_pref("obs_quality", "obs") or "obs").lower()
    if q not in QUALITY:
        return
    height, fps = QUALITY[q]
    try:
        v = _ask("GetVideoSettings")
        bw, bh = int(v.get("baseWidth") or 0), int(v.get("baseHeight") or 0)
        if bw <= 0 or bh <= 0:
            return
        oh = min(height, bh)
        ow = round(oh * bw / bh)
        ow, oh = ow - ow % 2, oh - oh % 2                                       # encoders want even sizes
        have = (v.get("outputWidth"), v.get("outputHeight"), v.get("fpsNumerator"), v.get("fpsDenominator"))
        if have == (ow, oh, fps, 1):
            return
        _ask("SetVideoSettings", {"outputWidth": ow, "outputHeight": oh, "fpsNumerator": fps, "fpsDenominator": 1})
        _log().info("recording quality %s: %dx%d at %d fps", q, ow, oh, fps)
    except ObsRefused as e:
        _log().warning("OBS would not change its video settings (%s): recording with OBS's current settings", e)


def start() -> dict:
    global _error, _live, _paused, _secs, _size
    if _builtin():
        return _wgc().start()
    with _lock:
        _error = ""
    try:
        try:
            rec = _ask("GetRecordStatus")
        except ObsError as e:
            if not _is_unreachable(e):
                raise
            opened = _ensure_obs()                                              # OBS closed: open it in the tray, then carry on
            if opened is None:
                raise
            if opened:
                raise ObsError(opened) from e
            rec = _ask("GetRecordStatus")
        if rec.get("outputActive"):
            return status()
        try:
            _ask("SetRecordDirectory", {"recordDirectory": str(out_dir())})      # OBS 30+: the file is made right in Lyra's folder
        except ObsRefused as e:
            _log().info("OBS would not change its recording folder (%s): the file is moved when the recording stops", e)
        _apply_quality()
        _ask("StartRecord")
        for _ in range(20):                                                     # StartRecord answers before the output is up
            if _ask("GetRecordStatus").get("outputActive"):
                break
            time.sleep(0.1)
    except ObsError as e:
        with _lock:
            _error = f"Couldn't start recording: {str(e)[:160]}"
            global _obs_msg
            _obs_msg = str(e)
        return status()
    with _lock:
        _live, _paused, _secs, _size = True, False, 0, 0
    _push()
    return status()


def pause() -> dict:
    return _wgc().pause() if _builtin() else _do("PauseRecord", paused=True)


def resume() -> dict:
    return _wgc().resume() if _builtin() else _do("ResumeRecord", paused=False)


def _do(kind: str, paused: bool) -> dict:
    global _error, _paused
    _refresh()
    if not _live or _paused == paused:
        with _lock:
            return _status()
    try:
        _ask(kind)
    except ObsError as e:
        with _lock:
            _error = f"{'Pause' if paused else 'Resume'} failed: {str(e)[:160]}"
        return status()
    with _lock:
        _paused = paused
    _push()
    return status()


def toggle() -> dict:
    """Start, or stop when recording (the keyboard shortcut)."""
    if not _builtin():
        _refresh()
    return stop() if _live else start()


def toggle_pause() -> dict:
    """Pause, or resume when paused (the keyboard shortcut). Does nothing when not recording."""
    if not _builtin():
        _refresh()
    if _paused:
        return resume()
    return pause() if _live else status()


def stop() -> dict:
    global _live, _paused, _stopping, _error, _saving, _mux_thread
    if _builtin():
        return _wgc().stop()
    _refresh()
    if not _live:
        with _lock:
            return _status()
    with _lock:
        _stopping = True
    try:
        out = _ask("StopRecord").get("outputPath") or ""
    except ObsError as e:
        with _lock:
            _stopping = False
            _error = f"Couldn't stop the recording: {str(e)[:160]}"
        return status()
    with _lock:
        _live, _paused, _stopping = False, False, False
        _saving += 1
        _mux_thread = threading.Thread(target=_finish, args=(out,), name="lyra-record-finish", daemon=True)
        _mux_thread.start()
        th = _mux_thread
    _push()
    th.join(timeout=8)                       # OBS finishes a normal file in a moment; a long one completes in the background (status["saving"])
    with _lock:
        return _status()


def _finish(path: str) -> None:
    """Wait until OBS has finished writing the file, then make sure it is in Lyra's folder."""
    global _last, _saving, _error
    result = ""
    try:
        p = Path(path) if path else None
        if p is not None:
            size, stable = -1, 0
            for _ in range(240):                                  # up to ~2 minutes for a very long recording
                try:
                    cur = p.stat().st_size
                except OSError:
                    cur = -1
                stable = stable + 1 if (cur == size and cur > 0) else 0
                size = cur
                if stable >= 3:
                    break
                time.sleep(0.5)
            if p.is_file():
                dest_dir = out_dir()
                if p.parent.resolve() != dest_dir.resolve():
                    dest = dest_dir / p.name
                    n = 1
                    while dest.exists():
                        dest = dest_dir / f"{p.stem} ({n}){p.suffix}"
                        n += 1
                    shutil.move(str(p), str(dest))
                    p = dest
                result = str(p)
    except Exception as e:  # noqa: BLE001 - the recording is still wherever OBS put it
        with _lock:
            _error = f"The recording is saved by OBS, but Lyra couldn't move it: {str(e)[:120]}"
        result = path or ""
    with _lock:
        if result:
            _last = result
        elif not _error:
            _error = "OBS stopped, but Lyra couldn't find the file. Look in OBS's recording folder."
        _saving = max(0, _saving - 1)
    _push()


def open_folder() -> bool:
    """Show the last recording in Explorer (or the folder when there is none)."""
    try:
        target = _last if _last and os.path.exists(_last) else ""
        if target:
            subprocess.Popen(["explorer", "/select,", target])
        else:
            os.startfile(str(out_dir()))   # type: ignore[attr-defined]
        return True
    except Exception:  # noqa: BLE001
        return False


def list_recordings() -> dict:
    """Newest first: [{name, size, mtime, current, duration, width, height, fps, thumb}] plus the folder.
    No ffmpeg runs for previews while something is being recorded or saved (those previews appear on a later refresh)."""
    items = []
    idle = not _recording_dir_busy()
    try:
        d = out_dir()
        found = []
        for p in [*d.glob("*.mp4"), *d.glob("*.mkv")]:
            try:
                found.append((p, p.stat()))
            except OSError:
                continue
        newest = max((st.st_mtime for _p, st in found), default=0)
        for p, st in found:
            current = _live and st.st_mtime >= newest - 0.01 and time.time() - st.st_mtime < 15
            item = {"name": p.name, "size": st.st_size, "mtime": int(st.st_mtime), "current": current}
            if not current:
                item.update(_meta(p, make=idle))
            items.append(item)
    except OSError:
        pass
    items.sort(key=lambda i: i["mtime"], reverse=True)
    return {"ok": True, "items": items, "folder": str(out_dir()), "total": sum(i["size"] for i in items),
            **{k: v for k, v in folder_info().items() if k != "ok"}}
