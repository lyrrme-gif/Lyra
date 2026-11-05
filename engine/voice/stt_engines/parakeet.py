"""Lyra - NVIDIA Parakeet TDT 0.6B v3 (ONNX int8) through onnx-asr.

Why onnx-asr and not sherpa-onnx (see docs/p1_voice_engine.md for the full comparison):
  * pure Python on top of numpy + onnxruntime; no extra native wheel next to the sherpa-onnx that
    streaming_stt.py already ships (two different sherpa builds in one process is a real DLL risk)
  * loads a plain folder of ONNX files, so OUR downloader owns the files (progress, resume, SHA-256)
  * ships its own feature extractor (no torch, no ffmpeg); accepts a float32 numpy array directly
Nothing here touches the network: the model folder is read from disk only.
"""
from __future__ import annotations

import gc
import logging
import os
import threading
import time
from pathlib import Path

import numpy as np

from .textutil import SAMPLE_RATE, apply_hints, prepare_audio, split_long

try:
    from engine.bridge import ui
    log = ui.log
except Exception:
    log = logging.getLogger("lyra.stt_engines")

MODEL_NAME = "nemo-parakeet-tdt-0.6b-v3"     # onnx-asr's model id (selects the TDT decoder class)
MAX_PIECE_SECONDS = 30.0                      # longer audio is cut at pauses; keeps memory flat


def available() -> bool:
    """The onnx-asr package (and onnxruntime) can be imported. The model may still be missing."""
    try:
        import onnx_asr  # noqa: F401
        import onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def default_threads() -> int:
    return max(2, min(4, (os.cpu_count() or 4) // 2))


class ParakeetEngine:
    def __init__(self, model_dir: Path | str, threads: int | None = None):
        self.model_dir = Path(model_dir)
        self.threads = threads or default_threads()
        self._model = None
        self._lock = threading.Lock()          # load once
        self._run_lock = threading.Lock()      # one decode at a time (ORT sessions are shared)
        self.last_error = ""
        self.last_use = time.monotonic()       # for the idle unloader (stt_engines.start_idle_unloader)

    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Load the ONNX sessions from the model folder. Raises on any problem (caller falls back)."""
        with self._lock:
            if self._model is not None:
                return
            import onnx_asr
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.intra_op_num_threads = self.threads
            so.inter_op_num_threads = 1
            t0 = time.monotonic()
            try:
                self._model = onnx_asr.load_model(MODEL_NAME, self.model_dir, quantization="int8",
                                                  sess_options=so, providers=["CPUExecutionProvider"])
            except Exception as e:
                self.last_error = f"{e.__class__.__name__}: {e}"
                raise
            self.last_error = ""
            log.info("Parakeet v3 loaded from %s in %.1fs (%d threads)", self.model_dir,
                     time.monotonic() - t0, self.threads)

    def unload(self) -> None:
        """Drop the sessions (Windows keeps model files locked while they are mapped)."""
        with self._lock, self._run_lock:
            self._model = None
        gc.collect()

    def warmup(self) -> None:
        """One second of silence through the model so the first real utterance is not the slow one."""
        try:
            self.load()
            with self._run_lock:
                self._model.recognize(np.zeros(SAMPLE_RATE, dtype=np.float32), sample_rate=SAMPLE_RATE)
        except Exception as e:
            log.debug("Parakeet warm-up skipped: %s", e)

    def transcribe(self, audio, hints=None, fuzzy_hints: bool = False) -> str:
        """16 kHz mono audio (float32 array, or int16) -> text. Language is detected by the model."""
        a = prepare_audio(audio)
        if a.size < int(0.08 * SAMPLE_RATE):
            return ""
        self.last_use = time.monotonic()
        self.load()
        parts = []
        for piece in split_long(a, MAX_PIECE_SECONDS):
            if piece.size < int(0.08 * SAMPLE_RATE):
                continue
            with self._run_lock:
                self.last_use = time.monotonic()
                text = self._model.recognize(piece, sample_rate=SAMPLE_RATE)
            text = (text if isinstance(text, str) else str(getattr(text, "text", text) or "")).strip()
            if text:
                parts.append(text)
        out = " ".join(parts).strip()
        return apply_hints(out, hints, fuzzy=fuzzy_hints) if hints else out
