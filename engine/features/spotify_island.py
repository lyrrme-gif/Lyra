"""Lyra — Spotify searches shown as song cards on the island.

"Search X on Spotify" fetches up to 4 songs (album art, title, artist) and shows them on the
island, the same way yt_island does for YouTube. Tapping a card (or "play the first one") opens
Spotify on that exact track and starts it, then the island shows a short
"Playing <song> · on Spotify" moment (with the album art) and goes back to the normal
music island.

"Play X on Spotify" does NOT show cards: it takes the best match and plays it straight away
(play_query), with the same "Playing <song>" banner. If nothing is found, callers fall back to
the old behaviour.

Results come from the Spotify Web API when spotify_client_id / spotify_client_secret are set,
otherwise from a web search of open.spotify.com plus Spotify's public oEmbed (no login).
"""

from __future__ import annotations

import base64
import os
import re
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
_TRACK_URL = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?track/([A-Za-z0-9]{22})")
_TRACK_URI = re.compile(r"^spotify:track:([A-Za-z0-9]{22})(?::play)?$")
_FIND_URI = re.compile(r"^spotify:find:(\d+)$")
_Q = r"(.+?)"
_END = r"[.!?]*$"
_PATTERNS = [
    # "search/find/look up/show me X on Spotify [for me]"
    re.compile(r"^(?:please\s+)?(?:look\s+for|look\s+up|search\s+for|search|find|show\s+me|play)\s+" + _Q +
               r"\s+(?:on|in|with)\s+(?:the\s+)?spotify(?:\s+for\s+me)?" + _END, re.I),
    # "search Spotify for X"
    re.compile(r"^(?:please\s+)?(?:search|look\s+up|find)\s+(?:on\s+)?spotify\s+(?:for\s+)?" + _Q + _END, re.I),
    # "open Spotify and search/play X"
    re.compile(r"^(?:please\s+)?(?:open\s+|go\s+to\s+|launch\s+)?spotify\s+(?:and\s+)?(?:search(?:\s+for)?|play)\s+" + _Q + _END, re.I),
]

# "play ..." (also "open spotify and play ...") = play it now; every other verb = show the results.
_PLAY_VERB = re.compile(r"^(?:please\s+)?(?:(?:open|go\s+to|launch)\s+)?(?:spotify\s+(?:and\s+)?)?play\b", re.I)

# The songs last shown on the island, by track id, so a tap or "play the first one" knows the title/art.
_last: dict[str, dict] = {}


def parse_query(command: str) -> str:
    """Return the song search if `command` asks to look up / play something on Spotify, else ''."""
    text = (command or "").strip()
    for pat in _PATTERNS:
        m = pat.match(text)
        if m:
            q = m.group(1).strip(" ,")
            if q:
                return q
    return ""


def intent_of(command: str) -> str:
    """'play' when the command starts with the verb play, else 'search' (show the results)."""
    return "play" if _PLAY_VERB.match((command or "").strip()) else "search"


def parse_intent(command: str) -> tuple[str, str]:
    """('play' | 'search', song query) for a Spotify command, or ('', '') when it is not one."""
    q = parse_query(command)
    return (intent_of(command), q) if q else ("", "")


def track_id(ref: str) -> str:
    """'spotify:track:ID[:play]' or an open.spotify.com track link -> ID, else ''."""
    ref = str(ref or "").strip()
    m = _TRACK_URI.match(ref) or _TRACK_URL.search(ref)
    return m.group(1) if m else ""


def is_song_ref(ref: str) -> bool:
    """A song card's link: an exact Spotify track, or an iTunes-found song (spotify:find:<id>)."""
    return bool(track_id(ref) or _FIND_URI.match(str(ref or "").strip()))


# ── Finding songs ────────────────────────────────────────────────────────────

def _row(tid: str, title: str, artist: str, art: str) -> dict:
    return {
        "title": title or "Song", "description": artist, "channel": artist,
        "url": f"spotify:track:{tid}", "source": "Spotify", "thumbnail": art,
        "provider": "Spotify", "track_id": tid,
    }


