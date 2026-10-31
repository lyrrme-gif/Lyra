"""Lyra - small, dependency-light helpers shared by the engines (numpy only)."""
from __future__ import annotations

import re

import numpy as np

SAMPLE_RATE = 16000

_WORD_RE = re.compile(r"[^\W_]+(?:['\u2019-][^\W_]+)*", re.UNICODE)


def _lev_le1(a: str, b: str) -> bool:
    """True if the edit distance between a and b is at most 1."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(x != y for x, y in zip(a, b)) == 1
    if la > lb:
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def _recase(hint: str, original: str) -> str:
    # Keep the dictionary's spelling/casing exactly (that is what the user typed on purpose).
    return hint


def apply_hints(text: str, hints, *, fuzzy: bool = False) -> str:
    """Make the engine's output use the user's spelling of names and terms.

    Parakeet has no decoder-side biasing, so hints are applied AFTER decoding, conservatively:
      1. a word equal to a hint, ignoring case            -> the hint's spelling
         (also a one-word transcript of a multi-word hint: "openai" -> "Open AI")
      2. two or three neighbouring words that join up to a hint ("spot ify" -> "Spotify")
      3. only if fuzzy=True: a single word one edit away from a hint of 7+ letters
         ("Spotifi" -> "Spotify"). Off for built-in vocabularies: "discard" is one edit from
         "discord" and must stay "discard". Use it only for the user's own dictionary.
    Punctuation around words is kept. Never raises.
    """
    try:
        terms = [str(h).strip() for h in (hints or []) if str(h).strip()]
        if not text or not terms:
            return text
        single = {t.lower(): t for t in terms if " " not in t}
        joined = {re.sub(r"[\s'\u2019-]", "", t.lower()): t for t in terms}
        long_single = [t for t in single.values() if len(t) >= 7]

        spans = [(m.start(), m.end(), m.group(0)) for m in _WORD_RE.finditer(text)]
        out: list[str] = []
        pos = 0
        i = 0
        while i < len(spans):
            s, e, w = spans[i]
            replaced = False
            # 2) join 3, then 2 neighbouring words (only when separated by plain spaces)
            for n in (3, 2):
                if i + n <= len(spans) and all(
                        text[spans[k][1]:spans[k + 1][0]].strip() == "" for k in range(i, i + n - 1)):
                    key = "".join(spans[k][2] for k in range(i, i + n)).lower()
                    if key in joined:
                        out.append(text[pos:s])
                        out.append(joined[key])
                        pos = spans[i + n - 1][1]
                        i += n
                        replaced = True
                        break
            if replaced:
                continue
            lw = w.lower()
            hit = single.get(lw) or joined.get(lw)      # "openai" -> hint "Open AI"
            if hit is None and fuzzy and len(lw) >= 6:
                for t in long_single:
                    if _lev_le1(lw, t.lower()):
                        hit = t
                        break
            if hit is not None and hit != w:
                out.append(text[pos:s])
                out.append(_recase(hit, w))
                pos = e
            i += 1
        out.append(text[pos:])
        return "".join(out)
    except Exception:
        return text


def split_long(audio: np.ndarray, max_seconds: float = 30.0, search_seconds: float = 4.0,
               sample_rate: int = SAMPLE_RATE) -> list[np.ndarray]:
    """Cut long audio into pieces of at most `max_seconds`, each cut at the quietest ~50 ms
    found in the last `search_seconds` of a piece, so words are not sliced in half."""
    n = len(audio)
    limit = int(max_seconds * sample_rate)
    if n <= limit:
        return [audio]
    win = max(1, int(0.05 * sample_rate))
    back = int(search_seconds * sample_rate)
    pieces: list[np.ndarray] = []
    start = 0
    while n - start > limit:
        lo = start + limit - back
        hi = start + limit
        seg = np.abs(audio[lo:hi])
        usable = len(seg) - win
        if usable <= 0:
            cut = hi
        else:
            # moving mean of |x| over `win` samples via cumulative sum
            c = np.concatenate(([0.0], np.cumsum(seg, dtype=np.float64)))
            means = (c[win:] - c[:-win])[: usable + 1]
            cut = lo + int(np.argmin(means)) + win // 2
        cut = min(max(cut, start + sample_rate), hi)
        pieces.append(audio[start:cut])
        start = cut
    pieces.append(audio[start:])
    return pieces


def prepare_audio(audio) -> np.ndarray:
    """float32 mono 1-D array; int16 input is scaled; NaN/inf removed; clipped to [-1, 1]."""
    a = np.asarray(audio)
    if a.dtype.kind in "iu":
        a = a.astype(np.float32) / (32768.0 if a.dtype == np.int16 else float(np.iinfo(a.dtype).max))
    a = a.astype(np.float32, copy=False)
    if a.ndim > 1:
        a = a.mean(axis=tuple(range(1, a.ndim)))
    a = np.nan_to_num(a, nan=0.0, posinf=1.0, neginf=-1.0)
    return np.clip(a, -1.0, 1.0)
