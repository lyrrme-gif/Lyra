"""Lyra — streaming speech recognition (sherpa-onnx).

A small streaming model that returns PARTIAL text while you are still talking.
It is NOT what commands run on: stt.py (Whisper) still produces the final text.
Partials are used for two things only:
  * wake.py / ptt.py: end a spoken turn sooner and pre-warm the LLM
  * dictation.py: type what you say word by word

The recognizer is loaded once (load() is called in the background at startup).
Everything degrades quietly: if sherpa-onnx or its model is missing,
new_session() returns None and the callers behave exactly as before.

Phase 1: a Session also keeps the audio of the CURRENT segment in memory (never on disk, capped at
MAX_BUFFER_SECONDS, dropped on reset()). Session.refine() re-decodes that segment with the accurate
offline engine (stt_engines: Parakeet v3) so dictation can replace the fast English-only guess with
a multilingual, punctuated final text. refine() returns None whenever that engine is not available,
and then the callers simply keep the streaming text, exactly as before.
"""

from __future__ import annotations

import threading
import time

import numpy as np

import ui
from config import (
    STREAMING_ENABLED, STREAMING_MODEL_NAME, STREAMING_MODEL_URL, STREAMING_THREADS,
)

log = ui.log

SAMPLE_RATE = 16000
MAX_BUFFER_SECONDS = 60       # in-memory audio kept for refine(); older audio is dropped
_TAIL_SECONDS = 0.66          # silence appended when a session finishes, so the last word is decoded

_rec = None
_load_lock = threading.Lock()
_decode_lock = threading.Lock()    # one decode at a time, whichever thread feeds
_failed = False


def available() -> bool:
    """True if the sherpa-onnx package can be imported (the model may still need a download)."""
    try:
        import sherpa_onnx  # noqa: F401
        return True
    except Exception:
        return False


_last_use = time.monotonic()


def loaded() -> bool:
    return _rec is not None


def _touch() -> None:
    global _last_use
    _last_use = time.monotonic()


def idle_for() -> float:
    """Seconds since a session last started (or the model loaded)."""
    return time.monotonic() - _last_use


def unload() -> bool:
    """Free the model (the idle unloader calls this). It loads again by itself on the next dictation / voice turn."""
    global _rec
    with _load_lock:
        if _rec is None:
            return False
        _rec = None
    import gc
    gc.collect()
    log.info("Streaming speech model unloaded after idling (it loads again when you next speak).")
    return True


def _load_in_background() -> None:
    threading.Thread(target=load, name="lyra-streaming-reload", daemon=True).start()


def load() -> bool:
    """Download (first run) and load the streaming model. Safe to call repeatedly; never raises."""
    global _rec, _failed
    if not STREAMING_ENABLED:
        return False
    with _load_lock:
        if _rec is not None:
            return True
        if _failed:
            return False
        try:
            import sherpa_onnx
            import model_setup
            files = model_setup.ensure_streaming_model(STREAMING_MODEL_NAME, STREAMING_MODEL_URL)
            _rec = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=files["tokens"],
                encoder=files["encoder"],
                decoder=files["decoder"],
                joiner=files["joiner"],
                num_threads=STREAMING_THREADS,
                sample_rate=SAMPLE_RATE,
                feature_dim=80,
                enable_endpoint_detection=True,
                rule1_min_trailing_silence=2.4,    # silence, nothing said yet
                rule2_min_trailing_silence=0.9,    # silence after speech: a segment ends
                rule3_min_utterance_length=20.0,   # hard cap on one segment (seconds)
                decoding_method="greedy_search",
            )
            _touch()
            log.info("Streaming speech model loaded: %s", STREAMING_MODEL_NAME)
            return True
        except Exception as e:
            _failed = True      # do not retry (and re-download) on every turn
            log.warning("Streaming speech unavailable (%s) - turns end on silence only, dictation is off.", e)
            return False


