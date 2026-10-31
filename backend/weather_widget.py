"""Weather for the Home widget. Open-Meteo needs no API key.

Location: the `city` the UI passes (Settings), otherwise a one-time IP lookup.
Results are cached for 15 minutes so the widget never hammers the network.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

_TTL = 15 * 60
_cache: dict[str, tuple[float, dict]] = {}

# WMO weather code -> (label, glyph kind used by the UI)
_CODES = {
    0: ("Clear", "clear"), 1: ("Mostly clear", "clear"), 2: ("Partly cloudy", "partly"),
    3: ("Cloudy", "cloud"), 45: ("Fog", "fog"), 48: ("Fog", "fog"),
    51: ("Drizzle", "rain"), 53: ("Drizzle", "rain"), 55: ("Drizzle", "rain"),
    56: ("Freezing drizzle", "rain"), 57: ("Freezing drizzle", "rain"),
    61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "rain"), 67: ("Freezing rain", "rain"),
    71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"), 77: ("Snow grains", "snow"),
    80: ("Showers", "rain"), 81: ("Showers", "rain"), 82: ("Heavy showers", "rain"),
    85: ("Snow showers", "snow"), 86: ("Snow showers", "snow"),
    95: ("Thunderstorm", "storm"), 96: ("Thunderstorm", "storm"), 99: ("Thunderstorm", "storm"),
}


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Lyra/1.0"})
    with urllib.request.urlopen(req, timeout=6) as r:
        return json.loads(r.read().decode("utf-8"))


def _locate(city: str) -> tuple[float, float, str]:
    if city:
        q = urllib.parse.urlencode({"name": city, "count": 1})
        res = _get(f"https://geocoding-api.open-meteo.com/v1/search?{q}").get("results") or []
        if not res:
            raise ValueError("city not found")
        return float(res[0]["latitude"]), float(res[0]["longitude"]), str(res[0]["name"])
    geo = _get("https://ipwho.is/")
    if not geo.get("success", True):
        raise ValueError("location unavailable")
    return float(geo["latitude"]), float(geo["longitude"]), str(geo.get("city") or "")


def get(city: str = "") -> dict:
    key = city.strip().lower()
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    try:
        lat, lon, name = _locate(city.strip())
        q = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 2,
            "current": "temperature_2m,weather_code,is_day",
            "hourly": "temperature_2m,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min",
        })
        d = _get(f"https://api.open-meteo.com/v1/forecast?{q}")
        cur = d["current"]
        label, kind = _CODES.get(int(cur["weather_code"]), ("", "cloud"))
        now_iso = str(cur["time"])[:13]
        times = d["hourly"]["time"]
        start = next((i for i, t in enumerate(times) if t[:13] >= now_iso), 0) + 1
        hourly = []
        for i in range(start, min(start + 6, len(times))):
            _, k = _CODES.get(int(d["hourly"]["weather_code"][i]), ("", "cloud"))
            hourly.append({"hour": int(times[i][11:13]), "temp": round(d["hourly"]["temperature_2m"][i]), "kind": k})
        out = {
            "ok": True, "city": name, "temp": round(cur["temperature_2m"]), "condition": label,
            "kind": kind, "night": not bool(cur.get("is_day", 1)),
            "hi": round(d["daily"]["temperature_2m_max"][0]), "lo": round(d["daily"]["temperature_2m_min"][0]),
            "hourly": hourly, "updated": int(time.time()),
        }
    except Exception as e:  # offline, blocked, bad city: the widget shows a quiet empty state
        out = {"ok": False, "error": str(e)}
        if hit:  # keep showing stale data rather than nothing
            return hit[1]
        return out
    _cache[key] = (time.time(), out)
    return out
