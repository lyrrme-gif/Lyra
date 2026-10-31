"""Lyra — dictation text rules (pure logic: no audio, no OS calls, easy to test).

dictation.py feeds the recognizer's growing partial text into a WordStream and types
whatever it returns. WordStream decides WHICH words are safe to type yet (the last
1-2 stay pending, they can still change) and Formatter decides HOW they are written:
capital letters, "I", fillers dropped, and spoken lists:

    "note one open the door next point close the door"
        ->  "1. Open the door\\n2. Close the door"

Punctuation and self-corrections ("nine, no, ten") are left to the polish pass.
"""

from __future__ import annotations

import re

_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}
_FILLER = re.compile(r"^(?:uh+|um+|umm+|erm|hmm+)$", re.I)
_I_FORMS = {"i": "I", "i'm": "I'm", "i'll": "I'll", "i've": "I've", "i'd": "I'd"}
_NEXT_ITEM = ("point", "item", "note")      # "next point", "next item", "next note"


def _number(word: str) -> int | None:
    w = word.strip(".,").lower()
    if w.isdigit() and len(w) <= 2:
        return int(w)
    return _NUMBERS.get(w)


# ---------------------------------------------------------------------------
# Phase 2: spoken phrases (dictionary replacements + voice snippets)
# ---------------------------------------------------------------------------
_TOKEN_STRIP = ".,;:!?\u2026\"\u201c\u201d()[]"


def norm_token(word: str) -> str:
    """Lower-case a spoken word without surrounding punctuation (\"Email,\" -> \"email\")."""
    return (word or "").strip().strip(_TOKEN_STRIP).lower()


def norm_phrase(text: str) -> tuple[str, ...]:
    return tuple(t for t in (norm_token(w) for w in (text or "").split()) if t)


class PhraseMap:
    """Spoken trigger phrases -> the text that replaces them.

    Matching is whole-word, case-insensitive and ignores punctuation. The longest trigger wins.
    Pure data structure: the stores (dictionary_store, actions.snippets) build it."""

    def __init__(self, entries: dict[str, str] | None = None) -> None:
        self._map: dict[tuple[str, ...], str] = {}
        self._prefixes: set[tuple[str, ...]] = set()
        self.max_len = 0
        for trigger, text in (entries or {}).items():
            key = norm_phrase(trigger)
            if not key or text is None:
                continue
            self._map[key] = str(text)
            self.max_len = max(self.max_len, len(key))
            for n in range(1, len(key)):
                self._prefixes.add(key[:n])

    def __bool__(self) -> bool:
        return bool(self._map)

    def __len__(self) -> int:
        return len(self._map)

    def match_at(self, words: list[str], i: int) -> tuple[int, str] | None:
        """Longest trigger that starts at words[i]: (number of words used, replacement) or None."""
        toks = [norm_token(w) for w in words[i:i + self.max_len]]
        for n in range(min(len(toks), self.max_len), 0, -1):
            hit = self._map.get(tuple(toks[:n]))
            if hit is not None:
                return n, hit
        return None

    def could_continue(self, tail: list[str]) -> bool:
        """True if `tail` (the last spoken words) is the START of a longer trigger, so the next words may
        still complete it and the words must not be typed yet."""
        return bool(tail) and tuple(norm_token(w) for w in tail) in self._prefixes


class Literal(str):
    """Text that is inserted exactly as given (a replacement or a snippet), not re-capitalised."""


def apply_phrases(text: str, phrases: "PhraseMap | dict[str, str]") -> str:
    """Replace trigger phrases inside finished text (any punctuation / capitals between the words).
    A trigger that ends the text swallows its own trailing full stop / comma, so a snippet such as an
    e-mail address is not left with a stray dot."""
    pm = phrases if isinstance(phrases, PhraseMap) else PhraseMap(phrases)
    if not pm or not text:
        return text
    pieces = list(re.finditer(r"\S+", text))
    out, pos, i = [], 0, 0
    words = [m.group(0) for m in pieces]
    while i < len(words):
        hit = pm.match_at(words, i)
        if hit is None:
            i += 1
            continue
        n, rep = hit
        start, end = pieces[i].start(), pieces[i + n - 1].end()
        last = words[i + n - 1]
        tail = last[len(last.rstrip(_TOKEN_STRIP)):]            # punctuation stuck to the last word
        out.append(text[pos:start])
        out.append(rep)
        if tail and i + n < len(words):
            out.append(tail)                                    # mid-text punctuation stays
        elif tail and not tail.strip(".,"):
            pass                                                # swallowed at the very end
        elif tail:
            out.append(tail)
        pos = end
        i += n
    out.append(text[pos:])
    return "".join(out)


