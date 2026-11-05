"""Lyra web search providers + structured rich results.

Tavily is preferred when configured; DDGS is the no-key fallback. Search results
are real provider results and are also available to the Agent as structured data.
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from urllib.parse import urljoin, urlparse
from html.parser import HTMLParser

import requests

try:
    DDGS = import_module("ddgs").DDGS
except (ImportError, AttributeError):
    try:
        DDGS = import_module("duckduckgo_search").DDGS
    except (ImportError, AttributeError):
        DDGS = None

from engine.bridge import ui
from engine.core.config import TAVILY_API_KEY, SERPER_API_KEY, SEARCH_MAX_RESULTS, SEARCH_TIMELIMIT, DEBUG_SEARCH

log = ui.log
trace = ui.trace

# ── Provider circuit breaker ─────────────────────────────────────────────────────────────────
# A provider that just failed (DNS error, 429, 402...) is skipped for a while instead of being retried (and
# waited on) for every search. Without this a dead Serper + rate-limited DDGS made one answer take 17 s.
_down_until: dict[str, float] = {}
_DEADLINE_DDGS = 9.0          # hard cap for the whole DuckDuckGo attempt (it walks several backends itself)
_DEADLINE_THUMBS = 2.8        # result pictures are optional: never hold the answer for them


def _is_down(name: str) -> bool:
    return _down_until.get(name, 0.0) > time.monotonic()


def _mark_down(name: str, why: object, secs: float | None = None) -> None:
    text = str(why).lower()
    if secs is None:
        secs = 300.0 if any(k in text for k in ("429", "402", "403", "401", "ratelimit", "rate limit", "quota")) else 90.0
    _down_until[name] = time.monotonic() + secs
    log.warning("search provider %s paused for %.0fs: %s", name, secs, str(why)[:160])

def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""

def _safe_thumbnail(url: str) -> str:
    """Derive a thumbnail only from known-safe URL patterns (currently YouTube)."""
    try:
        host = urlparse(url).netloc.lower()
        if host.endswith("youtube.com") or host == "youtu.be":
            path = urlparse(url).path
            query = urlparse(url).query
            vid = ""
            if host == "youtu.be":
                vid = path.strip("/").split("/")[0]
            else:
                m = re.search(r"(?:^|&)v=([A-Za-z0-9_-]{6,})", query)
                if m: vid = m.group(1)
            if vid:
                return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    except Exception:
        pass
    return ""


class _ImageMeta(HTMLParser):
    """Read the first Open Graph or Twitter preview image from a result page."""
    def __init__(self):
        super().__init__()
        self.image = ""

    def handle_starttag(self, tag, attrs):
        if self.image:
            return
        values = {str(k).lower(): str(v or "") for k, v in attrs}
        if tag.lower() == "link" and values.get("rel", "").lower() == "image_src" and values.get("href"):
            self.image = values["href"].strip()
            return
        if tag.lower() != "meta":
            return
        key = (values.get("property") or values.get("name") or "").lower()
        if key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"}:
            self.image = values.get("content", "").strip()


_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/124.0 Safari/537.36")


def _wikipedia_thumbnail(url: str) -> str:
    """A Wikipedia article's lead picture, from its own summary API (the page itself is slow to scan)."""
    try:
        p = urlparse(url)
        if not p.netloc.endswith("wikipedia.org") or not p.path.startswith("/wiki/"):
            return ""
        title = p.path[len("/wiki/"):]
        r = requests.get(f"https://{p.netloc}/api/rest_v1/page/summary/{title}",
                         headers={"User-Agent": "Lyra/1.0 (desktop assistant)"}, timeout=2.5)
        if r.status_code >= 400:
            return ""
        data = r.json() or {}
        return str((data.get("thumbnail") or {}).get("source") or (data.get("originalimage") or {}).get("source") or "")
    except Exception:
        return ""