def _search_api(query: str, n: int) -> list[dict]:
    import requests
    from engine.brain.llm_store import get
    s = get()
    cid, secret = s.get("spotify_client_id"), s.get("spotify_client_secret")
    if not cid or not secret:
        return []
    tok = requests.post("https://accounts.spotify.com/api/token", data={"grant_type": "client_credentials"},
                        auth=(cid, secret), timeout=8).json().get("access_token")
    if not tok:
        return []
    data = requests.get("https://api.spotify.com/v1/search", params={"q": query, "type": "track", "limit": n},
                        headers={"Authorization": f"Bearer {tok}"}, timeout=8).json()
    out = []
    for it in (data.get("tracks", {}).get("items") or [])[:n]:
        if not it.get("id"):
            continue
        imgs = (it.get("album") or {}).get("images") or []
        art = (imgs[1] if len(imgs) > 1 else imgs[0] if imgs else {}).get("url", "")
        artist = ", ".join(a.get("name", "") for a in it.get("artists") or [] if a.get("name"))
        out.append(_row(it["id"], it.get("name", ""), artist, art))
    return out


# The artist ends at the first "|" or "·": search engines often glue the page text on after "| Spotify".
_T1 = re.compile(r"^(.*?)\s*[-–—]\s*(?:song|single)(?:\s+and\s+lyrics)?\s+by\s+([^|·•]+?)\s*(?:[|·•].*)?$", re.I)
_T2 = re.compile(r"^(.*?)\s*[·•]\s*(?:song|single)\s*[·•]\s*([^|·•]+?)\s*(?:[|·•].*)?$", re.I)


def parse_title(raw: str) -> tuple[str, str]:
    """A Spotify page title from a web search -> (song, artist). Artist may be ''."""
    t = re.sub(r"\s+", " ", str(raw or "")).strip()
    for pat in (_T1, _T2):
        m = pat.match(t)
        if m and m.group(1).strip():
            return m.group(1).strip(), m.group(2).strip()
    return re.sub(r"\s*\|\s*Spotify.*$", "", t, flags=re.I).strip(), ""


def _oembed(tid: str) -> dict:
    import requests
    try:
        r = requests.get("https://open.spotify.com/oembed",
                         params={"url": f"https://open.spotify.com/track/{tid}"},
                         headers={"User-Agent": _UA}, timeout=3)
        return r.json() if r.ok else {}
    except Exception:
        return {}


def _ddg_ids(q: str, n: int) -> dict[str, str]:
    """One web search -> {spotify track id: page title}, in result order."""
    found: dict[str, str] = {}
    try:
        from ddgs import DDGS
        with DDGS() as d:
            rows = list(d.text(q, max_results=n * 3))
        for r in rows:
            u = str(r.get("href") or r.get("url") or "")
            m = _TRACK_URL.search(u)
            if m and m.group(1) not in found:
                found[m.group(1)] = str(r.get("title") or "")
    except Exception:
        pass
    if not found:
        try:
            import requests
            from urllib.parse import unquote
            r = requests.post("https://html.duckduckgo.com/html/", data={"q": q},
                              headers={"User-Agent": _UA}, timeout=8)
            for m in _TRACK_URL.finditer(unquote(r.text)):
                found.setdefault(m.group(1), "")
        except Exception:
            pass
    return found


