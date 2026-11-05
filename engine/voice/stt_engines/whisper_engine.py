"""Lyra - the faster-whisper side of contract C1 (what Phases 2, 5 and 7 call when Whisper is chosen).

It does NOT load a second copy of the model: it asks stt.py for the one that SpeechRecognizer already
holds (stt.shared_whisper_model()), so RAM use stays the same as before this phase.
stt.py keeps its own, unchanged Whisper path for commands; this wrapper is for C1 callers.
"""
from __future__ import annotations

import logging

from .languages import normalize_language
from .textutil import SAMPLE_RATE, prepare_audio

try:
    from engine.bridge import ui
    log = ui.log
except Exception:
    log = logging.getLogger("lyra.stt_engines")


def _config(name: str, default):
    try:
        from engine.core import config
        return getattr(config, name, default)
    except Exception:
        return default


def model_present() -> bool:
    """The Whisper model files are on disk (no loading)."""
    try:
        from engine.core import model_setup
        return bool(model_setup.whisper_present(_config("WHISPER_MODEL", "small")))
    except Exception:
        return False


def _get_model():
    from engine.voice import stt
    return stt.shared_whisper_model()


def _prompt(hints) -> str | None:
    base = str(_config("WHISPER_INITIAL_PROMPT", "") or "").strip()
    terms = [str(h).strip() for h in (hints or []) if str(h).strip()][:60]
    extra = ("Vocabulary: " + ", ".join(terms) + ".") if terms else ""
    joined = (base + " " + extra).strip()
    return joined or None


def transcribe(audio, language=None, hints=None) -> tuple[str, str]:
    """-> (text, language). language is the detected/used code ('' if unknown)."""
    a = prepare_audio(audio)
    if a.size < int(0.08 * SAMPLE_RATE):
        return "", normalize_language(language) or ""
    model = _get_model()
    lang = normalize_language(language)
    try:
        segments, info = model.transcribe(
            a, language=lang, beam_size=int(_config("WHISPER_BEAM_SIZE", 1)),
            vad_filter=True, vad_parameters=dict(min_silence_duration_ms=400, speech_pad_ms=200),
            condition_on_previous_text=False, initial_prompt=_prompt(hints), temperature=0.0)
    except ValueError:
        # An unknown language code: let Whisper detect it instead of failing the whole utterance.
        segments, info = model.transcribe(
            a, language=None, beam_size=int(_config("WHISPER_BEAM_SIZE", 1)),
            vad_filter=True, vad_parameters=dict(min_silence_duration_ms=400, speech_pad_ms=200),
            condition_on_previous_text=False, initial_prompt=_prompt(hints), temperature=0.0)
    text = " ".join(s.text.strip() for s in segments if s.text.strip()).strip()
    return text, str(getattr(info, "language", "") or lang or "")
