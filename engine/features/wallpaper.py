"""Lyra — Wallpaper tab on the island.

Shows the wallpapers you used recently (Windows keeps the last five) or the pictures in a
folder you pick; one tap sets it. Voice: "change my wallpaper", "open the wallpapers".

  snapshot(source)   -> {"items": [{"id", "name", "thumb", "current"}], ...}   source: recent | folder
  set_wallpaper(id)  -> bool   (only pictures the island was just shown can be set)
  choose_folder()    -> folder picker, remembered
  handle_command(t)  -> voice

Windows only, no extra installs beyond Pillow (already used by Lyra).
"""

from __future__ import annotations

import base64
import ctypes
import io
import json
import random
import re
import subprocess
import sys
import threading
from pathlib import Path

from engine.bridge import ui

log = ui.log

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}      # what Windows can set as a wallpaper
THUMB_SIZE = (256, 144)
MAX_ITEMS = 12
_NO_WINDOW = 0x08000000

_lock = threading.Lock()
_allowed: set[str] = set()                           # pictures the island was shown: the only ones it may set
_thumbs: dict[tuple[str, float], str] = {}


# ── settings ─────────────────────────────────────────────────────────────────────

def _cfg_file() -> Path:
    from engine.core import paths
    return paths.data_path("wallpaper.json")