def new_session() -> "Session | None":
    """A fresh recognition stream, or None if the model is not loaded (yet)."""
    if _rec is None:
        # Unloaded after idling: this turn goes without early end-of-speech (silence ends it), and the model comes back for the next one.
        if STREAMING_ENABLED and not _failed:
            _load_in_background()
        return None
    _touch()
    try:
        return Session(_rec)
    except Exception as e:
        log.warning("Could not start a streaming session: %s", e)
        return None


def refine_available(language: str | None = None) -> bool:
    """True when Session.refine() would use Parakeet for this language right now."""
    try:
        import stt_engines
        return stt_engines.route(language)[0] == "parakeet"
    except Exception:
        return False


def _text_of(result) -> str:
    text = result if isinstance(result, str) else getattr(result, "text", "")
    return (text or "").strip().lower()    # the model writes UPPERCASE; the typing layer adds capitals


class Session:
    """One stream of audio. Use from a single thread."""

    def __init__(self, recognizer):
        self._rec = recognizer
        self._s = recognizer.create_stream()
        self._pcm: list[bytes] = []      # int16 audio of the current segment (memory only)
        self._pcm_len = 0

    def feed(self, pcm: bytes) -> tuple[str, bool]:
        """Add 16 kHz mono int16 audio. Returns (partial text so far, segment ended).

        When the second value is True the speaker paused long enough for the recognizer to
        close the segment: take the text as final, then call reset()."""
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        self._keep(pcm)
        with _decode_lock:
            self._s.accept_waveform(SAMPLE_RATE, samples)
            while self._rec.is_ready(self._s):
                self._rec.decode_stream(self._s)
            text = _text_of(self._rec.get_result(self._s))
            return text, bool(self._rec.is_endpoint(self._s))

    def reset(self) -> None:
        """Start the next segment (after a segment ended)."""
        with _decode_lock:
            self._rec.reset(self._s)
        self._pcm.clear()
        self._pcm_len = 0

    def finish(self) -> str:
        """No more audio: decode what is left and return the final text of the current segment."""
        with _decode_lock:
            self._s.accept_waveform(SAMPLE_RATE, np.zeros(int(_TAIL_SECONDS * SAMPLE_RATE), dtype=np.float32))
            self._s.input_finished()
            while self._rec.is_ready(self._s):
                self._rec.decode_stream(self._s)
            return _text_of(self._rec.get_result(self._s))

    # -- Phase 1: accurate re-decode of the current segment ------------------------------------------
    def _keep(self, pcm: bytes) -> None:
        self._pcm.append(bytes(pcm))
        self._pcm_len += len(pcm)
        limit = MAX_BUFFER_SECONDS * SAMPLE_RATE * 2
        while self._pcm_len > limit and len(self._pcm) > 1:
            self._pcm_len -= len(self._pcm.pop(0))

    def segment_seconds(self) -> float:
        return self._pcm_len / 2.0 / SAMPLE_RATE

    def refine(self, language: str | None = None, hints=None, allow_whisper: bool = False) -> str | None:
        """Final text of the current segment from the accurate engine, or None to keep the streaming text.

        Call it when a segment ended (feed() returned True) or after finish(), BEFORE reset().
        With allow_whisper=False (default) it only runs when Parakeet is routed for `language`, so a
        machine without the voice model behaves exactly as before. Never raises."""
        try:
            if self._pcm_len < int(0.15 * SAMPLE_RATE) * 2:
                return None
            import stt_engines
            if not allow_whisper and stt_engines.route(language)[0] != "parakeet":
                return None
            audio = np.frombuffer(b"".join(self._pcm), dtype=np.int16).astype(np.float32) / 32768.0
            res = stt_engines.transcribe(audio, language=language, hints=hints)
            if res.get("error") or (res.get("engine") != "parakeet" and not allow_whisper):
                return None
            return (res.get("text") or "").strip() or None
        except Exception as e:
            log.debug("refine skipped: %s", e)
            return None
