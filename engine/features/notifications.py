"""Lyra — incoming notifications for the island's compact notch.

Watches the Windows notification database for new toasts from Discord, WhatsApp
and Telegram (messages and calls) and for reminders, and hands each one to the
UI bridge, which sends it to the island as a {"type": "notify"} event.

How it reads them: Windows keeps every delivered toast in
    %LOCALAPPDATA%\\Microsoft\\Windows\\Notifications\\wpndatabase.db
That file needs no permission prompt and no extra package, but Windows holds it
open, so each poll reads a private copy (db + -wal + -shm) and only when the
files changed. Nothing is ever written back.

What it needs from you: the app must still be allowed to show notifications in
Windows Settings > System > Notifications. If Windows never shows the toast, it
is never recorded, so Lyra never sees it either.

Debug on your machine (prints the last toasts Windows has stored, and what Lyra
makes of them):
    python notifications.py --dump
Turn the watcher off without touching code:  set LYRA_NOTIFY=0
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

log = logging.getLogger("lyra")

POLL_SECONDS = 1.0
PREVIEW_MAX = 140          # characters kept of the message body
DEDUPE_SECONDS = 3.0       # the same toast seen twice inside this window is shown once

# Package / app-id fragments (lower-case) → app key used by the island.
_CHAT_APPS = {
    "discord": "discord",
    "whatsapp": "whatsapp",
    "telegram": "telegram",
}
# Apps whose toasts are reminders. Calendar-style toasts from any other app are
# caught by the toast's own scenario="reminder" / "alarm" attribute below.
_REMINDER_APPS = ("windows.alarms", "microsoft.todos", "todos")

_CALL_RE = re.compile(
    r"\b(is\s+calling|incoming\s+(?:voice\s+|video\s+)?call|missed\s+(?:voice\s+|video\s+)?call|"
    r"calling\s+you|appel\s+entrant|appel\s+manqu)", re.IGNORECASE,
)

Emit = Callable[[str, str, str, str], None]   # (app, kind, sender, text)

_QUERY = (
    "SELECT n.rowid, h.PrimaryId, n.Payload FROM Notification n "
    "LEFT JOIN NotificationHandler h ON h.RecordId = n.HandlerId "
    "WHERE n.Type = 'toast' AND n.rowid > ? ORDER BY n.rowid"
)


def _db_path() -> Path | None:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    return Path(base) / "Microsoft" / "Windows" / "Notifications" / "wpndatabase.db"


def _decode(payload) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    return bytes(payload).decode("utf-8-sig", errors="replace").strip("\x00")


def parse_toast(app_id: str | None, payload) -> tuple[str, str, str, str] | None:
    """Turn one stored toast into (app, kind, sender, text), or None to ignore it.

    Only Discord / WhatsApp / Telegram toasts and reminders are kept — everything
    else Windows shows (mail, updates, other apps) stays out of the notch.
    """
    xml = _decode(payload)
    if not xml:
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None

    texts: list[str] = []
    for node in root.iter("text"):
        if (node.get("placement") or "").lower() == "attribution":
            continue
        value = " ".join((node.text or "").split())
        if value:
            texts.append(value)
    if not texts:
        return None
    sender, body = texts[0], " ".join(texts[1:])
    scenario = (root.get("scenario") or "").lower()
    ident = (app_id or "").lower()

    app = next((key for frag, key in _CHAT_APPS.items() if frag in ident), None)
    if app:
        is_call = scenario == "incomingcall" or bool(_CALL_RE.search(f"{sender} {body}"))
        return app, "call" if is_call else "message", sender, body[:PREVIEW_MAX]

    if scenario in ("reminder", "alarm") or any(frag in ident for frag in _REMINDER_APPS):
        return "reminder", "reminder", sender, body[:PREVIEW_MAX]
    return None


def _snapshot(db: Path, tmp: Path) -> Path | None:
    """Copy the database (and its WAL files) somewhere Windows isn't holding open."""
    target = tmp / db.name
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(db) + suffix)
        dst = Path(str(target) + suffix)
        if src.exists():
            shutil.copyfile(src, dst)
        elif dst.exists():
            dst.unlink()
    return target if target.exists() else None


def _read_new(db: Path, tmp: Path, after: int) -> tuple[list[tuple[int, str | None, object]], int]:
    """Rows newer than `after`, plus the highest rowid in the table."""
    snap = _snapshot(db, tmp)
    if snap is None:
        return [], after
    con = sqlite3.connect(f"file:{snap.as_posix()}?mode=ro", uri=True)
    try:
        top = con.execute("SELECT COALESCE(MAX(rowid), 0) FROM Notification").fetchone()[0]
        rows = con.execute(_QUERY, (after,)).fetchall()
    finally:
        con.close()
    return rows, int(top)


