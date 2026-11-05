"""Lyra — timers, stopwatch and Pomodoro.

Voice / chat examples (all handled before the LLM, so they are instant and offline):
    "set a timer for 10 minutes"      "10 minute pasta timer"      "timer 90 seconds"
    "remind me in 20 minutes to call mum"
    "25 min focus"   "start a pomodoro"   "pomodoro 50 minutes, 10 minute break"
    "start a stopwatch"   "pause the stopwatch"   "stop the stopwatch"
    "how much time is left"   "pause the timer"   "cancel the timer"   "stop the pomodoro"

How it reaches the screen
    Every change is published as one {"type": "timers", "items": [...], "now": ms}
    event (see ui_bridge.py). Items carry absolute times (`end_ms` / `start_ms`), so
    the island counts down by itself every second — no per-second traffic.

When something ends
    * a short chime plays (winsound — separate from the TTS mixer, so it never
      fights with Lyra's voice, and it plays even when a game is in front),
    * a toast is pushed to the notch ("Timer done" / "Focus complete"...), which the
      island keeps silent in game mode.
    Nothing is spoken from the timer thread on purpose: voice.speak() shares one
    pygame mixer with the conversation and has no lock.

Pomodoro: focus -> short break -> focus ... with a long break after every 4th focus.
It keeps cycling until you say "stop the pomodoro".

Timers live in memory only: they are gone when Lyra restarts.
"""

from __future__ import annotations

import logging
import math
import re
import struct
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable

log = logging.getLogger("lyra")

MAX_SECONDS = 24 * 3600
MAX_ITEMS = 8
POMO_FOCUS_S = 25 * 60
POMO_BREAK_S = 5 * 60
POMO_LONG_BREAK_S = 15 * 60
POMO_ROUNDS_BEFORE_LONG = 4
AWAIT_DURATION_S = 25          # how long "for how long?" stays open


def _pref_minutes(key: str, fallback_s: float) -> float:
    """Pomodoro length from Settings (minutes → seconds); the constants above are the fallback."""
    try:
        from engine.core import settings_store
        return float(settings_store.get(key)) * 60.0
    except Exception:
        return float(fallback_s)


def _pomo_focus_s() -> float:
    return _pref_minutes("pomo_focus_min", POMO_FOCUS_S)


def _pomo_break_s() -> float:
    return _pref_minutes("pomo_break_min", POMO_BREAK_S)


def _pomo_long_break_s() -> float:
    return _pref_minutes("pomo_long_break_min", POMO_LONG_BREAK_S)

_lock = threading.RLock()
_items: list[dict] = []
_next_id = 1
_thread: threading.Thread | None = None
_awaiting: dict | None = None   # {"label": str, "until": float} after "set a timer" with no duration

_publisher: Callable[[dict], None] | None = None
_notifier: Callable[[str, str, str, str], None] | None = None   # (app, kind, sender, text)
_player: Callable[[str], None] | None = None                    # test hook; default = winsound


def set_publisher(fn: Callable[[dict], None] | None) -> None:
    global _publisher
    _publisher = fn


def set_notifier(fn: Callable[[str, str, str, str], None] | None) -> None:
    global _notifier
    _notifier = fn


# ── State ────────────────────────────────────────────────────────────────────

def _now() -> float:
    return time.time()


def _public(it: dict, t: float) -> dict:
    running = it["state"] == "running"
    out = {
        "id": it["id"], "kind": it["kind"], "label": it["label"], "phase": it["phase"],
        "round": it["round"], "state": it["state"],
        "total_ms": int(it["total"] * 1000) if it["kind"] != "stopwatch" else None,
        "end_ms": None, "remaining_ms": None, "start_ms": None, "elapsed_ms": None,
    }
    if it["kind"] == "stopwatch":
        if running:
            out["start_ms"] = int(it["start"] * 1000)
            out["elapsed_ms"] = int((t - it["start"]) * 1000)
        else:
            out["elapsed_ms"] = int(it["elapsed"] * 1000)
    else:
        if running:
            out["end_ms"] = int(it["end"] * 1000)
            out["remaining_ms"] = max(0, int((it["end"] - t) * 1000))
        else:
            out["remaining_ms"] = int(it["left"] * 1000)
    return out


def snapshot() -> dict:
    with _lock:
        t = _now()
        return {"items": [_public(i, t) for i in _items], "now": int(t * 1000)}


