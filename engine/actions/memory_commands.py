"""Lyra — 'remember ...' voice/text commands, backed by memory.py.

This module provides the voice/text command handlers that let the user
store long-term facts and path aliases into the SQLite memory database.

Two patterns are checked in this order (path is more specific and must be
checked first, or the generic fact pattern would swallow it too):
    "remember my drive folder is at D:\\Sync\\Drive"   -> path alias
    "remember that my favorite color is blue"          -> fact

The actual storage is delegated to memory.py (remember_path / remember_fact).
These functions only parse the utterance and return a spoken confirmation.
"""

import re  # Used only for the two compiled regex patterns below

from engine.brain import memory  # The SQLite FTS5 memory backend (remember_path / remember_fact)

# ---------------------------------------------------------------------------
# Regex patterns that extract alias/path or key/value from the user's words
# ---------------------------------------------------------------------------

# Matches: "remember [that] my <alias> folder/path/location is [at] <path>"
# Named groups:
#   alias  → the friendly name the user wants to use later ("drive folder")
#   path   → the actual filesystem path they want stored
REMEMBER_PATH_PATTERN = re.compile(
    r"remember (?:that )?my (?P<alias>.+?) (?:folder|path|location) is (?:at )?(?P<path>.+)$",
    re.IGNORECASE,  # Case-insensitive so "Remember MY Drive..." still matches
)

# Matches: "remember [that] <key> is <value>"
# Named groups:
#   key    → the fact name ("my favorite color")
#   value  → the fact value ("blue")
# This is deliberately more generic and is only tried AFTER the path pattern.
REMEMBER_FACT_PATTERN = re.compile(
    r"remember (?:that )?(?P<key>.+?) is (?P<value>.+)$",
    re.IGNORECASE,
)


def remember_path_command(alias: str, path: str) -> str:
    """Store a named path alias and return a spoken confirmation string.

    Called by the action router when REMEMBER_PATH_PATTERN matches.
    Does not speak itself — the caller (main loop) is responsible for TTS.

    Args:
        alias: Friendly name extracted from speech (e.g. "drive folder")
        path:  Absolute or relative filesystem path to remember

    Returns:
        A short natural-language confirmation for the TTS engine.
    """
    # Persist the alias → path mapping into the SQLite memory DB
    memory.remember_path(alias, path)
    # Return the phrase Lyra will speak out loud
    return f"Got it — I'll remember your {alias} is at {path}."


def remember_fact_command(key: str, value: str) -> str:
    """Store an arbitrary key/value fact and return a spoken confirmation.

    Called by the action router when REMEMBER_FACT_PATTERN matches.

    Args:
        key:   Fact name extracted from speech (e.g. "my favorite color")
        value: Fact value (e.g. "blue")

    Returns:
        A short natural-language confirmation for the TTS engine.
    """
    # "arcane is my favourite anime" reads backwards for a fact table: store it as
    # "my favourite anime" -> "arcane" so it is clear in Settings and in the prompt.
    k, v = key.strip(), value.strip().rstrip(".!")
    if re.match(r"(?:my|our)\s", v, re.I) and not re.match(r"(?:my|our)\s", k, re.I):
        k, v = v, k
    # Persist the key → value mapping into the SQLite memory DB
    memory.remember_fact(k, v)
    # Return the phrase Lyra will speak out loud
    return f"Got it — I'll remember {k} is {v}."


# "remember ..." at the START of a request (optionally after "hey Lyra", "please" ...).
_REMEMBER_LEAD = re.compile(r"^\s*(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:please\s+)?remember\b", re.IGNORECASE)


def try_remember(command: str) -> str | None:
    """Handle "remember ..." directly, before any AI planner can turn it into a note.

    Returns the spoken confirmation, or None when the request is not a memory command."""
    if not _REMEMBER_LEAD.match(command or ""):
        return try_auto_remember(command)      # no keyword: still learn clear facts about the user
    text = re.sub(r"^\s*(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:please\s+)?", "", command.strip(), flags=re.I)
    m = REMEMBER_PATH_PATTERN.search(text)
    if m:
        return remember_path_command(m.group("alias").strip(), m.group("path").strip())
    m = REMEMBER_FACT_PATTERN.search(text)
    if m:
        return remember_fact_command(m.group("key").strip(), m.group("value").strip())
    return None


