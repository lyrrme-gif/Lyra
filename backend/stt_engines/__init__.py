"""Lyra - offline speech engines (Phase 1). Contract C1.

    stt_engines.transcribe(audio, language=None, hints=None) -> {text, language, engine, ms}
    stt_engines.status()                                     -> {ready, engine, downloading, progress, languages, ...}
    stt_engines.set_engine("auto" | "parakeet" | "whisper")

`audio` is 16 kHz mono (float32 in [-1, 1]; int16 is accepted too). `language` is a code such as
"en" / "fr-FR", or None when unknown. `hints` is a list of names/terms (the user's dictionary).

Engines
  parakeet  NVIDIA Parakeet TDT 0.6B v3, ONNX int8 (25 European languages, detects the language itself)
  whisper   faster-whisper (what Lyra used before; ~99 languages, incl. Arabic)
Routing is in languages.choose_engine(). If Parakeet is not downloaded, not installed, or fails for any
reason, the utterance is transcribed by Whisper instead; nothing here raises for a missing model.
Audio never leaves memory: nothing is written to disk and nothing goes to the network.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from . import languages as _lang
from .languages import MODES, PARAKEET_V3_LANGUAGES, normalize_language, parakeet_supports  # noqa: F401
from .textutil import SAMPLE_RATE, apply_hints, prepare_audio  # noqa: F401

try:
    import ui
    log = ui.log
except Exception:
    log = logging.getLogger("lyra.stt_engines")

PILL_READY = "Voice model ready"
PILL_DOWNLOAD = "Download the voice model to dictate"

_lock = threading.RLock()
_downloader = None
_engine = None
_dest_override: Path | None = None
_opener_override = None
last_route: dict = {"engine": "", "reason": "", "fallback": ""}


# -- settings (namespaced p1_, read through settings_store when it is there) -------------------------
def _setting(key: str, default):
    try:
        import settings_store
        v = settings_store.get(key)
        return default if v is None else v
    except Exception:
        return default


def mode() -> str:
    return _lang.normalize_mode(_setting("p1_stt_engine", "auto"))


def set_engine(name: str) -> str:
    """Persist the engine choice. Raises ValueError for anything but auto / parakeet / whisper."""
    n = str(name or "").strip().lower()
    if n not in MODES:
        raise ValueError(f"unknown engine {name!r}; expected one of {', '.join(MODES)}")
    import settings_store
    settings_store.set_many({"p1_stt_engine": n})
    return n


# -- model folder + managers (lazy: importing this package costs nothing) --------------------------------
def model_dir() -> Path:
    if _dest_override is not None:
        return _dest_override
    import paths
    return paths.data_dir() / "models" / "parakeet-tdt-0.6b-v3-int8"


def _toast(text: str) -> None:
    try:
        import ui_bridge
        ui_bridge.push_notification("reminder", "reminder", "Lyra", text, force=True)
    except Exception:
        pass


def _on_download_done(ok: bool, msg: str) -> None:
    if ok:
        _toast(PILL_READY)
        threading.Thread(target=preload, name="lyra-parakeet-preload", daemon=True).start()
    elif msg and "cancelled" not in msg.lower():
        _toast("Voice model download failed - open Settings > Voice for details")


def get_downloader():
    global _downloader
    with _lock:
        if _downloader is None:
            from .downloader import PARAKEET_V3_INT8, ModelDownloader
            _downloader = ModelDownloader(PARAKEET_V3_INT8, model_dir(), opener=_opener_override,
                                          on_done=_on_download_done, before_delete=_unload)
        return _downloader


def get_engine():
    """The shared Parakeet engine object (model not loaded until first use)."""
    global _engine
    with _lock:
        if _engine is None:
            from .parakeet import ParakeetEngine
            _engine = ParakeetEngine(model_dir())
        return _engine


def _unload() -> None:
    with _lock:
        if _engine is not None:
            _engine.unload()


def _reset_for_tests(dest: Path | None = None, opener=None) -> None:
    """Forget cached objects and point at a temp folder / fake network (tests only)."""
    global _downloader, _engine, _dest_override, _opener_override
    with _lock:
        _downloader = _engine = None
        _dest_override = Path(dest) if dest else None
        _opener_override = opener
        last_route.update(engine="", reason="", fallback="")


def start_download() -> bool:
    return get_downloader().start()


def cancel_download() -> None:
    get_downloader().cancel()


def delete_model() -> tuple[bool, str]:
    return get_downloader().delete()


def parakeet_installed() -> bool:
    return get_downloader().is_installed()


def parakeet_ready() -> bool:
    """Downloaded AND the onnx-asr package is importable."""
    from . import parakeet as _p
    return parakeet_installed() and _p.available()


def preload() -> bool:
    """Load + warm Parakeet in the background so the first utterance is fast. Never raises."""
    try:
        if mode() == "whisper" or not parakeet_ready():
            return False
        try:                                  # background warm-up must not fight the UI for the CPU
            import os as _os
            if _os.name == "nt":
                import ctypes
                _k = ctypes.windll.kernel32
                _k.SetThreadPriority(_k.GetCurrentThread(), -1)     # THREAD_PRIORITY_BELOW_NORMAL
        except Exception:
            pass
        get_engine().warmup()
        return get_engine().loaded()
    except Exception as e:
        log.debug("Parakeet preload skipped: %s", e)
        return False


def route(language: str | None = None, engine_override: str | None = None) -> tuple[str, str]:
    """Which engine would handle this language right now? -> ('parakeet' | 'whisper', reason)."""
    return _lang.choose_engine(engine_override or mode(), language, parakeet_ready=parakeet_ready())


# -- the contract ----------------------------------------------------------------------------------------
def transcribe(audio, language=None, hints=None, engine: str | None = None, *,
               fuzzy_hints: bool = False) -> dict:
    """C1. Returns {text, language, engine, ms} (+ route, fallback). Never raises for engine problems:
    if neither engine can run, text is "" and `error` says why. `engine` overrides the user's setting
    for this one call (benchmarks, tests)."""
    t0 = time.monotonic()
    code = normalize_language(language)
    try:
        a = prepare_audio(audio)
    except Exception as e:
        return {"text": "", "language": code or "", "engine": "", "ms": 0, "error": f"bad audio: {e}"}
    chosen, reason = route(code, engine)
    fallback = ""
    text, lang_out, used = "", code or "", chosen

    if chosen == "parakeet":
        try:
            text = _parakeet().transcribe(a, hints=hints, fuzzy_hints=fuzzy_hints)
            lang_out = code or ""
        except Exception as e:
            fallback = f"Parakeet failed ({e.__class__.__name__}: {e}); used Whisper"
            log.warning("%s", fallback)
            used = "whisper"
    if used == "whisper":
        try:
            from . import whisper_engine
            text, lang_out = whisper_engine.transcribe(a, code, hints)
            if hints:
                text = apply_hints(text, hints, fuzzy=fuzzy_hints)
        except Exception as e:
            log.error("Whisper failed too: %s", e)
            last_route.update(engine="", reason=reason, fallback=fallback)
            return {"text": "", "language": code or "", "engine": "", "ms": int((time.monotonic() - t0) * 1000),
                    "error": f"{e.__class__.__name__}: {e}", "route": reason, "fallback": fallback}
    ms = int((time.monotonic() - t0) * 1000)
    last_route.update(engine=used, reason=reason, fallback=fallback)
    out = {"text": text, "language": lang_out, "engine": used, "ms": ms, "route": reason}
    if fallback:
        out["fallback"] = fallback
    return out


def _parakeet():
    return get_engine()


def status() -> dict:
    """C1. {ready, engine, downloading, progress, languages} plus details for the Settings panel.

    ready        the engine that handles your default speech is usable right now
                 (Parakeet downloaded + onnx-asr importable, or Whisper selected and present)
    engine       'parakeet' | 'whisper': what an utterance of unknown language would use today
    languages    codes the active engine covers (['*'] = Whisper, any language)"""
    dl = get_downloader().status()
    from . import parakeet as _p
    from . import whisper_engine as _w
    m = mode()
    p_ready = dl["installed"] and _p.available()
    active, why = _lang.choose_engine(m, None, parakeet_ready=p_ready)
    w_present = _w.model_present()
    # Whisper mode needs the Whisper model; every other mode wants the Parakeet download (the pill asks
    # for it; Whisper keeps dictating in the meantime, so nothing breaks while it is missing).
    ready = w_present if m == "whisper" else p_ready
    pill = PILL_READY if ready else PILL_DOWNLOAD
    if dl["downloading"]:
        pill = f"Downloading the voice model… {int(dl['progress'] * 100)}%"
    return {
        "ready": bool(ready),
        "engine": active,
        "downloading": bool(dl["downloading"]),
        "progress": dl["progress"],
        "languages": sorted(PARAKEET_V3_LANGUAGES) if active == "parakeet" else ["*"],
        "mode": m,
        "pill": pill,
        "reason": why,
        "parakeet": {**dl, "available": _p.available(), "loaded": _engine is not None and _engine.loaded(),
                     "languages": dict(PARAKEET_V3_LANGUAGES), "last_error": _engine.last_error if _engine else ""},
        "whisper": {"present": w_present},
        "last": dict(last_route),
    }