def _publish() -> None:
    fn = _publisher
    if fn is None:
        return
    try:
        fn(snapshot())
    except Exception as e:  # a UI problem must never break a timer
        log.warning("timers: publish failed: %s", e)


def _notify(sender: str, text: str) -> None:
    fn = _notifier
    if fn is None:
        return
    try:
        fn("reminder", "reminder", sender, text)
    except Exception as e:
        log.warning("timers: notify failed: %s", e)


def _new(kind: str, label: str = "", **extra) -> dict:
    global _next_id
    it = {
        "id": _next_id, "kind": kind, "label": label, "state": "running", "phase": "",
        "round": 1, "total": 0.0, "end": None, "left": 0.0, "start": None, "elapsed": 0.0,
        "reminder": False, "focus_s": _pomo_focus_s(), "break_s": _pomo_break_s(),
    }
    _next_id += 1
    it.update(extra)
    return it


# ── Public API ───────────────────────────────────────────────────────────────

def start_timer(seconds: float, label: str = "", reminder: bool = False) -> dict | None:
    seconds = max(1.0, min(float(seconds), MAX_SECONDS))
    with _lock:
        if len(_items) >= MAX_ITEMS:
            return None
        it = _new("timer", label, total=seconds, end=_now() + seconds, reminder=reminder)
        _items.append(it)
        _ensure_thread()
    _publish()
    return it


def start_pomodoro(focus_s: float | None = None, break_s: float | None = None) -> dict | None:
    focus_s = _pomo_focus_s() if focus_s is None else focus_s
    break_s = _pomo_break_s() if break_s is None else break_s
    focus_s = max(1.0, min(float(focus_s), MAX_SECONDS))
    break_s = max(1.0, min(float(break_s), MAX_SECONDS))
    with _lock:
        if len(_items) >= MAX_ITEMS:
            return None
        it = _new("pomodoro", "", phase="focus", total=focus_s, end=_now() + focus_s,
                  focus_s=focus_s, break_s=break_s)
        _items.append(it)
        _ensure_thread()
    _publish()
    return it


def start_stopwatch() -> dict | None:
    with _lock:
        if len(_items) >= MAX_ITEMS:
            return None
        it = _new("stopwatch", "", start=_now())
        _items.append(it)
    _publish()
    return it


def cancel(item_id: int) -> dict | None:
    with _lock:
        for it in _items:
            if it["id"] == item_id:
                _items.remove(it)
                break
        else:
            return None
    _publish()
    return it


def pause(item_id: int) -> bool:
    with _lock:
        it = _get(item_id)
        if not it or it["state"] != "running":
            return False
        t = _now()
        if it["kind"] == "stopwatch":
            it["elapsed"], it["start"] = t - it["start"], None
        else:
            it["left"], it["end"] = max(0.0, it["end"] - t), None
        it["state"] = "paused"
    _publish()
    return True


def resume(item_id: int) -> bool:
    with _lock:
        it = _get(item_id)
        if not it or it["state"] != "paused":
            return False
        t = _now()
        if it["kind"] == "stopwatch":
            it["start"], it["elapsed"] = t - it["elapsed"], 0.0
        else:
            it["end"], it["left"] = t + it["left"], 0.0
            _ensure_thread()
        it["state"] = "running"
    _publish()
    return True


def _get(item_id: int) -> dict | None:
    return next((i for i in _items if i["id"] == item_id), None)


def _remaining(it: dict, t: float | None = None) -> float:
    if it["state"] == "running":
        return max(0.0, it["end"] - (t if t is not None else _now()))
    return it["left"]


def _elapsed(it: dict, t: float | None = None) -> float:
    if it["state"] == "running":
        return max(0.0, (t if t is not None else _now()) - it["start"])
    return it["elapsed"]


# ── Scheduler ────────────────────────────────────────────────────────────────

def _ensure_thread() -> None:
    """Called with _lock held. The thread only lives while a countdown is running."""
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_loop, name="lyra-timers", daemon=True)
        _thread.start()


def _loop() -> None:
    global _thread
    while True:
        time.sleep(0.2)
        due: list[dict] = []
        with _lock:
            t = _now()
            running = False
            for it in _items:
                if it["kind"] != "stopwatch" and it["state"] == "running":
                    running = True
                    if it["end"] <= t:
                        due.append(it)
            if not running:
                _thread = None
                return
        for it in due:
            try:
                _fire(it)
            except Exception as e:
                log.warning("timers: firing failed: %s", e)


