"""Lyra — web searches shown as result cards on the island.

"Search the web for X" / "search X on Google" fetch real results and push them to
the island, exactly like yt_island does for YouTube. The results are remembered by
ui_bridge.push_search_results, so "open the second one" works on them too.
Only explicit web/Google phrasing is matched, so "search Amazon for X",
"open Google Chrome" and similar commands keep their normal behaviour.
"""

from __future__ import annotations

import re

_END = r"[.!?]*$"
_PATTERNS = [
    # "search/find/look up X on|in Google" / "... on the web|internet"
    re.compile(
        r"^(?:please\s+)?(?:look\s+for|look\s+up|search\s+for|search|find)\s+(.+?)"
        r"\s+(?:on|in)\s+(?:the\s+)?(?:google|web|internet)(?:\s+for\s+me)?" + _END, re.I),
    # "search Google for X" / "search the web for X" / "google search for X"
    re.compile(
        r"^(?:please\s+)?(?:(?:search|look\s+up|find)\s+(?:on\s+)?(?:google|the\s+web|the\s+internet|web)|google\s+search)"
        r"\s+(?:for\s+)?(.+?)" + _END, re.I),
]

# Factual questions should show the same rich card as an explicit web search.
# Keep this narrow so conversational questions continue through the normal chat path.
_QUESTION_PATTERNS = [
    re.compile(r"^(?:please\s+)?(?:who|what|where|when)\s+(?:is|was|are|were|['’]?s)\s+(.+?)[.!?]*$", re.I),
    re.compile(r"^(?:please\s+)?(?:tell\s+me\s+(?:more\s+)?about|give\s+me\s+(?:info|information)\s+(?:on|about)|do\s+you\s+know\s+(?:anything\s+)?about)\s+(.+?)[.!?]*$", re.I),
]


# "is arcane an anime?" / "where can I watch dune?": questions about a title get the card with posters and
# where-to-watch. The whole question is kept as the query; web_card.clean_query() finds the title inside it.
_MEDIA_NOUN = r"(?:anime|movie|film|show|series|tv\s+show|tv\s+series|cartoon|manga|documentary)"
_QUESTION_PATTERNS += [
    re.compile(r"^(?:please\s+)?((?:is|was)\s+(?:the\s+)?.+?\s+(?:(?:an?|the)\s+)?" + _MEDIA_NOUN + r")[.!?\s]*$", re.I),
    re.compile(r"^(?:please\s+)?((?:where\s+(?:can|do|could|should)\s+(?:i|we|you)\s+watch|where\s+to\s+watch|how\s+(?:can|do)\s+i\s+watch)\s+.+?)[.!?\s]*$", re.I),
]


def _local_maths(text: str) -> bool:
    """\"what is 15 percent of 240\", \"what's 12 times 8\": answered by actions.quick, never by a web search."""
    if not re.search(r"\d", text):
        return False
    try:
        import importlib.util
        import sys as _sys
        q = _sys.modules.get("engine.actions.quick")
        if q is None:
            from engine.actions import quick as q
        low = re.sub(r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?", "", text.lower().rstrip("?.! ")).strip()
        return any(fn(low) for fn in (q._percent_of, q._calc))
    except Exception:
        return False


def parse_query(command: str) -> str:
    """Return the query if `command` explicitly asks to search the web / Google, else ''."""
    text = (command or "").strip()
    if _local_maths(text):
        return ""
    if re.search(r"\b(?:youtube|yt)\b", text, re.I):
        return ""  # YouTube has its own handler
    for pat in _PATTERNS:
        m = pat.match(text)
        if m:
            q = m.group(1).strip(" ,")
            if q:
                return q
    for pat in _QUESTION_PATTERNS:
        m = pat.match(text)
        if m:
            q = m.group(1).strip(" ,")
            if q:
                return q
    return ""


def gather(query: str, max_results: int = 4):
    """Run the web search and (in parallel) build the knowledge card for `query`.

    Returns (rows, card). `rows` already excludes the card's own page, so the island never
    lists the same Wikipedia article twice. Shared by "search the web for X" and by plain
    questions such as "who's X?", so both give the same result on the island."""
    import engine.brain.search as _search
    import engine.features.web_card as _web_card
    from concurrent.futures import ThreadPoolExecutor
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(_web_card.build, query)  # runs while the web search runs
        # "is X an anime?" / "where can I watch X?" are searched as asked; other questions by their bare subject.
        q = query if _web_card.is_media_question(query) else (_web_card.clean_query(query) or query)
        rows = _search.web_search_results(q, max_results=max_results + 1)
        try:
            card = fut.result(timeout=6)
        except Exception:
            card = None
    finally:
        ex.shutdown(wait=False)
    shown = list(rows or [])
    if card:
        page = (card.get("url") or "").rstrip("/")
        shown = [r for r in shown if str(r.get("url") or "").rstrip("/") != page]
    return shown[:3 if card else max_results], card


def _web_card_clean(query: str) -> str:
    """The query exactly as gather() searched it, so the cached Google answer is found again."""
    import engine.features.web_card as _web_card
    return query if _web_card.is_media_question(query) else (_web_card.clean_query(query) or query)


def spoken_summary(query: str, card: dict | None) -> str:
    """What Lyra says while the result is on screen. With a knowledge card (a person, place, film ...) she tells
    the first sentences about it; the card keeps the details. Otherwise a short line."""
    extract = re.sub(r"\s+", " ", str((card or {}).get("extract") or "")).strip()
    if extract:
        cut = extract.endswith("\u2026")                       # the card text was shortened mid-sentence
        extract = extract.rstrip("\u2026 ").strip()
        sentences = re.split(r"(?<=[.!?])\s+", extract)
        if cut and len(sentences) > 1:
            sentences = sentences[:-1]                        # drop the half sentence
        text = " ".join(sentences[:2]).strip()
        if len(text) > 300:
            text = sentences[0].strip()
        if text:
            if text[-1] not in ".!?":
                text += "."
            title = str((card or {}).get("title") or "").strip()
            return text if not title or title.lower() in text.lower() else f"{title}. {text}"
    return f"Searching the web for {query}."


def show(query: str, max_results: int = 4) -> str:
    """Search the web and push the results to the island, with a knowledge card above them
    when the query is about a known person / place / film... Returns the sentence Lyra should
    say while they are on screen, or '' when there were no results / it failed."""
    try:
        import engine.bridge.ui_bridge as _ui_bridge
        shown, card = gather(query, max_results)
        if not shown and not card:
            return ""
        try:
            import engine.brain.search as _search
            related = _search.google_related(_web_card_clean(query))
        except Exception:
            related = []
        _ui_bridge.push_search_results(query, shown, (shown[0].get("provider", "") if shown else ""), card=card, related=related)
        return spoken_summary(query, card)
    except Exception as e:
        try:
            from engine.bridge import ui
            ui.log.warning("web island search failed: %s", e)
        except Exception:
            pass
        return ""