# ---------------------------------------------------------------------------
# Automatic memory: personal facts said in passing, no "remember" needed
# ---------------------------------------------------------------------------
# Only clear, lasting statements about the user are saved ("my birthday is ...", "I'm 17 years old",
# "I live in ..."). Moods, devices and requests ("my battery is low", "I'm tired", "open spotify")
# never match. Email, phone and address are NOT saved automatically (they would travel with every
# prompt); say "remember my email is ..." if you want those kept.
_ATTR = (
    r"(?:first name|last name|full name|name|nickname|birthday|birth ?date|date of birth|age|"
    r"fav(?:ou?rite)?\s+[a-z][a-z' -]{1,30}|hometown|home town|city|country|nationality|job|occupation|profession|"
    r"major|school|university|college|company|employer|pronouns|zodiac sign|star sign|height|weight|shoe size|"
    r"blood type|allergy|allergies|hobby|hobbies|native language|mother tongue|time ?zone|"
    r"(?:dog|cat|pet|brother|sister|mom|mum|mother|dad|father|wife|husband|girlfriend|boyfriend|partner|best friend)(?:'s name)?)"
)
_AUTO_MY = re.compile(rf"^my\s+(?P<k>{_ATTR})\s+(?:is|are|was)\s+(?P<v>[^?]{{1,80}})$", re.IGNORECASE)
_AUTO_AGE = re.compile(r"^(?:i am|i'm|im)\s+(?P<v>\d{1,3})(?:\s+years?\s+old)?$", re.IGNORECASE)
_AUTO_AGE2 = re.compile(r"^(?:i am|i'm|im)\s+(?P<v>\d{1,3})\s+years?\s+old\b", re.IGNORECASE)
_AUTO_BORN = re.compile(r"^i\s+was\s+born\s+(?:on\s+|in\s+)(?P<v>[^?]{3,40})$", re.IGNORECASE)
_AUTO_LIVE = re.compile(r"^(?P<w>i live in|i'm based in|i am based in|i'm from|i am from|im from|i come from)\s+(?P<v>[^?]{2,50})$", re.IGNORECASE)
_AUTO_WORK = re.compile(r"^(?P<w>i work as an?|i work as|i work at|i work for|i study|i'm studying|i am studying)\s+(?P<v>[^?]{2,60})$", re.IGNORECASE)
_AUTO_CALL = re.compile(r"^(?:call me|you can call me|my friends call me)\s+(?P<v>[a-z][\w' -]{0,30})$", re.IGNORECASE)
_AUTO_ALLERGIC = re.compile(r"^i(?:'m| am)\s+allergic\s+to\s+(?P<v>[^?]{2,50})$", re.IGNORECASE)
_NOT_A_STATEMENT = re.compile(r"^(?:what|who|when|where|why|how|which|is|are|do|does|did|can|could|will|would|should|tell|show|open|play|set|turn|search)\b", re.IGNORECASE)
# a trailing clause that is really a second request ("... and open spotify")
_TRAILING_REQUEST = re.compile(r"\b(?:and|then|also)\s+(?:please\s+)?(?:open|play|set|turn|search|send|call|remind|tell|show)\b", re.IGNORECASE)


def _auto_enabled() -> bool:
    try:
        from engine.core import settings_store
        return bool(settings_store.get("auto_memory", True))
    except Exception:
        return True


def _auto_facts(text: str) -> tuple[str, str] | None:
    if _NOT_A_STATEMENT.match(text) or "?" in text or len(text) > 120 or _TRAILING_REQUEST.search(text):
        return None
    m = _AUTO_MY.match(text)
    if m:
        k, v = m.group("k").strip().lower(), m.group("v").strip().rstrip(".!")
        k = re.sub(r"^fav\s", "favourite ", k)
        k = re.sub(r"^favorite\b", "favourite", k)
        return ("my " + k, v)
    m = _AUTO_AGE.match(text) or _AUTO_AGE2.match(text)
    if m and 3 <= int(m.group("v")) <= 120:
        return ("my age", m.group("v"))
    m = _AUTO_BORN.match(text)
    if m:
        return ("my birthday", m.group("v").strip().rstrip(".!"))
    m = _AUTO_LIVE.match(text)
    if m:
        key = "where I'm from" if re.search(r"from", m.group("w"), re.I) else "where I live"
        return (key, m.group("v").strip().rstrip(".!"))
    m = _AUTO_WORK.match(text)
    if m:
        w = m.group("w").lower()
        key = "my job" if w.startswith("i work as") else "where I work" if w.startswith("i work") else "what I study"
        return (key, m.group("v").strip().rstrip(".!"))
    m = _AUTO_CALL.match(text)
    if m:
        return ("what to call me", m.group("v").strip().rstrip(".!"))
    m = _AUTO_ALLERGIC.match(text)
    if m:
        return ("I'm allergic to", m.group("v").strip().rstrip(".!"))
    return None


def try_auto_remember(command: str) -> str | None:
    """Save a clear personal fact without the word "remember". None when it is not one."""
    if not _auto_enabled():
        return None
    text = re.sub(r"^\s*(?:(?:hey|ok|okay|so|well)[,\s]+)?(?:lyra[,:]?\s+)?", "", (command or "").strip(), flags=re.I).strip()
    found = _auto_facts(text)
    if not found:
        return None
    key, value = found
    memory.remember_fact(key, value)
    return f"Got it — I'll remember {key} is {value}."