def _clean_query(q: str) -> str:
    """Punctuation makes web searches miss ("It Might Be Painful, but I Still Love It.")."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s&'-]", " ", str(q or ""))).strip()


def _search_web(query: str, n: int) -> list[dict]:
    q = _clean_query(query) or query
    found: dict[str, str] = {}
    variants = (f"site:open.spotify.com/track {q}", f"open.spotify.com track {q}", f"{q} spotify song")
    pool = ThreadPoolExecutor(max_workers=len(variants))
    try:
        futs = [pool.submit(_ddg_ids, v, n) for v in variants]
        for fut in as_completed(futs, timeout=9):      # all at once: the first that finds songs wins
            try:
                found = fut.result()
            except Exception:
                found = {}
            if found:
                break
    except Exception:
        pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    ids = list(found)[:n]
    if not ids:
        return []
    with ThreadPoolExecutor(max_workers=4) as pool:
        embeds = list(pool.map(_oembed, ids))
    out = []
    for tid, emb in zip(ids, embeds):
        title, artist = parse_title(found[tid])
        title = title or str(emb.get("title") or "")
        out.append(_row(tid, title, artist, str(emb.get("thumbnail_url") or "")))
    return out


# ── Fallback: Apple's free iTunes catalogue (no login), resolved to a Spotify song when tapped ──

def _http_json(url: str, params: dict | None = None, timeout: float = 6) -> dict:
    import requests
    r = requests.get(url, params=params, headers={"User-Agent": _UA}, timeout=timeout)
    return r.json() if r.ok else {}


def _search_itunes(query: str, n: int) -> list[dict]:
    data = _http_json("https://itunes.apple.com/search",
                      {"term": _clean_query(query) or query, "media": "music", "entity": "song", "limit": max(n * 3, 8)})
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for it in data.get("results") or []:
        tid, title, artist = it.get("trackId"), str(it.get("trackName") or ""), str(it.get("artistName") or "")
        if not tid or not title:
            continue
        key = (_norm(title), _norm(artist))
        if key in seen:
            continue
        seen.add(key)
        art = str(it.get("artworkUrl100") or "").replace("100x100bb", "300x300bb")
        row = _row("it%s" % tid, title, artist, art)
        row["url"] = f"spotify:find:{tid}"            # a Spotify id is looked up when the song is tapped
        row["apple_url"] = str(it.get("trackViewUrl") or "")
        out.append(row)
        if len(out) >= n:
            break
    return out


def resolve_spotify_id(row: dict) -> str:
    """The Spotify track id for an iTunes-found song: song.link first, then a precise web search."""
    if row.get("resolved_id"):
        return str(row["resolved_id"])
    title, artist, apple = str(row.get("title") or ""), str(row.get("channel") or ""), str(row.get("apple_url") or "")
    tid = ""
    if apple:
        try:
            data = _http_json("https://api.song.link/v1-alpha.1/links", {"url": apple, "userCountry": "US"}, timeout=8)
            m = _TRACK_URL.search(str(((data.get("linksByPlatform") or {}).get("spotify") or {}).get("url") or ""))
            tid = m.group(1) if m else ""
        except Exception:
            tid = ""
    if not tid:
        for q in (f'site:open.spotify.com/track "{_clean_query(title)}" {_clean_query(artist)}',
                  f"{_clean_query(title)} {_clean_query(artist)} spotify song"):
            found = _ddg_ids(q, 3)
            if found:
                tid = next(iter(found))
                break
    if tid:
        row["resolved_id"] = tid
    return tid


_ITUNES_GRACE = 1.2   # seconds to wait for exact Spotify ids once the (fast) iTunes list is ready
last_source = ""


def search(query: str, n: int = 4) -> list[dict]:
    """Ask every source at the same time. Spotify's own API wins, then the web search (exact Spotify
    songs); the fast iTunes list is used when the web search is slow or finds nothing."""
    global last_source
    pool = ThreadPoolExecutor(max_workers=3)
    futs = {pool.submit(_search_api, query, n): "api", pool.submit(_search_web, query, n): "web",
            pool.submit(_search_itunes, query, n): "itunes"}
    got: dict[str, list[dict]] = {}
    pending = set(futs)
    deadline, itunes_at = time.time() + 9, None
    try:
        while pending and time.time() < deadline:
            done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            for f in done:
                try:
                    got[futs[f]] = f.result() or []
                except Exception:
                    got[futs[f]] = []
            api_done = "api" in got
            if got.get("api") or (api_done and got.get("web")):
                break
            if got.get("itunes") and itunes_at is None:
                itunes_at = time.time()
            if itunes_at is not None and time.time() - itunes_at > _ITUNES_GRACE:
                break
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    for name in ("api", "web", "itunes"):
        if got.get(name):
            last_source = name
            return got[name]
    last_source = ""
    return []


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(t or "").lower()).strip()


def _first_artist(row: dict) -> str:
    return str(row.get("channel") or "").split(",")[0].strip()


def more_by(artist: str, skip: list[dict], n: int = 3) -> list[dict]:
    """Other songs by `artist` that are not already on the island (same title = same song)."""
    if not artist:
        return []
    seen = {_norm(r.get("title")) for r in skip} | {r.get("track_id") for r in skip}
    rows: list[dict] = []
    try:
        rows = _search_api(f'artist:"{artist}"', 12)
    except Exception:
        rows = []
    if not rows:
        try:
            rows = _search_itunes(artist, 12)
        except Exception:
            rows = []
    if not rows:
        try:
            rows = _search_web(f"{artist} songs", 10)
        except Exception:
            rows = []
    out: list[dict] = []
    for r in rows:
        key = _norm(r.get("title"))
        if not key or key in seen or r.get("track_id") in seen:
            continue
        if _norm(artist) not in _norm(r.get("channel")):
            continue                      # a different (or unknown) artist: not "more by" this one
        seen.add(key)
        out.append(r)
        if len(out) >= n:
            break
    return out


_CACHE_S = 600
_cache: dict[str, tuple[float, list[dict], list[dict], str]] = {}   # same search again = instant


def show(query: str, n: int = 4) -> str:
    """Search Spotify and push the song cards to the island. Returns the reply to speak, or ''
    when nothing was found / it failed (the caller then falls back to the old behaviour)."""
    t0 = time.time()
    try:
        from engine.bridge import ui_bridge
        key = _norm(query)
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < _CACHE_S:
            _, rows, more, more_title = hit
            source = "cache"
        else:
            rows = search(query, n)
            source = last_source
            if not rows:
                try:
                    from engine.bridge import ui
                    ui.log.warning("spotify island: no songs found for %r (web search blocked/offline?)", query)
                except Exception:
                    pass
                return ""
            artist = _first_artist(rows[0]) or next((_first_artist(r) for r in rows if _first_artist(r)), "")
            more, more_title = [], (f"More by {artist}" if artist else "")
            pool = ThreadPoolExecutor(max_workers=1)
            try:                                         # "more by" must never hold the cards back for long
                more = pool.submit(more_by, artist, rows).result(timeout=3)
            except Exception:
                more = []
            finally:
                pool.shutdown(wait=False)
            _cache[key] = (time.time(), rows, more, more_title)
        _last.clear()
        for r in rows + more:
            _last[r["track_id"]] = r
        ui_bridge.push_search_results(query, rows, "Spotify", more=more, more_title=more_title)
        if rows and str(rows[0].get("url", "")).startswith("spotify:find:"):
            # Most likely tap: look up its exact Spotify song now, so the tap itself is quick.
            threading.Thread(target=resolve_spotify_id, args=(rows[0],), daemon=True).start()
        try:
            from engine.bridge import ui
            ui.log.info("spotify island: %d songs + %d more for %r in %d ms (%s)",
                        len(rows), len(more), query, int((time.time() - t0) * 1000), source or "?")
        except Exception:
            pass
        return f"Here's what I found on Spotify for {query}."
    except Exception as e:
        try:
            from engine.bridge import ui
            ui.log.warning("spotify island search failed: %s", e)
        except Exception:
            pass
        return ""


# ── Playing a song ───────────────────────────────────────────────────────────

def _art_data_url(url: str) -> str | None:
    """Album art as a small data: URL (the island's activity icon only takes data: images)."""
    if not url:
        return None
    try:
        import requests
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=3)
        if r.ok and len(r.content) < 300_000:
            mime = (r.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
            if mime.startswith("image/"):
                return f"data:{mime};base64," + base64.b64encode(r.content).decode("ascii")
    except Exception:
        pass
    return None


def _wait_art(fut) -> str | None:
    try:
        return fut.result(timeout=2)
    except Exception:
        return None


def _open_search(row: dict) -> str:
    """No exact Spotify track could be found: open Spotify's own search for the song instead."""
    from urllib.parse import quote_plus
    title, artist = str(row.get("title") or "").strip(), str(row.get("channel") or "").strip()
    q = quote_plus(f"{title} {artist}".strip())
    try:
        getattr(os, "startfile")(f"spotify:search:{q}")
    except Exception:
        import webbrowser
        webbrowser.open(f"https://open.spotify.com/search/{q}")
    try:
        from engine.bridge import ui_bridge
        ui_bridge.push_search_dismiss()
        ui_bridge.push_activity("app_launch", f"Searching {title} on Spotify"[:80], phase="info", app="spotify", duration_ms=3200)
    except Exception:
        pass
    return f"I couldn't find the exact song, so I opened a Spotify search for {title}."


# Chromium/Electron helper windows of Spotify.exe that carry no song information.
_JUNK_TITLES = ("chrome legacy window", "default ime", "msctfime ui", "gdi+ window", "dwm notification window",
                "chrome_widgetwin", "spotify widget")


def _spotify_titles() -> "list[str] | None":
    """Titles of Spotify's own windows (Windows only). While a song plays the main window's title is
    '<artist> - <song>'; when nothing plays it is 'Spotify ...'. [] = Spotify is not running (yet),
    None = cannot be read on this system."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        import psutil
        user32 = ctypes.windll.user32
        found: list[tuple[str, int]] = []
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lp):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                t = buf.value.strip()
                if t:
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    found.append((t, int(pid.value)))
            return True

        user32.EnumWindows(enum_proc(_cb), 0)
        names: dict[int, str] = {}
        out: list[str] = []
        for t, pid in found:
            if pid not in names:
                try:
                    names[pid] = psutil.Process(pid).name().lower()
                except Exception:
                    names[pid] = ""
            if names[pid] == "spotify.exe" and not any(j in t.lower() for j in _JUNK_TITLES):
                out.append(t)
        return out
    except Exception:
        return None


def _core(t: str) -> str:
    """A song name without '(feat. ...)' / '[Remastered]' noise, normalised."""
    return _norm(re.sub(r"[(\[].*?[)\]]", " ", str(t or "")))


def _same_song(want: str, got: str) -> bool:
    """Does the Spotify window title / media title `got` belong to the song `want`?"""
    w = _core(want)
    if not w:
        return False
    g = _core(got)
    if w in g:
        return True
    wt, gt = set(w.split()), set(g.split())
    return bool(wt) and len(wt & gt) / len(wt) >= 0.75


def _playback_state(title: str) -> "bool | None":
    """True = Spotify is playing `title`; False = it is up but playing something else / nothing;
    None = cannot tell (not running yet, or the window titles are unreadable)."""
    if not title.strip():
        return None
    titles = _spotify_titles()
    if not titles:
        return None
    if any(_same_song(title, t) for t in titles):
        return True
    try:                                                   # second opinion: Windows media session
        from engine.features import media_control
        info = media_control.now_playing()
        if info.get("available") and "playing" in str(info.get("status", "")) and _same_song(title, str(info.get("title", ""))):
            return True
    except Exception:
        pass
    return False


def _can_verify() -> bool:
    return sys.platform == "win32"


def _wait_playing(title: str, seconds: float, step: float = 0.4) -> "bool | None":
    """Poll until Spotify plays `title` (True), or `seconds` pass. Returns the last state.
    None = cannot tell (not on Windows / no title to compare / Spotify never showed a window)."""
    if not _can_verify():
        return None
    end = time.monotonic() + seconds
    state = _playback_state(title)
    while state is not True and time.monotonic() < end:
        time.sleep(step)
        state = _playback_state(title)
    return state


def _double_click_row(title: str, artist: str) -> bool:
    """Last resort: Spotify showed the song (album page, row highlighted) but did not start it.
    Bring the window up and double-click that row, which always plays it."""
    try:
        from engine.actions import apps
        win = apps._find_window("Spotify")
        if win:
            try:
                if win.isMinimized:
                    win.restore()
                win.activate()
                time.sleep(0.5)
            except Exception:
                pass
        who = f" by {artist}" if artist else ""
        return bool(apps._click_via_vision(
            f'the highlighted track row named "{title}"{who} in the song list (not the album header or another row)',
            window_title="Spotify", double_click=True))
    except Exception:
        return False


def _confirm_playing(tid: str, title: str, artist: str, cold: bool) -> "bool | None":
    """The track link was just opened. Make sure the song really started: Spotify sometimes only
    navigates to it and highlights the row. Returns True / None (cannot tell: assume it did) or False
    (it did not start)."""
    state = _wait_playing(title, 10.0 if cold else 3.5)
    if state is not False:
        return state
    try:                                    # 1) the link again: the app is up now, so it takes it
        getattr(os, "startfile")(f"spotify:track:{tid}:play")
    except Exception:
        pass
    state = _wait_playing(title, 3.0)
    if state is not False:
        return state
    if _double_click_row(title, artist):    # 2) click the song itself
        state = _wait_playing(title, 3.0)
    return state


def play(ref: str) -> str:
    """Open Spotify on this song and start it, then show the "Playing ..." banner on the island.
    `ref` is 'spotify:track:ID' / 'spotify:find:<iTunes id>' (what a card carries) or a Spotify
    track link. The song is only announced once Spotify really plays it. Returns the reply text,
    or '' if `ref` is not a song."""
    ref = str(ref or "").strip()
    fm = _FIND_URI.match(ref)
    if fm:
        row = _last.get("it" + fm.group(1), {})
        tid = resolve_spotify_id(row) if row else ""
        if not tid:
            return _open_search(row) if row else ""
    else:
        tid = track_id(ref)
        if not tid:
            return ""
        row = _last.get(tid, {})
    title = str(row.get("title") or "").strip()
    artist = str(row.get("channel") or "").strip()
    art_pool = ThreadPoolExecutor(max_workers=1)
    art_fut = art_pool.submit(_art_data_url, str(row.get("thumbnail") or ""))   # while the song starts
    art_pool.shutdown(wait=False)
    cold = _spotify_titles() == []                   # Spotify not running: it needs a few seconds to start
    try:
        starter = getattr(os, "startfile")           # Windows: the spotify: URI opens the desktop app
        starter(f"spotify:track:{tid}:play")         # ":play" asks the app to start the track
    except Exception:
        import webbrowser
        webbrowser.open(f"https://open.spotify.com/track/{tid}")
        return f"I opened {title or 'the song'} in the browser."
    started = _confirm_playing(tid, title, artist, cold)
    try:
        from engine.bridge import ui_bridge
        ui_bridge.push_search_dismiss()
        if started is False:
            ui_bridge.push_activity("app_launch", f"Couldn't start {title or 'the song'}"[:80], phase="info",
                                    app="spotify", duration_ms=4000)
        else:
            # kind "song_play": the island shows the same swelling banner as a recognised song
            # (album cover left, name, Spotify icon right).
            ui_bridge.push_activity(
                "song_play", f"Playing {title}" if title else "Playing on Spotify", phase="success",
                subtitle=artist, app="spotify", duration_ms=5500,
                icon_url=_wait_art(art_fut), url=f"spotify:track:{tid}",
            )
    except Exception:
        pass
    if started is False:
        return f"I opened {title or 'the song'} in Spotify, but it didn't start. Press play on it."
    return f"Playing {title or 'that song'} on Spotify."


def play_query(query: str) -> str:
    """"Play X on Spotify": no cards, no search results on screen. Find the best match and play it
    right away (same \"Playing <song>\" banner as a tapped card). Returns the reply, or '' when
    nothing was found (the caller then falls back to the old direct-play path)."""
    try:
        rows = search(query, 4)
        if not rows:
            try:
                from engine.bridge import ui
                ui.log.warning("spotify island: no songs found to play for %r", query)
            except Exception:
                pass
            return ""
        row = rows[0]
        _last[row["track_id"]] = row
        return play(row["url"])
    except Exception as e:
        try:
            from engine.bridge import ui
            ui.log.warning("spotify play failed: %s", e)
        except Exception:
            pass
        return ""
