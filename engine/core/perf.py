"""Lyra - per-turn stage timing.

One "turn" starts the moment the user stops speaking (or sends typed text) and ends when Lyra has
finished answering. Each stage adds its own time to the turn:

    stt         speech-to-text (Whisper)
    llm_route   LLM routing: the agent planner and router.classify
    llm_chat    the chat model's answer (brain.ask_brain)
    action      running the action (open app, volume, media key, ...)
    tts_wait    asking for speech -> first sound (synthesis + start of playback)

and two points on the clock, both counted from the end of speech:

    to_audio    until the first sound of the reply  (the number you actually feel)
    total       until Lyra finished speaking

At the end of the turn one line goes to the backend log and one JSON line to %TEMP%\\lyra-timing.jsonl.
`python perf.py` prints the averages of that file, split by path (fast / llm / local).

Everything here is best effort: timing must never break a turn, so no function raises.
Switch it off with TIMING_LOGS = False in config.py.
"""

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager

LOG_PATH = os.path.join(tempfile.gettempdir(), "lyra-timing.jsonl")

_lock = threading.Lock()
_turn = None


def _enabled() -> bool:
    try:
        from engine.core import config
        return bool(getattr(config, "TIMING_LOGS", True))
    except Exception:
        return True


class _Turn:
    def __init__(self, source: str):
        self.source = source
        self.t0 = time.monotonic()
        self.text = ""
        self.path = ""
        self.stages: dict = {}     # stage -> seconds (summed over the turn)
        self.points: dict = {}     # point -> seconds since t0 (the first time only)
        self.open: set = set()     # stages with a span running right now (spans do not nest)
        self.counts: dict = {}     # phase L: llm_calls, llm_prompt_chars


def begin(source: str, text: str = "") -> None:
    """The user stopped speaking (or sent a message). Starts a fresh turn."""
    global _turn
    if not _enabled():
        return
    with _lock:
        _turn = _Turn(source)
        _turn.text = text or ""


def active() -> bool:
    return _turn is not None


def set_text(text: str) -> None:
    t = _turn
    if t is not None and text and not t.text:
        t.text = text


def set_path(path: str) -> None:
    """Label the turn: 'fast' (local fast path). Without a label it is 'llm' if an LLM stage ran, else 'local'."""
    t = _turn
    if t is not None:
        t.path = path


def add(stage: str, seconds: float) -> None:
    t = _turn
    if t is not None:
        t.stages[stage] = t.stages.get(stage, 0.0) + max(0.0, seconds)


def count(name: str, n: int = 1) -> None:
    """Phase L: add n to a per-turn counter (llm_calls, llm_prompt_chars). Best effort, never raises."""
    try:
        t = _turn
        if t is not None:
            t.counts[name] = t.counts.get(name, 0) + int(n)
    except Exception:
        pass


def mark(point: str) -> None:
    """Remember how long after the end of speech `point` happened (first time only)."""
    t = _turn
    if t is not None and point not in t.points:
        t.points[point] = time.monotonic() - t.t0


@contextmanager
def span(stage: str):
    """Time a block and add it to `stage`. A span inside a span of the same stage is not counted twice."""
    t = _turn
    if t is None or stage in t.open:
        yield
        return
    t.open.add(stage)
    start = time.monotonic()
    try:
        yield
    finally:
        t.open.discard(stage)
        t.stages[stage] = t.stages.get(stage, 0.0) + (time.monotonic() - start)


def summary(t: "_Turn", spoken: bool = True) -> dict:
    now = time.monotonic() - t.t0
    path = t.path or ("llm" if ("llm_route" in t.stages or "llm_chat" in t.stages) else "local")
    first_audio = t.points.get("tts_first_audio")
    asked = t.points.get("tts_request")
    tts_wait = (first_audio - asked) if (first_audio is not None and asked is not None) else None
    return {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": t.source,
        "text": t.text[:80],
        "path": path,
        "spoken": bool(spoken and first_audio is not None),
        "stt_ms": round(t.stages["stt"] * 1000) if "stt" in t.stages else None,
        "llm_route_ms": round(t.stages["llm_route"] * 1000) if "llm_route" in t.stages else None,
        "llm_first_token_ms": round(t.points["llm_first_token"] * 1000) if "llm_first_token" in t.points else None,
        "llm_chat_ms": round(t.stages["llm_chat"] * 1000) if "llm_chat" in t.stages else None,
        "action_ms": round(t.stages["action"] * 1000) if "action" in t.stages else None,
        "tts_wait_ms": round(tts_wait * 1000) if tts_wait is not None else None,
        "to_audio_ms": round(first_audio * 1000) if first_audio is not None else None,
        "total_ms": round(now * 1000),
        "llm_calls": t.counts.get("llm_calls", 0),
        "llm_prompt_chars": t.counts.get("llm_prompt_chars", 0),
    }


def format_line(s: dict) -> str:
    def f(key):
        v = s.get(key)
        return "-" if v is None else f"{v}ms"
    chat = f("llm_chat_ms")
    if s.get("llm_first_token_ms") is not None:
        chat += f" (first word {s['llm_first_token_ms']}ms)"
    return (f"TIMING [{s['source']}] {s['text']!r} path={s['path']} | speech end @0 | stt {f('stt_ms')} | "
            f"llm route {f('llm_route_ms')} | llm chat {chat} | action {f('action_ms')} | "
            f"tts wait {f('tts_wait_ms')} | first audio @{f('to_audio_ms')} | done @{f('total_ms')} | "
            f"llm calls {s.get('llm_calls', 0)} ({s.get('llm_prompt_chars', 0)} prompt chars)")


def finish(spoken: bool = True) -> None:
    """End the turn: log one line and append one JSON line. Does nothing when no turn is running."""
    global _turn
    with _lock:
        t, _turn = _turn, None
    if t is None:
        return
    try:
        s = summary(t, spoken)
        if not s["spoken"] and not t.stages:
            return      # nothing was measured (an ignored utterance): not worth a line
        try:
            from engine.bridge import ui
            ui.log.info(format_line(s))
        except Exception:
            pass
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    except Exception:
        pass


def report(path: str = "") -> str:
    """Averages of every turn in the log, split by path."""
    path = path or LOG_PATH
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    except OSError:
        return f"No timing log yet ({path})."
    if not rows:
        return "The timing log is empty."
    keys = ("stt_ms", "llm_route_ms", "llm_chat_ms", "action_ms", "tts_wait_ms", "to_audio_ms", "total_ms")
    out = [f"{len(rows)} turns in {path}", ""]
    for p in sorted({r["path"] for r in rows}):
        group = [r for r in rows if r["path"] == p]
        cells = []
        for k in keys:
            vals = sorted(r[k] for r in group if r.get(k) is not None)
            if vals:
                cells.append(f"{k[:-3]} avg {sum(vals) // len(vals)} / median {vals[len(vals) // 2]}")
        out.append(f"[{p}] {len(group)} turns: " + "; ".join(cells))
    return "\n".join(out)


if __name__ == "__main__":
    print(report())