def _page_thumbnail(url: str) -> str:
    """Use a page's published preview image when the search provider has none."""
    try:
        if urlparse(url).scheme not in {"http", "https"}:
            return ""
        wiki = _wikipedia_thumbnail(url)
        if wiki:
            return wiki
        # A browser-like User-Agent: many sites send no preview picture (or a 403) to anything that looks like a bot.
        response = requests.get(url, headers={"User-Agent": _BROWSER_UA, "Accept": "text/html,*/*;q=0.8",
                                              "Accept-Language": "en-US,en;q=0.8"}, timeout=2.5, stream=True)
        try:
            if response.status_code >= 400:
                return ""
            content_type = response.headers.get("Content-Type", "").lower()
            if content_type and "html" not in content_type:
                return ""
            parser = _ImageMeta()
            seen = 0
            # The preview tags live in <head>: read in chunks and stop as soon as the picture is found
            # or the head ends (up to ~256 KB, the old single 64 KB read missed pages with big inline heads).
            for chunk in response.iter_content(32768):
                seen += len(chunk)
                text = chunk.decode(response.encoding or "utf-8", errors="ignore")
                parser.feed(text)
                if parser.image or "</head>" in text.lower() or seen >= 262144:
                    break
            img = parser.image.strip()
            if img.startswith("//"):
                img = "https:" + img
            return urljoin(url, img) if img else ""
        finally:
            response.close()
    except Exception:
        return ""


def _enrich_page_thumbnails(rows: list[dict]) -> list[dict]:
    missing = [row for row in rows if not row.get("thumbnail")]
    if not missing:
        return rows
    # Resolve a few result previews concurrently so missing metadata does not
    # add a serial page-load delay to the search response.
    pool = ThreadPoolExecutor(max_workers=min(4, len(missing)))
    futs = [(row, pool.submit(_page_thumbnail, row.get("url", ""))) for row in missing]
    end = time.monotonic() + _DEADLINE_THUMBS
    for row, fut in futs:
        try:
            row["thumbnail"] = fut.result(timeout=max(0.0, end - time.monotonic()))
        except Exception:
            row["thumbnail"] = ""                    # too slow / failed: the card simply has no picture
    pool.shutdown(wait=False, cancel_futures=True)
    return rows

def _normalise(rows: list[dict], provider: str, enrich: bool = True) -> list[dict]:
    out = []
    for row in rows[:SEARCH_MAX_RESULTS]:
        title = str(row.get("title") or row.get("name") or "").strip()
        url = str(row.get("url") or row.get("href") or "").strip()
        desc = str(row.get("content") or row.get("body") or row.get("snippet") or "").strip()
        if not title or not url:
            continue
        thumb = str(row.get("thumbnail") or row.get("image") or row.get("image_url") or "").strip()
        item = {
            "title": title,
            "url": url,
            "description": desc[:700],
            "source": str(row.get("source") or _host(url)),
            "channel": str(row.get("channel") or row.get("author") or ""),
            "published": str(row.get("published_date") or row.get("date") or ""),
            "thumbnail": thumb,
            "provider": provider,
            "metadata": {k: row.get(k) for k in ("score", "type", "duration") if row.get(k) is not None},
        }
        if enrich and not item["thumbnail"]:
            item["thumbnail"] = _safe_thumbnail(url)
        out.append(item)
    return out

def tavily_search_rich(prompt: str, max_results: int = SEARCH_MAX_RESULTS) -> list[dict]:
    if not TAVILY_API_KEY or _is_down("Tavily"):
        return []
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": TAVILY_API_KEY, "query": prompt, "max_results": max_results, "search_depth": "basic"},
            timeout=(3, 8),
        )
        resp.raise_for_status()
        rows = resp.json().get("results", [])
        data = _normalise(rows, "Tavily", enrich=True)
        if data:
            data = _enrich_page_thumbnails(data)
            trace(f"web search: Tavily, query='{prompt}'")
        return data
    except Exception as e:
        log.error("Tavily search failed: %s", e)
        _mark_down("Tavily", e)
        return []

def ddgs_search_rich(prompt: str, max_results: int = SEARCH_MAX_RESULTS) -> list[dict]:
    if DDGS is None:
        log.error("DDGS search unavailable: install the 'ddgs' package")
        return []
    if _is_down("DuckDuckGo"):
        return []
    box: dict = {}

    def run() -> None:
        try:
            with DDGS(timeout=6) as ddgs:
                box["rows"] = list(ddgs.text(prompt, max_results=max_results, timelimit=SEARCH_TIMELIMIT))
        except Exception as e:                        # noqa: BLE001
            box["err"] = e

    th = threading.Thread(target=run, name="lyra-ddgs", daemon=True)
    th.start()
    th.join(_DEADLINE_DDGS)
    if th.is_alive():
        log.error("DDGS search gave up after %.0fs", _DEADLINE_DDGS)
        _mark_down("DuckDuckGo", "timeout", 60.0)
        return []
    if "err" in box:
        log.error("DDGS search failed: %s", box["err"])
        _mark_down("DuckDuckGo", box["err"])
        return []
    data = _normalise(box.get("rows") or [], "DuckDuckGo", enrich=True)
    if data:
        data = _enrich_page_thumbnails(data)
        trace(f"web search: DDGS (Tavily fallback), query='{prompt}'")
    return data

