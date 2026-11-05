"""Lyra — YouTube searches shown as video cards on the island.

"Search X on YouTube" used to open the YouTube results page in the browser. Now it
fetches real results (title, channel, duration, thumbnail) and pushes them to the
island, the same rich cards Chat already knows how to draw. Clicking a card opens
that video. If nothing is found, callers fall back to the browser as before.
Playback ("play X on YouTube") is untouched.
"""

from __future__ import annotations

import re
import time

_Q = r"(.+?)"
_END = r"[.!?]*$"
_PATTERNS = [
    # "search/find/look for/look up/show me X on|in YouTube [for me]"
    re.compile(
        r"^(?:please\s+)?(?:look\s+for|look\s+up|search\s+for|search|find|show\s+me)\s+" + _Q +
        r"\s+(?:on|in)\s+(?:the\s+)?(?:youtube|yt)(?:\s+for\s+me)?" + _END, re.I),
    # "search YouTube for X" / "look up on youtube X"
    re.compile(
        r"^(?:please\s+)?(?:search|look\s+up|find)\s+(?:on\s+)?(?:youtube|yt)\s+(?:for\s+)?" + _Q + _END, re.I),
    # "open YouTube and search (for) X"
    re.compile(
        r"^(?:please\s+)?(?:open\s+|go\s+to\s+)?(?:youtube|yt)\s+(?:and\s+)?search(?:\s+for)?\s+" + _Q + _END, re.I),
]
# "...and play it" means the user wants playback, not a list.
_WANTS_PLAY = re.compile(r"\b(?:and\s+)?(?:play|watch)(?:\s+it|\s+the\s+first(?:\s+one)?)?\s*$", re.I)


def parse_query(command: str) -> str:
    """Return the search query if `command` asks to look something up on YouTube, else ''."""
    text = (command or "").strip()
    for pat in _PATTERNS:
        m = pat.match(text)
        if m:
            q = m.group(1).strip(" ,")
            if q and not _WANTS_PLAY.search(q):
                return q
    return ""


# The results last shown on the island, so "play the first result" can pick one.
_last: list[dict] = []
_last_at = 0.0
_PICK_TTL = 15 * 60  # seconds

_ORDINALS = {"top": 1, "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
             "fourth": 4, "4th": 4, "last": -1}
_PICK = re.compile(
    r"^(?:please\s+)?(?:play|open|watch|start|click|go\s+with|choose|pick)\s+(?:on\s+)?(?:the\s+)?"
    r"(?:(top|first|1st|second|2nd|third|3rd|fourth|4th|last)(?:\s+(?:one|result|video))*"
    r"|(?:number|result|video|#)\s*([1-4])(?:\s+(?:one|result|video))*)"
    r"(?:\s+(?:on|from)\s+(?:the\s+)?(?:youtube|yt))?[.!?]*$", re.I)