def _load_cfg() -> dict:
    try:
        d = json.loads(_cfg_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_cfg(d: dict) -> None:
    try:
        _cfg_file().write_text(json.dumps(d), encoding="utf-8")
    except Exception as e:
        log.warning("wallpaper settings: %s", e)


def get_folder() -> str:
    f = str(_load_cfg().get("folder") or "")
    if f and Path(f).is_dir():
        return f
    pics = Path.home() / "Pictures"
    for cand in (pics / "Wallpapers", pics / "Wallpaper", pics):
        if cand.is_dir():
            return str(cand)
    return str(pics)


def set_folder(path: str) -> bool:
    p = Path(path or "")
    if not p.is_dir():
        return False
    cfg = _load_cfg()
    cfg["folder"] = str(p)
    _save_cfg(cfg)
    return True


# ── what's on the desktop now / recently ─────────────────────────────────────────

def current() -> str:
    try:
        buf = ctypes.create_unicode_buffer(520)
        ctypes.windll.user32.SystemParametersInfoW(0x0073, len(buf), buf, 0)   # SPI_GETDESKWALLPAPER
        return buf.value
    except Exception:
        return ""


def recent_paths() -> list[str]:
    """The wallpapers Windows remembers (Settings > Personalization > Background)."""
    out: list[str] = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Wallpapers") as k:
            for i in range(10):
                try:
                    v = winreg.QueryValueEx(k, f"BackgroundHistoryPath{i}")[0]
                except OSError:
                    break
                if v and v not in out and Path(v).is_file():
                    out.append(v)
    except Exception:
        pass
    cur = current()
    if cur and Path(cur).is_file() and Path(cur).suffix.lower() in IMAGE_EXTS and cur not in out:
        out.insert(0, cur)
    return out


def folder_images(folder: str, limit: int = MAX_ITEMS) -> list[str]:
    try:
        files = [p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [str(p) for p in files[:limit]]
    except Exception:
        return []


def thumb(path: str) -> str:
    """A small JPEG as a data: URI (cached), '' if the picture can't be read."""
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        return ""
    key = (path, mtime)
    with _lock:
        if key in _thumbs:
            return _thumbs[key]
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            try:
                im.draft("RGB", (THUMB_SIZE[0] * 2, THUMB_SIZE[1] * 2))   # fast path for big JPEGs
            except Exception:
                pass
            im = ImageOps.exif_transpose(im).convert("RGB")
            im = ImageOps.fit(im, THUMB_SIZE, method=Image.BILINEAR)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=72)
        uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        log.warning("wallpaper thumbnail %s: %s", path, e)
        return ""
    with _lock:
        if len(_thumbs) > 80:
            _thumbs.clear()
        _thumbs[key] = uri
    return uri


def _same(a: str, b: str) -> bool:
    try:
        return bool(a) and bool(b) and Path(a).resolve() == Path(b).resolve()
    except Exception:
        return a == b


def snapshot(source: str = "recent") -> dict:
    source = "folder" if source == "folder" else "recent"
    folder = get_folder()
    paths_ = folder_images(folder) if source == "folder" else recent_paths()
    cur = current()
    items = []
    for p in paths_:
        t = thumb(p)
        if not t:
            continue
        items.append({"id": p, "name": Path(p).stem, "thumb": t, "current": _same(p, cur)})
    with _lock:
        _allowed.update(i["id"] for i in items)
    return {"ok": True, "source": source, "folder": folder, "folder_name": Path(folder).name or folder,
            "items": items}


# ── changing it ──────────────────────────────────────────────────────────────────

def set_wallpaper(path: str) -> bool:
    with _lock:
        allowed = path in _allowed
    if not allowed or not Path(path).is_file() or Path(path).suffix.lower() not in IMAGE_EXTS:
        return False
    try:
        # SPI_SETDESKWALLPAPER, remember it + tell every window (SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)
        return bool(ctypes.windll.user32.SystemParametersInfoW(0x0014, 0, path, 0x03))
    except Exception as e:
        log.warning("set wallpaper: %s", e)
        return False


def choose_folder() -> str:
    """Windows folder picker. Returns the chosen folder ('' if cancelled) and remembers it."""
    start = get_folder().replace("'", "''")
    script = ("Add-Type -AssemblyName System.Windows.Forms; "
              "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
              "$d.Description = 'Choose your wallpaper folder'; "
              f"$d.SelectedPath = '{start}'; "
              "if ($d.ShowDialog() -eq 'OK') { Write-Output $d.SelectedPath }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script], capture_output=True,
                           text=True, timeout=300,
                           creationflags=_NO_WINDOW if sys.platform == "win32" else 0)
        chosen = (r.stdout or "").strip().splitlines()[-1].strip() if (r.stdout or "").strip() else ""
    except Exception as e:
        log.warning("folder picker: %s", e)
        return ""
    return chosen if chosen and set_folder(chosen) else ""


def next_random() -> str | None:
    """Switch to another picture from the folder. Returns its name, or None."""
    pics = folder_images(get_folder(), limit=60)
    cur = current()
    pool = [p for p in pics if not _same(p, cur)] or pics
    if not pool:
        return None
    pick = random.choice(pool)
    with _lock:
        _allowed.add(pick)
    return Path(pick).stem if set_wallpaper(pick) else None


# ── voice ────────────────────────────────────────────────────────────────────────

_OPEN = re.compile(r"^(?:please\s+)?(?:open|show|bring\s+up|go\s+to)\s+(?:me\s+)?(?:the\s+|my\s+)?"
                   r"wallpapers?(?:\s+tab)?[.!?]*$", re.I)
_CHANGE = re.compile(r"^(?:please\s+)?(?:(?:change|switch|shuffle|randomi[sz]e|swap)\s+(?:my\s+|the\s+)?"
                     r"(?:wallpaper|desktop\s+background|background)|next\s+wallpaper|"
                     r"(?:set|pick)\s+a\s+(?:random|new)\s+wallpaper)[.!?]*$", re.I)


def parse_command(text: str) -> str | None:
    t = (text or "").strip()
    if _OPEN.match(t):
        return "open"
    if _CHANGE.match(t):
        return "change"
    return None


def handle_command(text: str) -> str | None:
    cmd = parse_command(text)
    if not cmd:
        return None
    try:
        from engine.bridge import ui_bridge
        if cmd == "open":
            ui_bridge.push_wallpaper(True)
            return "Here are your wallpapers."
        name = next_random()
        ui_bridge.push_wallpaper(False)
        if name:
            return "Changed your wallpaper."
        return f"I couldn't find any pictures in {Path(get_folder()).name}."
    except Exception as e:
        log.warning("wallpaper command: %s", e)
        return None
