"""Lyra - dictation statistics (Phase 3).

A tiny, local-only SQLite log of *how much* you dictate: words, seconds, which app, which engine,
which language. It never stores what you said (no text, no audio) and nothing here touches the
network. The file lives at paths.data_path("dictation_stats.db") (backend/*.db is already ignored by git).

Contract C3 (called by Phase 2's dictation code through a guarded import):

    dictation_stats.record(words, seconds, app, engine, language)

`record` never raises: a broken stats file must never get in the way of typing what you said.

Maths (kept simple and honest, see the tests):
  * words/min       = words / minutes spoken, over dictations that have a measured duration.
  * time saved      = what typing the same words at TYPING_WPM (45) would have taken, minus the time
                      you actually spent dictating, summed over the range and never below zero.
  * streak          = consecutive local days with at least one dictation. A streak that ended
                      yesterday is still alive today (you have until midnight to extend it).
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

log = logging.getLogger("lyra.dictation_stats")

TYPING_WPM = 45                 # the typing speed time saved is measured against
MAX_SECONDS = 6 * 3600          # one dictation longer than this is treated as a glitch and clamped
MAX_APP_LEN = 64
UNKNOWN_APP = "Unknown"
RANGES = ("week", "month", "all")
RANGE_DAYS = {"week": 7, "month": 30}
MONTH_BUCKET_AFTER_DAYS = 120   # "All" switches from per-day to per-month bars beyond this span

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dictations (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       INTEGER NOT NULL,      -- unix seconds (UTC)
    day      TEXT    NOT NULL,      -- local calendar day, YYYY-MM-DD, fixed when recorded
    words    INTEGER NOT NULL,
    seconds  REAL    NOT NULL,      -- 0 = duration unknown
    app      TEXT    NOT NULL,
    engine   TEXT    NOT NULL,
    language TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dictations_day ON dictations(day);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


# ── pure maths (no database) ────────────────────────────────────────────────────

def typing_seconds(words: int, wpm: int = TYPING_WPM) -> float:
    """How long typing `words` would take at `wpm`."""
    return max(0, words) / float(wpm) * 60.0


def time_saved_seconds(words: int, seconds_spoken: float, wpm: int = TYPING_WPM) -> float:
    """Typing time minus dictating time, never negative."""
    return max(0.0, typing_seconds(words, wpm) - max(0.0, seconds_spoken))


def words_per_minute(words: int, seconds: float) -> float:
    return round(words / (seconds / 60.0), 1) if seconds > 0 and words > 0 else 0.0


def normalize_app(app: object) -> str:
    """'  Chrome.EXE ' -> 'Chrome'. Empty -> 'Unknown'."""
    s = str(app or "").strip()
    if s.lower().endswith(".exe"):
        s = s[:-4].strip()
    s = " ".join(s.split())[:MAX_APP_LEN]
    return s or UNKNOWN_APP


def streaks(days: Iterable[str], today: date) -> tuple[int, int]:
    """(current, longest) streak from ISO day strings. `current` counts back from today, or from
    yesterday when today has no dictation yet."""
    ds = sorted({date.fromisoformat(d) for d in days})
    if not ds:
        return 0, 0
    longest = run = 1
    for prev, cur in zip(ds, ds[1:]):
        run = run + 1 if (cur - prev).days == 1 else 1
        longest = max(longest, run)
    have = set(ds)
    cursor = today if today in have else today - timedelta(days=1)
    current = 0
    while cursor in have:
        current += 1
        cursor -= timedelta(days=1)
    return current, longest


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


# ── storage ─────────────────────────────────────────────────────────────────────

class Store:
    """One SQLite file. A fresh connection per call keeps it safe across the dictation and UI threads."""

    def __init__(self, path: Path | str | None = None):
        self._path = Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._ready = False

    def _file(self) -> Path:
        if self._path is not None:
            return self._path
        import paths
        return paths.data_path("dictation_stats.db")

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self._file()), timeout=5)
        if not self._ready:
            con.executescript(_SCHEMA)
            con.commit()
            self._ready = True
        return con

    # -- settings kept next to the data (so no hot file needs a new key)
    def enabled(self) -> bool:
        try:
            with self._lock, self._connect() as con:
                row = con.execute("SELECT value FROM meta WHERE key='enabled'").fetchone()
            return row is None or row[0] != "0"
        except Exception as e:
            log.warning("stats: could not read setting: %s", e)
            return True

    def set_enabled(self, on: bool) -> None:
        with self._lock, self._connect() as con:
            con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('enabled', ?)", ("1" if on else "0",))

    # -- writing
    def record(self, words: int, seconds: float = 0.0, app: str = "", engine: str = "",
               language: str = "", *, now: datetime | None = None) -> bool:
        """Log one finished dictation. Returns True when a row was written. Never raises."""
        try:
            words = int(words)
            if words <= 0:
                return False
            if not self.enabled():
                return False
            try:
                seconds = float(seconds)
            except (TypeError, ValueError):
                seconds = 0.0
            if not (seconds > 0):            # also catches NaN
                seconds = 0.0
            seconds = min(seconds, float(MAX_SECONDS))
            local = now or datetime.now().astimezone()
            with self._lock, self._connect() as con:
                con.execute(
                    "INSERT INTO dictations(ts, day, words, seconds, app, engine, language) VALUES(?,?,?,?,?,?,?)",
                    (int(local.timestamp()), local.date().isoformat(), words, round(seconds, 2),
                     normalize_app(app), str(engine or "").strip()[:32], str(language or "").strip()[:16]),
                )
            return True
        except Exception as e:                # a stats problem must never break dictation
            log.warning("stats: could not record: %s", e)
            return False

    def clear(self) -> int:
        """Delete every row (the user's 'reset statistics'). Returns how many were removed."""
        with self._lock, self._connect() as con:
            n = con.execute("SELECT COUNT(*) FROM dictations").fetchone()[0]
            con.execute("DELETE FROM dictations")
            return int(n)

    # -- reading
    def summary(self, range_: str = "week", *, today: date | None = None, app_limit: int = 8) -> dict:
        """Everything the Statistics panel shows, for 'week' (last 7 days), 'month' (last 30) or 'all'."""
        range_ = range_ if range_ in RANGES else "week"
        today = today or datetime.now().astimezone().date()
        enabled = self.enabled()
        with self._lock, self._connect() as con:
            all_days = [r[0] for r in con.execute("SELECT DISTINCT day FROM dictations")]
            first_day = min(all_days) if all_days else None
            if range_ == "all":
                start = date.fromisoformat(first_day) if first_day else today
            else:
                start = today - timedelta(days=RANGE_DAYS[range_] - 1)
            lo, hi = start.isoformat(), today.isoformat()
            rows = con.execute(
                "SELECT day, words, seconds, app FROM dictations WHERE day >= ? AND day <= ?", (lo, hi)
            ).fetchall()
            engines = con.execute(
                "SELECT engine, COUNT(*), SUM(words) FROM dictations WHERE day >= ? AND day <= ? "
                "AND engine != '' GROUP BY engine ORDER BY SUM(words) DESC", (lo, hi)
            ).fetchall()

        words = sum(r[1] for r in rows)
        timed = [r for r in rows if r[2] > 0]            # dictations whose length we know
        timed_words, timed_secs = sum(r[1] for r in timed), sum(r[2] for r in timed)
        seconds = sum(r[2] for r in rows)
        saved = time_saved_seconds(timed_words, timed_secs)
        today_rows = [r for r in rows if r[0] == hi]
        current, longest = streaks(all_days, today)

        return {
            "ok": True,
            "range": range_,
            "enabled": enabled,
            "has_data": bool(rows),
            "ever_dictated": bool(all_days),
            "first_day": first_day,
            "typing_wpm": TYPING_WPM,
            "totals": {
                "words": words,
                "dictations": len(rows),
                "seconds": round(seconds, 1),
                "wpm": words_per_minute(timed_words, timed_secs),
                "saved_seconds": round(saved),
                "avg_words": round(words / len(rows), 1) if rows else 0,
            },
            "today": {"words": sum(r[1] for r in today_rows), "dictations": len(today_rows)},
            "streak": {"current": current, "longest": longest},
            "series": self._series(rows, start, today, range_),
            "apps": self._apps(rows, app_limit),
            "engines": [{"engine": e, "dictations": int(n), "words": int(w or 0)} for e, n, w in engines],
        }

    @staticmethod
    def _series(rows: list, start: date, today: date, range_: str) -> dict:
        per_day: dict[str, list[int]] = {}
        for day, w, _s, _a in rows:
            cell = per_day.setdefault(day, [0, 0])
            cell[0] += w
            cell[1] += 1
        span = (today - start).days + 1
        if range_ == "all" and span > MONTH_BUCKET_AFTER_DAYS:
            months: dict[str, list[int]] = {}
            d = date(start.year, start.month, 1)
            while d <= today:                              # zero-fill every month in between
                months[_month_key(d)] = [0, 0]
                d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
            for day, (w, n) in per_day.items():
                cell = months[_month_key(date.fromisoformat(day))]
                cell[0] += w
                cell[1] += n
            pts = [{"key": k, "words": v[0], "dictations": v[1]} for k, v in months.items()]
            return {"granularity": "month", "points": pts}
        pts = []
        for i in range(span):                              # zero-fill every day so gaps show as gaps
            key = (start + timedelta(days=i)).isoformat()
            w, n = per_day.get(key, [0, 0])
            pts.append({"key": key, "words": w, "dictations": n})
        return {"granularity": "day", "points": pts}

    @staticmethod
    def _apps(rows: list, limit: int) -> list[dict]:
        agg: dict[str, list] = {}
        spelled: dict[str, dict[str, int]] = {}
        for _d, w, _s, app in rows:
            key = app.lower()                                  # 'chrome' and 'Chrome' are one app
            cell = agg.setdefault(key, [app, 0, 0])
            cell[1] += w
            cell[2] += 1
            sp = spelled.setdefault(key, {})
            sp[app] = sp.get(app, 0) + w
        for key, cell in agg.items():                          # show the spelling used most (ties: A-Z)
            cell[0] = sorted(spelled[key].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        total = sum(v[1] for v in agg.values()) or 1
        ordered = sorted(agg.values(), key=lambda v: (-v[1], v[0].lower()))
        out = [{"app": a, "words": w, "dictations": n, "share": round(w / total, 4)} for a, w, n in ordered]
        if limit > 0 and len(out) > limit:
            rest = out[limit - 1:]
            out = out[: limit - 1] + [{
                "app": "Other", "words": sum(r["words"] for r in rest),
                "dictations": sum(r["dictations"] for r in rest),
                "share": round(sum(r["share"] for r in rest), 4), "other": True,
            }]
        return out


# ── module-level API (what other phases import) ─────────────────────────────────

_default = Store()


def record(words: int, seconds: float = 0.0, app: str = "", engine: str = "", language: str = "") -> bool:
    """Contract C3."""
    return _default.record(words, seconds, app, engine, language)


def summary(range_: str = "week") -> dict:
    return _default.summary(range_)


def clear() -> int:
    return _default.clear()


def enabled() -> bool:
    return _default.enabled()


def set_enabled(on: bool) -> None:
    _default.set_enabled(on)
