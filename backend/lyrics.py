"""Lyra — lyrics lookup for the island's Music tab.

Uses LRCLIB (https://lrclib.net): free, no account, no API key. Returns
time-synced lines when they exist so the UI can follow the song, otherwise
plain text. Lyrics are only held in memory for the session.

Called by ui_bridge:  GET /lyrics?title=...&artist=...&album=...&duration=SECONDS
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import ui

log = ui.log

_API = "https://lrclib.net/api"
_UA = "Lyra/1.0 (personal desktop assistant)"
_TIMEOUT = 8

_cache: dict[tuple, dict] = {}
_lock = threading.Lock()

_TS_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")


def _get_json(path: str, params: dict):
    url = f"{_API}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    # LRCLIB occasionally returns a transient 503. Retry that (and rate
    # limiting) once before surfacing a lookup failure to the UI.
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if attempt or e.code not in (429, 500, 502, 503, 504):
                raise
            time.sleep(0.5)


def parse_lrc(text: str) -> list[dict]:
    """'[01:23.45] words' -> [{'t': ms, 'text': 'words'}], sorted by time."""
    out: list[dict] = []
    for raw in (text or "").splitlines():
        stamps = list(_TS_RE.finditer(raw))
        if not stamps:
            continue
        words = raw[stamps[-1].end():].strip()
        for m in stamps:
            frac = m.group(3) or "0"
            ms = int(m.group(1)) * 60000 + int(m.group(2)) * 1000 + int(frac.ljust(3, "0")[:3])
            out.append({"t": ms, "text": words})
    out.sort(key=lambda x: x["t"])
    return out


def _clean_title(title: str) -> str:
    """'Song - Remastered 2011' / 'Song (feat. X)' -> 'Song' for the fallback search."""
    t = re.sub(r"\s*[\(\[].*?[\)\]]", "", title or "")
    t = re.sub(r"\s+-\s+.*$", "", t)
    return t.strip() or (title or "").strip()


def _shape(rec: dict) -> dict:
    if rec.get("instrumental"):
        return {"found": True, "instrumental": True, "synced": [], "plain": ""}
    synced = parse_lrc(rec.get("syncedLyrics") or "")
    plain = (rec.get("plainLyrics") or "").strip()
    if not synced and not plain:
        return {"found": False}
    return {"found": True, "instrumental": False, "synced": synced, "plain": plain}


def lookup(title: str, artist: str, album: str = "", duration: int | None = None) -> dict:
    title, artist, album = (title or "").strip(), (artist or "").strip(), (album or "").strip()
    if not title or not artist:
        return {"found": False}
    key = (title.lower(), artist.lower(), album.lower(), duration or 0)
    with _lock:
        if key in _cache:
            return _cache[key]

    result: dict = {"found": False}
    try:
        # 1) exact match (title + artist + album + duration)
        params = {"track_name": title, "artist_name": artist}
        if album:
            params["album_name"] = album
        if duration:
            params["duration"] = int(duration)
        try:
            result = _shape(_get_json("get", params))
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise

        # 2) looser search (handles "- Remastered", "(feat. ...)", album mismatches)
        if not result.get("found") or (not result.get("synced") and not result.get("instrumental")):
            hits = _get_json("search", {"track_name": _clean_title(title), "artist_name": artist})
            best = None
            for h in hits if isinstance(hits, list) else []:
                if duration and h.get("duration") and abs(float(h["duration"]) - duration) > 6:
                    continue
                shaped = _shape(h)
                if not shaped.get("found"):
                    continue
                if shaped.get("synced"):
                    best = shaped
                    break
                best = best or shaped
            if best and (not result.get("found") or best.get("synced")):
                result = best
    except Exception as e:
        log.warning("lyrics lookup failed for %r - %r: %s", artist, title, e)
        # Do not cache network failures: the next attempt should retry.
        return {"found": False, "error": str(e)}

    with _lock:
        _cache[key] = result
    return result