# ── Google (through Serper.dev) ─────────────────────────────────────────────────────────────
import threading

_serper_lock = threading.Lock()
_serper_cache: dict[str, dict] = {}


def _serper(query: str) -> dict:
    """One Google search through Serper.dev, cached so the results list and the knowledge panel
    (asked for in parallel) share a single request. {} without a key or on any failure."""
    if not SERPER_API_KEY or not query.strip():
        return {}
    key = query.strip().lower()
    if _is_down("Google"):
        return {}
    with _serper_lock:
        if key in _serper_cache:
            return _serper_cache[key]
        if _is_down("Google"):                        # the parallel call just failed: don't repeat it
            return {}
        try:
            resp = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                json={"q": query.strip(), "num": 10, "hl": "en"},
                timeout=(3, 6),
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.error("Google (Serper) search failed: %s", e)
            _mark_down("Google", e)
            return {}
        if isinstance(data, dict) and data:
            if len(_serper_cache) >= 24:
                _serper_cache.clear()
            _serper_cache[key] = data
            return data
        return {}


def google_search_rich(prompt: str, max_results: int = SEARCH_MAX_RESULTS) -> list[dict]:
    """Google's own ranking and snippets (via Serper.dev), with a picture for each result."""
    data = _serper(prompt)
    rows = [
        {"title": r.get("title"), "url": r.get("link"), "body": r.get("snippet"),
         "image": r.get("imageUrl"), "date": r.get("date")}
        for r in (data.get("organic") or []) if isinstance(r, dict)
    ]
    out = _normalise(rows, "Google", enrich=True)
    if out:
        out = _enrich_page_thumbnails(out)
        trace(f"web search: Google, query='{prompt}'")
    return out


def google_knowledge_graph(query: str) -> dict | None:
    """Google's knowledge panel for `query` (title, type, description, picture), if it shows one."""
    kg = _serper(query).get("knowledgeGraph")
    return kg if isinstance(kg, dict) and kg.get("title") else None


def google_related(query: str, limit: int = 4) -> list[str]:
    """Google's \"related searches\" (then \"people also ask\") for `query`, from the cached Serper answer
    (no extra request). [] without a key / when Google showed none."""
    data = _serper(query)
    out: list[str] = []
    for r in (data.get("relatedSearches") or []):
        q = str(r.get("query") or "").strip() if isinstance(r, dict) else ""
        if q and q.lower() not in (x.lower() for x in out):
            out.append(q)
    if len(out) < limit:
        for r in (data.get("peopleAlsoAsk") or []):
            q = str(r.get("question") or "").strip() if isinstance(r, dict) else ""
            if q and q.lower() not in (x.lower() for x in out):
                out.append(q)
    return out[:limit]


def web_search_results(prompt: str, max_results: int = SEARCH_MAX_RESULTS) -> list[dict]:
    """Return real structured search results: Google first, then Tavily, then DuckDuckGo.
    Providers that recently failed are skipped; the per-provider timings land in the log."""
    t0 = time.monotonic()
    notes: list[str] = []
    for name, fn in (("Google", google_search_rich), ("Tavily", tavily_search_rich), ("DuckDuckGo", ddgs_search_rich)):
        if _is_down(name):
            notes.append(f"{name} paused {_down_until[name] - time.monotonic():.0f}s")
            continue
        t1 = time.monotonic()
        data = fn(prompt, max_results)
        ms = (time.monotonic() - t1) * 1000
        notes.append(f"{name} {'ok' if data else 'empty/failed'} {ms:.0f}ms")
        if data:
            trace(f"web search timing: {' | '.join(notes)} | total {(time.monotonic() - t0) * 1000:.0f}ms")
            return data
    trace(f"web search timing: {' | '.join(notes)} | NO RESULTS, total {(time.monotonic() - t0) * 1000:.0f}ms")
    return []