class Watcher(threading.Thread):
    def __init__(self, emit: Emit, db: Path | None = None):
        super().__init__(name="lyra-notifications", daemon=True)
        self._emit = emit
        self._db = db or _db_path()
        self._stop_event = threading.Event()
        self._recent: dict[tuple, float] = {}
        self._warned: set[str] = set()

    def stop(self) -> None:
        self._stop_event.set()

    def _warn_once(self, msg: str) -> None:
        if msg not in self._warned:
            self._warned.add(msg)
            log.warning("notifications: %s", msg)

    def _fresh(self, key: tuple) -> bool:
        now = time.monotonic()
        self._recent = {k: t for k, t in self._recent.items() if now - t < DEDUPE_SECONDS}
        if key in self._recent:
            return False
        self._recent[key] = now
        return True

    def run(self) -> None:
        if self._db is None or not self._db.exists():
            log.warning("notifications: Windows notification database not found (%s) — watcher off", self._db)
            return
        tmp = Path(tempfile.mkdtemp(prefix="lyra-wpn-"))
        last_seen = -1          # -1 = baseline not taken yet: old toasts must not replay
        last_sig = None
        try:
            while not self._stop_event.is_set():
                try:
                    sig = tuple(
                        (p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None
                        for p in (self._db, Path(str(self._db) + "-wal"))
                    )
                    if sig != last_sig:
                        last_sig = sig
                        rows, top = _read_new(self._db, tmp, max(last_seen, 0))
                        if last_seen < 0:
                            last_seen = top            # first read = baseline only
                        else:
                            if top < last_seen:        # database was reset by Windows
                                last_seen = 0
                                rows, top = _read_new(self._db, tmp, 0)
                            for rowid, app_id, payload in rows:
                                last_seen = max(last_seen, int(rowid))
                                self._on_toast(app_id, payload)
                except Exception as e:  # never let the watcher die
                    self._warn_once(f"read failed: {e!r}")
                self._stop_event.wait(POLL_SECONDS)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _on_toast(self, app_id, payload) -> None:
        parsed = parse_toast(app_id, payload)
        if not parsed:
            return
        app, kind, sender, text = parsed
        if not self._fresh((app, kind, sender, text)):
            return
        log.info("notification: %s/%s from %r", app, kind, sender)
        self._emit(app, kind, sender, text)


def _in_quiet_hours(start: str, end: str, now=None) -> bool:
    """True if the local time is inside [start, end) — the window may wrap past midnight."""
    from datetime import datetime
    def mins(t: str) -> int:
        h, _, m = t.partition(":")
        return int(h) * 60 + int(m)
    try:
        a, b = mins(start), mins(end)
    except ValueError:
        return False
    n = now or datetime.now()
    cur = n.hour * 60 + n.minute
    if a == b:
        return False
    return a <= cur < b if a < b else (cur >= a or cur < b)


def should_show(app: str, kind: str) -> bool:
    """Settings filter applied to every notification before it reaches the island:
    per-app switches, calls-only for chat apps, and quiet hours."""
    try:
        import engine.core.settings_store as st
    except Exception:
        return True                      # never lose a notification because settings broke
    per_app = {
        "discord": "notify_discord", "whatsapp": "notify_whatsapp",
        "telegram": "notify_telegram", "reminder": "notify_reminders",
    }
    key = per_app.get(app)
    if key and not st.get(key):
        return False
    if st.get("notify_calls_only") and app in _CHAT_APPS.values() and kind != "call":
        return False
    if st.get("notify_quiet_enabled") and _in_quiet_hours(
        str(st.get("notify_quiet_start")), str(st.get("notify_quiet_end"))
    ):
        return False
    return True


def recent(limit: int = 15) -> list[dict]:
    """Newest-first chat / reminder toasts Windows has stored (read-only snapshot).

    Used when Lyra is asked "any notifications?" and her own in-memory history is empty
    because she was started after those toasts arrived.
    """
    db = _db_path()
    if db is None or not db.exists():
        return []
    tmp = Path(tempfile.mkdtemp(prefix="lyra-wpn-"))
    try:
        rows, _top = _read_new(db, tmp, 0)
    except Exception as e:
        log.warning("notifications: could not read stored history: %s", e)
        return []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out: list[dict] = []
    for _rowid, app_id, payload in reversed(rows):
        parsed = parse_toast(app_id, payload)
        if not parsed:
            continue
        app, kind, sender, text = parsed
        if not should_show(app, kind):
            continue
        out.append({"app": app, "kind": kind, "sender": sender, "text": text, "time": ""})
        if len(out) >= max(1, int(limit)):
            break
    return out


_watcher: Watcher | None = None


def start(emit: Emit) -> None:
    """Start the background watcher once. `emit(app, kind, sender, text)` is called per toast."""
    global _watcher
    if _watcher is not None:
        return
    if os.environ.get("LYRA_NOTIFY", "1") == "0":
        log.info("notifications: disabled (LYRA_NOTIFY=0)")
        return
    _watcher = Watcher(emit)
    _watcher.start()
    log.info("notifications: watching %s", _watcher._db)


def _dump() -> None:
    """Print the last stored toasts and how Lyra would classify each one."""
    db = _db_path()
    if db is None or not db.exists():
        print("wpndatabase.db not found:", db)
        return
    tmp = Path(tempfile.mkdtemp(prefix="lyra-wpn-"))
    try:
        rows, top = _read_new(db, tmp, 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"{len(rows)} toast(s) stored, newest rowid {top}\n")
    for rowid, app_id, payload in rows[-15:]:
        print(f"#{rowid}  app={app_id}")
        print("   lyra sees:", parse_toast(app_id, payload) or "(ignored)")


if __name__ == "__main__":
    import sys

    if "--dump" in sys.argv:
        _dump()
    else:
        print(__doc__)
