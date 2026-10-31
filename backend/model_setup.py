"""Lyra - first-run model downloads + a tiny backend status file.

Models are never bundled in the installer. They are fetched on first run into
<data dir>\\models (installed: %APPDATA%\\Lyra\\models) and re-used afterwards:
  models\\openwakeword\\   hey_jarvis + the two feature models (a few MB)
  models\\whisper\\        faster-whisper model (WHISPER_MODEL in config.py)
  models\\parakeet-tdt-0.6b-v3-int8\\   optional Parakeet v3 voice model (~670 MB, one click in
                                       Settings > Voice; downloaded by stt_engines, not here)

While a download runs, a "Downloading voice models..." toast is shown in the
island (via the existing notification channel, re-sent every 10 s) and the
bridge's /health reports downloading_models=true.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import paths
import ui

log = ui.log

_state = {"downloading": False, "what": ""}


def state() -> dict:
    return dict(_state)


def models_dir() -> Path:
    d = paths.data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


# -- backend_status.json ------------------------------------------------------
def write_backend_status(state_: str, code: str = "", message: str = "") -> None:
    """Small file other parts (UI, Settings check) can read: ok | error + reason."""
    try:
        paths.data_path("backend_status.json").write_text(
            json.dumps({"state": state_, "code": code, "message": message,
                        "pid": os.getpid(), "ts": int(time.time())}, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning("could not write backend_status.json: %s", e)


# -- island toast -------------------------------------------------------------
def _toast(text: str) -> None:
    try:
        import ui_bridge
        ui_bridge.push_notification("reminder", "reminder", "Lyra", text, force=True)
    except Exception:
        pass


class _Announce:
    def __init__(self, what: str):
        self.what = what
        self._stop = threading.Event()
        self._beat: threading.Thread | None = None

    def __enter__(self):
        _state.update(downloading=True, what=self.what)
        log.info("First run: downloading %s into %s (needs internet, may take a few minutes)...",
                 self.what, models_dir())

        def beat():
            while True:
                _toast("Downloading voice models\u2026")
                if self._stop.wait(10):
                    return

        self._beat = threading.Thread(target=beat, name="lyra-model-toast", daemon=True)
        self._beat.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._beat:
            self._beat.join(1.0)
        _state.update(downloading=False, what="")
        if exc_type is None:
            log.info("Downloaded %s.", self.what)
            _toast("Voice models ready")
        else:
            log.error("Download of %s failed: %s", self.what, exc)
            _toast("Voice model download failed - check your internet connection")
        return False


# -- openWakeWord -------------------------------------------------------------
def wake_model_files(name: str) -> dict:
    d = models_dir() / "openwakeword"

    def pick(pattern: str):
        m = sorted(d.glob(pattern))
        return str(m[0]) if m else None

    return {"wake": pick(f"{name}*.onnx"),
            "melspec": pick("melspectrogram.onnx"),
            "embedding": pick("embedding_model.onnx")}


def ensure_wake_models(name: str) -> dict:
    files = wake_model_files(name)
    if all(files.values()):
        return files
    target = models_dir() / "openwakeword"
    target.mkdir(parents=True, exist_ok=True)
    from openwakeword.utils import download_models
    with _Announce("wake-word models"):
        download_models(model_names=[name], target_directory=str(target))
    files = wake_model_files(name)
    missing = [k for k, v in files.items() if not v]
    if missing:
        raise RuntimeError(f"openWakeWord files missing after download in {target}: {', '.join(missing)}")
    return files


# -- faster-whisper -----------------------------------------------------------
def ensure_whisper(size: str) -> str:
    """Local path of the model folder, downloading it first if needed."""
    from faster_whisper.utils import download_model
    root = str(models_dir() / "whisper")
    try:
        return download_model(size, cache_dir=root, local_files_only=True)
    except Exception:
        pass
    with _Announce(f"Whisper '{size}' speech model"):
        return download_model(size, cache_dir=root)


def whisper_present(size: str) -> bool:
    """True if the Whisper model folder already exists under models/whisper."""
    root = models_dir() / "whisper"
    if not root.is_dir():
        return False
    # faster-whisper stores under models--* or a size-named folder depending on version
    for p in root.rglob("config.json"):
        return True
    return any(root.glob(f"*{size}*"))


# -- Parakeet v3 (Phase 1; the download itself is stt_engines.downloader) ----------------------------
def parakeet_present() -> bool:
    """True if the optional Parakeet voice model is fully downloaded and verified."""
    try:
        import stt_engines
        return bool(stt_engines.parakeet_installed())
    except Exception:
        return False


# -- sherpa-onnx streaming model (Phase 4) -----------------------------------
def streaming_model_files(name: str) -> dict | None:
    """Paths of the streaming model's four files under models/streaming, or None if incomplete."""
    root = models_dir() / "streaming" / name
    if not root.is_dir():
        return None

    def pick(*patterns: str):
        for pattern in patterns:
            m = sorted(root.rglob(pattern))
            if m:
                return str(m[0])
        return None

    files = {
        "tokens": pick("tokens.txt"),
        "encoder": pick("encoder*.int8.onnx", "encoder*.onnx"),
        "decoder": pick("decoder*[!8].onnx", "decoder*.onnx"),   # the small plain decoder first
        "joiner": pick("joiner*.int8.onnx", "joiner*.onnx"),
    }
    return files if all(files.values()) else None


