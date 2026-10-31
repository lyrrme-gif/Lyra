"""Lyra - knowledge card for web searches on the island.

When a web search is about a known person, place, film, company..., the island shows a
card above the results: picture, short description and the official links (website,
Instagram, X, Facebook, YouTube, TikTok, IMDb). For films, series and anime it also lists
where to watch them (Netflix, Crunchyroll, ...) in your country.

Sources: Wikipedia + Wikidata (no key), TMDB (free key: poster, socials, where to watch) and
Google's own knowledge panel (through Serper.dev). Each one is optional: if anything fails or
nothing matches, build() returns None and the island simply shows the normal result cards.
"""

from __future__ import annotations

import re
from urllib.parse import quote

import requests

_HEADERS = {"User-Agent": "LyraAssistant/1.0 (desktop assistant)", "Accept": "application/json"}
_TIMEOUT = 4

# Wikidata property -> (label, link template). {v} is the property's value.
_SOCIALS = [
    ("P856", "Website", "{v}"),
    ("P2003", "Instagram", "https://www.instagram.com/{v}/"),
    ("P2002", "X", "https://x.com/{v}"),
    ("P2013", "Facebook", "https://www.facebook.com/{v}"),
    ("P2397", "YouTube", "https://www.youtube.com/channel/{v}"),
    ("P7085", "TikTok", "https://www.tiktok.com/@{v}"),
    ("P345", "IMDb", None),  # handled below: nm... -> /name/, tt... -> /title/
]

_STOP = {"a", "an", "the", "of", "who", "what", "is", "about", "me", "tell", "on", "in"}


# "who's tom holland?" / "tell me about the eiffel tower" -> "tom holland" / "eiffel tower".
_LEAD = re.compile(
    r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
    r"(?:(?:who|what|where|when)(?:\s+is|\s+was|\s+are|\s+were|['\u2019]?s)|who\s+the\s+(?:heck|hell)\s+is"
    r"|tell\s+me\s+(?:more\s+)?about|(?:do\s+you\s+)?know\s+(?:anything\s+)?about|give\s+me\s+(?:info|information)\s+(?:on|about)"
    r"|(?:look\s+up|search\s+for|search|find|google))\s+(?:the\s+|a\s+|an\s+)?", re.I)
_TRAIL = re.compile(r"(?:[,\s]+(?:please|for\s+me|now|thanks|thank\s+you))*[\s?.!]*$", re.I)


_NOUN = r"(?:anime|movie|film|show|series|tv\s+show|tv\s+series|cartoon|manga|documentary)"
_PRE = r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:please\s+)?"
# "is arcane an anime?" / "was dune a movie?"  ->  "arcane" / "dune"
_IS_A = re.compile(_PRE + r"(?:is|was)\s+(?:the\s+)?(?P<s>.+?)\s+(?:(?:an?|the)\s+)?" + _NOUN + r"$", re.I)
# "where can I watch arcane?" / "where to watch dune"  ->  "arcane" / "dune"
_WATCH = re.compile(_PRE + r"(?:where\s+(?:can|do|could|should)\s+(?:i|we|you)\s+watch|where\s+to\s+watch|how\s+(?:can|do)\s+i\s+watch)"
                    r"\s+(?:the\s+)?(?P<s>.+?)(?:\s+(?:online|for\s+free|free|streaming))*$", re.I)


def is_media_question(text: str) -> bool:
    """\"is X an anime?\" / \"where can I watch X?\": a question about a title, not a general search."""
    q = _TRAIL.sub("", re.sub(r"\s+", " ", (text or "").strip()))
    return bool(_IS_A.match(q) or _WATCH.match(q))


def clean_query(text: str) -> str:
    """The bare subject of a question: strips 'who's', 'tell me about'... and the final '?'."""
    q = re.sub(r"\s+", " ", (text or "").strip())
    q = _TRAIL.sub("", q)
    for pat in (_IS_A, _WATCH):
        m = pat.match(q)
        if m and m.group("s").strip(" ,"):
            return m.group("s").strip(" ,")
    stripped = _LEAD.sub("", q).strip(" ,")
    return stripped or q


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOP}


def title_matches(query: str, title: str) -> bool:
    """The page must really be about the query (avoids a random card for generic searches)."""
    q, t = _tokens(query), _tokens(re.sub(r"\(.*?\)", "", title or ""))
    # same name, or the name plus at most one extra word ("tom holland age"); long phrases are not a name
    return bool(q) and bool(t) and (q <= t or (t <= q and len(q - t) <= 1))


