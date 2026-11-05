"""Lyra — personal dictionary (Phase 2, contract C4).

Two lists, both stored as plain JSON in Lyra's data folder (paths.data_path):
  * words         names / jargon you want recognised ("Kubernetes", "Anouk"). They are passed to the
                  speech engine as hints, and a word with capitals or digits ("iPhone", "GitHub")
                  also fixes the capitalisation when the engine writes it in lower case.
  * replacements  "what the recognizer writes" -> "what you want" ("gonna" -> "going to").

Applied AFTER speech recognition. Nothing here touches the network.
Public API (C4): hints() -> list[str], apply_replacements(text) -> str.
"""
from __future__ import annotations

import json
import os
import re
import threading

MAX_WORDS = 500
MAX_REPLACEMENTS = 500
MAX_LEN = 200

_lock = threading.RLock()
_cache: dict | None = None
_path_override = None            # tests point this at a temp file


def _file():
    if _path_override is not None:
        return _path_override
    from engine.core import paths
    return paths.data_path("dictionary.json")


def _empty() -> dict:
    return {"words": [], "replacements": []}


def _clean_text(v, limit=MAX_LEN) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:limit]


def _load() -> dict:
    global _cache
    with _lock:
        if _cache is None:
            try:
                d = json.loads(_file().read_text(encoding="utf-8"))
                words = [w for w in (_clean_text(x) for x in d.get("words", [])) if w]
                reps = []
                for r in d.get("replacements", []):
                    a, b = _clean_text(r.get("from")), _clean_text(r.get("to"), 1000)
                    if a:
                        reps.append({"from": a, "to": b})
                _cache = {"words": words[:MAX_WORDS], "replacements": reps[:MAX_REPLACEMENTS]}
            except Exception:
                _cache = _empty()
        return _cache


def _save(d: dict) -> None:
    global _cache
    with _lock:
        _cache = d
        f = _file()
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, f)


def reload() -> None:
    """Forget the in-memory copy (the file is read again on next use)."""
    global _cache
    with _lock:
        _cache = None


# -- words ----------------------------------------------------------------------------------
def words() -> list[str]:
    return list(_load()["words"])


def add_word(word: str) -> bool:
    w = _clean_text(word)
    with _lock:
        d = _load()
        if not w or any(x.lower() == w.lower() for x in d["words"]) or len(d["words"]) >= MAX_WORDS:
            return False
        _save({**d, "words": d["words"] + [w]})
        return True


def remove_word(word: str) -> bool:
    w = _clean_text(word).lower()
    with _lock:
        d = _load()
        keep = [x for x in d["words"] if x.lower() != w]
        if len(keep) == len(d["words"]):
            return False
        _save({**d, "words": keep})
        return True


def hints() -> list[str]:
    """Words to bias the speech engine with (C4)."""
    return words()


# -- replacements ---------------------------------------------------------------------------
def replacements() -> list[dict]:
    return [dict(r) for r in _load()["replacements"]]


def set_replacement(src: str, dst: str) -> bool:
    a, b = _clean_text(src), _clean_text(dst, 1000)
    if not a:
        return False
    with _lock:
        d = _load()
        reps = [r for r in d["replacements"] if r["from"].lower() != a.lower()]
        if len(reps) >= MAX_REPLACEMENTS:
            return False
        reps.append({"from": a, "to": b})
        _save({**d, "replacements": reps})
        return True


def clear() -> None:
    """Erase the whole dictionary (words and replacements)."""
    _save(_empty())


# -- applying -------------------------------------------------------------------------------
def _canonical_case_entries(ws: list[str]) -> dict[str, str]:
    """Single words that carry capitals or digits after the first letter become automatic fixes."""
    out = {}
    for w in ws:
        if " " not in w and (any(c.isupper() for c in w[1:]) or any(c.isdigit() for c in w) or (w[:1].isupper() and w.lower() != w)):
            out[w.lower()] = w
    return out


def phrase_entries() -> dict[str, str]:
    """trigger -> replacement for dictation_text.PhraseMap (explicit replacements win over case fixes)."""
    d = _load()
    entries = _canonical_case_entries(d["words"])
    for r in d["replacements"]:
        entries[r["from"]] = r["to"]
    return entries


def apply_replacements(text: str) -> str:
    """Apply the dictionary to finished text (C4). Whole words only, any capitals, punctuation kept."""
    if not text:
        return text
    entries = phrase_entries()
    if not entries:
        return text
    from engine.voice import dictation_text
    return dictation_text.apply_phrases(text, dictation_text.PhraseMap(entries))


# -- voice snippets (live in actions/snippets.py) --------------------------------------------
_snippets_mod = None


def snippets_module():
    """actions.snippets, loaded safely. Importing the `actions` package starts the whole action router;
    if that is not possible (a bare test environment, a broken optional dependency) the file is
    loaded on its own, because snippets.py only needs the standard library."""
    global _snippets_mod
    if _snippets_mod is None:
        try:
            from engine.actions import snippets as mod
        except Exception:
            import importlib.util
            from pathlib import Path
            spec = importlib.util.spec_from_file_location(
                "lyra_snippets_standalone", Path(__file__).resolve().parent.parent / "actions" / "snippets.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        _snippets_mod = mod
    return _snippets_mod
