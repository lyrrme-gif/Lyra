"""Activity log: what Lyra did for you, kept on this PC (data folder, activity_log.jsonl).

* record()    - called after every command Lyra answered with an action
* set_undo()  - an ability that can be reversed leaves a hint; the next record() stores it
* recent()    - used by "what did I do today" and "show my activity"
* last_undoable() / mark_undone() - used by "undo that"
Only the command text and Lyra's short reply are stored, nothing else, and never anything typed
into other apps. Delete the file to clear it ("clear my activity log").
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_pending_undo: dict | None = None
_pending_at = 0.0
_MAX_LINES = 2000


def _file() -> Path:
    import paths
    return paths.data_path("activity_log.jsonl")


def set_undo(hint: dict | None) -> None:
    global _pending_undo, _pending_at
    _pending_undo, _pending_at = hint, time.time()


def record(kind: str, command: str, reply: str) -> None:
    global _pending_undo
    try:
        hint, _pending_undo = _pending_undo, None
        if hint and time.time() - _pending_at > 5:
            hint = None  # left over from something else: never attach it to this entry
        entry = {"id": int(time.time() * 1000), "t": int(time.time()), "kind": kind,
                 "cmd": (command or "")[:200], "reply": (reply or "")[:240]}
        if hint:
            entry["undo"] = hint
        with _lock:
            f = _file()
            with f.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            _trim(f)
    except Exception:
        pass


def _trim(f: Path) -> None:
    try:
        if f.stat().st_size < 400_000:
            return
        lines = f.read_text(encoding="utf-8").splitlines()
        f.write_text("\n".join(lines[-_MAX_LINES:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def _all() -> list[dict]:
    out = []
    try:
        for line in _file().read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        pass
    return out


def recent(since: float = 0.0, until: float | None = None) -> list[dict]:
    return [e for e in _all() if e.get("t", 0) >= since and (until is None or e.get("t", 0) < until)]


def last_undoable(max_age: int = 1800) -> dict | None:
    now = time.time()
    for e in reversed(_all()):
        if e.get("undo") and not e.get("undone") and now - e.get("t", 0) <= max_age:
            return e
        if now - e.get("t", 0) > max_age:
            break
    return None


def mark_undone(entry_id: int) -> None:
    try:
        with _lock:
            f = _file()
            rows = _all()
            for e in rows:
                if e.get("id") == entry_id:
                    e["undone"] = True
            f.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in rows) + "\n", encoding="utf-8")
    except Exception:
        pass


def clear() -> int:
    n = len(_all())
    try:
        _file().write_text("", encoding="utf-8")
    except Exception:
        pass
    return n