def _fire(it: dict) -> None:
    with _lock:
        if it not in _items or it["state"] != "running":
            return
        t = _now()
        if it["end"] > t:
            return
        if it["kind"] == "timer":
            _items.remove(it)
            if it["reminder"]:
                sender, text, sound = "Reminder", it["label"] or "Time's up", "ding"
            else:
                sender = f"{it['label']} timer done" if it["label"] else "Timer done"
                text, sound = _say(it["total"]), "ding"
        else:  # pomodoro: roll into the next phase and keep going
            if it["phase"] == "focus":
                long_break = it["round"] % POMO_ROUNDS_BEFORE_LONG == 0
                it["phase"] = "long" if long_break else "break"
                dur = _pomo_long_break_s() if long_break else it["break_s"]
                sender, text = "Focus complete", f"Break time · {_say(dur)}"
            else:
                it["round"] += 1
                it["phase"] = "focus"
                dur = it["focus_s"]
                sender, text = "Break over", f"Back to focus · {_say(dur)}"
            it["total"], it["end"] = float(dur), t + dur
            sound = "soft"
    log.info("timers: %s — %s", sender, text)
    _notify(sender, text)          # toast first, then the state change (see island.onTimers)
    _chime(sound)
    _publish()


# ── Sound ────────────────────────────────────────────────────────────────────

_NOTES = {
    # (frequency Hz or 0 = rest, seconds)
    "ding": [(1318.5, 0.28), (1760.0, 0.42), (0, 0.45)] * 3,
    "soft": [(784.0, 0.22), (1046.5, 0.45)],
    "alarm": [(1174.7, 0.16), (1568.0, 0.16), (1174.7, 0.16), (1568.0, 0.16), (0, 0.4)] * 6,   # ~6 s of beeps
}


def _wav_path(name: str) -> Path:
    return Path(tempfile.gettempdir()) / f"lyra-timer-{name}.wav"


def _make_wav(name: str, rate: int = 22050) -> Path:
    path = _wav_path(name)
    if path.exists() and path.stat().st_size > 1000:
        return path
    frames = bytearray()
    for freq, dur in _NOTES[name]:
        n = int(rate * dur)
        for i in range(n):
            if freq:
                env = math.exp(-4.5 * i / n) * min(1.0, i / (rate * 0.006))
                s = math.sin(2 * math.pi * freq * i / rate) + 0.3 * math.sin(4 * math.pi * freq * i / rate)
                v = 0.5 * env * s / 1.3
            else:
                v = 0.0
            frames += struct.pack("<h", int(v * 32767))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


def _chime(name: str) -> None:
    try:
        from engine.core import settings_store
        if not settings_store.get("timer_alarm_sound", True):
            return                       # alarm sound switched off in Settings
    except Exception:
        pass
    try:
        path = str(_make_wav(name))
        if _player is not None:
            _player(path)
            return
        import winsound
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception as e:
        log.warning("timers: could not play chime: %s", e)


# ── Speech helpers ───────────────────────────────────────────────────────────

def _say(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h != 1 else ''}")
    if m:
        parts.append(f"{m} minute{'s' if m != 1 else ''}")
    if sec and not h:
        parts.append(f"{sec} second{'s' if sec != 1 else ''}")
    return " ".join(parts) or "0 seconds"


# ── Parsing ──────────────────────────────────────────────────────────────────

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}

_WAKE_RE = re.compile(r"^(?:(?:hey|ok|okay|yo)\s+)?lyra\b[\s,:\-]*", re.I)
_POLITE_RE = re.compile(
    r"\b(?:please|can you|could you|would you|will you|i want you to|i need you to|go ahead and)\b")


def _digitize(t: str) -> str:
    toks, out, i = t.split(), [], 0
    while i < len(toks):
        w = toks[i]
        if w in _TENS:
            v = _TENS[w]
            if i + 1 < len(toks) and toks[i + 1] in _UNITS and 0 < _UNITS[toks[i + 1]] < 10:
                v += _UNITS[toks[i + 1]]
                i += 1
            out.append(str(v))
        elif w in _UNITS:
            out.append(str(_UNITS[w]))
        else:
            out.append(w)
        i += 1
    return " ".join(out)


