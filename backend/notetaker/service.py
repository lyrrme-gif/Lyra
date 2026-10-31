"""Lyra notetaker - the one object the API talks to: recorder + background processing + housekeeping."""
from __future__ import annotations

import logging
import threading

from . import REMINDER, diarize, pipeline, settings, store
from .recorder import NotAcknowledged, Recorder

try:
    import ui
    log = ui.log
except Exception:
    log = logging.getLogger("lyra.notetaker")

_lock = threading.RLock()
recorder = Recorder(max_seconds=lambda: float(settings.get("p7_max_hours")) * 3600)
_pipe = pipeline.Pipeline()
_worker: threading.Thread | None = None
_queue: list[str] = []
_started = False


def _toast(text: str) -> None:
    try:
        import ui_bridge
        ui_bridge.push_notification("reminder", "reminder", "Lyra", text, force=True)
    except Exception:
        pass


def _drain():
    global _worker
    while True:
        with _lock:
            if not _queue:
                _worker = None
                return
            sid = _queue.pop(0)
        try:
            _pipe.process(sid)
            _toast("Meeting notes are ready")
        except Exception as e:
            log.warning("notetaker: processing %s failed: %s", sid, e)


def enqueue(sid: str) -> None:
    global _worker
    with _lock:
        busy = _pipe.state.get("id") == sid and _pipe.state.get("stage") in ("transcribing", "speakers", "summarizing")
        if sid not in _queue and not busy:
            _queue.append(sid)
        if _worker is None:
            _worker = threading.Thread(target=_drain, name="lyra-notes-proc", daemon=True)
            _worker.start()


def _push_call(recording: bool, elapsed: float = 0.0) -> None:
    """Push the recording state to the island (best effort: the island also re-checks every 30 s)."""
    try:
        import time
        import ui_bridge
        ui_bridge.push_call_record(recording, int((time.time() - elapsed) * 1000) if recording else 0)
    except Exception:
        pass


def start(title="", acknowledged=False, language="") -> dict:
    st = recorder.start(title, acknowledged, language)
    _toast("Recording the call - make sure everyone knows")
    _push_call(True, float(st.get("elapsed") or 0) if isinstance(st, dict) else 0.0)
    return st


def stop() -> dict:
    mk = recorder.stop()
    _push_call(False)
    if mk:
        enqueue(mk["id"])
    return {"stopped": bool(mk), "id": mk["id"] if mk else None}


def _on_auto_stop(mk: dict) -> None:
    _push_call(False)
    enqueue(mk["id"])


recorder.on_auto_stop = _on_auto_stop


def retry(sid: str) -> bool:
    if sid in pipeline.pending() or pipeline.read_marker(sid):
        enqueue(sid)
        return True
    return False


def startup() -> None:
    """Once per backend run: purge old transcripts, sweep stray audio, finish interrupted sessions."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    try:
        store.purge_expired(int(settings.get("p7_retention_days")))
        pipeline.sweep()
        for sid in pipeline.pending():
            enqueue(sid)
    except Exception as e:
        log.warning("notetaker startup housekeeping failed: %s", e)


def status() -> dict:
    return {
        "recorder": recorder.status(),
        "processing": dict(_pipe.state), "queued": list(_queue),
        "pending": pipeline.pending(),
        "reminder": REMINDER,
        "settings": settings.snapshot(),
        "diarization": diarize.manager().status(),
    }
