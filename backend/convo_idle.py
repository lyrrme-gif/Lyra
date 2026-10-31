"""Lyra's chat memory fades after a quiet spell.

The conversation list that is sent to the LLM (the last few turns, see config.CONVO_MEMORY_TURNS) used to live until the
app was closed, so a question asked hours later was still answered with the old chat in mind. Now the list is emptied
when nobody has spoken to Lyra for CONVO_IDLE_RESET_SECONDS; the next question starts a fresh conversation.
(The past chats you can browse in the chat bar are a separate record and are not touched.)
"""
from __future__ import annotations

import time

_last: float | None = None   # when the last turn started / finished (time.monotonic)


def _limit() -> float:
    try:
        import config
        return float(getattr(config, "CONVO_IDLE_RESET_SECONDS", 180))
    except Exception:  # noqa: BLE001
        return 180.0


def reset_if_idle(history: list, now: float | None = None) -> bool:
    """Call when a turn starts: empties `history` if the last turn was longer ago than the limit. True if it did."""
    global _last
    now = time.monotonic() if now is None else now
    cleared = False
    limit = _limit()
    if history and _last is not None and limit > 0 and now - _last > limit:
        history.clear()
        cleared = True
    _last = now
    return cleared


def touch(now: float | None = None) -> None:
    """Call when a turn ends (an answer can take a while): the quiet spell counts from here."""
    global _last
    _last = time.monotonic() if now is None else now
