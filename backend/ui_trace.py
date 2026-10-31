"""Lyra - UI side of the live trace.

The UI (island, settings, wheel, caption, mascot) reports what you clicked, what opened/appeared,
what is animating and what failed with  POST /trace  (see ui/src/core/trace.ts). This module prints
those lines in the backend terminal and ui.log, in the same "[trace] ..." stream as dbg.py, so one log
shows both what happened on screen and what the backend did at the same moment:

    [trace] ui:island:click: left press on #island | inside island ...
    [trace] ui:island:state: mode: compact -> expanded | view: overview -> music   <= caused by left click ...
    [trace] http: POST /controls/volume  keys: level
    [trace] push: UI <- backend: state=thinking

Also here: what the backend pushes to the UI (event_out) and what the UI asks the backend for (http_in).

Switch off with LYRA_TRACE=0 (same switch as dbg.py).
Privacy: chat / dictation / clipboard text is never written here, only its length and the key names.
"""
from __future__ import annotations

import re
import threading
import time

_ALLOWED_WIN = {"island", "settings", "wheel", "caption", "mascot", "other"}
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

# Never logged at all (the UI polls these several times a second).
_SILENT_PATHS = {"/trace", "/events", "/status", "/record/status", "/pidprobe", "/settings-log"}   # only the very fastest polls; everything else is shown
# Request bodies whose VALUES are private: only the key names are logged.
_PRIVATE_PATH = re.compile(r"(chat|dictation|clipboard|memory|secret|key|token|password|translate|search|notify|google|smarthome|snippets|dictionary|settings/llm)", re.I)
# Events the backend pushes very often: logged at most once per few seconds.
_FREQUENT = {"stats", "timers", "controls", "status", "appvolume", "todos", "schedule"}
_QUIET = {"wave", "levels", "heartbeat"}
_PRIVATE_EVENT = {"chat", "reply", "notify", "translate", "web_search", "mochi_sound"}

_lock = threading.Lock()
_last: dict[str, float] = {}


def _dbg():
    try:
        import dbg
        return dbg if dbg.on() else None
    except Exception:
        return None


def _limited(key: str, every: float) -> bool:
    """True if this line may be written now (at most once per `every` seconds per key)."""
    now = time.monotonic()
    with _lock:
        if now - _last.get(key, 0.0) < every:
            return False
        _last[key] = now
        if len(_last) > 800:
            _last.clear()
    return True


def _clean(s: object, limit: int = 400) -> str:
    t = _CTRL.sub(" ", str(s)).replace("\r", " ").replace("\n", " | ")
    return t if len(t) <= limit else t[:limit] + "..."


# --------------------------------------------------------------------------------------------
# UI -> backend: the lines the page sent with POST /trace
# --------------------------------------------------------------------------------------------
def ingest(body: object) -> int:
    """Write the lines of one /trace request. Returns how many were accepted."""
    d = _dbg()
    if d is None or not isinstance(body, dict):
        return 0
    win = body.get("win")
    win = win if win in _ALLOWED_WIN else "other"
    lines = body.get("lines")
    if not isinstance(lines, list):
        return 0
    now_ms = time.time() * 1000.0
    n = 0
    for item in lines[:200]:
        if not isinstance(item, dict):
            continue
        tag = re.sub(r"[^A-Za-z0-9_-]", "", str(item.get("tag") or "event"))[:16] or "event"
        msg = _clean(item.get("msg") or "")
        try:
            late = now_ms - float(item.get("t"))
        except (TypeError, ValueError):
            late = 0.0
        if late > 1500:                     # the page sat on it (backend was busy / starting): say so
            msg += f"   (reported {late / 1000:.1f}s late)"
        d.ev(f"ui:{win}:{tag}", msg)
        n += 1
    return n


# --------------------------------------------------------------------------------------------
# UI asks the backend for something (every HTTP request that is not background polling)
# --------------------------------------------------------------------------------------------
def http_in(method: str, path: str, body: object = None) -> None:
    d = _dbg()
    if d is None or path in _SILENT_PATHS:
        return
    if method == "GET" and not _limited(f"get:{path}", 5.0):
        return
    extra = ""
    if method == "POST" and isinstance(body, dict):
        keys = list(body.keys())[:8]
        if _PRIVATE_PATH.search(path):
            extra = f"  keys: {', '.join(map(str, keys))} (values hidden)"
        else:
            bits = []
            for k in keys:
                v = body[k]
                bits.append(f"{k}={'{...}' if isinstance(v, (dict, list)) else _clean(v, 24)}")
            extra = "  " + ", ".join(bits)
    d.ev("http", f"UI asked: {method} {path}{extra}")


def http_error(method: str, path: str, code: int, obj: object = None) -> None:
    """A request the backend answered with an error (4xx/5xx): always logged."""
    d = _dbg()
    if d is None or path.split("?")[0] == "/trace":
        return
    why = ""
    if isinstance(obj, dict) and obj.get("error"):
        why = f" ({_clean(obj.get('error'), 120)})"
    d.ev("http", f"!! backend answered {code} to {method} {path.split('?')[0]}{why}")


# --------------------------------------------------------------------------------------------
# backend -> UI: every event pushed to the island / windows
# --------------------------------------------------------------------------------------------
def event_out(payload: dict) -> None:
    d = _dbg()
    if d is None or not isinstance(payload, dict):
        return
    typ = str(payload.get("type") or "?")
    if typ in _QUIET:
        return
    if typ in _FREQUENT and not _limited(f"ev:{typ}:{payload.get('kind', '')}", 4.0):
        return
    f: list[str] = []
    for k in ("state", "view", "action", "kind", "phase", "open", "on", "app", "source", "provider",
              "percent", "level", "muted", "mic", "recording"):
        v = payload.get(k)
        if v is None or v == "" or (typ == "notify" and k == "sender"):
            continue
        f.append(f"{k}={_clean(v, 30)}")
    if typ in _PRIVATE_EVENT:
        text = payload.get("text") if isinstance(payload.get("text"), str) else payload.get("answer")
        if isinstance(text, str) and text:
            f.append(f"text={len(text)} chars (hidden)")
        res = payload.get("results")
        if isinstance(res, list):
            f.append(f"results={len(res)}")
    else:
        if isinstance(payload.get("title"), str):
            f.append(f"title={_clean(payload['title'], 30)!r}")
        if isinstance(payload.get("items"), list):
            f.append(f"items={len(payload['items'])}")
    d.ev("push", f"backend -> UI: {typ}" + (" " + " ".join(f) if f else ""))
