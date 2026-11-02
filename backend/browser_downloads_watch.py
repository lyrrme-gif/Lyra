"""Chromium download-history watcher for Lyra's Dynamic Island.

Filesystem watching catches most downloads, but Chromium browsers can place a
partial file outside the normal Downloads folder. This watcher reads the
browser's History database from a temporary copy (never the live DB) and
surfaces active/new downloads.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

Publisher = Callable[[dict], None]
_SIGS: dict = {}        # history file -> (signature, when it was last read)
_FORCE_READ_S = 30.0    # read anyway this often, in case a change never shows in the file times


def _history_changed(src) -> bool:
    """True when the browser's History (or its WAL) changed since the last read. Copying that whole database every
    second or two just to learn that nothing happened was the cost; a file-time check costs nothing."""
    try:
        sig = []
        for p in (src, src.with_name(src.name + "-wal")):
            try:
                st = p.stat()
                sig.append((st.st_mtime_ns, st.st_size))
            except OSError:
                sig.append(None)
        sig = tuple(sig)
        now = time.monotonic()
        old = _SIGS.get(str(src))
        if old is not None and old[0] == sig and now - old[1] < _FORCE_READ_S:
            return False
        _SIGS[str(src)] = (sig, now)
        return True
    except Exception:                                    # noqa: BLE001 - when unsure, read it
        return True


_INTERVAL = 0.8
_IDLE_INTERVAL = 2.5   # light mode: nothing downloading -> copy and read the browser history less often


def _light() -> bool:
    try:
        import settings_store
        return settings_store.feature_on("light_mode")
    except Exception:
        return False
_HEARTBEAT = 1.5
_stop = threading.Event()
_thread: threading.Thread | None = None


def _running_user_data_dirs() -> list[tuple[str, Path]]:
    out=[]
    try:
        import psutil,re
        names={"brave.exe":"Brave","chrome.exe":"Chrome","msedge.exe":"Edge","vivaldi.exe":"Vivaldi","opera.exe":"Opera"}
        for p in psutil.process_iter(["name","cmdline"]):
            name=names.get((p.info.get("name") or "").lower())
            if not name: continue
            cmd=" ".join(p.info.get("cmdline") or [])
            m=re.search(r"--user-data-dir=(?:\"([^\"]+)\"|(\S+))",cmd,re.I)
            if m:
                out.append((name,Path(m.group(1) or m.group(2))))
    except Exception: pass
    return out

def _profiles() -> list[tuple[str, Path]]:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    roaming = Path(os.environ.get("APPDATA", ""))
    roots = _running_user_data_dirs() + [
        ("Chrome", local / "Google" / "Chrome" / "User Data"),
        ("Edge", local / "Microsoft" / "Edge" / "User Data"),
        ("Brave", local / "BraveSoftware" / "Brave-Browser" / "User Data"),
        ("Vivaldi", local / "Vivaldi" / "User Data"),
        ("Opera", roaming / "Opera Software" / "Opera Stable"),
    ]
    out: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for browser, root in roots:
        if not root.exists():
            continue
        try:
            # Chromium keeps one History database per profile. Recursive discovery
            # also catches Profile 1/2, Guest and browser builds that move profile
            # folders around.
            for db in root.glob("*/History"):
                if db.is_file() and str(db) not in seen:
                    seen.add(str(db)); out.append((browser, db))
            if (root / "History").is_file() and str(root / "History") not in seen:
                seen.add(str(root / "History")); out.append((browser, root / "History"))
        except OSError:
            pass
    return out


def _copy_db(src: Path) -> Path | None:
    """Create a consistent snapshot of a live Chromium History database.

    Chromium normally keeps the newest rows in a WAL while the browser is
    running. SQLite's backup API understands that WAL, unlike a plain
    ``shutil.copy2(History)``. A sidecar copy remains as a fallback.
    """
    folder = Path(tempfile.mkdtemp(prefix="lyra-history-"))
    dst = folder / "History"
    con = None
    out = None
    try:
        con = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=0.8)
        out = sqlite3.connect(str(dst))
        con.backup(out)
        out.close()
        out = None
        con.close()
        con = None
        return dst
    except Exception:
        try:
            if out:
                out.close()
        except Exception:
            pass
        try:
            if con:
                con.close()
        except Exception:
            pass
        # Fallback for profiles where SQLite refuses a live read snapshot.
        try:
            shutil.copy2(src, dst)
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(src) + suffix)
                if sidecar.exists():
                    try:
                        shutil.copy2(sidecar, folder / ("History" + suffix))
                    except OSError:
                        pass
            return dst
        except OSError:
            shutil.rmtree(folder, ignore_errors=True)
            return None


def _cleanup_db(db: Path) -> None:
    try:
        folder = db.parent
        shutil.rmtree(folder, ignore_errors=True)
    except OSError:
        try:
            db.unlink(missing_ok=True)
        except OSError:
            pass

def _rows(db: Path) -> list[tuple]:
    con = None
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=0.2)
        cols = {row[1] for row in con.execute("PRAGMA table_info(downloads)").fetchall()}
        # Chromium versions differ slightly. Some expose a direct `url` column,
        # others only expose tab_url/referrer. Build the query from columns that
        # actually exist so the watcher never dies just because one browser build
        # lacks `url`.
        url_expr = "COALESCE(url,'')" if "url" in cols else "''"
        # The real file URL lives in downloads_url_chains (last hop), not in `downloads`.
        has_chain = bool(con.execute("PRAGMA table_info(downloads_url_chains)").fetchall())
        if has_chain:
            url_expr = ("COALESCE((SELECT url FROM downloads_url_chains c WHERE c.id = downloads.id "
                        "ORDER BY chain_index DESC LIMIT 1), " + url_expr + ")")
        return con.execute(
            "SELECT id, guid, COALESCE(NULLIF(target_path,''),NULLIF(current_path,''),''), "
            "state, COALESCE(received_bytes,0), COALESCE(total_bytes,0), "
            "start_time, end_time, COALESCE(tab_url,''), " + url_expr + " FROM downloads "
            "ORDER BY id DESC LIMIT 80"
        ).fetchall()
    except Exception:
        return []
    finally:
        if con:
            con.close()


def _downloads_dir() -> Path:
    """Resolve the Windows Downloads folder for active Chromium partial files."""
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates: list[Path] = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            raw, _ = winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")
            if raw:
                candidates.append(Path(os.path.expandvars(str(raw))))
    except Exception:
        pass
    candidates += [
        Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Downloads",
        Path(os.environ.get("USERPROFILE", str(Path.home()))) / "OneDrive" / "Downloads",
        Path.home() / "Downloads",
    ]
    for folder in candidates:
        if folder.exists():
            return folder
    return candidates[0] if candidates else Path.home() / "Downloads"


def _is_placeholder(name: str, guid: str = "") -> bool:
    n = (name or "").strip()
    if not n:
        return True
    low = n.lower()
    if low.startswith("unconfirmed "):
        return True
    if guid and n == guid:
        return True
    return False


def _strip_partial_suffix(name: str) -> str:
    for suffix in (".crdownload", ".part", ".tmp", ".download"):
        if name.lower().endswith(suffix):
            return name[:-len(suffix)]
    return name


def _partial_name_for_size(received: int) -> str:
    """Find the most likely real Chromium temporary filename.

    Chromium can expose an ``Unconfirmed <id>`` target in History while the
    actual file in Downloads already has its final filename. The DB snapshot
    and filesystem are not atomic, so an exact byte-size match is too strict.
    Prefer a close match, then a recently modified partial file.
    """
    folder = _downloads_dir()
    candidates: list[tuple[float, int, str]] = []
    now_ns = time.time_ns()
    try:
        for p in folder.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in {".crdownload", ".part", ".tmp", ".download"}:
                continue
            if _is_placeholder(_strip_partial_suffix(p.name)):
                continue
            try:
                st = p.stat()
                age_s = max(0.0, (now_ns - st.st_mtime_ns) / 1_000_000_000)
                # Ignore stale partials; an active Chromium download should be
                # changing recently. Score size first, recency second.
                if age_s > 180:
                    continue
                size_delta = abs(int(st.st_size) - int(received)) if received > 0 else 0
                candidates.append((float(size_delta), st.st_mtime_ns, p.name))
            except OSError:
                continue
    except OSError:
        pass
    if not candidates:
        return ""
    if received > 0:
        # If there are several active downloads, the closest byte count is the
        # strongest signal. A 256 MB mismatch is still preferable to displaying
        # Chromium's meaningless Unconfirmed id when only one active partial exists.
        candidates.sort(key=lambda x: (x[0], -x[1]))
    else:
        candidates.sort(key=lambda x: -x[1])
    return _strip_partial_suffix(candidates[0][2])


def _url_name(tab_url: str) -> str:
    """Best-effort filename from a direct download URL when History is placeholder-only."""
    try:
        path = unquote(urlparse(tab_url or "").path or "")
        name = Path(path).name
        if name and not _is_placeholder(name):
            return name
    except Exception:
        pass
    return ""


def _name(path: str, guid: str, received: int = 0, tab_url: str = "") -> str:
    p = Path(path)
    name = p.name
    if _is_placeholder(name, guid):
        real = _partial_name_for_size(received)
        if real:
            return real
        real = _url_name(tab_url)
        if real:
            return real
        return ""
    return _strip_partial_suffix(name)


# Active downloads whose real file name is known: key -> (name, total, received, seen_at).
_ACTIVE: dict = {}


def resolve_active(size: int):
    """Real (name, total_bytes) for the in-progress download whose size is closest to `size`.
    Used by the Downloads-folder watcher, which only sees Chromium's temporary 'Unconfirmed N' name."""
    now = time.monotonic()
    live = [v for v in _ACTIVE.values() if now - v[3] < 10]
    if not live:
        return None
    best = min(live, key=lambda v: abs(v[2] - size))
    if len(live) > 1 and abs(best[2] - size) > max(64 * 1024 * 1024, size * 0.5):
        return None  # ambiguous: several downloads, none clearly matching
    return best[0], best[1]


