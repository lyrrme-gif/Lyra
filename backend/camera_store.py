"""Camera tab storage: the UI takes the picture / records the video in the page (getUserMedia +
canvas + MediaRecorder) and POSTs the finished bytes here.

  Photos -> Pictures\\Lyra Camera
  Videos -> Videos\\Lyra Camera

Files are streamed to a ".part" file first and renamed only when the whole body arrived, so an
interrupted upload never leaves a half file that looks like a real capture.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import BinaryIO

log = logging.getLogger("lyra.camera")

PHOTO_EXTS = ("png", "jpg", "jpeg", "webp")
VIDEO_EXTS = ("mp4", "webm")
MAX_PHOTO = 60 * 1024 * 1024          # 60 MB
MAX_VIDEO = 2 * 1024 * 1024 * 1024    # 2 GB
_CHUNK = 1 << 20

_lock = threading.Lock()
_last: str = ""                       # last file saved (for "show in folder")


class CameraSaveError(Exception):
    pass


def folder_for(kind: str) -> Path:
    home = Path.home()
    base = home / ("Pictures" if kind == "photo" else "Videos")
    return base / "Lyra Camera"


def _ext(kind: str, ext: str) -> str:
    e = re.sub(r"[^a-z0-9]", "", (ext or "").lower())
    allowed = PHOTO_EXTS if kind == "photo" else VIDEO_EXTS
    if e not in allowed:
        return "png" if kind == "photo" else "webm"
    return "jpg" if e == "jpeg" else e


def _unique(folder: Path, stem: str, ext: str) -> Path:
    p = folder / f"{stem}.{ext}"
    n = 2
    while p.exists():
        p = folder / f"{stem} ({n}).{ext}"
        n += 1
    return p


def save_stream(kind: str, ext: str, rfile: BinaryIO, length: int | None) -> dict:
    """Read exactly `length` bytes of the request body into a new file and return {ok, path, name, folder, kind}."""
    global _last
    kind = "photo" if kind == "photo" else "video"
    if length is None or length <= 0:
        raise CameraSaveError("Nothing was received from the camera.")
    limit = MAX_PHOTO if kind == "photo" else MAX_VIDEO
    if length > limit:
        raise CameraSaveError("That file is too large to save.")
    folder = folder_for(kind)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise CameraSaveError(f"Lyra can't save into {folder} ({e.strerror or e}).") from e

    stamp = time.strftime("%Y-%m-%d %H.%M.%S")
    label = "Lyra Photo" if kind == "photo" else "Lyra Video"
    with _lock:
        dest = _unique(folder, f"{label} {stamp}", _ext(kind, ext))
        part = dest.with_name(dest.name + ".part")
        remaining = length
        try:
            with open(part, "wb") as out:
                while remaining > 0:
                    chunk = rfile.read(min(_CHUNK, remaining))
                    if not chunk:
                        raise CameraSaveError("The upload was interrupted. Nothing was saved.")
                    out.write(chunk)
                    remaining -= len(chunk)
            os.replace(part, dest)
        except CameraSaveError:
            _silent_unlink(part)
            raise
        except OSError as e:
            _silent_unlink(part)
            raise CameraSaveError(f"Couldn't write the file ({e.strerror or e}).") from e
        _last = str(dest)
    log.info("camera %s saved: %s (%d bytes)", kind, dest, length)
    return {"ok": True, "kind": kind, "path": str(dest), "name": dest.name, "folder": str(folder)}


def _silent_unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def reveal_last() -> dict:
    """Show the last capture selected in Explorer (or its folder when it is gone)."""
    try:
        if _last and os.path.exists(_last):
            subprocess.Popen(["explorer", "/select,", _last])
            return {"ok": True}
        for kind in ("photo", "video"):
            f = folder_for(kind)
            if f.exists():
                os.startfile(str(f))  # type: ignore[attr-defined]
                return {"ok": True}
        return {"ok": False, "error": "Nothing has been saved yet."}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Couldn't open it: {str(e)[:100]}"}


def open_privacy_settings() -> dict:
    """Open Windows Settings -> Privacy -> Camera (where a blocked camera is switched back on)."""
    try:
        os.startfile("ms-settings:privacy-webcam")  # type: ignore[attr-defined]
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Couldn't open Settings: {str(e)[:100]}"}
