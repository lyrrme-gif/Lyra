"""Text snippets: short texts you type often (email, address, a signature line).

  "save snippet email as me@example.com"   -> stores it
  "type my email" / "type snippet email"   -> types it into the window that is in front
  "list my snippets" / "delete snippet email"

Stored as plain text in Lyra's data folder - do NOT keep passwords here. Lyra never reads a
snippet's content aloud; she only says which one she typed.
"""
from __future__ import annotations

import json
import re
import threading
import time

_lock = threading.Lock()


def _file():
    import paths
    return paths.data_path("snippets.json")


def _load() -> dict:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    _file().write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _text(v) -> str:
    """A stored snippet is a plain string (older versions) or {"text": ..., "trigger": ...} (Phase 2)."""
    return v.get("text", "") if isinstance(v, dict) else str(v)


def _find(d: dict, spoken: str) -> str | None:
    """The stored name for what was said: exact words first, then without a leading my / the."""
    full = re.sub(r"\s+", " ", spoken.strip().lower())
    for cand in (full, _key(spoken)):
        if cand in d:
            return cand
    return None


def _key(name: str) -> str:
    n = re.sub(r"^(?:my|the)\s+", "", name.strip().lower())
    return re.sub(r"\s+", " ", n)


_ADD = re.compile(r"^(?:add|save|create|set|store)\s+(?:a\s+|new\s+)*snippet\s+(?:called\s+|named\s+)?(?P<name>[\w ]{1,30}?)\s*(?:as|:|=|to\s+be)\s*(?P<text>.+)$")
_TYPE = re.compile(r"^(?:type|insert|enter|write|paste)\s+(?:out\s+)?(?:my\s+|the\s+)?(?:snippet\s+)?(?P<name>[\w ]{1,30})$")
_LIST = re.compile(r"^(?:list|show|what)\s+(?:me\s+)?(?:my\s+|are\s+my\s+)?snippets(?:\s+do\s+i\s+have)?$|^what\s+snippets\s+do\s+i\s+have$")
_DEL = re.compile(r"^(?:delete|remove|forget)\s+(?:my\s+|the\s+)?snippet\s+(?P<name>[\w ]{1,30})$")


def try_snippets(command: str) -> str | None:
    original = command.strip()
    low = original.lower().rstrip("?.! ")
    low = re.sub(r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:please\s+|can\s+you\s+|could\s+you\s+)?", "", low).strip()
    if not low:
        return None
    m = _ADD.match(low)
    if m:
        name = _key(m.group("name"))
        mo = re.search(r"(?:as|:|=|to\s+be)\s*(.+)$", original.strip().rstrip("."), re.I)
        text = (mo.group(1) if mo else m.group("text")).strip()
        if not name or not text:
            return None
        with _lock:
            d = _load()
            d[name] = text
            _save(d)
        return f"Saved the snippet {name}. Don't keep passwords in snippets."
    if _LIST.match(low):
        names = sorted(_load())
        return ("Your snippets: " + ", ".join(names) + ".") if names else "You have no snippets yet."
    m = _DEL.match(low)
    if m:
        with _lock:
            d = _load()
            name = _find(d, m.group("name"))
            if name is None:
                return f"I don't have a snippet called {_key(m.group('name'))}."
            d.pop(name)
            _save(d)
        return f"Deleted the snippet {name}."
    m = _TYPE.match(low)
    if m:
        d = _load()
        name = _find(d, m.group("name"))
        if name is None:
            return None  # not a snippet: "type hello world" goes to the normal typing command
        try:
            from actions.apps import type_text
            time.sleep(0.25)
            type_text(_text(d[name]), 0.01)
        except Exception as e:  # noqa: BLE001
            return f"I couldn't type it - {e}"
        return f"Typed your {name}."
    return None


# ---------------------------------------------------------------------------
# Phase 2: snippets inside dictation. Say a snippet's TRIGGER phrase while dictating and the full text
# is inserted. Triggers are chosen explicitly (default: the name) - older snippets made by voice have
# none, so an everyday word like "email" never fires by accident.
# ---------------------------------------------------------------------------
MAX_NAME = 40
MAX_TEXT = 4000
MAX_TRIGGER_WORDS = 6
_NAME_OK = re.compile(r"^[\w][\w '\-]*$", re.UNICODE)


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def all_snippets() -> list:
    """[{name, text, trigger}] sorted by name. `trigger` is "" for snippets without a dictation trigger."""
    out = []
    for k, v in sorted(_load().items()):
        if isinstance(v, (str, dict)):
            out.append({"name": k, "text": _text(v), "trigger": (v.get("trigger", "") if isinstance(v, dict) else "")})
    return out


def set_snippet(name: str, text: str, trigger: str | None = None) -> tuple[bool, str]:
    """Create or replace a snippet. `trigger` None = same as the name, "" = voice command only.
    Returns (ok, message or the stored name)."""
    key = _norm(name)
    text = (text or "").strip("\r\n")
    if not key or len(key) > MAX_NAME or not _NAME_OK.match(key):
        return False, "Use a short name made of letters, numbers and spaces."
    if not text.strip():
        return False, "The snippet text is empty."
    if len(text) > MAX_TEXT:
        return False, f"The snippet is too long (max {MAX_TEXT} characters)."
    trig = key if trigger is None else _norm(trigger)
    if trig and (len(trig) > MAX_NAME or len(trig.split()) > MAX_TRIGGER_WORDS or not _NAME_OK.match(trig) or len(trig) < 3):
        return False, "The trigger must be 3-40 characters, at most six words."
    with _lock:
        d = _load()
        clash = [k for k, v in d.items() if k != key and trig and isinstance(v, dict) and v.get("trigger") == trig]
        if clash:
            return False, f"The trigger '{trig}' is already used by the snippet '{clash[0]}'."
        d[key] = {"text": text, "trigger": trig}
        _save(d)
    return True, key


def delete_snippet(name: str) -> bool:
    with _lock:
        d = _load()
        key = _find(d, name or "")
        if key is None:
            return False
        d.pop(key)
        _save(d)
        return True


def clear_all() -> int:
    with _lock:
        n = len(_load())
        _save({})
        return n


def trigger_map() -> dict:
    """spoken trigger -> text, for dictation."""
    return {s["trigger"]: s["text"] for s in all_snippets() if s["trigger"]}
