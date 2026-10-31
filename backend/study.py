"""Lyra — study mode: focus sessions, flashcards / quizzes, summaries and study minutes.

Voice / chat examples (all handled before the LLM; see try_study at the bottom):
    "start a 25 minute focus"        -> handled by timers.py (Pomodoro). This module WATCHES that
                                        Pomodoro: while its focus phase runs it mutes message
                                        pop-ups, logs study minutes and reminds you of distractions.
    "start focus mode" / "end focus mode"
    "add discord to my distractions"  "remove discord from my distractions"
    "what are my distractions"        "close distractions"   (asks before closing each app)
    "quiz me on the Fourier transform"       "quiz me on my clipboard"      "quiz me on this"
    "review my flashcards"            "list my decks"
    while a quiz runs:  "my answer is ..."  "show the answer"  "got it" / "missed it"
                        "next"  "repeat"  "stop the quiz"
    "summarize this"                  (selected text, else the clipboard; 5 bullets)
    "how long have I studied today"   "open study"
    Math: cards may contain LaTeX between $...$; the tab draws it, answers can be typed, picked from a
    symbol palette or handwritten (read by the vision model), and are checked symbolically (study_math.py).

Data (all in Lyra's data folder, never in the project):
    decks.json       decks of cards with simple Leitner boxes 1..5
    study_log.json   minutes / sessions / cards per day

Settings (settings_store): feature_study, study_focus_mutes, study_distractors,
pomo_focus_min / pomo_break_min / pomo_long_break_min (read by timers.py).
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import study_math

log = logging.getLogger("lyra")

MAX_CARDS = 10
MIN_CARDS = 5
SOURCE_CHARS = 6000
LEITNER_DAYS = {1: 0, 2: 1, 3: 3, 4: 7, 5: 14}   # days until a card is due again, per box
SESSION_SIZE = 10
INK_MAX_B64 = 3_000_000                          # a handwriting snapshot (PNG, base64) larger than this is refused
CHAT_APPS = ("discord", "whatsapp", "telegram")   # same apps notifications.py mirrors

_lock = threading.RLock()
_emit: Callable[[dict], None] | None = None


# ── small helpers ────────────────────────────────────────────────────────────────────────────

def _settings(key: str, default=None):
    try:
        import settings_store
        v = settings_store.get(key)
        return default if v is None else v
    except Exception:
        return default


def _enabled() -> bool:
    try:
        import settings_store
        return settings_store.feature_on("study")
    except Exception:
        return True


def _today() -> str:
    return date.today().isoformat()


def _push(payload: dict) -> None:
    fn = _emit
    if fn is None:
        return
    try:
        fn({**payload, "ts": int(time.time() * 1000)})
    except Exception as e:  # a UI problem must never break studying
        log.warning("study: push failed: %s", e)


def _announce(sender: str, text: str) -> None:
    """A toast on the island (reminders are never muted by focus mode)."""
    try:
        import ui_bridge
        ui_bridge.push_notification("reminder", "reminder", sender, text)
    except Exception as e:
        log.warning("study: could not show toast: %s", e)


def _read_json(path: Path, fallback):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, type(fallback)) else fallback
    except Exception:
        return fallback


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _decks_file() -> Path:
    import paths
    return paths.data_path("decks.json")


def _log_file() -> Path:
    import paths
    return paths.data_path("study_log.json")


# ── study log (minutes per day) ──────────────────────────────────────────────────────────────

_log_cache: dict | None = None
_log_dirty = False
_log_saved_at = 0.0


def _log_data() -> dict:
    global _log_cache
    if _log_cache is None:
        d = _read_json(_log_file(), {})
        d.setdefault("days", {})
        _log_cache = d
    return _log_cache


def _day(day: str | None = None) -> dict:
    days = _log_data()["days"]
    return days.setdefault(day or _today(), {"sec": 0.0, "sessions": 0, "cards": 0})


def _log_add(sec: float = 0.0, sessions: int = 0, cards: int = 0) -> None:
    global _log_dirty
    with _lock:
        d = _day()
        d["sec"] = float(d.get("sec", 0.0)) + sec
        d["sessions"] = int(d.get("sessions", 0)) + sessions
        d["cards"] = int(d.get("cards", 0)) + cards
        _log_dirty = True


def _log_flush(force: bool = False) -> None:
    global _log_dirty, _log_saved_at
    with _lock:
        if not _log_dirty:
            return
        if not force and time.time() - _log_saved_at < 30:
            return
        try:
            _write_json(_log_file(), _log_data())
            _log_dirty = False
            _log_saved_at = time.time()
        except Exception as e:
            log.warning("study: could not save study_log.json: %s", e)


def stats_snapshot() -> dict:
    """Study numbers for the Stats tab (GET /stats adds this as `study`)."""
    with _lock:
        days = _log_data()["days"]
        out_days = []
        for i in range(6, -1, -1):
            dd = (date.today() - timedelta(days=i)).isoformat()
            out_days.append({"date": dd, "min": int(round(float(days.get(dd, {}).get("sec", 0)) / 60))})
        streak, cur = 0, date.today()
        if float(days.get(cur.isoformat(), {}).get("sec", 0)) < 60:
            cur -= timedelta(days=1)          # today may not have started yet
        while float(days.get(cur.isoformat(), {}).get("sec", 0)) >= 60:
            streak += 1
            cur -= timedelta(days=1)
        t = days.get(_today(), {})
        return {
            "today_min": int(round(float(t.get("sec", 0)) / 60)),
            "week_min": sum(d["min"] for d in out_days),
            "sessions_today": int(t.get("sessions", 0)),
            "cards_today": int(t.get("cards", 0)),
            "streak_days": streak,
            "days": out_days,
        }


# ── focus mode: watches the Pomodoro in timers.py ────────────────────────────────────────────

_focus_active = False
_focus_item: int | None = None
_gate_installed = False


def focus_active() -> bool:
    return _focus_active


def _install_gate() -> None:
    """Mute chat pop-ups during focus.

    notifications.should_show() is the one gate every island toast passes (it is what quiet hours
    use). We wrap it once instead of editing notifications.py: while focus is on, chat MESSAGES
    are hidden; calls, reminders and timer alerts still get through."""
    global _gate_installed
    if _gate_installed:
        return
    try:
        import notifications
    except Exception as e:
        log.warning("study: focus mute unavailable: %s", e)
        return
    original = notifications.should_show

    def gated(app: str, kind: str) -> bool:
        if _focus_active and _settings("study_focus_mutes", True) and app in CHAT_APPS and kind != "call":
            return False
        return original(app, kind)

    notifications.should_show = gated
    _gate_installed = True


def _pomodoro() -> dict | None:
    try:
        import timers
        return next((i for i in timers.snapshot()["items"] if i["kind"] == "pomodoro"), None)
    except Exception:
        return None


def distractor_list() -> list[str]:
    raw = str(_settings("study_distractors", "") or "")
    seen: list[str] = []
    for part in re.split(r"[,\n;]+", raw):
        p = part.strip().lower()
        if p.endswith(".exe"):
            p = p[:-4]
        if p and p not in seen:
            seen.append(p)
    return seen


def _save_distractors(items: list[str]) -> None:
    import settings_store
    settings_store.set_many({"study_distractors": ", ".join(items)[:300]})


def running_distractors() -> dict[str, list[int]]:
    """{distractor name: [pids]} for the user's list that is open right now."""
    wanted = distractor_list()
    if not wanted:
        return {}
    try:
        import psutil
    except Exception:
        return {}
    found: dict[str, list[int]] = {}
    for p in psutil.process_iter(["name", "pid"]):
        try:
            name = (p.info.get("name") or "").lower()
            if name.endswith(".exe"):
                name = name[:-4]
            for w in wanted:
                if w == name or (len(w) >= 4 and w in name):
                    found.setdefault(w, []).append(int(p.info["pid"]))
        except Exception:
            continue
    return found