_POLITE_PRE = re.compile(
    r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?", re.I)
_POLITE_POST = re.compile(r"(?:[,\s]+(?:please|for\s+me|now|thanks|thank\s+you|sir))+\s*$", re.I)


def parse_pick(command: str) -> int | None:
    """'play the first result' -> 1, 'open the last one' -> -1, anything else -> None.
    Politeness ("can you ... please", "for me") is ignored."""
    text = re.sub(r"[.!?\s]+$", "", (command or "").strip())
    text = re.sub(r"[.!?\s]+$", "", _POLITE_POST.sub("", text))
    text = _POLITE_PRE.sub("", text).strip()
    m = _PICK.match(text)
    if not m:
        return None
    return _ORDINALS[m.group(1).lower()] if m.group(1) else int(m.group(2))


_OPEN_ALL = re.compile(
    r"^(?:please\s+)?open\s+(?:all|every\s+one|each)(?:\s+of)?\s+(?:(?:those|these|the)\s+)?"
    r"(?:search(?:es)?|results?|links?|tabs?|pages?|sites?|videos?)[.!?]*$"
    r"|^(?:please\s+)?open\s+(?:all\s+of\s+)?(?:them|those|these)(?:\s+all)?[.!?]*$",
    re.I)


def parse_open_all(command: str) -> bool:
    """'open all those searches' / 'open them all' -> True: open every result on the island."""
    text = _POLITE_PRE.sub("", re.sub(r"[.!?\s]+$", "", _POLITE_POST.sub("", (command or "").strip()))).strip()
    return bool(_OPEN_ALL.match(text))


def open_all() -> str:
    """Open every result last shown on the island in the browser. Returns the reply to speak."""
    if not _last or time.time() - _last_at > _PICK_TTL:
        return "I don't have any recent results to open."
    urls = []
    for row in _last:
        url = str(row.get("url") or "")
        if url.startswith("spotify:"):
            continue                                   # songs play one at a time, never all at once
        if url and url not in urls:
            urls.append(url)
    if not urls:
        return "I don't have any recent results to open."
    opened = 0
    for url in urls:
        try:
            try:
                from engine.actions.apps import open_url
                open_url(url)
            except Exception:
                import webbrowser
                webbrowser.open(url)
            opened += 1
            time.sleep(0.4)
        except Exception:
            pass
    try:
        import engine.bridge.ui_bridge as _ui_bridge
        _ui_bridge.push_search_dismiss()
    except Exception:
        pass
    return f"Opened {opened} results." if opened else "I couldn't open those results."


def remember(rows: list[dict]) -> None:
    """Remember the results now on the island (YouTube or web) so \"open the second one\" works."""
    global _last, _last_at
    _last, _last_at = [dict(r) for r in (rows or [])[:4]], time.time()


def play_pick(n: int) -> str:
    """Open the n-th result last shown on the island. Returns the reply to speak, or ''
    when there are no recent results (so the command can be handled normally)."""
    if not _last or time.time() - _last_at > _PICK_TTL:
        return ""
    idx = len(_last) - 1 if n == -1 else n - 1
    if not 0 <= idx < len(_last):
        return f"I only showed {len(_last)} results."
    row = _last[idx]
    url = str(row.get("url") or "")
    if not url:
        return ""
    if url.startswith(("spotify:track:", "spotify:find:")):
        try:
            from engine.features import spotify_island
            return spotify_island.play(url) or "I couldn't play that song."
        except Exception:
            return "I couldn't play that song."
    try:
        try:
            from engine.actions.apps import open_url
            open_url(url)
        except Exception:
            import webbrowser
            webbrowser.open(url)
    except Exception:
        return "I couldn't open that result."
    try:
        import engine.bridge.ui_bridge as _ui_bridge
        _ui_bridge.push_search_dismiss()  # the results are done: close the island panel
    except Exception:
        pass
    title = str(row.get("title") or "the result")
    is_video = "youtube.com" in url or "youtu.be" in url
    return f"{'Playing' if is_video else 'Opening'} {title[:70]}."


def show(query: str, max_results: int = 4) -> str:
    """Search YouTube and push the results to the island. Returns the reply to speak,
    or '' when there were no results / it failed (caller may fall back to the browser)."""
    try:
        import engine.brain.search as _search
        import engine.bridge.ui_bridge as _ui_bridge
        rows = _search.youtube_search_results(query, max_results=max_results)
        if not rows:
            return ""
        global _last, _last_at
        _last, _last_at = list(rows[:max_results]), time.time()
        _ui_bridge.push_search_results(query, rows, rows[0].get("provider", ""))
        return f"Here's what I found on YouTube for {query}."  # spoken over the results (not silent)
    except Exception as e:
        try:
            from engine.bridge import ui
            ui.log.warning("youtube island search failed: %s", e)
        except Exception:
            pass
        return ""


def is_shown_reply(reply: str) -> bool:
    """True for the \"Top ... results for X.\" line returned by show() here and in web_island.
    The island already shows the results, so the caller stays silent instead of speaking it."""
    return bool(re.match(r"^Top (?:YouTube )?results for ", reply or ""))