def _clean(text: str) -> str:
    t = (text or "").lower().strip()
    t = _WAKE_RE.sub("", t)
    t = re.sub(r"[-–—]", " ", t)
    t = re.sub(r"[^\w\s.:]", " ", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)       # keep decimals ("1.5 hours"), drop sentence dots
    t = _POLITE_RE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return _digitize(t)


_UNIT_RE = r"(?:hours?|hrs?|h|minutes?|mins?|m|seconds?(?!\s+timer)|secs?|s)"
_DUR_RE = re.compile(rf"(\d+(?:\.\d+)?|an?)\s*({_UNIT_RE})\b")
_UNIT_S = {"h": 3600, "m": 60, "s": 1}


def _extract_duration(t: str) -> tuple[int | None, str]:
    """Total seconds named in `t` and `t` with those words removed."""
    total = 0.0
    found = False

    def take(pattern: str, secs) -> None:
        nonlocal t, total, found
        m = re.search(pattern, t)
        if not m:
            return
        total += secs(m)
        found = True
        t = (t[:m.start()] + " " + t[m.end():]).strip()

    take(r"\bquarter (?:of an? )?hour\b", lambda m: 900)
    take(r"\ban? hour and a half\b", lambda m: 5400)
    take(r"\bhalf an? hour\b|\bhalf hour\b", lambda m: 1800)
    take(r"\bhalf an? minute\b|\bhalf minute\b", lambda m: 30)
    take(r"(\d+) and a half (hours?|hrs?)\b", lambda m: (int(m.group(1)) + 0.5) * 3600)
    take(r"(\d+) and a half (minutes?|mins?)\b", lambda m: (int(m.group(1)) + 0.5) * 60)
    take(r"\b(\d+)\s*h\s*(\d{1,2})\b(?!\s*(?:s\b|sec|m\b|min))", lambda m: int(m.group(1)) * 3600 + int(m.group(2)) * 60)
    take(r"\b(\d+)\s*(?:hours?|hrs?)\s+(?:and\s+)?(\d{1,2})\b(?!\s*(?:h|m|s|min|sec))",
         lambda m: int(m.group(1)) * 3600 + int(m.group(2)) * 60)
    while True:
        m = _DUR_RE.search(t)
        if not m:
            break
        n = 1.0 if m.group(1) in ("a", "an") else float(m.group(1))
        total += n * _UNIT_S[m.group(2)[0]]
        found = True
        t = (t[:m.start()] + " " + t[m.end():]).strip()
    secs = int(round(total)) if found else None
    return (secs if secs and secs > 0 else None), re.sub(r"\s+", " ", t).strip()


_FILLER = set(
    "set start create make begin run launch a an the timer timers countdown for of me my to on up new "
    "another next in and called named label labeled with at that this one it first second third other".split())


def _label(rest: str) -> str:
    words = [w for w in rest.split() if w not in _FILLER]
    return " ".join(words[:3])[:30].strip()


_CANCEL = re.compile(r"\b(?:cancel|stop|end|quit|abort|clear|delete|remove|kill|dismiss|reset|turn off|shut off)\b")
_PAUSE = re.compile(r"\b(?:pause|hold)\b")
_RESUME = re.compile(r"\b(?:resume|continue|unpause)\b")
_START = re.compile(r"\b(?:set|start|create|make|begin|new|run|launch|open)\b")
_STATUS = re.compile(
    r"\bhow (?:much|long)\b.*\b(?:left|remaining|elapsed|so far|on the|is it at)\b"
    r"|\b(?:time|timer|timers) (?:left|remaining)\b|\btime remaining\b"
    r"|\bwhat (?:is |s )?(?:the )?(?:time|timer|timers|stopwatch|pomodoro) (?:left|status|at)\b"
    r"|\b(?:check|show|status)\b.*\b(?:timers?|stopwatch|pomodoro)\b"
    r"|\b(?:any|which|what) (?:timers?|stopwatch)\b.*\b(?:running|active|set|have)\b"
    r"|\bdo i have (?:any|a) (?:timers?|stopwatch)\b")
_QUESTION = re.compile(r"^(?:what|whats|who|why|when|where|which|explain|tell me|define|describe|is|are|does|do|did|can i|how do|how does|how to|give me)\b")
_POM_WORD = re.compile(r"\b(?:pomodoro|pomo|focus (?:timer|session))\b")
_REMIND_A = re.compile(r"^remind me (?:in|after) (.+?) (?:to|that|about) (.+)$")
_REMIND_B = re.compile(r"^remind me (?:to|that|about) (.+?) (?:in|after) (.+)$")


