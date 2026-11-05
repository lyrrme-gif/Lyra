"""Lyra - the optional offline voice (Kokoro). One click to download, never automatic.

Lyra speaks with Microsoft's Edge voice by default (Settings -> Voice -> Voice engine). The offline voice is
an OPTION: nothing is downloaded or loaded until the user picks it and presses "Download". The files go to
<data dir>\\voices (installed: %APPDATA%\\Lyra\\voices), where voice._voices_dir() looks for them.

    GET  /voice/offline            status: installed, package present, download progress, engine in use
    POST /voice/offline/download   start (or resume) the ~350 MB download
    POST /voice/offline/cancel     stop it (the partial file is kept, the next start resumes)
    POST /voice/offline/delete     remove the downloaded files
"""
from __future__ import annotations

import importlib.util
import os
import threading
import time
import urllib.request
from pathlib import Path

from engine.core import paths
from engine.bridge import ui

log = ui.log

_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
# (file name, size in bytes): sizes are checked after the download so a cut-off file is never used.
FILES = (("kokoro-v1.0.onnx", 325_532_387), ("voices-v1.0.bin", 28_214_398))

_lock = threading.Lock()
_state = {"downloading": False, "done": 0, "total": sum(s for _, s in FILES), "error": "", "cancel": False}


def voices_dir() -> Path:
    d = paths.data_dir() / "voices"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _present(name: str, size: int) -> bool:
    try:
        return (voices_dir() / name).stat().st_size == size
    except OSError:
        return False


def installed() -> bool:
    return all(_present(n, s) for n, s in FILES)


def package_ok() -> bool:
    """The kokoro-onnx Python package (needed besides the model files)."""
    return importlib.util.find_spec("kokoro_onnx") is not None


def engine() -> str:
    try:
        from engine.core import settings_store
        return "offline" if str(settings_store.get("tts_engine") or "edge").lower() == "offline" else "edge"
    except Exception:
        return "edge"


def status() -> dict:
    with _lock:
        st = dict(_state)
    return {
        "engine": engine(),
        "installed": installed(),
        "package_ok": package_ok(),
        "downloading": st["downloading"],
        "done": st["done"],
        "total": st["total"],
        "error": st["error"],
        # What actually speaks: the offline voice only when it was chosen AND is ready to use.
        "active": "offline" if engine() == "offline" and installed() and package_ok() else "edge",
    }


def _download_one(name: str, size: int, base_done: int) -> bool:
    dest = voices_dir() / name
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have > size:
        part.unlink()
        have = 0
    req = urllib.request.Request(_BASE + name, headers={"User-Agent": "Lyra"})
    if have:
        req.add_header("Range", f"bytes={have}-")
    with urllib.request.urlopen(req, timeout=30) as r:
        if have and getattr(r, "status", 200) != 206:   # the server ignored Range: start over
            have = 0
        with open(part, "ab" if have else "wb") as f:
            done = have
            while True:
                if _state["cancel"]:
                    return False
                chunk = r.read(1 << 17)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                with _lock:
                    _state["done"] = base_done + done
    if part.stat().st_size != size:
        raise IOError(f"{name}: got {part.stat().st_size} bytes, expected {size}")
    os.replace(part, dest)
    return True


def _run() -> None:
    try:
        base = 0
        for name, size in FILES:
            if _present(name, size):
                base += size
                with _lock:
                    _state["done"] = base
                continue
            if not _download_one(name, size, base):
                return
            base += size
        log.info("offline voice downloaded")
        try:
            from engine.voice import voice
            voice.preload_offline()
        except Exception:
            pass
    except Exception as e:  # noqa: BLE001
        log.warning("offline voice download failed: %s", e)
        with _lock:
            _state["error"] = str(e)[:160] or "download failed"
    finally:
        with _lock:
            _state["downloading"] = False


def start_download() -> bool:
    with _lock:
        if _state["downloading"]:
            return False
        _state.update(downloading=True, cancel=False, error="", done=0)
    threading.Thread(target=_run, name="lyra-offline-voice-dl", daemon=True).start()
    return True


def cancel_download() -> None:
    _state["cancel"] = True


def delete_files() -> None:
    cancel_download()
    time.sleep(0.2)
    for name, _ in FILES:
        for p in (voices_dir() / name, voices_dir() / (name + ".part")):
            try:
                p.unlink()
            except OSError:
                pass
    with _lock:
        _state.update(done=0, error="")


def dispatch(method: str, path: str) -> tuple[int, dict] | None:
    if not path.startswith("/voice/offline"):
        return None
    route = path[len("/voice/offline"):].strip("/")
    if method == "GET" and route == "":
        return 200, {"ok": True, **status()}
    if method != "POST":
        return 405, {"ok": False, "error": "method not allowed"}
    if route == "download":
        started = start_download()
        return 200, {"ok": True, "started": started, **status()}
    if route == "cancel":
        cancel_download()
        return 200, {"ok": True, **status()}
    if route == "delete":
        delete_files()
        return 200, {"ok": True, **status()}
    return 404, {"ok": False, "error": "unknown route"}