def _yt_id(url: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{6,})", url or "")
    return m.group(1) if m else ""

_YT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/124.0 Safari/537.36")


def _walk_video_renderers(node):
    """Yield every videoRenderer dict nested anywhere in YouTube's page data."""
    if isinstance(node, dict):
        vr = node.get("videoRenderer")
        if isinstance(vr, dict):
            yield vr
        for v in node.values():
            yield from _walk_video_renderers(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_video_renderers(v)


def _yt_text(obj) -> str:
    if not isinstance(obj, dict):
        return ""
    if obj.get("simpleText"):
        return str(obj["simpleText"])
    return "".join(str(r.get("text", "")) for r in obj.get("runs", []) if isinstance(r, dict))


def parse_youtube_page(html: str, max_results: int) -> list[dict]:
    """Videos (title, channel, duration, ...) from YouTube's own results page; [] if the page has none."""
    i = html.find("ytInitialData")
    j = html.find("{", i) if i >= 0 else -1
    if j < 0:
        return []
    try:
        data, _ = json.JSONDecoder().raw_decode(html[j:])
    except ValueError:
        return []
    rows: list[dict] = []
    seen: set[str] = set()
    for vr in _walk_video_renderers(data):
        vid = str(vr.get("videoId") or "")
        if not re.fullmatch(r"[\w-]{11}", vid) or vid in seen:
            continue
        seen.add(vid)
        rows.append({
            "title": _yt_text(vr.get("title")) or "YouTube video",
            "url": f"https://www.youtube.com/watch?v={vid}",
            "description": _yt_text(vr.get("descriptionSnippet")),
            "source": "youtube.com",
            "channel": _yt_text(vr.get("ownerText")),
            "published": _yt_text(vr.get("publishedTimeText")),
            "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            "duration": _yt_text(vr.get("lengthText")),
        })
        if len(rows) >= max_results:
            break
    return rows


def _youtube_page_rows(query: str, max_results: int) -> list[dict]:
    """Ask YouTube directly (no third-party search engine, so no rate limits or timeouts)."""
    try:
        r = requests.get(
            "https://www.youtube.com/results",
            params={"search_query": query, "hl": "en"},
            headers={"User-Agent": _YT_UA, "Accept-Language": "en-US,en;q=0.9"},
            cookies={"CONSENT": "YES+1"},
            timeout=8,
        )
        return parse_youtube_page(r.text, max_results)
    except Exception as e:
        log.warning("YouTube page search failed: %s", e)
        return []


def youtube_search_results(query: str, max_results: int = 6) -> list[dict]:
    """Real YouTube video results (title, channel, duration, thumbnail) for chat cards."""
    rows: list[dict] = _youtube_page_rows(query, max_results)
    if not rows and DDGS is not None:
        try:
            with DDGS() as ddgs:
                raw = list(ddgs.videos(query, max_results=max_results * 2))
            for r in raw:
                url = str(r.get("content") or r.get("url") or "")
                vid = _yt_id(url)
                if not vid:
                    continue
                imgs = r.get("images") or {}
                rows.append({
                    "title": r.get("title"), "url": f"https://www.youtube.com/watch?v={vid}",
                    "description": r.get("description") or "",
                    "source": "youtube.com",
                    "channel": r.get("uploader") or r.get("publisher") or "",
                    "published": r.get("published") or "",
                    "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
                    "duration": r.get("duration") or "",
                })
        except Exception as e:
            log.error("YouTube video search failed: %s", e)
    if not rows:  # fallback: text search restricted to YouTube
        rows = [r for r in web_search_results(f"site:youtube.com/watch {query}", max_results * 2)
                if _yt_id(r.get("url", ""))]
        for r in rows:
            r["thumbnail"] = f"https://i.ytimg.com/vi/{_yt_id(r['url'])}/mqdefault.jpg"
            r["description"] = r.get("description", "")
    out = _normalise(rows, "YouTube", enrich=True)
    for o, r in zip(out, rows):  # keep duration/channel which _normalise does not copy
        o["metadata"]["duration"] = r.get("duration", "")
        o["channel"] = r.get("channel", o["channel"])
        o["thumbnail"] = r.get("thumbnail", o["thumbnail"])
    return out[:max_results]