def _kind_items(kind: str | None) -> list[dict]:
    with _lock:
        if kind == "countdown":
            return [i for i in _items if i["kind"] in ("timer", "pomodoro")]
        return [i for i in _items if kind is None or i["kind"] == kind]


def _pick(kind: str | None, rest: str) -> dict | None:
    """Which running thing does the user mean? A label match wins, else the next to end."""
    items = _kind_items(kind)
    if not items:
        return None
    words = [w for w in rest.split() if w not in _FILLER and len(w) > 2]
    for it in items:
        if it["label"] and any(w in it["label"].lower() for w in words):
            return it
    t = _now()
    if kind == "stopwatch":
        return items[0]
    return min(items, key=lambda i: _remaining(i, t))


def _name(it: dict) -> str:
    if it["kind"] == "stopwatch":
        return "stopwatch"
    if it["kind"] == "pomodoro":
        return "break" if it["phase"] in ("break", "long") else "focus session"
    base = f"{it['label']} timer" if it["label"] else "timer"
    return base


def _status_line(it: dict) -> str:
    t = _now()
    paused = " (paused)" if it["state"] == "paused" else ""
    if it["kind"] == "stopwatch":
        return f"The stopwatch is at {_say(_elapsed(it, t))}{paused}."
    left = math.ceil(_remaining(it, t))
    if it["kind"] == "pomodoro":
        phase = "break" if it["phase"] in ("break", "long") else "focus"
        return f"{_say(left)} left of your {phase}{paused}."
    return f"{_say(left)} left on the {_name(it)}{paused}."


