"""Lyra — persistent memory: remembered facts and path aliases.

The storage pattern (SQLite FTS5 virtual table, BM25-ranked search) is
adapted from Ari-VoiceCommand's memory_index.py (MIT licensed) — it's a
clean, dependency-free way to get ranked full-text search out of stdlib
sqlite3 alone. Deliberately much smaller than Ari's version though: no
conversation history, consolidation, or embeddings — just facts and path
aliases, which is what Lyra's roadmap actually called for.

Two kinds of rows share one FTS5 table (kind='fact' / kind='path'):
  - facts: arbitrary "key is value" pairs, injected into brain.py's system
    prompt every turn so Lyra actually knows them in conversation.
  - path aliases: "my drive folder" -> "D:\\Sync\\Drive", checked by
    actions/files.py's resolve_location() before the hardcoded
    COMMON_LOCATIONS dict, so "open my drive folder" works without any
    Google Drive API integration (that's still Phase 2 on the roadmap).
"""

import os
import sys
import sqlite3

import paths
import threading
from datetime import datetime


DB_PATH = str(paths.data_path("lyra_memory.db"))
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def _ensure_db() -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory USING fts5(kind, key, value, timestamp)"
        )


_ensure_db()


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------
def remember_fact(key: str, value: str) -> None:
    """Store or overwrite a fact, e.g. remember_fact('favorite color', 'blue')."""
    key = key.strip().lower()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM memory WHERE kind = 'fact' AND key = ?", (key,))
        conn.execute(
            "INSERT INTO memory(kind, key, value, timestamp) VALUES ('fact', ?, ?, ?)",
            (key, value.strip(), datetime.now().isoformat()),
        )


def forget_fact(key: str) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM memory WHERE kind = 'fact' AND key = ?", (key.strip().lower(),))
        return cur.rowcount > 0


def all_facts() -> list[tuple[str, str]]:
    """Every remembered fact as (key, value) pairs, oldest first — what
    brain.py folds into the system prompt so Lyra actually knows them."""
    with _lock, _connect() as conn:
        return conn.execute(
            "SELECT key, value FROM memory WHERE kind = 'fact' ORDER BY timestamp"
        ).fetchall()


def search_facts(query: str, limit: int = 5) -> list[tuple[str, str, float]]:
    """BM25-ranked search — for once there are enough facts that dumping
    all of them into every prompt stops being the right call."""
    with _lock, _connect() as conn:
        try:
            return conn.execute(
                "SELECT key, value, bm25(memory) FROM memory "
                "WHERE memory MATCH ? AND kind = 'fact' ORDER BY bm25(memory) LIMIT ?",
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []  # malformed FTS5 query syntax (stray quote, etc.) — fail soft


# ---------------------------------------------------------------------------
# Path aliases
# ---------------------------------------------------------------------------
def remember_path(alias: str, path: str) -> None:
    """e.g. remember_path('drive folder', r'D:\\Sync\\Drive')"""
    alias = alias.strip().lower()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM memory WHERE kind = 'path' AND key = ?", (alias,))
        conn.execute(
            "INSERT INTO memory(kind, key, value, timestamp) VALUES ('path', ?, ?, ?)",
            (alias, path.strip(), datetime.now().isoformat()),
        )


def forget_path(alias: str) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM memory WHERE kind = 'path' AND key = ?", (alias.strip().lower(),))
        return cur.rowcount > 0


def get_path(alias: str) -> str | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT value FROM memory WHERE kind = 'path' AND key = ?", (alias.strip().lower(),)
        ).fetchone()
    return row[0] if row else None


def all_paths() -> list[tuple[str, str]]:
    with _lock, _connect() as conn:
        return conn.execute(
            "SELECT key, value FROM memory WHERE kind = 'path' ORDER BY timestamp"
        ).fetchall()


# ---------------------------------------------------------------------------
# Learned website targets
# ---------------------------------------------------------------------------
# When router.py's LLM pass confidently resolves something like "open
# notion" to a real URL (not in the hardcoded SEARCHABLE_SITES/PLAIN_SITES
# dicts in actions/apps.py), and opening it doesn't raise, we remember the
# mapping here. Next time it's an instant lookup — no LLM call needed.
#
# Deliberately scoped to websites only. Local desktop apps aren't safe to
# "learn" the same way: there's no way to verify an arbitrary executable
# name actually exists on this machine, and a wrong guess could try to
# launch something unintended. Those still need to be added by hand to
# LOCAL_APPS in actions/apps.py.
def remember_learned_site(name: str, url: str) -> None:
    name = name.strip().lower()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM memory WHERE kind = 'learned_site' AND key = ?", (name,))
        conn.execute(
            "INSERT INTO memory(kind, key, value, timestamp) VALUES ('learned_site', ?, ?, ?)",
            (name, url.strip(), datetime.now().isoformat()),
        )


def get_learned_site(name: str) -> str | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT value FROM memory WHERE kind = 'learned_site' AND key = ?", (name.strip().lower(),)
        ).fetchone()
    return row[0] if row else None


def all_learned_sites() -> list[tuple[str, str]]:
    with _lock, _connect() as conn:
        return conn.execute(
            "SELECT key, value FROM memory WHERE kind = 'learned_site' ORDER BY timestamp"
        ).fetchall()


# ---------------------------------------------------------------------------
# Browsing / clearing (Settings > Privacy & Memory)
# ---------------------------------------------------------------------------
KINDS = ("fact", "path", "learned_site")


def snapshot() -> dict:
    """Everything Lyra remembers, grouped for the Settings window."""
    return {
        "facts": [{"key": k, "value": v} for k, v in all_facts()],
        "paths": [{"key": k, "value": v} for k, v in all_paths()],
        "sites": [{"key": k, "value": v} for k, v in all_learned_sites()],
    }


def forget(kind: str, key: str) -> bool:
    """Delete one remembered item of `kind` (fact | path | learned_site)."""
    if kind not in KINDS:
        return False
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM memory WHERE kind = ? AND key = ?", (kind, key.strip().lower()))
        return cur.rowcount > 0


def clear(kind: str | None = None) -> int:
    """Delete every item of one kind, or everything when `kind` is None. Returns the count."""
    if kind is not None and kind not in KINDS:
        return 0
    with _lock, _connect() as conn:
        if kind is None:
            cur = conn.execute("DELETE FROM memory")
        else:
            cur = conn.execute("DELETE FROM memory WHERE kind = ?", (kind,))
        return max(cur.rowcount, 0)
