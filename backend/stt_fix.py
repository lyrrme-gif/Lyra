"""Lyra — fix what the recognizer misheard in a VOICE COMMAND, before the command is acted on.

Same idea as dictation's correction (dictionary.json: your words + "heard -> want" replacements),
but for commands, and local only: no LLM, no network, about a millisecond. Three steps:

  1. Your dictionary replacements (Settings > Dictation) are applied.
  2. A word that is close to a name Lyra knows (dictionary words, contacts, installed apps, the
     built-in brands) is replaced by that name: "anagash" -> "anogashi", "mosab" -> "mosaab",
     "discards" -> "discord". "Close" = very similar spelling, or the same consonant skeleton.
  3. Nothing else is touched. Short words, common words and words that are already a known
     name are never changed, so a normal sentence passes through exactly as it came.

Commands that still do not match anything afterwards keep going to the existing LLM router
(router.classify), which does the heavier correction only when it is really needed.

fix() never raises: on any problem it returns the text it was given.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import threading
import time

log = logging.getLogger("lyra.stt_fix")

_TTL = 30.0                    # seconds the name list is reused (new contacts / apps show up within it)
_MIN_LEN = 4                   # shorter words are never changed
_RATIO = 0.86                  # spelling this close is enough on its own
_RATIO_SKEL = 0.72             # ... or this close when the consonant skeleton is the same

# Words that must never be "corrected" into a name, even if a name happens to look like them.
_STOP = frozenset("""
open close start stop play pause resume next previous skip volume mute unmute louder quieter search find look
send message text call tell show give take make turn switch change set remind reminder timer alarm note notes
what when where which who whom whose why how that this these those there here with from into onto about after
before again always never maybe please thanks thank sorry hello music song songs video videos screen screenshot
record watch read write file files folder folders create delete remove rename move copy paste window windows
tab tabs page browser website google youtube spotify discord whatsapp telegram chrome mode lock sleep wake
weather time today tomorrow tonight morning evening night week month year minute minutes second seconds hour
hours list todo calendar event events schedule scheduled every could would should shall might must have been
being does done doing going gonna want need like love hate know think feel mean help stuff thing things some
something anything nothing everything someone anyone everyone more most less least much many very just only
also even still then than them they their your yours mine ours here's that's what's who's
""".split())

# Always-known names (the brands Lyra's commands are built around).
_BUILTIN = ("spotify", "discord", "whatsapp", "telegram", "youtube", "chrome", "bluetooth", "instagram",
            "netflix", "twitch", "steam", "unity", "valorant", "notepad", "calculator", "brave", "firefox",
            "outlook", "gmail", "github", "vscode", "photoshop", "premiere", "obsidian", "notion")

_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
_VOWELS = re.compile(r"[aeiouyhw]")

_lock = threading.Lock()
_cache: dict = {"t": 0.0, "targets": {}, "known": frozenset()}


def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (word or "").lower())


def skeleton(word: str) -> str:
    """Consonant skeleton: 'anagash' and 'anogashi' both give 'angs' (vowels, h, w, y dropped, repeats merged)."""
    w = _norm(word)
    if not w:
        return ""
    s = w[0] + _VOWELS.sub("", w[1:])
    return re.sub(r"(.)\1+", r"\1", s)


# ---------------------------------------------------------------------------
# The names Lyra knows
# ---------------------------------------------------------------------------
def _dictionary_words() -> list[str]:
    try:
        import dictionary_store
        return list(dictionary_store.words() or [])
    except Exception:
        return []


def _contact_names() -> list[str]:
    out: list[str] = []
    try:
        import paths
        data = json.loads(paths.data_path("contacts.json").read_text(encoding="utf-8"))
    except Exception:
        return out
    if not isinstance(data, dict):
        return out
    for key, entry in data.items():
        if str(key).startswith("_") or not isinstance(entry, dict):
            continue
        out.append(str(key))
        out.extend(str(x) for x in (entry.get("names") or []))
        if entry.get("display"):
            out.append(str(entry["display"]))
        for chat in (entry.get("chat_names") or {}).values():
            out.append(str(chat))
    return out


def _app_names() -> list[str]:
    try:
        import app_scanner
        return list(app_scanner.names() or [])
    except Exception:
        return []


def _build() -> tuple[dict, frozenset]:
    """-> ({normalised word: spoken form}, set of every known normalised word)"""
    targets: dict[str, str] = {}

    def add(raw: str, whole_only: bool = False) -> None:
        raw = (raw or "").strip()
        if not raw:
            return
        parts = [p for p in re.split(r"[\s_\-.]+", raw) if p]
        for p in ([raw] if whole_only else parts):
            n = _norm(p)
            if len(n) >= _MIN_LEN and n not in _STOP and n not in targets:
                targets[n] = n
        if len(parts) == 1:
            n = _norm(raw)
            if len(n) >= _MIN_LEN and n not in _STOP:
                targets[n] = n

    for w in _BUILTIN:
        add(w)
    for w in _dictionary_words():
        add(w)
    for w in _contact_names():
        add(w)
    for w in _app_names():
        add(w)
    return targets, frozenset(targets)


def _vocab() -> tuple[dict, frozenset]:
    now = time.monotonic()
    with _lock:
        if _cache["targets"] and now - _cache["t"] < _TTL:
            return _cache["targets"], _cache["known"]
    targets, known = _build()
    with _lock:
        _cache.update(t=now, targets=targets, known=known)
    return targets, known


def refresh() -> None:
    """Forget the cached name list (tests, or right after the dictionary changed)."""
    with _lock:
        _cache.update(t=0.0, targets={}, known=frozenset())


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def best_match(word: str, targets: dict) -> str | None:
    """The known name `word` was most likely meant to be, or None. Pure: no I/O."""
    n = _norm(word)
    if len(n) < _MIN_LEN or n in _STOP or n in targets:
        return None
    sk = skeleton(n)
    best, best_r = None, 0.0
    for cand in targets:
        if cand[0] != n[0] or abs(len(cand) - len(n)) > 3:
            continue
        r = difflib.SequenceMatcher(None, n, cand).ratio()
        ok = r >= _RATIO or (r >= _RATIO_SKEL and len(sk) >= 3 and skeleton(cand) == sk)
        if ok and r > best_r:
            best, best_r = cand, r
    return best


def _join_pairs(tokens: list[re.Match], text: str, targets: dict) -> list[tuple[int, int, str]]:
    """'spot ify' / 'ano gashi': two neighbouring words that together spell a known name."""
    out = []
    for a, b in zip(tokens, tokens[1:]):
        if text[a.end():b.start()].strip():        # punctuation between them: not one word
            continue
        joined = _norm(a.group() + b.group())
        if len(joined) < 6 or joined in _STOP:
            continue
        if joined in targets:
            out.append((a.start(), b.end(), joined))
            continue
        hit = best_match(joined, targets)
        if hit:
            out.append((a.start(), b.end(), hit))
    return out


def correct_names(text: str, targets: dict | None = None) -> str:
    """Step 2 only (pure when `targets` is given)."""
    if not text:
        return text
    if targets is None:
        targets, _ = _vocab()
    if not targets:
        return text
    tokens = list(_TOKEN.finditer(text))
    edits: list[tuple[int, int, str]] = []
    taken: set[int] = set()
    for s, e, rep in _join_pairs(tokens, text, targets):
        # only join two words when NEITHER is already a known name or a common word
        inner = [m for m in tokens if s <= m.start() < e]
        if any(_norm(m.group()) in targets or _norm(m.group()) in _STOP for m in inner):
            continue
        edits.append((s, e, rep))
        taken.update(m.start() for m in inner)
    for m in tokens:
        if m.start() in taken:
            continue
        hit = best_match(m.group(), targets)
        if hit:
            edits.append((m.start(), m.end(), hit))
    if not edits:
        return text
    out, pos = [], 0
    for s, e, rep in sorted(edits):
        out.append(text[pos:s])
        out.append(rep)
        pos = e
    out.append(text[pos:])
    return "".join(out)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def fix(text: str) -> str:
    """The command with misheard words repaired (dictionary replacements, then known names)."""
    if not text or not text.strip():
        return text
    original = text
    try:
        try:
            import dictionary_store
            text = dictionary_store.apply_replacements(text)
        except Exception:
            pass
        text = correct_names(text)
        text = re.sub(r"\s+", " ", text).strip()
        if text.lower() != original.lower().strip():
            log.info("stt_fix: %r -> %r", original, text)
        return text
    except Exception as e:                      # noqa: BLE001 - must never break a turn
        log.debug("stt_fix skipped: %s", e)
        return original


def prompt_terms(limit: int = 24) -> list[str]:
    """Names to give Whisper as a hint, so it hears them right in the first place (not only fixed afterwards)."""
    try:
        targets, _ = _vocab()
        own = []
        for w in _dictionary_words() + _contact_names():
            n = _norm(w.split()[0]) if w.strip() else ""
            if len(n) >= _MIN_LEN and n not in own:
                own.append(n)
        rest = [t for t in targets if t not in own]
        return (own + rest)[:limit]
    except Exception:
        return []
