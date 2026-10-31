"""Reply length: Settings -> AI -> "Reply length & style" (short / balanced / detailed).

Before this module the base persona told the model "1-3 sentences" and "keep every answer to 1-2 short
sentences" in the SYSTEM prompt no matter what was chosen, so Balanced and Detailed could never be longer, and
Short was only a polite hint the model could ignore. Now:
  * the base prompt's length wording is REPLACED by the chosen style (system_prompt),
  * the chosen style is repeated next to the question (user_note), where models obey it best,
  * max_tokens follows the style,
  * Short is also ENFORCED: the answer is cut after its second sentence, while it streams (SentenceLimiter)
    and again on the final text (cut_sentences).
Pure Python (no network), so it is unit-tested in tests/test_reply_style.py."""
from __future__ import annotations

import re

STYLES = ("short", "balanced", "detailed")

_SPEC = {
    "short": {
        "intro": "short, natural, one or two short sentences",
        "rule": "Keep every answer to one or two short sentences (about 30 words at most), even if the topic is big. "
                "Give the answer first, skip background, and offer more only if they ask.",
        "note": "[Reply length: one or two short sentences only.]",
        "sentences": 2,
        "max_tokens": 160,
    },
    "balanced": {
        "intro": "short, natural, usually two to four sentences",
        "rule": "Keep answers to two to four sentences: the answer, then the one thing worth knowing.",
        "note": "",
        "sentences": 0,
        "max_tokens": 700,
    },
    "detailed": {
        "intro": "natural and clear, as long as the question deserves",
        "rule": "Give a fuller, well-explained answer: a short paragraph or a few numbered steps, with an example "
                "when it helps. Still no essays and no filler.",
        "note": "[Reply length: a fuller, well-explained answer.]",
        "sentences": 0,
        "max_tokens": 2048,
    },
}

_BASE_INTRO = "short, natural, usually 1-3 sentences"
_BASE_RULE = "Keep every answer to 1-2 short sentences."


def current() -> str:
    try:
        import settings_store
        v = str(settings_store.get("reply_style") or "balanced").lower()
    except Exception:
        v = "balanced"
    return v if v in STYLES else "balanced"


def system_prompt(base: str, style: str | None = None) -> str:
    """The persona prompt with its fixed length wording swapped for the chosen style."""
    spec = _SPEC[style if style in STYLES else current()]
    out = base.replace(_BASE_INTRO, spec["intro"]).replace(_BASE_RULE, spec["rule"])
    return out


def user_note(style: str | None = None) -> str:
    return _SPEC[style if style in STYLES else current()]["note"]


def max_tokens(style: str | None = None) -> int:
    return _SPEC[style if style in STYLES else current()]["max_tokens"]


def max_sentences(style: str | None = None) -> int:
    """0 = no cap."""
    return _SPEC[style if style in STYLES else current()]["sentences"]


_ABBREV = {"e.g", "i.e", "vs", "mr", "mrs", "ms", "dr", "st", "etc", "approx", "no"}
_END = re.compile(r"[.!?…]+[\"'”’)\]]*(?=\s|$)")


def _end_of_sentence(text: str, n: int) -> int | None:
    """Index just after the n-th sentence of `text`, or None while it has fewer than n finished sentences."""
    count = 0
    for m in _END.finditer(text):
        before = text[: m.start()].rsplit(None, 1)
        last = before[-1].lower() if before else ""
        if m.group().startswith(".") and last.strip(".,;:()\"'") in _ABBREV:
            continue
        count += 1
        if count >= n:
            return m.end()
    return None


def cut_sentences(text: str, n: int) -> str:
    if n <= 0:
        return text
    end = _end_of_sentence(text, n)
    return text if end is None else text[:end].rstrip()


class SentenceLimiter:
    """Feed streamed pieces; get back only the part inside the first `n` sentences. `done` turns True when the
    n-th sentence is finished, so the caller can stop reading the stream (and stop paying for tokens)."""

    def __init__(self, n: int):
        self.n = n
        self.text = ""
        self.sent = 0
        self.done = False

    def feed(self, piece: str) -> str:
        if self.done or not piece:
            return ""
        self.text += piece
        if self.n <= 0:
            self.sent = len(self.text)
            return piece
        end = _end_of_sentence(self.text, self.n)
        if end is None:
            self.sent = len(self.text)
            return piece
        self.done = True
        out = self.text[self.sent:end]
        self.text = self.text[:end]
        self.sent = end
        return out
