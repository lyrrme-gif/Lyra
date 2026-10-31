"""Notes and lists by voice, plus clipboard AI (translate / summarize / fix what you copied).

Notes and lists are plain JSON in Lyra's data folder (paths.data_path("notes.json")), so they
survive restarts and stay on this PC. Everything here returns a short sentence Lyra can speak,
or None when the phrase is not for this module.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

_lock = threading.Lock()


def _file() -> Path:
    import paths
    return paths.data_path("notes.json")


def _load() -> dict:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("lists", {})
            data.setdefault("notes", [])
            data.setdefault("done", {})   # completed to-dos kept (struck through) until cleared
            return data
    except Exception:
        pass
    return {"lists": {}, "notes": [], "done": {}}


def _save(data: dict) -> None:
    _file().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── to-do list on the island ─────────────────────────────────────────────────────────────────
# The island reads/changes the "to-do" list through the helpers below (via ui_bridge /todos).
# Open items live in lists["to-do"] exactly as before (voice commands are unchanged); ticked
# items move to done["to-do"] so they stay visible, struck through, until removed or cleared.

TODO = "to-do"


def _changed(open_view: bool = False) -> None:
    """Tell the island the to-do list changed (and optionally to show it)."""
    try:
        import ui_bridge
        ui_bridge.push_todos(open_view)
    except Exception:
        pass


def todo_items() -> list[dict]:
    with _lock:
        d = _load()
        active = list(d["lists"].get(TODO, []))
        done = list(d["done"].get(TODO, []))
    return [{"text": t, "done": False} for t in active] + [{"text": t, "done": True} for t in done]


def todo_add(text: str) -> bool:
    text = re.sub(r"\s+", " ", (text or "")).strip()[:200]
    if not text:
        return False
    with _lock:
        d = _load()
        lst = d["lists"].setdefault(TODO, [])
        if text.lower() in (x.lower() for x in lst):
            return False
        lst.append(text)
        _save(d)
    return True


def todo_set_done(text: str, done: bool) -> bool:
    src, dst = (TODO, TODO)
    with _lock:
        d = _load()
        a, b = d["lists"].setdefault(src, []), d["done"].setdefault(dst, [])
        frm, to = (a, b) if done else (b, a)
        hit = next((x for x in frm if x == text), None)
        if hit is None:
            return False
        frm.remove(hit)
        if hit.lower() not in (x.lower() for x in to):
            to.append(hit)
        _save(d)
    return True


def todo_remove(text: str) -> bool:
    with _lock:
        d = _load()
        removed = False
        for bucket in (d["lists"].setdefault(TODO, []), d["done"].setdefault(TODO, [])):
            hit = next((x for x in bucket if x == text), None)
            if hit is not None:
                bucket.remove(hit)
                removed = True
        if removed:
            _save(d)
    return removed


# ── list names ───────────────────────────────────────────────────────────────────────────────

_ALIASES = {
    "list": "to-do", "to-do": "to-do", "todo": "to-do", "to do": "to-do", "tasks": "to-do", "task": "to-do",
    "shopping": "shopping", "grocery": "shopping", "groceries": "shopping", "buy": "shopping",
}


def _undo_hint(hint: dict) -> None:
    try:
        import activity_log
        activity_log.set_undo(hint)
    except Exception:
        pass


def _list_name(raw: str | None) -> str:
    n = (raw or "").strip().lower().replace("-", " ")
    n = re.sub(r"\s+", " ", n)
    if not n:
        return "to-do"
    return _ALIASES.get(n, _ALIASES.get(n.replace(" ", "-"), n))


def _say_items(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


# ── patterns ─────────────────────────────────────────────────────────────────────────────────

_NOTE_ADD = re.compile(r"^(?:take|make|add|write|save)\s+(?:a\s+)?(?:quick\s+)?note\s*(?:that|:|-)?\s*(?P<t>.+)$"
                       r"|^note\s+(?:that|:)\s*(?P<t2>.+)$|^write\s+(?:this|that)\s+down\s*[:\-]?\s*(?P<t3>.+)$")
_NOTE_SHOW = re.compile(r"^(?:what(?:'s|\s+are|\s+is)?\s+(?:in\s+)?my\s+notes|(?:show|read|list)(?:\s+me)?\s+(?:all\s+)?(?:my\s+)?notes"
                        r"|what\s+notes\s+do\s+i\s+have|what\s+did\s+i\s+(?:note|write\s+down))$")
_NOTE_CLEAR = re.compile(r"^(?:clear|delete|erase)\s+(?:all\s+)?(?:my\s+)?notes$")

_LIST_ADD = re.compile(r"^(?:please\s+)?add\s+(?P<item>.+?)\s+to\s+(?:my\s+|the\s+)?(?P<list>[\w\- ]+?)(?:\s+list)?$")
_LIST_SHOW = re.compile(r"^(?:what(?:'s|\s+is)\s+(?:on|in)\s+(?:my\s+|the\s+)?(?P<list>[\w\- ]+?)\s+list"
                        r"|(?:show|read|list)(?:\s+me)?\s+(?:my\s+|the\s+)?(?P<list2>[\w\- ]+?)\s+list"
                        r"|what\s+(?:do\s+i\s+need\s+to\s+buy|should\s+i\s+buy)(?P<shop>)"
                        r"|what\s+(?:do\s+i\s+have\s+to\s+do|are\s+my\s+tasks)(?P<todo>))$")
_LIST_REMOVE = re.compile(r"^(?:remove|delete|cross\s+off|take\s+off|tick\s+off)\s+(?P<item>.+?)\s+(?:from|off)\s+"
                          r"(?:my\s+|the\s+)?(?P<list>[\w\- ]+?)(?:\s+list)?$")
_LIST_CLEAR = re.compile(r"^(?:clear|empty|wipe)\s+(?:my\s+|the\s+)?(?P<list>[\w\- ]+?)\s+list$")


def _notes(low: str, original: str) -> str | None:
    m = _NOTE_ADD.match(low)
    if m:
        # keep the user's capitalisation for the note text
        text = (m.group("t") or m.group("t2") or m.group("t3") or "").strip()
        mo = re.search(re.escape(text), original, re.I)
        text = original[mo.start():mo.end()] if mo else text
        if not text:
            return None
        with _lock:
            d = _load()
            d["notes"].append({"t": int(time.time()), "text": text})
            _save(d)
        _undo_hint({"type": "note_remove", "text": text})
        return "Noted."
    if _NOTE_SHOW.match(low):
        d = _load()
        if not d["notes"]:
            return "You have no notes."
        last = d["notes"][-5:]
        return f"You have {len(d['notes'])} note{'s' if len(d['notes']) != 1 else ''}. Latest: " + "; ".join(n["text"] for n in reversed(last)) + "."
    if _NOTE_CLEAR.match(low):
        with _lock:
            d = _load()
            n = len(d["notes"])
            d["notes"] = []
            _save(d)
        return f"Cleared {n} note{'s' if n != 1 else ''}."
    return None


def _lists(low: str, original: str) -> str | None:
    m = _LIST_ADD.match(low)
    if m and not re.search(r"\b(?:playlist|queue|calendar|favou?rites?|contacts?|library|cart|basket|bookmarks?)\b", low):
        name = _list_name(m.group("list"))
        # only treat it as a list command when the list word is plausible
        if name in ("to-do", "shopping") or "list" in low:
            item = m.group("item").strip(" .,")
            mo = re.search(re.escape(item), original, re.I)
            item = original[mo.start():mo.end()] if mo else item
            if not item:
                return None
            with _lock:
                d = _load()
                lst = d["lists"].setdefault(name, [])
                if item.lower() in (x.lower() for x in lst):
                    return f"{item} is already on your {name} list."
                lst.append(item)
                _save(d)
            _undo_hint({"type": "list_remove", "list": name, "item": item})
            if name == TODO:
                _changed()
            return f"Added {item} to your {name} list."
    m = _LIST_SHOW.match(low)
    if m:
        if m.group("shop") is not None:
            name = "shopping"
        elif m.group("todo") is not None:
            name = "to-do"
        else:
            name = _list_name(m.group("list") or m.group("list2"))
        items = _load()["lists"].get(name, [])
        if name == TODO:
            _changed(True)   # "show my to-do list" also opens it on the island
        if not items:
            return f"Your {name} list is empty."
        return f"Your {name} list has {len(items)} item{'s' if len(items) != 1 else ''}: {_say_items(items)}."
    m = _LIST_REMOVE.match(low)
    if m:
        name, item = _list_name(m.group("list")), m.group("item").strip(" .,").lower()
        with _lock:
            d = _load()
            lst = d["lists"].get(name, [])
            hit = next((x for x in lst if x.lower() == item), None) or next((x for x in lst if item in x.lower()), None)
            if not hit:
                return None  # maybe not about our lists (e.g. removing a file): let others try
            lst.remove(hit)
            _save(d)
        if name == TODO:
            _changed()
        return f"Removed {hit} from your {name} list."
    m = _LIST_CLEAR.match(low)
    if m:
        name = _list_name(m.group("list"))
        with _lock:
            d = _load()
            if name not in d["lists"]:
                return None
            n = len(d["lists"][name])
            d["lists"][name] = []
            d["done"].pop(name, None)
            _save(d)
        if name == TODO:
            _changed()
        return f"Cleared your {name} list ({n} item{'s' if n != 1 else ''})."
    return None


# ── clipboard AI ─────────────────────────────────────────────────────────────────────────────

_CLIP_AI = re.compile(
    r"^(?P<verb>translate|summari[sz]e|fix|proofread|rewrite|shorten|explain|simplify|correct)\s+"
    r"(?:(?:what\s+i\s+(?:just\s+)?copied)|(?:the\s+|my\s+)?clipboard(?:\s+text)?|(?:the\s+)?(?:text\s+)?i\s+copied)"
    r"(?:\s+(?:to|into|in)\s+(?P<lang>[a-z]+))?$"
)
_LANGS = {"french", "english", "spanish", "arabic", "german", "italian", "portuguese", "dutch", "russian",
          "turkish", "japanese", "korean", "chinese", "hindi", "darija"}


def _clipboard_ai(low: str) -> str | None:
    m = _CLIP_AI.match(low)
    if not m:
        return None
    verb, lang = m.group("verb"), (m.group("lang") or "").lower()
    if verb == "translate" and lang and lang not in _LANGS:
        return None
    try:
        from actions.system import get_clipboard_text
        text = (get_clipboard_text() or "").strip()
    except Exception:
        text = ""
    if not text:
        return "Your clipboard is empty."
    text = text[:6000]
    tasks = {
        "translate": f"Translate the text below into {lang or 'English'}.",
        "summarize": "Summarize the text below in two or three short sentences.",
        "summarise": "Summarize the text below in two or three short sentences.",
        "fix": "Fix the spelling and grammar of the text below, keeping its meaning and tone.",
        "correct": "Fix the spelling and grammar of the text below, keeping its meaning and tone.",
        "proofread": "Fix the spelling and grammar of the text below, keeping its meaning and tone.",
        "rewrite": "Rewrite the text below so it reads clearly and naturally.",
        "shorten": "Make the text below shorter while keeping the key points.",
        "explain": "Explain the text below simply, in a few short sentences.",
        "simplify": "Rewrite the text below in simpler words.",
    }
    prompt = (f"{tasks[verb]} Reply with ONLY the result - no introduction, no quotes, no comments.\n\n"
              f"Text:\n{text}")
    try:
        import brain
        reply = brain.ask_brain(prompt, [], intent="chat")
    except Exception as e:  # noqa: BLE001
        return f"I couldn't do that - {e}"
    return (reply or "").strip() or "I couldn't get a result."


# ── entry point ──────────────────────────────────────────────────────────────────────────────

def try_notes(command: str) -> str | None:
    original = command.strip()
    low = original.lower().rstrip("?.! ")
    low = re.sub(r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:please\s+|can\s+you\s+|could\s+you\s+)?", "", low).strip()
    if not low:
        return None
    return _notes(low, original) or _lists(low, original) or _clipboard_ai(low)
