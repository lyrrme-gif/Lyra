"""Lyra notetaker - audio capture helpers. Sources are small objects, so tests use fakes.

Source interface:  name: str;  start(on_chunk) ;  stop()
  on_chunk(pcm: bytes)  16 kHz mono int16 audio, delivered from the source's own thread.
Real sources: MicSource (PyAudio) and LoopbackSource (PyAudioWPatch, WASAPI loopback = what you hear).
Audio is written to temporary wav files only (see Track); the pipeline deletes them after the transcript.
"""
from __future__ import annotations

import threading
import time
import wave
from pathlib import Path
from typing import Callable

import numpy as np

SR = 16000


class Resampler:
    """Stateful int16 (any rate, any channel count) -> 16 kHz mono int16, chunk by chunk."""

    def __init__(self, rate: int, channels: int):
        self.rate, self.ch = int(rate), max(1, int(channels))
        self._pos = 0.0                      # fractional input position carried between chunks
        self._tail = np.zeros(0, dtype=np.float32)

    def feed(self, raw: bytes) -> bytes:
        a = np.frombuffer(raw, dtype=np.int16)
        if self.ch > 1:
            a = a[: len(a) // self.ch * self.ch].reshape(-1, self.ch).mean(axis=1)
        a = a.astype(np.float32)
        if self.rate == SR:
            return a.astype(np.int16).tobytes()
        x = np.concatenate([self._tail, a])
        if self.rate > SR:                    # crude anti-alias: average over the decimation window
            k = max(1, int(round(self.rate / SR)))
            if self.rate % SR == 0:
                n = len(a) // k * k
                out = a[:n].reshape(-1, k).mean(axis=1) if n else np.zeros(0, np.float32)
                self._tail = a[n:]
                return out.astype(np.int16).tobytes()
        step = self.rate / SR
        idx = np.arange(self._pos, len(x) - 1, step)
        out = np.interp(idx, np.arange(len(x)), x) if len(idx) else np.zeros(0, np.float32)
        used = int(idx[-1] + step) if len(idx) else 0
        self._pos = (idx[-1] + step - used) if len(idx) else self._pos
        self._tail = x[used:]
        return out.astype(np.int16).tobytes()


class Track:
    """One channel written to a temporary wav. Keeps time aligned with the wall clock: WASAPI loopback
    delivers nothing while the PC is silent, so gaps are filled with silence."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.monotonic, t0: float | None = None):
        self.path = Path(path)
        self._clock = clock
        self._t0 = clock() if t0 is None else t0
        self._w = wave.open(str(self.path), "wb")
        self._w.setnchannels(1)
        self._w.setsampwidth(2)
        self._w.setframerate(SR)
        self._n = 0
        self._patched = clock()
        self._lock = threading.Lock()
        self.level = 0.0

    @property
    def seconds(self) -> float:
        return self._n / SR

    def push(self, pcm: bytes) -> None:
        if not pcm:
            return
        with self._lock:
            if self._w is None:
                return
            want = int((self._clock() - self._t0) * SR) - len(pcm) // 2
            if want - self._n > int(0.3 * SR):                        # a gap: fill with silence
                gap = want - self._n
                self._w.writeframes(bytes(gap * 2))
                self._n += gap
            self._w.writeframes(pcm)
            self._n += len(pcm) // 2
            now = self._clock()
            if now - self._patched >= 10.0:          # keep the wav header valid, so a crash loses nothing
                self._patched = now
                try:
                    self._w._patchheader()
                    self._w._file.flush()
                except Exception:
                    pass
            a = np.frombuffer(pcm, dtype=np.int16)
            self.level = float(np.sqrt(np.mean((a.astype(np.float32) / 32768.0) ** 2))) if len(a) else 0.0

    def finish(self, total_seconds: float) -> None:
        """Pad with silence up to `total_seconds` (so all channels have the same length) and close."""
        with self._lock:
            if self._w is None:
                return
            missing = int(total_seconds * SR) - self._n
            if missing > 0:
                self._w.writeframes(bytes(missing * 2))
                self._n += missing
            self._w.close()
            self._w = None


def read_wav(path: Path) -> np.ndarray:
    """float32 samples of a track written by Track."""
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


class MicSource:
    name = "mic"

    def __init__(self, device_name: str = ""):
        self.device_name = device_name
        self._stop = threading.Event()
        self._t: threading.Thread | None = None
        self.error = ""

    def _device_index(self, pa):
        want = (self.device_name or "").strip()
        if not want or want.lower().startswith("default"):
            return None
        for i in range(pa.get_device_count()):
            d = pa.get_device_info_by_index(i)
            if d.get("maxInputChannels", 0) > 0 and want.lower() in str(d.get("name", "")).lower():
                return i
        return None

    def start(self, on_chunk):
        import pyaudio                                    # raises ImportError when missing: caller reports it
        pa = pyaudio.PyAudio()
        stream = pa.open(format=pyaudio.paInt16, channels=1, rate=SR, input=True,
                         input_device_index=self._device_index(pa), frames_per_buffer=1024)

        def run():
            try:
                while not self._stop.is_set():
                    on_chunk(stream.read(1024, exception_on_overflow=False))
            except Exception as e:
                self.error = str(e)
            finally:
                try:
                    stream.stop_stream()
                    stream.close()
                    pa.terminate()
                except Exception:
                    pass
        self._t = threading.Thread(target=run, name="lyra-notes-mic", daemon=True)
        self._t.start()

    def stop(self):
        self._stop.set()
        if self._t:
            self._t.join(2.0)


class LoopbackSource:
    """Everything the PC plays (the other people on a call). Windows only (WASAPI loopback)."""
    name = "system"

    def __init__(self):
        self._pa = None
        self._stream = None
        self.error = ""

    def start(self, on_chunk):
        import pyaudiowpatch as pyaudio                   # Windows only; ImportError elsewhere
        pa = pyaudio.PyAudio()
        try:
            dev = pa.get_default_wasapi_loopback()
        except Exception:
            dev = next(iter(pa.get_loopback_device_info_generator()), None)
        if not dev:
            pa.terminate()
            raise RuntimeError("no loopback device found")
        rs = Resampler(int(dev["defaultSampleRate"]), int(dev["maxInputChannels"]))

        def cb(in_data, frame_count, time_info, status):
            try:
                on_chunk(rs.feed(in_data))
            except Exception as e:
                self.error = str(e)
            return (None, pyaudio.paContinue)
        self._pa = pa
        self._stream = pa.open(format=pyaudio.paInt16, channels=int(dev["maxInputChannels"]),
                               rate=int(dev["defaultSampleRate"]), input=True,
                               input_device_index=dev["index"], frames_per_buffer=1024, stream_callback=cb)

    def stop(self):
        try:
            if self._stream:
                self._stream.stop_stream()
                self._stream.close()
            if self._pa:
                self._pa.terminate()
        except Exception:
            pass