def _fmt_size(n: float) -> str:
    n = float(max(0, n))
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else (f"{n:.1f} {unit}" if n < 100 else f"{n:.0f} {unit}")
        n /= 1024
    return f"{n:.1f} GB"


def _fmt_eta(sec: float) -> str:
    sec = int(max(0, sec))
    if sec < 60:
        return f"{sec}s left"
    if sec < 3600:
        return f"{sec // 60}m {sec % 60:02d}s left"
    return f"{sec // 3600}h {(sec % 3600) // 60:02d}m left"


def start(publisher: Publisher) -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    # Track the latest observed state. We intentionally inspect active downloads
    # immediately, even if they began before Lyra started.
    seen: dict[tuple[str, str], tuple] = {}

    light = _light()

    def _wait_s() -> float:
        if not light or _ACTIVE or seen:
            return _INTERVAL
        try:
            import downloads_watch as _dw
            if time.monotonic() - _dw.LAST_PROGRESS_TS < 5.0:      # the Downloads folder sees one right now
                return _INTERVAL
        except Exception:
            return _INTERVAL
        return _IDLE_INTERVAL

    def run() -> None:
        nonlocal seen
        while not _stop.wait(_wait_s()):
            for browser, src in _profiles():
                if light and not seen and not _ACTIVE and not _history_changed(src):
                    continue                              # the browser wrote nothing since the last look: no copy, no read
                db = _copy_db(src)
                if not db:
                    continue
                try:
                    rows_now = _rows(db)
                    for did, guid, path, state, received, total, _start, _end, tab_url, download_url in rows_now:
                        key = (browser, str(guid or did))
                        now = time.monotonic()
                        title = _name(str(path), str(guid or did), int(received), str(download_url or tab_url or ""))[:80]
                        old = seen.get(key)
                        # Chromium: state 0 = in progress, 1 = complete, 2 = interrupted.
                        if int(state) == 0:
                            # The browser DB is authoritative for an active download.
                            # This catches redirected folders and Brave downloads whose
                            # temporary file is not named .crdownload/.part.
                            speed = old[4] if old is not None and len(old) > 4 else 0.0
                            if title:
                                _ACTIVE[key] = (title, int(total), int(received), now)
                            # The Downloads-folder watcher has live byte counts: when it is
                            # reporting this download, only keep the name/total from here.
                            try:
                                import downloads_watch as _dw
                                fs_live = time.monotonic() - _dw.LAST_PROGRESS_TS < 3.0
                            except Exception:
                                fs_live = False
                            if title and not fs_live and (old is None or received != old[0] or now - old[2] >= 1.5):
                                # Smoothed speed from the previous sample of the same download.
                                if old is not None and now > old[2] and received >= old[0]:
                                    inst = (received - old[0]) / (now - old[2])
                                    speed = inst if not speed else speed * 0.6 + inst * 0.4
                                parts = []
                                if total:
                                    parts.append(f"{_fmt_size(received)} of {_fmt_size(total)}")
                                else:
                                    parts.append(_fmt_size(received))
                                if speed > 1024:
                                    parts.append(f"{_fmt_size(speed)}/s")
                                    if total and total > received:
                                        parts.append(_fmt_eta((total - received) / speed))
                                subtitle = " · ".join(parts)
                                publisher({
                                    "type": "activity", "kind": "download", "phase": "start",
                                    "title": title, "subtitle": subtitle,
                                    "progress": (round(received * 100 / total, 1) if total else None),
                                    "duration_ms": 5000, "ts": int(time.time() * 1000),
                                })
                            seen[key] = (int(received), int(total), now, title, speed if int(state) == 0 else 0.0)
                        elif old is not None and old[0] >= 0:
                            final_title = title or (old[3] if len(old) > 3 else "Download") or "Download"
                            if int(state) == 1:
                                publisher({
                                    "type": "activity", "kind": "download", "phase": "success",
                                    "title": final_title,
                                    "subtitle": "Saved to Downloads" + (f" · {_fmt_size(total or received)}" if (total or received) else ""),
                                    "progress": 100,
                                    "duration_ms": 3000, "ts": int(time.time() * 1000),
                                })
                            else:
                                publisher({
                                    "type": "activity", "kind": "download", "phase": "fail",
                                    "title": final_title,
                                    "subtitle": "Download interrupted" + (f" at {int(old[0] * 100 / old[1])}%" if old and len(old) > 1 and old[1] else ""),
                                    "duration_ms": 3000, "ts": int(time.time() * 1000),
                                })
                            seen.pop(key, None)
                            _ACTIVE.pop(key, None)
                    # A download that was in progress and has vanished from History (cancelled
                    # and removed) will never report a final state: end it explicitly.
                    present = {(browser, str(r[1] or r[0])) for r in rows_now}
                    for k in [k for k in list(seen) if k[0] == browser and k not in present]:
                        v = seen[k]
                        if time.monotonic() - v[2] > 5:
                            publisher({
                                "type": "activity", "kind": "download", "phase": "fail",
                                "title": (v[3] if len(v) > 3 else "") or "Download",
                                "subtitle": "Download canceled",
                                "duration_ms": 1500, "ts": int(time.time() * 1000),
                            })
                            seen.pop(k, None)
                            _ACTIVE.pop(k, None)
                finally:
                    _cleanup_db(db)
            # Keep memory bounded to recent browser entries.
            if len(seen) > 300:
                seen = dict(list(seen.items())[-150:])

    _thread = threading.Thread(target=run, name="lyra-browser-downloads", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