def _first_sentences(text: str, limit: int = 230) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end > 80 else cut.rsplit(" ", 1)[0] + "…"


def _pick_value(claims: list):
    """First usable value of a Wikidata claim list; preferred rank first, deprecated never."""
    ordered = sorted(
        (c for c in claims or [] if c.get("rank") != "deprecated"),
        key=lambda c: 0 if c.get("rank") == "preferred" else 1,
    )
    for c in ordered:
        value = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def parse_socials(entity_json: dict, qid: str) -> list[dict]:
    """[{label, url}] from a Wikidata Special:EntityData document."""
    claims = (((entity_json or {}).get("entities") or {}).get(qid) or {}).get("claims") or {}
    out: list[dict] = []
    for prop, label, template in _SOCIALS:
        value = _pick_value(claims.get(prop, []))
        if not value:
            continue
        if prop == "P345":
            if value.startswith("nm"):
                url = f"https://www.imdb.com/name/{value}/"
            elif value.startswith("tt"):
                url = f"https://www.imdb.com/title/{value}/"
            else:
                continue
        elif prop == "P856":
            url = value if value.lower().startswith(("http://", "https://")) else ""
        else:
            url = template.format(v=quote(value.lstrip("@"), safe=""))
        if url:
            out.append({"label": label, "url": url})
    return out


def _get_json(url: str, params: dict | None = None):
    r = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _find_title(query: str) -> str:
    data = _get_json(
        "https://en.wikipedia.org/w/api.php",
        {"action": "opensearch", "search": query, "limit": 1, "namespace": 0, "format": "json"},
    )
    titles = data[1] if isinstance(data, list) and len(data) > 1 else []
    return titles[0] if titles else ""


def _commons_image(query: str) -> str:
    """Find a usable Commons thumbnail when the Wikipedia summary has no lead image."""
    try:
        data = _get_json(
            "https://commons.wikimedia.org/w/api.php",
            {
                "action": "query", "generator": "search", "gsrsearch": f"filetype:bitmap {query}",
                "gsrnamespace": 6, "gsrlimit": 8, "prop": "imageinfo",
                "iiprop": "url", "iiurlwidth": 640, "format": "json",
            },
        )
        pages = ((data.get("query") or {}).get("pages") or {}).values()
        for page in pages:
            info = (page.get("imageinfo") or [{}])[0]
            url = str(info.get("thumburl") or info.get("url") or "")
            if url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                return url
    except Exception:
        pass
    return ""


