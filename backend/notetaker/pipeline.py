"""Lyra notetaker - from temporary audio to a stored transcript, then the audio is deleted.

Order matters and is the privacy promise:   transcribe -> save transcript -> DELETE AUDIO -> (optional) summary.
If no speech engine can run, the audio is kept (so nothing is lost) and the session is marked failed;
a retry later finishes it. Audio left behind for any reason is swept at startup and after 7 days at the latest."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from . import diarize as _diar
from . import merge, settings, store, summarize as _sum
from .audio import SR, read_wav
from .segment import segments
from .transcribe import default_transcriber, transcribe_segments

try:
    import ui
    log = ui.log
except Exception:
    log = logging.getLogger("lyra.notetaker")

STALE_AUDIO_DAYS = 7


class PipelineError(Exception):
    pass


def tmp_dir() -> Path:
    import paths
    d = paths.data_dir() / "notetaker" / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def marker_path(sid: str) -> Path:
    if not store.valid_id(sid):
        raise ValueError("bad session id")
    return tmp_dir() / f"{sid}.json"


def read_marker(sid: str) -> dict | None:
    try:
        return json.loads(marker_path(sid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_marker(m: dict) -> None:
    p = marker_path(m["id"])
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m), encoding="utf-8")
    os.replace(tmp, p)


def wipe_audio(sid: str) -> list[str]:
    """Delete every audio file of a session and its marker. Returns the names that could NOT be deleted."""
    left = []
    d = tmp_dir()
    for p in list(d.glob(f"{sid}-*.wav")) + [d / f"{sid}.json"]:
        for attempt in range(3):                     # Windows can hold a file for a moment
            try:
                p.unlink()
                break
            except FileNotFoundError:
                break
            except OSError:
                if attempt == 2:
                    left.append(p.name)
                else:
                    time.sleep(0.2)
    return left


def sweep(now: float | None = None) -> dict:
    """Delete audio that must not exist: sessions whose transcript is already stored, audio without a
    marker, and anything older than STALE_AUDIO_DAYS."""
    now = now or time.time()
    d = tmp_dir()
    removed = 0
    for m in list(d.glob("*.json")):
        sid = m.stem
        if not store.valid_id(sid):
            continue
        age_days = (now - m.stat().st_mtime) / 86400
        if store.exists(sid) or age_days > STALE_AUDIO_DAYS:
            removed += 1
            wipe_audio(sid)
    for w in list(d.glob("*.wav")):
        sid = w.name.rsplit("-", 1)[0]
        if not store.valid_id(sid) or not marker_path(sid).exists():
            try:
                w.unlink()
                removed += 1
            except OSError:
                pass
    return {"removed": removed}


def pending() -> list[str]:
    out = []
    for m in sorted(tmp_dir().glob("*.json")):
        mk = read_marker(m.stem)
        if mk and mk.get("state") in ("pending", "failed") and not store.exists(m.stem):
            out.append(m.stem)
    return out


class Pipeline:
    def __init__(self, transcriber=None, engine_label: str = "", diarizer=None, summarizer=None):
        self._tr, self._engine = transcriber, engine_label
        self._diarizer = diarizer or _diar.diarize
        self._summarizer = summarizer or _sum.summarize
        self.state = {"id": "", "stage": "idle", "progress": 0.0, "error": ""}

    def _set(self, **kw):
        self.state.update(kw)

    def process(self, sid: str) -> dict:
        """Run the whole pipeline for one session. Returns the stored document. Raises PipelineError."""
        mk = read_marker(sid)
        if not mk:
            raise PipelineError("session not found")
        self._set(id=sid, stage="transcribing", progress=0.0, error="")
        mk["state"] = "processing"
        write_marker(mk)
        try:
            doc = self._transcribe_and_store(mk)
        except Exception as e:
            mk["state"], mk["error"] = "failed", str(e)
            write_marker(mk)
            self._set(stage="failed", error=str(e))
            raise
        left = wipe_audio(sid)                               # <- the audio is gone before anything else happens
        if left:
            log.warning("notetaker: could not delete %s now; the next sweep will", left)
        self._set(stage="done", progress=1.0)
        if settings.get("p7_summarize"):
            self.summarize(sid)
        return store.get(sid) or doc

    def _transcribe_and_store(self, mk: dict) -> dict:
        sid = mk["id"]
        if self._tr is None:
            try:
                self._tr, self._engine = default_transcriber()
            except Exception as e:
                raise PipelineError(f"No speech engine is available ({e}). The audio is kept; try again later.")
        loaded = {}
        for name, fn in (mk.get("tracks") or {}).items():
            p = tmp_dir() / fn
            audio = read_wav(p) if p.is_file() else None
            loaded[name] = (audio, segments(audio) if audio is not None and audio.size else [])
        nseg = sum(len(v[1]) for v in loaded.values())
        done, failed, tracks = 0, 0, {}
        for name, (audio, segs) in loaded.items():
            if not segs:
                tracks[name] = ([], audio)
                continue
            rows, f = transcribe_segments(
                audio, segs, self._tr, mk.get("language") or None,
                progress=lambda i, n, d=done: self._set(progress=round(0.9 * (d + i) / max(1, nseg), 3)))
            failed += f
            done += len(segs)
            tracks[name] = (rows, audio)
        if nseg and failed == nseg:
            raise PipelineError("The speech engine failed on every segment. The audio is kept; try again later.")
        mic_rows = tracks.get("mic", ([], None))[0]
        sys_rows, sys_audio = tracks.get("system", ([], None))
        diarized = False
        if sys_rows and settings.get("p7_diarize") and _diar.available():
            self._set(stage="speakers")
            try:
                turns = self._diarizer(sys_audio)
                merge.assign_speakers(sys_rows, turns)
                diarized = any(s.get("spk") is not None for s in sys_rows)
            except Exception as e:
                log.warning("notetaker: speaker separation skipped: %s", e)
        lines, speakers = merge.build_lines(mic_rows, sys_rows, diarized=diarized)
        doc = {
            "version": 1, "id": sid, "title": mk.get("title") or "Meeting", "started": mk["started"],
            "ended": mk["started"] + mk.get("duration", 0), "duration": mk.get("duration", 0),
            "language": mk.get("language") or "", "engine": self._engine, "diarized": diarized,
            "system_audio": bool(mk.get("system_audio")), "speakers": speakers, "lines": lines,
            "summary": None, "skipped_segments": failed,
        }
        store.save(doc)
        return doc

    def summarize(self, sid: str) -> dict | None:
        """Summary + to-dos from the stored transcript text. Never raises (the transcript is already safe)."""
        doc = store.get(sid)
        if not doc:
            return None
        self._set(id=sid, stage="summarizing", error="")
        try:
            s = self._summarizer(doc.get("lines") or [], settings.get("p7_summary_provider"))
            s["ts"] = int(time.time())
            store.update(sid, summary=s)
            self._set(stage="done")
            return s
        except Exception as e:
            log.warning("notetaker: summary failed: %s", e)
            self._set(stage="done", error=f"Summary failed: {e}")
            return None