def ensure_streaming_model(name: str, url: str) -> dict:
    """Files of the streaming model, downloading + unpacking the archive first if needed."""
    files = streaming_model_files(name)
    if files:
        return files
    import tarfile
    import tempfile
    import urllib.request

    target = models_dir() / "streaming"
    target.mkdir(parents=True, exist_ok=True)
    with _Announce("streaming speech model"):
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "model.tar.bz2")
            urllib.request.urlretrieve(url, archive)
            with tarfile.open(archive, "r:bz2") as tar:
                try:
                    tar.extractall(target, filter="data")   # refuses paths outside `target`
                except TypeError:                            # Python without extraction filters
                    tar.extractall(target)
    files = streaming_model_files(name)
    if not files:
        raise RuntimeError(f"streaming model files missing after download in {target / name}")
    return files


def check_microphone() -> dict:
    """Best-effort: list default input device. Does not open a stream long-term."""
    try:
        import pyaudio
        pa = pyaudio.PyAudio()
        try:
            info = pa.get_default_input_device_info()
            name = str(info.get("name") or "default")
            return {"ok": True, "name": name}
        finally:
            pa.terminate()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def diagnostics() -> dict:
    """Snapshot for Settings → Advanced → Check for problems."""
    from config import WHISPER_MODEL

    whisper_ok = whisper_present(WHISPER_MODEL)

    llm = {"provider": "", "has_key": False}
    try:
        from llm_store import load
        data = load()
        prov = str(data.get("provider") or "openrouter")
        key = data.get(f"{prov}_api_key") or ""
        llm = {"provider": prov, "has_key": bool(str(key).strip())}
    except Exception as e:
        llm = {"provider": "", "has_key": False, "error": str(e)}

    mic = check_microphone()
    problems: list[str] = []
    if not whisper_ok:
        problems.append(f"Whisper '{WHISPER_MODEL}' model not downloaded yet (fetches on first listen).")
    if not llm["has_key"]:
        problems.append(f"No API key set for provider '{llm.get('provider') or 'openrouter'}'.")
    if not mic.get("ok"):
        problems.append("No working microphone detected.")

    return {
        "ok": len(problems) == 0,
        "problems": problems,
        "models": {
            "whisper": whisper_ok,
            "whisper_size": WHISPER_MODEL,
            "parakeet": parakeet_present(),      # optional: Auto/Parakeet engine (Settings > Voice)
            "downloading": bool(_state.get("downloading")),
            "downloading_what": _state.get("what") or "",
        },
        "llm": llm,
        "microphone": mic,
        "data_dir": str(paths.data_dir()),
        "port": 8765,
    }