def _wiki_card(query: str) -> dict | None:
    """Card from Wikipedia + Wikidata for an already-cleaned query, or None."""
    try:
        title = _find_title(query)
        if not title or not title_matches(query, title):
            return None
        page = _get_json(f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'), safe='')}")
        if page.get("type") != "standard" or not page.get("extract"):
            return None  # disambiguation pages and the like
        socials: list[dict] = []
        qid = str(page.get("wikibase_item") or "")
        if qid:
            try:
                socials = parse_socials(_get_json(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"), qid)
            except Exception:
                socials = []
        page_url = ((page.get("content_urls") or {}).get("desktop") or {}).get("page", "")
        image = str((page.get("thumbnail") or {}).get("source") or "")
        if not image:
            image = _commons_image(title)
        return {
            "title": str(page.get("title") or title),
            "subtitle": str(page.get("description") or ""),
            "extract": _first_sentences(str(page.get("extract") or "")),
            "image": image,
            "url": page_url,
            "url_label": "Wikipedia",
            "socials": socials[:6],
        }
    except Exception:
        return None


# ── TMDB: posters, social links and "where to watch" for people, films, series and anime ─────────

_TMDB = "https://api.themoviedb.org/3"
_TMDB_IMG = "https://image.tmdb.org/t/p"


def _cfg(name: str) -> str:
    try:
        import config
        return str(getattr(config, name, "") or "")
    except Exception:
        return ""


def _tmdb(path: str, **params):
    key = _cfg("TMDB_API_KEY").strip()
    if not key:
        return None
    headers = dict(_HEADERS)
    if len(key) > 40:                       # the long "Read Access Token"
        headers["Authorization"] = f"Bearer {key}"
    else:
        params["api_key"] = key
    r = requests.get(_TMDB + path, params=params, headers=headers, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def watch_region() -> str:
    """Two-letter country for "where to watch": LYRA_REGION, else the Windows region, else the locale, else US."""
    r = _cfg("WATCH_REGION").strip().upper()
    if re.fullmatch(r"[A-Z]{2}", r):
        return r
    try:
        import os
        if os.name == "nt":
            import ctypes
            buf = ctypes.create_unicode_buffer(16)
            if ctypes.windll.kernel32.GetUserDefaultGeoName(buf, 16) > 1 and re.fullmatch(r"[A-Za-z]{2}", buf.value):
                return buf.value.upper()
    except Exception:
        pass
    try:
        import locale
        m = re.search(r"[_-]([A-Za-z]{2})$", locale.getlocale()[0] or "")
        if m:
            return m.group(1).upper()
    except Exception:
        pass
    return "US"


# Where a service's own search page takes a title. Anything else opens the JustWatch page TMDB links to.
_WATCH_SEARCH = [
    ("netflix", "https://www.netflix.com/search?q={q}"),
    ("crunchyroll", "https://www.crunchyroll.com/search?q={q}"),
    ("prime video", "https://www.primevideo.com/search/?phrase={q}"),
    ("hulu", "https://www.hulu.com/search?q={q}"),
    ("apple tv", "https://tv.apple.com/search?term={q}"),
    ("youtube", "https://www.youtube.com/results?search_query={q}"),
]


def _watch_chips(details: dict, title: str, region: str) -> list[dict]:
    block = (((details.get("watch/providers") or {}).get("results") or {}).get(region)) or {}
    if not block:
        return []
    link = str(block.get("link") or "")
    order = [("flatrate", "free", "ads"), ("rent", "buy")]
    picked: list[dict] = []
    for group in order:
        for kind in group:
            picked += [p for p in (block.get(kind) or []) if isinstance(p, dict)]
        if picked:
            break                            # subscriptions / free first; rent and buy only when there is nothing else
    chips: list[dict] = []
    seen: set[str] = set()
    for p in picked:
        name = str(p.get("provider_name") or "").strip()
        if not name:
            continue
        base = re.sub(r"\s+(?:basic\s+)?(?:with\s+ads|standard.*|premium.*|amazon channel|apple tv channel)$", "", name, flags=re.I)
        if base.lower() in seen:
            continue
        seen.add(base.lower())
        url = link
        for needle, template in _WATCH_SEARCH:
            if needle in base.lower():
                url = template.format(q=quote(title, safe=""))
                break
        if not url.startswith(("http://", "https://")):
            continue
        logo = str(p.get("logo_path") or "")
        chips.append({"label": base, "url": url, "logo": f"{_TMDB_IMG}/w45{logo}" if logo else ""})
        if len(chips) >= 4:
            break
    if not chips and link.startswith(("http://", "https://")):
        chips.append({"label": "Where to watch", "url": link, "logo": ""})
    return chips


def _tmdb_socials(ext: dict, homepage: str = "") -> list[dict]:
    out: list[dict] = []
    if homepage.lower().startswith(("http://", "https://")):
        out.append({"label": "Website", "url": homepage})
    ig, tw, fb, tt, yt, imdb = (str((ext or {}).get(k) or "").strip() for k in
                                ("instagram_id", "twitter_id", "facebook_id", "tiktok_id", "youtube_id", "imdb_id"))
    if ig: out.append({"label": "Instagram", "url": f"https://www.instagram.com/{quote(ig.lstrip('@'), safe='')}/"})
    if tw: out.append({"label": "X", "url": f"https://x.com/{quote(tw.lstrip('@'), safe='')}"})
    if fb: out.append({"label": "Facebook", "url": f"https://www.facebook.com/{quote(fb, safe='')}"})
    if tt: out.append({"label": "TikTok", "url": f"https://www.tiktok.com/@{quote(tt.lstrip('@'), safe='')}"})
    if yt and yt.startswith("@"): out.append({"label": "YouTube", "url": f"https://www.youtube.com/{quote(yt, safe='@')}"})
    if imdb.startswith("nm"): out.append({"label": "IMDb", "url": f"https://www.imdb.com/name/{imdb}/"})
    elif imdb.startswith("tt"): out.append({"label": "IMDb", "url": f"https://www.imdb.com/title/{imdb}/"})
    return out


def _tmdb_card(query: str) -> dict | None:
    """Card for a person, film, series or anime from TMDB, or None (no key, no match, any error)."""
    try:
        found = _tmdb("/search/multi", query=query, include_adult="false")
        pick = next((r for r in (found or {}).get("results", [])[:8]
                     if r.get("media_type") in ("movie", "tv", "person")
                     and title_matches(query, str(r.get("title") or r.get("name") or ""))), None)
        if not pick:
            return None
        kind, tid = pick["media_type"], pick["id"]
        if kind == "person":
            d = _tmdb(f"/person/{tid}", append_to_response="external_ids") or {}
            born = str(d.get("birthday") or "")[:4]
            dept = {"Acting": "Actor", "Directing": "Director", "Writing": "Writer", "Production": "Producer"}.get(
                str(d.get("known_for_department") or ""), str(d.get("known_for_department") or "Person"))
            img = str(d.get("profile_path") or "")
            return {
                "title": str(d.get("name") or ""), "subtitle": f"{dept} (born {born})" if born else dept,
                "extract": _first_sentences(str(d.get("biography") or "")),
                "image": f"{_TMDB_IMG}/w342{img}" if img else "",
                "url": f"https://www.themoviedb.org/person/{tid}", "url_label": "TMDB",
                "socials": _tmdb_socials(d.get("external_ids") or {}), "media": False,
            }
        d = _tmdb(f"/{kind}/{tid}", append_to_response="external_ids,watch/providers") or {}
        title = str(d.get("title") or d.get("name") or "")
        year = str(d.get("release_date") or d.get("first_air_date") or "")[:4]
        genres = [str(g.get("name")) for g in (d.get("genres") or []) if isinstance(g, dict)]
        anime = "Animation" in genres and (str(d.get("original_language")) == "ja" or "JP" in (d.get("origin_country") or []))
        label = "Anime" if anime else ("Movie" if kind == "movie" else "TV series")
        if not anime and "Animation" in genres:
            label = "Animated " + ("movie" if kind == "movie" else "series")
        img = str(d.get("poster_path") or "")
        return {
            "title": title, "subtitle": " · ".join(x for x in (label, year, ", ".join(genres[:2])) if x),
            "extract": _first_sentences(str(d.get("overview") or "")),
            "image": f"{_TMDB_IMG}/w342{img}" if img else "",
            "url": f"https://www.themoviedb.org/{kind}/{tid}", "url_label": "TMDB",
            "socials": _tmdb_socials(d.get("external_ids") or {}, str(d.get("homepage") or "")),
            "watch": _watch_chips(d, title, watch_region()), "media": True,
        }
    except Exception:
        return None


def _google_card(query: str) -> dict | None:
    """Google's own knowledge panel (through Serper.dev), used when nothing else matched."""
    try:
        import search as _search
        kg = _search.google_knowledge_graph(query)
        if not kg or not title_matches(query, str(kg.get("title") or "")):
            return None
        link = str(kg.get("descriptionLink") or kg.get("website") or "")
        image = str(kg.get("imageUrl") or "")
        return {
            "title": str(kg.get("title")), "subtitle": str(kg.get("type") or ""),
            "extract": _first_sentences(str(kg.get("description") or "")),
            "image": image if image.startswith(("http://", "https://")) else "",
            "url": link if link.startswith(("http://", "https://")) else "",
            "url_label": "Wikipedia" if "wikipedia.org" in link else "Website",
            "socials": [],
        }
    except Exception:
        return None


def _merge(wiki: dict | None, tmdb: dict | None) -> dict | None:
    """Wikipedia's text for people, TMDB's poster / subtitle / links / where-to-watch for titles."""
    if not wiki or not tmdb:
        return tmdb or wiki
    if not (title_matches(wiki["title"], tmdb["title"]) or title_matches(tmdb["title"], wiki["title"])):
        return tmdb                           # they found different things: the one with where-to-watch wins
    media = bool(tmdb.get("media"))
    socials: list[dict] = []
    for s in list(wiki.get("socials") or []) + list(tmdb.get("socials") or []):
        if s["label"] not in {x["label"] for x in socials}:
            socials.append(s)
    return {
        "title": wiki["title"],
        "subtitle": (tmdb["subtitle"] if media else wiki.get("subtitle") or tmdb["subtitle"]),
        "extract": wiki.get("extract") or tmdb.get("extract") or "",
        "image": (tmdb.get("image") if media else wiki.get("image")) or wiki.get("image") or tmdb.get("image") or "",
        "url": wiki.get("url") or tmdb.get("url") or "", "url_label": wiki.get("url_label") or "Wikipedia",
        "socials": socials[:6], "watch": tmdb.get("watch") or [],
    }


def build(query: str) -> dict | None:
    """The knowledge card for `query`, or None. Never raises."""
    try:
        query = clean_query(query)
        if not query:
            return None
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_wiki, f_tmdb = pool.submit(_wiki_card, query), pool.submit(_tmdb_card, query)
            wiki, tmdb = f_wiki.result(), f_tmdb.result()
        card = _merge(wiki, tmdb) or _google_card(query)
        if card:
            card.pop("media", None)
        return card
    except Exception:
        return None