def handle_command(text: str) -> str | None:
    """Run a timer / stopwatch / Pomodoro command. Returns Lyra's reply, or None if
    `text` isn't about timers (so the rest of the chain handles it as before)."""
    global _awaiting
    t = _clean(text)
    if not t:
        return None
    t = re.sub(r"\bstop ?watch(?:es)?\b", "stopwatch", t)

    # "set a timer" → "for how long?" → "10 minutes"
    aw = _awaiting
    if aw is not None:
        _awaiting = None
        if time.monotonic() < aw["until"] and len(t.split()) <= 6:
            secs, rest = _extract_duration(t)
            if secs:
                return _start_timer_reply(secs, aw["label"] or _label(rest), False)

    # Reminders: "remind me in 20 minutes to call mum"
    m = _REMIND_A.match(t)
    if m:
        secs, _ = _extract_duration(m.group(1))
        if secs:
            return _start_timer_reply(secs, m.group(2).strip()[:60], True)
    m = _REMIND_B.match(t)
    if m:
        secs, _ = _extract_duration(m.group(2))
        if secs:
            return _start_timer_reply(secs, m.group(1).strip()[:60], True)

    # Questions ("what is a pomodoro", "tell me about stopwatches") are for the chat brain,
    # unless they ask about a running timer ("what's the timer at", "how much time is left").
    if _QUESTION.match(t) and not _STATUS.search(t):
        return None

    has_stopwatch = "stopwatch" in t
    pom_word = bool(_POM_WORD.search(t))
    has_timer = bool(re.search(r"\b(?:timers?|countdown)\b", t))
    secs, rest = _extract_duration(t)
    focus_start = (
        "focus" in t.split() or "focusing" in t.split()
    ) and secs is not None and not re.search(r"\b(?:mode|music|playlist|assistant)\b", t)

    # ── stopwatch ───────────────────────────────────────────────────────────
    if has_stopwatch:
        sw = next(iter(_kind_items("stopwatch")), None)
        if _PAUSE.search(t):
            if sw and pause(sw["id"]):
                return f"Stopwatch paused at {_say(_elapsed(sw))}."
            return "There's no running stopwatch." if not sw else "The stopwatch is already paused."
        if _RESUME.search(t):
            if sw and resume(sw["id"]):
                return "Stopwatch running again."
            return "There's no paused stopwatch."
        if _CANCEL.search(t):
            if not sw:
                return "There's no stopwatch running."
            took = _say(_elapsed(sw))
            cancel(sw["id"])
            return f"Stopwatch stopped at {took}." if took != "0 seconds" else "Stopwatch cleared."
        if _STATUS.search(t) and not _START.search(t):
            return _status_line(sw) if sw else "There's no stopwatch running."
        if not (_START.search(t) or len(t.split()) <= 3):
            return None                      # a sentence that merely mentions stopwatches
        if sw:
            if sw["state"] == "paused":
                resume(sw["id"])
                return "Stopwatch running again."
            return "A stopwatch is already running."
        return "Stopwatch started." if start_stopwatch() else "That's too many timers — cancel one first."

    # ── pomodoro / focus ────────────────────────────────────────────────────
    if pom_word or focus_start:
        pom = next(iter(_kind_items("pomodoro")), None)
        if pom_word or pom:
            if _CANCEL.search(t) and secs is None:
                if not pom:
                    return "There's no focus session running."
                cancel(pom["id"])
                return "Focus session stopped."
            if _PAUSE.search(t):
                return "Paused." if pom and pause(pom["id"]) else "There's no running focus session."
            if _RESUME.search(t):
                return "Back to it." if pom and resume(pom["id"]) else "There's no paused focus session."
            if _STATUS.search(t) and secs is None:
                return _status_line(pom) if pom else "There's no focus session running."
        if secs is None and not _START.search(t) and len(t.split()) > 3:
            return None
        # start (or replace) a focus session
        brk_m = re.search(r"(\d+)\s*(?:min(?:ute)?s?)?\s*(?:short\s+)?break\b", t)
        brk = int(brk_m.group(1)) * 60 if brk_m else None
        focus_secs = secs
        if brk_m:
            focus_secs, _ = _extract_duration(t[:brk_m.start()] + " " + t[brk_m.end():])
        if pom:
            cancel(pom["id"])
        it = start_pomodoro(focus_secs or _pomo_focus_s(), brk or _pomo_break_s())
        if not it:
            return "That's too many timers — cancel one first."
        brk = _say(it["break_s"]).replace("minutes", "minute").replace("hours", "hour").replace("seconds", "second")
        return f"Focus session started: {_say(it['total'])}, then a {brk} break."

    # ── timers ──────────────────────────────────────────────────────────────
    if has_timer:
        if _CANCEL.search(t):
            if re.search(r"\b(?:all|every|everything)\b", t):
                items = _kind_items("countdown")
                for i in items:
                    cancel(i["id"])
                return f"Cancelled {len(items)} timer{'s' if len(items) != 1 else ''}." if items else "There are no timers running."
            it = _pick("timer", rest)
            if not it:
                return "There are no timers running."
            cancel(it["id"])
            more = len(_kind_items("timer"))
            tail = f" {more} more running." if more else ""
            return f"Cancelled the {_name(it)}.{tail}"
        if _PAUSE.search(t):
            it = _pick("timer", rest)
            return f"Paused the {_name(it)}." if it and pause(it["id"]) else "There's no running timer to pause."
        if _RESUME.search(t):
            paused = [i for i in _kind_items("timer") if i["state"] == "paused"]
            if paused and resume(paused[0]["id"]):
                return f"Resumed the {_name(paused[0])}."
            return "There's no paused timer."
        if secs is not None and not _STATUS.search(t):
            return _start_timer_reply(secs, _label(rest), False)
        if _STATUS.search(t):
            items = _kind_items("countdown")
            if not items:
                return "There are no timers running."
            if len(items) == 1:
                return _status_line(items[0])
            return " ".join(_status_line(i) for i in sorted(items, key=lambda i: _remaining(i))[:3])
        if _START.search(t) or re.search(r"\btimer for\b", t):
            _awaiting = {"label": _label(rest), "until": time.monotonic() + AWAIT_DURATION_S}
            return "How long should I set the timer for?"
        return None

    # "how much time is left?" with no noun — only if something is actually running
    if _STATUS.search(t) and re.search(r"\b(?:time|long)\b.*\b(?:left|remaining)\b", t):
        items = _kind_items("countdown") + _kind_items("stopwatch")
        if items:
            return " ".join(_status_line(i) for i in items[:3])
    return None


def _start_timer_reply(secs: int, label: str, reminder: bool) -> str:
    if secs > MAX_SECONDS:
        return "I can set timers up to 24 hours."
    it = start_timer(secs, label, reminder)
    if not it:
        return "That's too many timers — cancel one first."
    if reminder:
        return f"Okay, I'll remind you in {_say(secs)}."
    return f"{label.capitalize() + ' timer' if label else 'Timer'} set for {_say(secs)}."
