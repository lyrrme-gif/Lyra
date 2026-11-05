"""Lyra notetaker - offline transcription of speech segments.

Uses stt_engines (contract C1, Phase 1) when it is there; otherwise the existing stt.SpeechRecognizer
(faster-whisper). Both are optional-import: nothing here needs the network."""
from __future__ import annotations

import logging
from typing import Callable

import numpy as np

from .audio import SR

try:
    from engine.bridge import ui
    log = ui.log
except Exception:
    log = logging.getLogger("lyra.notetaker")

Transcriber = Callable[[np.ndarray, "str | None", list], str]


def _hints() -> list:
    try:
        from engine.voice import dictionary_store                    # Phase 2 (C4); fine if absent
        return list(dictionary_store.hints() or [])
    except Exception:
        return []


def default_transcriber() -> tuple[Transcriber, str]:
    """-> (function(audio_float32, language, hints) -> text, engine label). Raises if no engine works."""
    try:
        from engine.voice import stt_engines

        def c1(audio, language, hints):
            r = stt_engines.transcribe(audio, language=language, hints=hints)
            if r.get("error"):
                raise RuntimeError(r["error"])
            return str(r.get("text") or "")
        return c1, "stt_engines"
    except ImportError:
        pass
    from engine.voice import stt
    rec = stt.SpeechRecognizer()

    def fallback(audio, language, hints):
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
        status, text = rec.transcribe(pcm, sample_rate=SR)
        if status == "error":
            raise RuntimeError("speech recognizer failed")
        return text
    return fallback, "stt"


def transcribe_segments(audio: np.ndarray, segs: list[tuple[float, float]], transcriber: Transcriber,
                        language: str | None = None, hints: list | None = None,
                        progress: Callable[[int, int], None] | None = None) -> tuple[list[dict], int]:
    """-> (rows [{start,end,text}], number of segments that FAILED). Empty text is not a failure."""
    rows, failed = [], 0
    hints = _hints() if hints is None else hints
    for i, (a, b) in enumerate(segs):
        if progress:
            progress(i, len(segs))
        chunk = audio[int(a * SR):int(b * SR)]
        try:
            text = (transcriber(chunk, language, hints) or "").strip()
        except Exception as e:
            failed += 1
            log.warning("notetaker: segment %d failed: %s", i, e)
            continue
        if text:
            rows.append({"start": a, "end": b, "text": text})
    if progress:
        progress(len(segs), len(segs))
    return rows, failed
