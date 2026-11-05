"""Lyra notetaker - the recording session: two sources -> two temporary wav tracks."""
from __future__ import annotations

import secrets
import threading
import time
from typing import Callable

from . import REMINDER, pipeline
from .audio import LoopbackSource, MicSource, Track


class NotAcknowledged(Exception):
    pass


def default_sources() -> dict:
    mic = ""
    try:
        from engine.core import settings_store
        mic = str(settings_store.get("audio_input") or "")
    except Exception:
        pass
    return {"mic": MicSource(mic), "system": LoopbackSource()}


class Recorder:
    def __init__(self, source_factory: Callable[[], dict] = default_sources, clock=time.monotonic,
                 wall=time.time, max_seconds: Callable[[], float] = lambda: 4 * 3600):
        self._factory, self._clock, self._wall, self._max = source_factory, clock, wall, max_seconds
        self._lock = threading.Lock()
        self._rec: dict | None = None
        self._watch: threading.Thread | None = None
        self.on_auto_stop: Callable[[dict], None] | None = None

    @property
    def active(self) -> bool:
        return self._rec is not None

    def start(self, title: str = "", acknowledged: bool = False, language: str = "") -> dict:
        if acknowledged is not True:
            raise NotAcknowledged(REMINDER)
        with self._lock:
            if self._rec is not None:
                raise RuntimeError("Already recording.")
            now = self._wall()
            sid = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + "-" + secrets.token_hex(2)
            t0 = self._clock()
            d = pipeline.tmp_dir()
            sources, tracks, warnings = self._factory(), {}, []
            opened = {}
            for name, src in sources.items():
                tr = Track(d / f"{sid}-{name}.wav", self._clock, t0)
                try:
                    src.start(tr.push)
                    tracks[name], opened[name] = tr, src
                except Exception as e:                           # missing device / not Windows / no pyaudio
                    tr.finish(0)
                    try:
                        (d / f"{sid}-{name}.wav").unlink()
                    except OSError:
                        pass
                    warnings.append(_why(name, e))
            if not tracks:
                raise RuntimeError("Could not open any audio input. " + " ".join(warnings))
            mk = {"id": sid, "title": (title or "").strip()[:80] or "Meeting", "started": now, "language": language or "",
                  "state": "recording", "system_audio": "system" in tracks,
                  "tracks": {n: f"{sid}-{n}.wav" for n in tracks}}
            pipeline.write_marker(mk)
            self._rec = {"mk": mk, "tracks": tracks, "sources": opened, "t0": t0, "warnings": warnings}
            self._watch = threading.Thread(target=self._watchdog, args=(sid,), name="lyra-notes-watch", daemon=True)
            self._watch.start()
        return self.status()

    def _watchdog(self, sid: str):
        while True:
            time.sleep(1.0)
            r = self._rec
            if r is None or r["mk"]["id"] != sid:
                return
            if self._clock() - r["t0"] >= self._max():
                mk = self.stop()
                if mk and self.on_auto_stop:
                    self.on_auto_stop(mk)
                return

    def stop(self) -> dict | None:
        with self._lock:
            r, self._rec = self._rec, None
        if r is None:
            return None
        for src in r["sources"].values():
            try:
                src.stop()
            except Exception:
                pass
        total = self._clock() - r["t0"]
        for tr in r["tracks"].values():
            tr.finish(total)
        mk = r["mk"]
        mk.update(state="pending", duration=round(total, 2))
        pipeline.write_marker(mk)
        return mk

    def status(self) -> dict:
        r = self._rec
        if r is None:
            return {"recording": False}
        return {"recording": True, "id": r["mk"]["id"], "title": r["mk"]["title"],
                "elapsed": round(self._clock() - r["t0"], 1), "system_audio": r["mk"]["system_audio"],
                "levels": {n: round(t.level, 3) for n, t in r["tracks"].items()}, "warnings": list(r["warnings"])}


def _why(name: str, e: Exception) -> str:
    label = "System audio" if name == "system" else "Microphone"
    if isinstance(e, ImportError):
        extra = "needs Windows" if name == "system" else "PyAudio is not installed"
        return f"{label} is unavailable ({extra})."
    return f"{label} could not be opened ({e})."
