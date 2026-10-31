"""Lyra — lyrics lookup for the island's Music tab.

Uses LRCLIB (https://lrclib.net): free, no account, no API key. Returns
time-synced lines when they exist so the UI can follow the song, otherwise
plain text. If LRCLIB is down (502/503/timeouts) or has no synced lyrics,
falls back to NetEase Cloud Music's public lyric endpoint. Lyrics are only
held in memory for the session.

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


_RETRY_CODES = (429, 500, 502, 503, 504)
_down_until = 0.0  # LRCLIB circuit breaker (monotonic time)


class _Outage(Exception):
    """LRCLIB is unreachable / erroring (as opposed to 'no lyrics for this song')."""


def _get_json(path: str, params: dict):
    global _down_until
    if time.monotonic() < _down_until:
        raise _Outage("LRCLIB marked down, skipping")
    url = f"{_API}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code not in _RETRY_CODES:
                raise  # e.g. 404 = genuinely no such track
            last = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        if attempt < 2:
            time.sleep(0.5 * (attempt + 1))
    # Still failing after retries: stop hammering LRCLIB for a minute so the
    # fallback answers fast instead of every song waiting on dead retries.
    _down_until = time.monotonic() + 60
    raise _Outage(str(last))


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


# ── Fallback: NetEase Cloud Music (public endpoints, no key) ────────────────
_NE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://music.163.com/",
}
_NE_CREDIT = re.compile(r"^\s*(作词|作曲|编曲|制作人|混音|母带|监制|录音|词|曲)\s*[:：]")


def _norm(x: str) -> str:
    return re.sub(r"[^a-z0-9\u0600-\u06ff\u4e00-\u9fff]+", "", (x or "").lower())


def _ne_json(url: str, data: dict | None = None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=_NE_HEADERS)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _netease_lookup(title: str, artist: str, duration: int | None) -> dict:
    """Search NetEase for the track and return its LRC in the same shape as _shape()."""
    res = _ne_json(
        "https://music.163.com/api/search/get/web?csrf_token=",
        {"s": f"{_clean_title(title)} {artist}", "type": 1, "offset": 0, "limit": 8},
    )
    songs = ((res or {}).get("result") or {}).get("songs") or []
    want_a = _norm(artist.split(",")[0].split("&")[0])
    want_t = _norm(_clean_title(title))
    for sng in songs:
        names = [_norm(a.get("name", "")) for a in (sng.get("artists") or sng.get("ar") or [])]
        if want_a and not any(want_a in n or n in want_a for n in names if n):
            continue
        got_t = _norm(sng.get("name", ""))
        if want_t and got_t and want_t not in got_t and got_t not in want_t:
            continue
        dur_ms = sng.get("duration") or sng.get("dt")
        if duration and dur_ms and abs(dur_ms / 1000 - duration) > 8:
            continue
        lyr = _ne_json(
            "https://music.163.com/api/song/lyric?" + urllib.parse.urlencode(
                {"id": sng["id"], "lv": 1, "kv": 1, "tv": -1}))
        lrc = ((lyr or {}).get("lrc") or {}).get("lyric") or ""
        synced = [l for l in parse_lrc(lrc) if not _NE_CREDIT.match(l["text"])]
        if synced:
            return {"found": True, "instrumental": False, "synced": synced, "plain": ""}
    return {"found": False}


def _lrclib_lookup(title: str, artist: str, album: str, duration: int | None) -> dict:
    result: dict = {"found": False}
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
            raise _Outage(str(e))

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
    return result


def lookup(title: str, artist: str, album: str = "", duration: int | None = None) -> dict:
    title, artist, album = (title or "").strip(), (artist or "").strip(), (album or "").strip()
    if not title or not artist:
        return {"found": False}
    key = (title.lower(), artist.lower(), album.lower(), duration or 0)
    with _lock:
        if key in _cache:
            return _cache[key]

    result: dict = {"found": False}
    lrclib_err: str | None = None
    try:
        result = _lrclib_lookup(title, artist, album, duration)
    except Exception as e:  # outage / network error
        lrclib_err = str(e)
        log.warning("lyrics: LRCLIB failed for %r - %r: %s (trying fallback)", artist, title, e)

    # Fallback when LRCLIB is down, or has nothing time-synced for this song.
    if lrclib_err or not result.get("instrumental") and not result.get("synced"):
        try:
            alt = _netease_lookup(title, artist, duration)
            if alt.get("found"):
                result = alt
                lrclib_err = None
        except Exception as e:
            log.warning("lyrics: NetEase fallback failed for %r - %r: %s", artist, title, e)
            if lrclib_err is None and not result.get("found"):
                lrclib_err = str(e)

    if lrclib_err and not result.get("found"):
        # Do not cache outages: the next attempt should retry.
        return {"found": False, "error": lrclib_err}

    with _lock:
        _cache[key] = result
    return result