class Formatter:
    """Words in, typed text out. Keeps the list and capitalisation state of one dictation."""

    def __init__(self) -> None:
        self._list_n = 0              # 0 = no list yet, else the number of the current item
        self._has_output = False
        self._cap = True              # capitalise the next word
        self._held: str | None = None  # "note" / "next": waiting for the word that decides

    def push(self, words: list[str]) -> str:
        return "".join(self._literal(w) if isinstance(w, Literal) else self._word(w) for w in words)

    def _literal(self, text: str) -> str:
        """A replacement / snippet: inserted as is (a pending \"note\" is plain text first)."""
        out = self.flush()
        if not text:
            return out
        out += (" " if self._has_output else "") + str(text)
        self._has_output = True
        self._cap = text.rstrip()[-1:] in ".!?\n"
        return out

    def flush(self) -> str:
        """End of the dictation: a held word that never turned into a list marker is plain text."""
        if self._held is None:
            return ""
        held, self._held = self._held, None
        return self._plain(held)

    def _word(self, w: str) -> str:
        low = w.lower()
        if self._held is not None:
            first, self._held = self._held, None
            marker = self._marker(first, low)
            if marker is not None:
                return marker
            return self._plain(first) + self._word(w)    # `w` may itself be "note" / "next"
        if low == "note" or (low == "next" and self._list_n):
            self._held = low
            return ""
        return self._plain(w)

    def _marker(self, first: str, low: str) -> str | None:
        if first == "note":
            n = _number(low)
            # "note one" starts a list; later items must follow on (2, 3, ...), so a sentence like
            # "take note 5 minutes ago" is left alone.
            if n is not None and ((not self._list_n and n == 1) or (self._list_n and n == self._list_n + 1)):
                return self._item(n)
        elif first == "next" and low in _NEXT_ITEM:
            return self._item(self._list_n + 1)
        return None

    def _item(self, n: int) -> str:
        self._list_n = n
        out = ("\n" if self._has_output else "") + f"{n}."
        self._has_output = True
        self._cap = True
        return out

    def _plain(self, w: str) -> str:
        if _FILLER.match(w):
            return ""
        w = _I_FORMS.get(w.lower(), w)
        if self._cap:
            w = w[:1].upper() + w[1:]
            self._cap = False
        out = (" " if self._has_output else "") + w
        self._has_output = True
        return out


class WordStream:
    """Turns the recognizer's growing partial text of ONE segment into text to type now."""

    def __init__(self, pending: int = 2, phrases: PhraseMap | None = None) -> None:
        self._pending = max(0, pending)
        self._done = 0                # words of the current segment already handed out
        self._fmt = Formatter()
        self._phrases = phrases or PhraseMap()

    def update(self, text: str, flush: bool = False) -> str:
        """`text` is the latest partial. Returns the text to type now ("" = nothing yet).
        flush=True hands out the pending words too (the speaker paused / the segment ended)."""
        words = (text or "").split()
        limit = len(words) if flush else max(0, len(words) - self._pending)
        if self._phrases and not flush:
            # do not type words that may still grow into a longer trigger ("my email" -> "my email address")
            for k in range(max(self._done, limit - self._phrases.max_len + 1), limit):
                if self._phrases.could_continue(words[k:limit]) or self._phrases.could_continue(words[k:]):
                    limit = k
                    break
        if limit <= self._done:          # nothing new, or the recognizer shortened its guess
            return ""
        new = words[self._done:limit]
        self._done = limit
        return self._fmt.push(self._substitute(new))

    def _substitute(self, new: list[str]) -> list[str]:
        if not self._phrases:
            return new
        out: list[str] = []
        i = 0
        while i < len(new):
            hit = self._phrases.match_at(new, i)
            if hit:
                out.append(Literal(hit[1]))
                i += hit[0]
            else:
                out.append(new[i])
                i += 1
        return out

    def end_segment(self) -> None:
        """The recognizer closed a segment and starts a new one from scratch."""
        self._done = 0

    def finish(self) -> str:
        return self._fmt.flush()


def accept_polish(raw: str, polished: str | None) -> str | None:
    """The polished text if it is a believable clean-up of `raw` and actually different, else None."""
    if not polished:
        return None
    out = polished.replace("\r\n", "\n").strip()
    if out.startswith("```") and out.endswith("```"):
        out = out.strip("`").strip()
    if len(out) >= 2 and out[0] in "\"“" and out[-1] in "\"”":
        out = out[1:-1].strip()
    if not out or len(out) > len(raw) * 1.6 + 30 or len(out) < len(raw) * 0.35 - 10:
        return None
    return None if out == raw.strip() else out