def _close_app(name: str) -> str:
    exe = name if name.endswith(".exe") else f"{name}.exe"
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(["taskkill", "/IM", exe], capture_output=True, text=True, timeout=10, creationflags=flags)
        if r.returncode == 0:
            return f"Closed {name}."
        return f"{name} didn't close ({(r.stdout or r.stderr).strip()[:80]})."
    except Exception as e:
        return f"I couldn't close {name} — {e}"


def _focus_started(item: dict) -> None:
    global _focus_active
    _focus_active = True
    _install_gate()
    log.info("study: focus on (round %s)", item.get("round"))
    if int(item.get("round") or 1) == 1:       # remind once per session, not at every round
        open_apps = running_distractors()
        if open_apps:
            names = ", ".join(sorted(open_apps)[:4])
            _announce("Focus mode", f"{names} {'is' if len(open_apps) == 1 else 'are'} open. Say “close distractions” if you want me to ask about closing.")
    _push({"type": "study", "open": False, "focus": True})
    try:
        import ui_bridge
        minutes = int(_settings("pomo_focus_min", 25) or 25)
        ui_bridge.push_mascot_emote("focus", minutes * 60_000)
    except Exception:
        pass


def _focus_ended() -> None:
    global _focus_active
    if _focus_active:
        _focus_active = False
        log.info("study: focus off")
        _push({"type": "study", "open": False, "focus": False})
        try:
            import ui_bridge
            ui_bridge.push_mascot_emote("idle", 300)
        except Exception:
            pass


class _Watcher(threading.Thread):
    """Polls timers.snapshot() once a second. Cheap, and it needs no change to timers.py."""

    def __init__(self) -> None:
        super().__init__(name="lyra-study", daemon=True)
        self._stop_event = threading.Event()
        self._last = time.monotonic()
        self._prev_phase: str | None = None
        self._prev_id: int | None = None

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:  # never let the watcher die
                log.warning("study: watcher: %s", e)
            self._stop_event.wait(1.0)
        _log_flush(force=True)

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(now - self._last, 3.0)
        self._last = now
        it = _pomodoro()
        running_focus = bool(it and it["state"] == "running" and it["phase"] == "focus")
        if running_focus:
            if not _focus_active:
                _focus_started(it)
            _log_add(sec=dt)
        elif _focus_active:
            _focus_ended()
        # a focus phase that rolled into a break counts as one finished session
        if it and self._prev_id == it["id"] and self._prev_phase == "focus" and it["phase"] in ("break", "long"):
            _log_add(sessions=1)
        self._prev_id = it["id"] if it else None
        self._prev_phase = it["phase"] if it else None
        _log_flush()


