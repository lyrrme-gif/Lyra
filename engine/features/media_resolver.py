"""Network-assisted media resolvers. No browser automation required."""
from urllib.parse import quote_plus
import re
import webbrowser

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0 Safari/537.36")


def _youtube_scrape(query: str):
    """Read the first real video (not an ad / short / channel) off YouTube's results page."""
    try:
        import requests
        r = requests.get(
            "https://www.youtube.com/results",
            params={"search_query": query, "hl": "en"},
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
            cookies={"CONSENT": "YES+1"},
            timeout=8,
        )
        m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})"', r.text)
        if m:
            return f"https://www.youtube.com/watch?v={m.group(1)}"
    except Exception:
        pass
    return None


def youtube_first(query: str):
    u = _youtube_scrape(query)
    if u:
        return u
    try:
        from ddgs import DDGS
        with DDGS() as d:
            rows = list(d.text(f"site:youtube.com/watch {query}", max_results=5))
        for r in rows:
            u = str(r.get("href") or r.get("url") or "")
            if "youtube.com/watch" in u:
                return u
    except Exception:
        pass
    return None


def open_youtube_video(query: str):
    u = youtube_first(query)
    if not u:
        # Never leave the user with nothing: show the results page instead.
        webbrowser.open(f"https://www.youtube.com/results?search_query={quote_plus(query)}")
        return False, (f"I couldn't pick a video for {query} automatically, "
                       "so I opened the YouTube search results instead.")
    webbrowser.open(u)
    return True, u


def _spotify_track_via_search(query: str):
    """No-credentials fallback: find the track id through a web search of open.spotify.com."""
    try:
        from ddgs import DDGS
        with DDGS() as d:
            rows = list(d.text(f"site:open.spotify.com/track {query}", max_results=5))
        for r in rows:
            u = str(r.get("href") or r.get("url") or "")
            m = re.search(r"open\.spotify\.com/(?:intl-[a-z]+/)?track/([A-Za-z0-9]{22})", u)
            if m:
                return "spotify:track:" + m.group(1)
    except Exception:
        pass
    # ddgs missing or blocked: ask DuckDuckGo's plain HTML page directly.
    try:
        import requests
        from urllib.parse import unquote
        r = requests.post("https://html.duckduckgo.com/html/",
                          data={"q": f"site:open.spotify.com/track {query}"},
                          headers={"User-Agent": _UA}, timeout=8)
        mm = re.search(r"open\.spotify\.com/(?:intl-[a-z]+/)?track/([A-Za-z0-9]{22})", unquote(r.text))
        if mm:
            return "spotify:track:" + mm.group(1)
    except Exception:
        pass
    return None


def spotify_track_uri(query: str):
    import requests
    from engine.brain.llm_store import get
    # Client credentials are optional settings; no secrets are invented.
    s=get(); cid=s.get("spotify_client_id"); secret=s.get("spotify_client_secret")
    if not cid or not secret: return _spotify_track_via_search(query)
    try:
        tok=requests.post("https://accounts.spotify.com/api/token",data={"grant_type":"client_credentials"},auth=(cid,secret),timeout=8).json().get("access_token")
        if not tok: return _spotify_track_via_search(query)
        data=requests.get("https://api.spotify.com/v1/search",params={"q":query,"type":"track","limit":1},headers={"Authorization":f"Bearer {tok}"},timeout=8).json()
        item=(data.get("tracks",{}).get("items") or [None])[0]
        if item and item.get("id"): return "spotify:track:"+item["id"]
    except Exception:
        return _spotify_track_via_search(query)
    return _spotify_track_via_search(query)
