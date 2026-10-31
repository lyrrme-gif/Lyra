"""Lyra — local dictation history (Phase 2, contract C4).

Every finished dictation is kept as TEXT in a small SQLite file in Lyra's data folder
(paths.data_path). It never leaves the machine, audio is never stored, and old entries are
deleted on their own after the retention period chosen in Settings (7 / 30 / 90 days / forever).

    add(text, app, engine, language, seconds)   -> id or None (None = history is switched off)
    search(query="", limit=50, offset=0)        -> newest first
    last()                                      -> newest entry or None
    delete(id) / clear() / purge_expired()
"""
from __future__ import annotations

import sqlite3
import threading
import time

_lock = threading.RLock()
_path_override = None            # tests point this at a temp file
_last_purge = 0.0
MAX_TEXT = 20000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    text TEXT NOT NULL,
    app TEXT NOT NULL DEFAULT '',
    engine TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    seconds REAL NOT NULL DEFAULT 0,
    words INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS entries_ts ON entries (ts);
"""


def _db_file():
    if _path_override is not None:
        return _path_override
    import paths
    return paths.data_path("dictation_history.db")


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(_db_file()), timeout=5)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


def _pref(key: str):
    import dictation_clean
    return dictation_clean.pref(key)


def enabled() -> bool:
    try:
        return bool(_pref("p2_history_enabled"))
    except Exception:
        return True


def retention_days() -> int:
    """0 = keep forever."""
    try:
        return max(0, int(_pref("p2_history_retention_days")))
    except Exception:
        return 30


def _row(r: sqlite3.Row) -> dict:
    return {k: r[k] for k in r.keys()}


def add(text: str, app: str = "", engine: str = "", language: str = "", seconds: float = 0.0) -> int | None:
    text = (text or "").strip()
    if not text or not enabled():
        return None
    with _lock:
        con = _connect()
        try:
            cur = con.execute(
                "INSERT INTO entries (ts, text, app, engine, language, seconds, words) VALUES (?,?,?,?,?,?,?)",
                (time.time(), text[:MAX_TEXT], str(app or "")[:80], str(engine or "")[:40],
                 str(language or "")[:16], max(0.0, float(seconds or 0)), len(text.split())))
            con.commit()
            new_id = cur.lastrowid
        finally:
            con.close()
    _maybe_purge()
    return new_id


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search(query: str = "", limit: int = 50, offset: int = 0) -> list[dict]:
    limit = max(1, min(500, int(limit)))
    offset = max(0, int(offset))
    q = (query or "").strip()
    with _lock:
        con = _connect()
        try:
            if q:
                rows = con.execute(
                    "SELECT * FROM entries WHERE text LIKE ? ESCAPE '\\' OR app LIKE ? ESCAPE '\\' "
                    "ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?", (_like(q), _like(q), limit, offset)).fetchall()
            else:
                rows = con.execute("SELECT * FROM entries ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
                                   (limit, offset)).fetchall()
            return [_row(r) for r in rows]
        finally:
            con.close()


def count() -> int:
    with _lock:
        con = _connect()
        try:
            return int(con.execute("SELECT COUNT(*) FROM entries").fetchone()[0])
        finally:
            con.close()


def last() -> dict | None:
    rows = search("", 1)
    return rows[0] if rows else None


def delete(entry_id: int) -> bool:
    with _lock:
        con = _connect()
        try:
            cur = con.execute("DELETE FROM entries WHERE id = ?", (int(entry_id),))
            con.commit()
            return cur.rowcount > 0
        finally:
            con.close()


def clear() -> int:
    """Erase the whole history. Returns how many entries were removed."""
    with _lock:
        con = _connect()
        try:
            n = int(con.execute("SELECT COUNT(*) FROM entries").fetchone()[0])
            con.execute("DELETE FROM entries")
            con.commit()
            con.execute("VACUUM")             # really drop the old text from the file
            return n
        finally:
            con.close()


def purge_expired(now: float | None = None) -> int:
    days = retention_days()
    if days <= 0:
        return 0
    cutoff = (now if now is not None else time.time()) - days * 86400
    with _lock:
        con = _connect()
        try:
            cur = con.execute("DELETE FROM entries WHERE ts < ?", (cutoff,))
            con.commit()
            return cur.rowcount
        finally:
            con.close()


def _maybe_purge() -> None:
    """At most once an hour, while dictating."""
    global _last_purge
    if time.time() - _last_purge < 3600:
        return
    _last_purge = time.time()
    try:
        purge_expired()
    except Exception:
        pass