_watcher: _Watcher | None = None


def start(emit: Callable[[dict], None]) -> None:
    """Start the focus watcher once. Called from ui_bridge.start_bridge() behind feature_on("study")."""
    global _watcher, _emit
    _emit = emit
    if _watcher is not None:
        return
    _install_gate()
    _watcher = _Watcher()
    _watcher.start()
    log.info("study: focus watcher started")


# ── LLM helpers ──────────────────────────────────────────────────────────────────────────────

def _llm(system: str, user: str) -> str:
    """One plain chat call with the provider chosen in Settings. Raises with a readable message."""
    import brain
    client, model = brain._get_client()
    if client is None:
        raise RuntimeError("no AI key is set up yet")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    resp = brain._chat_completion(client, model, messages)
    return (resp.choices[0].message.content or "").strip()


def _json_from(text: str, opener: str):
    """First JSON value starting with `opener` ('[' or '{') in an LLM reply (fences tolerated)."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    closer = "]" if opener == "[" else "}"
    a, b = text.find(opener), text.rfind(closer)
    if a < 0 or b <= a:
        raise ValueError("no JSON in the reply")
    return json.loads(text[a:b + 1])


def _make_cards(source: str, topic: str, math: bool = False) -> list[dict]:
    math = math or study_math.looks_mathy(topic if not source else f"{topic}\n{source[:600]}")
    if source:
        task = ("Write flashcards using ONLY the text below. Do not add facts that are not in it.\n\n"
                f"Text:\n{source[:SOURCE_CHARS]}")
    elif math:
        task = f"Write practice problems on: {topic}. Mix easy and medium difficulty; every problem has ONE final answer."
    else:
        task = f"Write flashcards that teach the topic: {topic}"
    if math:
        system = ("You write maths practice cards. Reply with ONLY a JSON array of "
                  f"{MIN_CARDS} to {MAX_CARDS} objects, each {{\"q\": \"problem\", \"a\": \"final answer\", \"s\": \"worked solution\"}}. "
                  "Write ALL mathematics as LaTeX between single dollar signs, e.g. $\\\\frac{1}{2}x^2$ (remember JSON needs "
                  "every backslash doubled). The problem states what to find. The answer is ONE short expression, number or "
                  "equation in LaTeX (simplified; for several solutions write them separated by commas). The worked solution is "
                  "at most 3 short steps. No markdown, no commentary.")
    else:
        system = ("You write study flashcards. Reply with ONLY a JSON array of "
                  f"{MIN_CARDS} to {MAX_CARDS} objects, each {{\"q\": \"question\", \"a\": \"short answer\"}}. "
                  "Questions must be clear on their own. Answers are short (a word, a number or one short sentence). "
                  "Write any mathematics as LaTeX between single dollar signs (JSON needs every backslash doubled). "
                  "No markdown, no commentary.")
    raw = study_math.repair_json_latex(_llm(system, task))
    items = _json_from(raw, "[")
    cards: list[dict] = []
    seen: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        q, a = str(it.get("q") or "").strip(), str(it.get("a") or "").strip()
        if len(q) < 4 or not a or q.lower() in seen:
            continue
        seen.add(q.lower())
        card = {"id": uuid.uuid4().hex[:8], "q": q[:300], "a": a[:300], "box": 1,
                "due": _today(), "seen": 0, "right": 0}
        sol = str(it.get("s") or "").strip()
        if sol:
            card["s"] = sol[:600]
        if math or study_math.looks_mathy(f"{q} {a}"):
            card["math"] = True
        cards.append(card)
        if len(cards) >= MAX_CARDS:
            break
    if len(cards) < 2:
        raise ValueError("the AI did not return usable cards")
    return cards


# ── decks and Leitner boxes ──────────────────────────────────────────────────────────────────

def _load_decks() -> list[dict]:
    d = _read_json(_decks_file(), {"decks": []})
    decks = d.get("decks")
    return decks if isinstance(decks, list) else []


def _save_decks(decks: list[dict]) -> None:
    _write_json(_decks_file(), {"decks": decks})


def _add_cards(name: str, cards: list[dict]) -> dict:
    with _lock:
        decks = _load_decks()
        deck = next((d for d in decks if str(d.get("name", "")).lower() == name.lower()), None)
        if deck is None:
            deck = {"id": uuid.uuid4().hex[:8], "name": name, "created": _today(), "cards": []}
            decks.append(deck)
        have = {c["q"].lower() for c in deck["cards"]}
        deck["cards"].extend(c for c in cards if c["q"].lower() not in have)
        _save_decks(decks)
        return deck


def _due(deck: dict) -> list[dict]:
    t = _today()
    return sorted((c for c in deck["cards"] if str(c.get("due", t)) <= t), key=lambda c: (c.get("box", 1), c.get("seen", 0)))


def _record(deck_id: str, card_id: str, verdict: str) -> None:
    """Leitner: right -> next box, partly right -> same box, wrong -> box 1."""
    with _lock:
        decks = _load_decks()
        for d in decks:
            if d["id"] != deck_id:
                continue
            for c in d["cards"]:
                if c["id"] != card_id:
                    continue
                box = int(c.get("box", 1))
                if verdict == "correct":
                    box = min(5, box + 1)
                    c["right"] = int(c.get("right", 0)) + 1
                elif verdict == "wrong":
                    box = 1
                c["box"], c["seen"] = box, int(c.get("seen", 0)) + 1
                c["due"] = (date.today() + timedelta(days=LEITNER_DAYS[box])).isoformat()
        _save_decks(decks)
    _log_add(cards=1)


def deck_summaries() -> list[dict]:
    return [{"id": d["id"], "name": d["name"], "cards": len(d["cards"]), "due": len(_due(d))} for d in _load_decks()]


# ── grading ──────────────────────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()


def _local_grade(expected: str, given: str) -> str:
    e, g = _norm(expected), _norm(given)
    if not g:
        return "wrong"
    if e == g or (len(g) >= 3 and (g in e.split() or g == e)):
        return "correct"
    ew, gw = set(e.split()), set(g.split())
    overlap = len(ew & gw) / max(1, len(ew))
    return "correct" if overlap >= 0.8 else "partial" if overlap >= 0.5 else "wrong"


def grade(card: dict, given: str) -> tuple[str, str]:
    """(verdict, one short sentence). Exact matches skip the LLM; everything else is graded by it."""
    quick = _local_grade(card["a"], given)
    if quick == "correct" and _norm(card["a"]) == _norm(given):
        return "correct", "Exactly right."
    if card.get("math") or study_math.looks_mathy(card["a"]):
        same = study_math.equivalent(card["a"], given)       # symbolic check first; None = could not tell
        if same is True:
            return "correct", "Same value as the expected answer."
        if same is False:
            return "wrong", "That is not equivalent to the expected answer."
    try:
        raw = _llm(
            "You grade a student's short answer. Reply with ONLY JSON: "
            "{\"verdict\": \"correct\" | \"partial\" | \"wrong\", \"feedback\": \"one short sentence\"}. "
            "Accept different wording with the same meaning. 'partial' = right idea but missing something important. "
            "For maths, accept any algebraically equivalent form.",
            f"Question: {card['q']}\nExpected answer: {card['a']}\nStudent answer: {given}")
        j = _json_from(raw, "{")
        v = str(j.get("verdict", "")).lower()
        if v in ("correct", "partial", "wrong"):
            return v, str(j.get("feedback") or "").strip()[:200]
    except Exception as e:
        log.info("study: LLM grading unavailable, using word match (%s)", e)
    if card.get("math"):
        return "wrong" if quick != "correct" else quick, "Could not check this answer without the AI; compare it with the answer shown."
    return quick, "Marked by word match because the AI was unavailable."


# ── quiz session ─────────────────────────────────────────────────────────────────────────────

_session: dict | None = None
_busy = ""          # shown on the Study tab while cards are being made
_error = ""


def _current() -> tuple[dict, dict] | None:
    """(deck, card) the session is on, or None."""
    s = _session
    if not s or s["index"] >= len(s["queue"]):
        return None
    for d in _load_decks():
        if d["id"] == s["deck_id"]:
            c = next((c for c in d["cards"] if c["id"] == s["queue"][s["index"]]), None)
            return (d, c) if c else None
    return None


def _begin(deck: dict, only_due: bool) -> str:
    global _session
    pool = _due(deck) if only_due else list(deck["cards"])
    if not pool:
        return f"Nothing is due in {deck['name']} right now."
    queue = [c["id"] for c in pool[:SESSION_SIZE]]
    _session = {"deck_id": deck["id"], "deck_name": deck["name"], "queue": queue, "index": 0,
                "revealed": False, "result": None, "right": 0, "wrong": 0}
    _push({"type": "study", "open": True})
    return f"{deck['name']}: {len(queue)} card{'s' if len(queue) != 1 else ''}. {_ask_line()}"


def _ask_line() -> str:
    cur = _current()
    if not cur:
        return ""
    s = _session
    return f"Question {s['index'] + 1} of {len(s['queue'])}: {study_math.speakable(cur[1]['q'])}"


def _finish_line() -> str:
    s = _session
    if not s:
        return "No quiz is running."
    total = s["right"] + s["wrong"]
    return f"That's the set: {s['right']} of {total} right." if total else "That's the set."


def _advance() -> str:
    s = _session
    if not s:
        return "No quiz is running."
    s["index"] += 1
    s["revealed"], s["result"] = False, None
    if s["index"] >= len(s["queue"]):
        line = _finish_line()
        _push({"type": "study", "open": False})
        return line
    return _ask_line()


def _answer(given: str) -> str:
    cur = _current()
    if not cur or not _session:
        return "No quiz is running. Say “quiz me on” a topic."
    deck, card = cur
    if _session["result"]:
        return "You've answered that one. Say next for the next question."
    verdict, feedback = grade(card, given)
    _session["revealed"] = True
    _session["result"] = {"verdict": verdict, "feedback": feedback, "answer": card["a"], "given": given,
                          "solution": card.get("s", "")}
    _record(deck["id"], card["id"], verdict)
    _session["right" if verdict == "correct" else "wrong"] += 1
    head = {"correct": "Correct.", "partial": "Partly right.", "wrong": "Not quite."}[verdict]
    tail = "" if verdict == "correct" else f" The answer is: {study_math.speakable(card['a'])}."
    _push({"type": "study", "open": False})
    return f"{head} {feedback}{tail} Say next when you're ready.".replace("  ", " ")


def _reveal() -> str:
    cur = _current()
    if not cur or not _session:
        return "No quiz is running."
    _session["revealed"] = True
    _push({"type": "study", "open": False})
    return f"The answer is: {study_math.speakable(cur[1]['a'])}. Say “got it” or “missed it”, then next."


def _mark(correct: bool) -> str:
    cur = _current()
    if not cur or not _session:
        return "No quiz is running."
    if _session["result"]:
        return "Already marked. Say next."
    deck, card = cur
    verdict = "correct" if correct else "wrong"
    _session["revealed"] = True
    _session["result"] = {"verdict": verdict, "feedback": "Marked by you.", "answer": card["a"], "given": "",
                          "solution": card.get("s", "")}
    _record(deck["id"], card["id"], verdict)
    _session["right" if correct else "wrong"] += 1
    _push({"type": "study", "open": False})
    return "Nice, moving it up a box." if correct else "No problem, it will come back soon."


def _stop_quiz() -> str:
    global _session
    if not _session:
        return "No quiz is running."
    line = _finish_line()
    _session = None
    _push({"type": "study", "open": False})
    return f"Quiz stopped. {line}"


# ── getting the text to study ────────────────────────────────────────────────────────────────

def _clipboard() -> str:
    try:
        from actions.system import get_clipboard_text
        return (get_clipboard_text() or "").strip()
    except Exception:
        return ""


def _selection() -> str:
    """The text selected in the foreground app (Ctrl+C trick, your own clipboard is put back)."""
    try:
        from actions.screen_tools import _get_clip, _set_clip, _ctrl_c
    except Exception:
        return ""
    old = _get_clip()
    _set_clip("")
    time.sleep(0.05)
    _ctrl_c()
    text = ""
    for _ in range(8):
        time.sleep(0.1)
        text = (_get_clip() or "").strip()
        if text:
            break
    if old:
        _set_clip(old)
    return text


def _text_for(kind: str) -> str:
    if kind == "selection":
        return _selection() or ""
    return _clipboard()


# ── building a deck (voice and UI share this) ────────────────────────────────────────────────

def _build_deck(topic: str, kind: str) -> tuple[dict | None, str]:
    """kind: "topic" | "math" | "clipboard" | "selection". Returns (deck, error message)."""
    global _busy, _error
    source = ""
    if kind in ("clipboard", "selection"):
        source = _text_for(kind)
        if not source:
            return None, ("Your clipboard is empty." if kind == "clipboard"
                          else "I didn't see any selected text. Select it first, then ask again.")
        name = (topic or source.split("\n", 1)[0])[:40].strip() or "Notes"
        if not topic:
            name = f"{name} ({date.today():%d %b})"
    else:
        name = topic.strip()[:40].title()
        if not name:
            return None, "What should I quiz you on?"
        if kind == "math" and not name.lower().startswith("math"):
            name = f"Math · {name}"[:40]
    _busy, _error = (f"Writing practice problems on {topic.strip() or name}…" if kind == "math"
                     else f"Making flashcards on {name}…"), ""
    _push({"type": "study", "open": False})
    try:
        cards = _make_cards(source, topic or name, math=(kind == "math"))
    except Exception as e:
        _busy, _error = "", f"I couldn't make cards — {e}"
        _push({"type": "study", "open": False})
        return None, _error
    _busy = ""
    return _add_cards(name, cards), ""


def _quiz_from(topic: str, kind: str) -> str:
    deck, err = _build_deck(topic, kind)
    if not deck:
        return err
    return _begin(deck, only_due=False)


# ── summary ──────────────────────────────────────────────────────────────────────────────────

def summarize_selected() -> str:
    text = _selection() or _clipboard()
    if not text:
        return "I found no selected text and your clipboard is empty."
    try:
        reply = _llm("You summarize text. Reply with exactly 5 short bullet points, each starting with “• ”. "
                     "No introduction, no closing line.", text[:SOURCE_CHARS])
    except Exception as e:
        return f"I couldn't summarize that — {e}"
    lines = [ln.strip() for ln in reply.splitlines() if ln.strip()]
    bullets = [("• " + re.sub(r"^[-*•\d.)\s]+", "", ln)) for ln in lines][:5]
    return "\n".join(bullets) or "I couldn't get a summary."


# ── handwriting ──────────────────────────────────────────────────────────────────────────────

_INK_PROMPT = ("Transcribe the handwritten mathematics in this image into LaTeX. Reply with ONLY the LaTeX: no $ signs, "
               "no code fence, no explanation. Keep exactly what is written (do not solve or simplify it). "
               "Use \\frac, \\sqrt, ^ and _ where the writing uses them. If nothing legible is written, reply with exactly: ???")


def recognize_ink(data_url: str) -> dict:
    """PNG snapshot of the handwriting pad (data URL) -> {"latex": "...", "error": ""}. Needs a vision-capable key."""
    import base64
    m = re.fullmatch(r"data:image/png;base64,([A-Za-z0-9+/=]+)", data_url or "")
    if not m:
        return {"latex": "", "error": "That drawing could not be read."}
    b64 = m.group(1)
    if len(b64) > INK_MAX_B64:
        return {"latex": "", "error": "That drawing is too large. Clear the pad and write a little smaller."}
    try:
        if not base64.b64decode(b64, validate=True).startswith(b"\x89PNG"):
            raise ValueError("not a png")
    except Exception:
        return {"latex": "", "error": "That drawing could not be read."}
    try:
        from actions import apps
        if not apps._vision_candidates():
            return {"latex": "", "error": "Handwriting needs an AI key that can read images. Add one in Lyra settings, or type the maths instead."}
        raw, err = apps._vision_complete([{
            "role": "user",
            "content": [{"type": "text", "text": _INK_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}],
        }], max_tokens=200)
    except Exception as e:  # noqa: BLE001 - the pad must never crash the tab
        log.warning("study: handwriting recognition failed: %s", e)
        return {"latex": "", "error": "I could not reach the handwriting reader."}
    latex = study_math.clean_latex(raw)
    if not latex or latex.strip("?") == "":
        return {"latex": "", "error": f"I couldn't read that ({err}). Try writing larger and clearer." if err
                else "I couldn't read that. Try writing larger and clearer."}
    return {"latex": latex, "error": ""}


# ── HTTP side (ui_bridge: GET /study, POST /study/action) ────────────────────────────────────

def state_snapshot() -> dict:
    s = _session
    sess = None
    if s:
        cur = _current()
        done = cur is None
        sess = {
            "deck": s["deck_name"], "index": s["index"], "total": len(s["queue"]), "done": done,
            "question": cur[1]["q"] if cur else "", "revealed": bool(s["revealed"]),
            "answer": cur[1]["a"] if cur and s["revealed"] else "", "result": s["result"],
            "right": s["right"], "wrong": s["wrong"],
            "math": bool(cur and (cur[1].get("math") or study_math.looks_mathy(f"{cur[1]['q']} {cur[1]['a']}"))),
            "solution": cur[1].get("s", "") if cur and s["revealed"] else "",
        }
    pom = _pomodoro()
    focus = {"active": _focus_active, "phase": pom["phase"] if pom else "", "state": pom["state"] if pom else "",
             "remaining_ms": pom["remaining_ms"] if pom else None, "round": pom["round"] if pom else 0}
    return {"ok": True, "enabled": _enabled(), "busy": _busy, "error": _error, "focus": focus,
            "session": sess, "decks": deck_summaries(), "stats": stats_snapshot()}


def handle_http(msg: dict) -> dict:
    """POST /study/action. Long jobs (making cards) run in a thread; the tab polls GET /study."""
    global _session, _error
    action = str(msg.get("action") or "")
    _error = "" if action != "noop" else _error
    if action == "start":
        kind = str(msg.get("source") or "topic")
        if kind == "deck":
            deck = next((d for d in _load_decks() if d["id"] == msg.get("deck_id")), None)
            if not deck:
                return {"ok": False, "error": "deck not found"}
            _begin(deck, only_due=bool(msg.get("only_due", True)))
        elif kind in ("topic", "math", "clipboard", "selection"):
            topic = str(msg.get("topic") or "")[:80]
            if kind in ("topic", "math") and not topic.strip():
                return {"ok": False, "error": "type a topic first"}

            def job() -> None:
                deck, _err = _build_deck(topic, kind)
                if deck:
                    _begin(deck, only_due=False)
            threading.Thread(target=job, name="lyra-study-cards", daemon=True).start()
        else:
            return {"ok": False, "error": "unknown source"}
    elif action == "answer":
        _answer(str(msg.get("text") or "")[:500])
    elif action == "reveal":
        _reveal()
    elif action == "mark":
        _mark(bool(msg.get("correct")))
    elif action == "next":
        _advance()
    elif action == "stop":
        _stop_quiz()
    elif action == "speak":
        cur = _current()
        if cur:
            text = cur[1]["a"] if (_session or {}).get("revealed") and msg.get("what") == "answer" else cur[1]["q"]
            text = study_math.speakable(text)
            try:
                import voice
                threading.Thread(target=voice.speak, args=(text,), daemon=True).start()
            except Exception as e:
                log.warning("study: could not speak: %s", e)
    elif action == "recognize_ink":
        return {**state_snapshot(), "ink": recognize_ink(str(msg.get("image") or ""))}
    elif action == "start_focus":
        try:
            import timers
            timers.start_pomodoro()
        except Exception as e:
            return {"ok": False, "error": str(e)}
    elif action == "delete_deck":
        with _lock:
            _save_decks([d for d in _load_decks() if d["id"] != msg.get("deck_id")])
    elif action != "noop":
        return {"ok": False, "error": "unknown action"}
    return state_snapshot()


# ── voice / typed commands ───────────────────────────────────────────────────────────────────

_PREFIX = re.compile(r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:please\s+|can\s+you\s+|could\s+you\s+)?", re.I)

_QUIZ_ON = re.compile(
    r"^(?:quiz\s+me|test\s+me|make\s+(?:me\s+)?(?:some\s+)?(?:flash\s?cards|a\s+quiz))\s*"
    r"(?:(?:on|about|from|for)\s+(?P<what>.+))?$")
_FLASH_ON = re.compile(r"^(?:flash\s?cards|make\s+flash\s?cards|create\s+flash\s?cards)\s+(?:on|about|from|for)\s+(?P<what>.+)$")
_REVIEW = re.compile(r"^(?:review|study|practice|go\s+through)\s+(?:my\s+)?(?:flash\s?cards|decks?|cards)$")
_LIST_DECKS = re.compile(r"^(?:list|show|what\s+are)\s+(?:my\s+)?(?:flash\s?card\s+)?decks$")
_SUMMARY = re.compile(r"^summari[sz]e\s+(?:this|that|it|the\s+selected\s+text|the\s+selection|what\s+i\s+selected|my\s+selection)"
                      r"(?:\s+(?:in|into)\s+(?:5|five)\s+bullets?(?:\s+points?)?)?$")
_OPEN = re.compile(r"^(?:open|show)\s+(?:my\s+)?(?:study|flash\s?cards|study\s+tab)$")
_FOCUS_ON = re.compile(r"^(?:start|begin|turn\s+on|enable)\s+(?:a\s+)?(?:focus|study)\s+(?:mode|session)$|^focus\s+mode(?:\s+on)?$")
_FOCUS_OFF = re.compile(r"^(?:stop|end|exit|turn\s+off|disable)\s+(?:the\s+|my\s+)?(?:focus|study)\s+(?:mode|session)$|^focus\s+mode\s+off$")
_STUDIED = re.compile(r"^how\s+(?:long|much)\s+(?:have\s+i|did\s+i|time\s+did\s+i)\s+(?:been\s+)?(?:stud(?:y|ied|ying)|focus(?:ed)?)"
                      r"(?:\s+(?P<when>today|this\s+week|yesterday))?$|^(?:my\s+)?study\s+(?:stats|time|minutes)$")
_DIST_ADD = re.compile(r"^(?:add|put)\s+(?P<app>[\w .+-]{2,30}?)\s+(?:to|on|in)\s+(?:my\s+)?(?:distract(?:ions?|ing)(?:\s+(?:apps|list))?)$")
_DIST_DEL = re.compile(r"^(?:remove|delete|take)\s+(?P<app>[\w .+-]{2,30}?)\s+(?:from|off|out\s+of)\s+(?:my\s+)?(?:distract(?:ions?|ing)(?:\s+(?:apps|list))?)$")
_DIST_LIST = re.compile(r"^(?:what\s+are|list|show)\s+(?:my\s+)?distract(?:ions?|ing\s+apps)$")
_DIST_CLOSE = re.compile(r"^close\s+(?:my\s+|the\s+)?distract(?:ions?|ing\s+apps)$")
# only while a quiz is running
_Q_NEXT = re.compile(r"^(?:next(?:\s+(?:card|question|one))?|skip(?:\s+(?:this|it))?|i\s+(?:don'?t|do\s+not)\s+know)$")
_Q_REVEAL = re.compile(r"^(?:show|reveal|tell)\s+(?:me\s+)?(?:the\s+)?answer$|^flip(?:\s+(?:the\s+)?card)?$|^what(?:'s|\s+is)\s+the\s+answer$")
_Q_REPEAT = re.compile(r"^(?:repeat|say\s+(?:that|it)\s+again|read)(?:\s+(?:the\s+)?(?:question|card))?(?:\s+again)?$")
_Q_ANSWER = re.compile(r"^(?:(?:my\s+)?answer\s+(?:is\s+)?|(?:i\s+think\s+)?(?:it'?s|it\s+is)\s+|i\s+think\s+)(?P<a>.+)$")
_Q_GOT = re.compile(r"^(?:got\s+it|i\s+(?:knew|got)\s+(?:it|that)|knew\s+it|correct)$")
_Q_MISSED = re.compile(r"^(?:missed\s+it|i\s+(?:missed|forgot)\s+(?:it|that)|wrong|i\s+got\s+it\s+wrong)$")
_Q_STOP = re.compile(r"^(?:stop|end|exit|quit|cancel)\s+(?:the\s+|my\s+)?(?:quiz|flash\s?cards|review)$")


def _clean(command: str) -> str:
    low = (command or "").strip().lower()
    low = _PREFIX.sub("", low).strip().rstrip("?.! ").strip()
    return re.sub(r"\s+", " ", low)


def _what_kind(what: str) -> tuple[str, str]:
    """('clipboard'|'selection'|'topic', topic text) from the words after 'quiz me on'."""
    w = (what or "").strip()
    if re.fullmatch(r"(?:my\s+|the\s+)?clip\s?board(?:\s+text)?|what\s+i\s+copied", w):
        return "clipboard", ""
    if re.fullmatch(r"this|that|it|(?:the\s+|my\s+)?(?:selected\s+text|selection)|what\s+i\s+selected", w):
        return "selection", ""
    return "topic", re.sub(r"^(?:the\s+|a\s+|an\s+)", "", w)


def try_study(command: str, engine_context=None) -> str | None:
    """Return Lyra's reply if `command` is a study command, else None (the chain carries on)."""
    if not _enabled():
        return None
    low = _clean(command)
    if not low:
        return None

    # ── while a quiz is running: answers and navigation ────────────────────────────────────
    if _session:
        if _Q_STOP.match(low):
            return _stop_quiz()
        if _Q_NEXT.match(low):
            if low.startswith("i don") and not _session["result"]:
                cur = _current()
                if cur:
                    _mark(False)
                    return f"The answer is: {cur[1]['a']}. " + _advance()
            return _advance()
        if _Q_REVEAL.match(low):
            return _reveal()
        if _Q_REPEAT.match(low):
            return _ask_line() or "No question right now."
        if _Q_GOT.match(low):
            return _mark(True)
        if _Q_MISSED.match(low):
            return _mark(False)
        m = _Q_ANSWER.match(low)
        if m and not _session["result"]:
            return _answer(m.group("a"))

    # ── quizzes and decks ──────────────────────────────────────────────────────────────────
    m = _QUIZ_ON.match(low) or _FLASH_ON.match(low)
    if m:
        what = (m.groupdict().get("what") or "").strip()
        if not what:
            decks = [d for d in _load_decks() if _due(d)]
            if decks:
                return _begin(decks[0], only_due=True)
            return "What should I quiz you on? Say a topic, or “quiz me on my clipboard”."
        kind, topic = _what_kind(what)
        return _quiz_from(topic, kind)
    if _REVIEW.match(low):
        due = [d for d in _load_decks() if _due(d)]
        return _begin(due[0], only_due=True) if due else "Nothing is due. You're all caught up."
    if _LIST_DECKS.match(low):
        ds = deck_summaries()
        if not ds:
            return "You have no decks yet. Say “quiz me on” a topic to make one."
        return "Your decks: " + "; ".join(f"{d['name']} ({d['cards']} cards, {d['due']} due)" for d in ds[:6]) + "."
    if _SUMMARY.match(low):
        return summarize_selected()
    if _OPEN.match(low):
        _push({"type": "study", "open": True})
        return "Opening your study tab."

    # ── focus ──────────────────────────────────────────────────────────────────────────────
    if _FOCUS_ON.match(low):
        try:
            import timers
            if _pomodoro():
                return "A focus session is already running."
            it = timers.start_pomodoro()
        except Exception as e:
            return f"I couldn't start focus mode — {e}"
        if not it:
            return "That's too many timers — cancel one first."
        return f"Focus mode on for {timers._say(it['total'])}. Chat pop-ups are muted until the break."
    if _FOCUS_OFF.match(low):
        pom = _pomodoro()
        if not pom:
            return "There's no focus session running."
        import timers
        timers.cancel(pom["id"])
        return "Focus mode off. Pop-ups are back."
    m = _STUDIED.match(low)
    if m:
        st = stats_snapshot()
        when = (m.groupdict().get("when") or "today").replace("  ", " ")
        if when == "this week":
            return f"You've studied {_say_min(st['week_min'])} this week."
        if when == "yesterday":
            return f"Yesterday you studied {_say_min(st['days'][-2]['min'])}."
        extra = f" That's {st['sessions_today']} focus round{'s' if st['sessions_today'] != 1 else ''}." if st["sessions_today"] else ""
        streak = f" Streak: {st['streak_days']} days." if st["streak_days"] > 1 else ""
        return f"Today you've studied {_say_min(st['today_min'])}.{extra}{streak}"

    # ── distraction list ───────────────────────────────────────────────────────────────────
    m = _DIST_ADD.match(low)
    if m:
        app = m.group("app").strip()
        items = distractor_list()
        if app in items:
            return f"{app.title()} is already on your list."
        _save_distractors(items + [app])
        return f"Added {app.title()} to your distractions."
    m = _DIST_DEL.match(low)
    if m:
        app = m.group("app").strip()
        items = distractor_list()
        if app not in items:
            return f"{app.title()} isn't on your list."
        _save_distractors([i for i in items if i != app])
        return f"Removed {app.title()}."
    if _DIST_LIST.match(low):
        items = distractor_list()
        return ("Your distractions: " + ", ".join(i.title() for i in items) + ".") if items else \
            "Your distraction list is empty. Say “add Discord to my distractions”."
    if _DIST_CLOSE.match(low):
        return _close_distractions(engine_context)
    return None


def _say_min(n: int) -> str:
    h, m = divmod(int(n), 60)
    parts = ([f"{h} hour{'s' if h != 1 else ''}"] if h else []) + ([f"{m} minute{'s' if m != 1 else ''}"] if m or not h else [])
    return " ".join(parts)


def _close_distractions(engine_context) -> str:
    """Closing always asks first, one app at a time."""
    open_apps = running_distractors()
    if not open_apps:
        return "None of your distracting apps are open."
    try:
        from permissions import PermissionManager
    except Exception as e:
        return f"I can't ask for confirmation right now — {e}"
    pm = PermissionManager()
    closed: list[str] = []
    for name in sorted(open_apps):
        ctx = dict(engine_context) if isinstance(engine_context, dict) else {}
        ctx.setdefault("confirm_callback", lambda n=name: _close_app(n))   # typed "yes" runs this
        allowed, reason = pm.check_permission("study_close_apps", f"close {name.title()}", ctx)
        if not allowed:
            return reason or "Okay, I'll leave it open."          # typed chat: waits for yes / no
        closed.append(_close_app(name))
    return " ".join(closed)
