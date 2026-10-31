"""Lyra — search your own files.

"Find my notes about Fourier transform", "search my files for invoice 2026",
"open the first one", "answer from my files: when is the rent due?".

* Only folders the user chose are indexed (setting ``index_folders``; when it was never set:
  Documents + Desktop).  ``index_exclude`` lists names / paths / wildcards to skip.
* Reads txt, md, rst, pdf (pypdf), docx (python-docx) and, if python-pptx is installed, pptx.
* Text is cut into chunks and stored in SQLite FTS5 (``file_index.db`` in the data folder,
  i.e. %APPDATA%\\Lyra in the installed app).
* Incremental: a file is read again only when its modified time or size changed; files that
  disappeared (or were excluded / un-chosen) are dropped.  Runs in one low-priority thread.
* Never indexes llm_keys*, notes.json, lyra_memory.db, settings.json, contacts.json, Lyra's own
  data folder, hidden / dependency folders (.git, node_modules, venv, ...).

Public surface:
  start()                      background indexer (behind feature "file_search")
  try_file_search(command)     voice / typed commands -> reply text or None
  http(method, path, msg)      the /files/... endpoints used by Settings -> (status, dict)
"""
from __future__ import annotations

import fnmatch
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import ui
    log = ui.log
except Exception:  # noqa: BLE001 - lets the self-test import this module on its own
    import logging
    log = logging.getLogger("lyra.file_index")

# ── limits ───────────────────────────────────────────────────────────────────────────────────
TEXT_EXTS = {".txt", ".md", ".markdown", ".rst"}
SUPPORTED = TEXT_EXTS | {".pdf", ".docx", ".pptx"}
MAX_FILE_BYTES = 40 * 1024 * 1024
MAX_TEXT_CHARS = 1_500_000
PDF_MAX_PAGES = 400
CHUNK_CHARS = 1000
CHUNK_OVERLAP = 150
START_DELAY_S = 25            # let Lyra finish starting before the first scan
RESCAN_S = 30 * 60
ACTIVITY_MIN_FILES = 8        # background rescans only show the island activity above this
OPEN_TTL_S = 15 * 60

NEVER_NAMES = {"notes.json", "lyra_memory.db", "settings.json", "contacts.json", "app_profiles.json"}
SKIP_DIRS = {"node_modules", "venv", ".venv", "env", "__pycache__", "site-packages", "target", "dist",
             "build", "appdata", "$recycle.bin", "system volume information", "windows", "program files",
             "program files (x86)", "programdata"}

_DDL = [
    "CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL, size INTEGER, chunks INTEGER,"
    " status TEXT, indexed_at REAL)",
    "CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY, path TEXT NOT NULL, idx INTEGER, text TEXT)",
    "CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path)",
    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id',"
    " tokenize='unicode61 remove_diacritics 2')",
    "CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN"
    " INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text); END",
    "CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN"
    " INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text); END",
]

_init_lock = threading.Lock()
_scan_lock = threading.Lock()
_wake = threading.Event()
_stop = threading.Event()
_started = False
_force_show = False
_state: dict = {"phase": "idle", "total": 0, "done": 0, "current": "", "last": None, "last_at": 0.0}
_last_files: list[dict] = []
_last_files_at = 0.0


class _Unavailable(Exception):
    """An optional reader library is not installed: skip the file and try again on the next scan."""


# ── settings ─────────────────────────────────────────────────────────────────────────────────
def _setting(key, default=None):
    try:
        import settings_store
        v = settings_store.get(key)
        return default if v is None else v
    except Exception:  # noqa: BLE001
        return default


def _set_settings(updates: dict) -> None:
    import settings_store
    settings_store.set_many(updates)


def _feature_on() -> bool:
    try:
        import settings_store
        return settings_store.feature_on("file_search")
    except Exception:  # noqa: BLE001
        return True


def default_folders() -> list[str]:
    home = Path.home()
    cands = [home / "Documents", home / "Desktop"]
    for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        od = os.environ.get(env)
        if od:
            cands += [Path(od) / "Documents", Path(od) / "Desktop"]
    out: list[str] = []
    for c in cands:
        if c.is_dir() and str(c) not in out:
            out.append(str(c))
    return out


def folders() -> list[str]:
    raw = _setting("index_folders")
    lst = default_folders() if not isinstance(raw, list) else [str(x).strip() for x in raw if str(x).strip()]
    out: list[str] = []
    for f in lst:
        n = os.path.normpath(f)
        if n not in out:
            out.append(n)
    return out


def excludes() -> list[str]:
    raw = _setting("index_exclude")
    return [str(x).strip() for x in raw if str(x).strip()] if isinstance(raw, list) else []


# ── database ─────────────────────────────────────────────────────────────────────────────────
def _db_path() -> Path:
    from paths import data_path
    return data_path("file_index.db")


def _connect() -> sqlite3.Connection:
    p = _db_path()
    with _init_lock:
        fresh = not p.exists()
        conn = sqlite3.connect(str(p), timeout=30)
        if fresh or not _has_tables(conn):
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                for stmt in _DDL:
                    conn.execute(stmt)
                conn.commit()
            except sqlite3.OperationalError as e:
                conn.close()
                raise RuntimeError(f"this Python's SQLite has no FTS5 support ({e})") from e
    conn.row_factory = sqlite3.Row
    return conn


def _has_tables(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name='chunks_fts'").fetchone() is not None


# ── exclusion rules ──────────────────────────────────────────────────────────────────────────
def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def _under(path: str, root: str) -> bool:
    p, r = _norm(path), _norm(root)
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def _never(name: str) -> bool:
    low = name.lower()
    return low in NEVER_NAMES or low.startswith("llm_keys") or low.startswith("~$")


def _user_excluded(path: str, name: str, patterns: list[str]) -> bool:
    lp, ln = path.lower(), name.lower()
    for pat in patterns:
        p = pat.lower()
        if any(c in p for c in "*?["):
            if fnmatch.fnmatch(ln, p) or fnmatch.fnmatch(lp, p):
                return True
        elif p in lp:
            return True
    return False


def _skip_dir(full: str, name: str, patterns: list[str], data_root: str) -> bool:
    low = name.lower()
    if low in SKIP_DIRS or low.startswith("."):
        return True
    if data_root and _under(full, data_root):
        return True
    return _user_excluded(full, name, patterns)


def _iter_files(root: str, patterns: list[str]):
    try:
        from paths import data_dir
        data_root = str(data_dir())
    except Exception:  # noqa: BLE001
        data_root = ""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if _stop.is_set():
            return
        dirnames[:] = [d for d in dirnames if not _skip_dir(os.path.join(dirpath, d), d, patterns, data_root)]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() not in SUPPORTED or _never(fn):
                continue
            full = os.path.join(dirpath, fn)
            if _user_excluded(full, fn, patterns):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            if 0 < st.st_size <= MAX_FILE_BYTES:
                yield os.path.normpath(full), st


# ── reading files ────────────────────────────────────────────────────────────────────────────
def _read_text_file(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read(MAX_TEXT_CHARS * 4)
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)[:MAX_TEXT_CHARS]
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")[:MAX_TEXT_CHARS]


def _read_pdf(path: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise _Unavailable("pypdf") from e
    reader = PdfReader(path)
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                raise ValueError("password-protected")
        except Exception as e:  # noqa: BLE001
            raise ValueError("password-protected") from e
    parts: list[str] = []
    total = 0
    for i, page in enumerate(reader.pages):
        if i >= PDF_MAX_PAGES or total > MAX_TEXT_CHARS:
            break
        try:
            t = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - one bad page must not lose the rest
            t = ""
        parts.append(t)
        total += len(t)
    return "\n\n".join(parts)


def _read_docx(path: str) -> str:
    try:
        import docx
    except ImportError as e:
        raise _Unavailable("python-docx") from e
    d = docx.Document(path)
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)[:MAX_TEXT_CHARS]


def _read_pptx(path: str) -> str:
    try:
        from pptx import Presentation
    except ImportError as e:
        raise _Unavailable("python-pptx") from e
    prs = Presentation(path)
    parts: list[str] = []
    for n, slide in enumerate(prs.slides, 1):
        parts.append(f"Slide {n}")
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
                parts.append(shape.text_frame.text)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    parts.append(" | ".join(c.text for c in row.cells if c.text.strip()))
    return "\n".join(parts)[:MAX_TEXT_CHARS]


def extract_text(path: str) -> tuple[str, str]:
    """(text, status).  status: ok | empty | error: <why>.  Raises _Unavailable for a missing library."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in TEXT_EXTS:
            text = _read_text_file(path)
        elif ext == ".pdf":
            text = _read_pdf(path)
        elif ext == ".docx":
            text = _read_docx(path)
        elif ext == ".pptx":
            text = _read_pptx(path)
        else:
            return "", "error: unsupported"
    except _Unavailable:
        raise
    except Exception as e:  # noqa: BLE001 - a corrupt file is recorded, never fatal
        return "", f"error: {str(e)[:80]}"
    text = text.strip()
    return (text, "ok") if text else ("", "empty")


def chunk_text(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    out: list[str] = []
    n, i = len(text), 0
    half = CHUNK_CHARS // 2
    while i < n:
        end = min(n, i + CHUNK_CHARS)
        if end < n:
            window = text[i + half:end]
            cut = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("\n"))
            if cut >= 0:
                end = i + half + cut + 1
        piece = text[i:end].strip()
        if piece:
            out.append(piece)
        if end >= n:
            break
        i = max(end - CHUNK_OVERLAP, i + 1)
    return out


def _name_text(path: str) -> str:
    return re.sub(r"[_\-.]+", " ", Path(path).stem) + " " + Path(path).name


def _index_one(conn: sqlite3.Connection, path: str, st: os.stat_result) -> str:
    text, status = extract_text(path)           # may raise _Unavailable: nothing is written
    pieces = chunk_text(text) if text else []
    rows = [(path, -1, _name_text(path))] + [(path, i, c) for i, c in enumerate(pieces)]
    with conn:
        conn.execute("DELETE FROM chunks WHERE path=?", (path,))
        conn.executemany("INSERT INTO chunks(path, idx, text) VALUES (?,?,?)", rows)
        conn.execute("INSERT OR REPLACE INTO files(path, mtime, size, chunks, status, indexed_at)"
                     " VALUES (?,?,?,?,?,?)", (path, st.st_mtime, st.st_size, len(pieces), status, time.time()))
    return status


def _drop(conn: sqlite3.Connection, paths: list[str]) -> None:
    with conn:
        for p in paths:
            conn.execute("DELETE FROM chunks WHERE path=?", (p,))
            conn.execute("DELETE FROM files WHERE path=?", (p,))


# ── scanning ─────────────────────────────────────────────────────────────────────────────────
def _activity(title: str, phase: str, subtitle: str = "", ms: int = 4000) -> None:
    try:
        import ui_bridge
        ui_bridge.push_activity("file_index", title, phase=phase, subtitle=subtitle or None, duration_ms=ms)
    except Exception:  # noqa: BLE001
        pass


def _lower_priority() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.GetCurrentThread.restype = ctypes.c_void_p
        k.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        k.SetThreadPriority(k.GetCurrentThread(), -2)  # THREAD_PRIORITY_LOWEST
    except Exception:  # noqa: BLE001
        pass


def scan(show: bool = False, throttle: bool = True) -> dict:
    """Bring the index in line with the chosen folders. Returns counts (see keys below)."""
    if not _scan_lock.acquire(blocking=False):
        return {"busy": True}
    t0 = time.time()
    stats = {"scanned": 0, "indexed": 0, "unchanged": 0, "removed": 0, "errors": 0, "unavailable": 0}
    try:
        roots = folders()
        patterns = excludes()
        conn = _connect()
        try:
            _state.update(phase="scanning", total=0, done=0, current="")
            known = {r["path"]: (r["mtime"], r["size"]) for r in conn.execute("SELECT path, mtime, size FROM files")}
            # 1. forget everything outside the chosen folders (a folder was removed in Settings)
            gone = [p for p in known if not any(_under(p, r) for r in roots)]
            _drop(conn, gone)
            stats["removed"] += len(gone)
            for p in gone:
                known.pop(p, None)
            # 2. walk
            seen: set[str] = set()
            pending: list[tuple[str, os.stat_result]] = []
            walked: list[str] = []
            for root in roots:
                if not os.path.isdir(root):
                    continue            # e.g. an unplugged drive: keep what we know about it
                walked.append(root)
                for path, st in _iter_files(root, patterns):
                    seen.add(path)
                    old = known.get(path)
                    if old and abs(old[0] - st.st_mtime) < 0.001 and old[1] == st.st_size:
                        stats["unchanged"] += 1
                    else:
                        pending.append((path, st))
            stats["scanned"] = len(seen)
            # 3. files that vanished or became excluded
            vanished = [p for p in known if any(_under(p, r) for r in walked) and p not in seen]
            if vanished and not _stop.is_set():
                _drop(conn, vanished)
                stats["removed"] += len(vanished)
            # 4. read the new / changed ones
            show = show or len(pending) >= ACTIVITY_MIN_FILES
            _state.update(phase="indexing", total=len(pending), done=0)
            last_push = 0.0
            if show and pending:
                _activity("Indexing your files…", "start", f"0 of {len(pending)}", 6000)
                last_push = time.time()
            for i, (path, st) in enumerate(pending, 1):
                if _stop.is_set() or (i % 25 == 0 and not _feature_on()):
                    break
                _state.update(done=i - 1, current=os.path.basename(path))
                t1 = time.time()
                try:
                    status = _index_one(conn, path, st)
                    stats["indexed"] += 1
                    if status.startswith("error"):
                        stats["errors"] += 1
                except _Unavailable:
                    stats["unavailable"] += 1
                except Exception as e:  # noqa: BLE001
                    stats["errors"] += 1
                    log.warning("file index: %s: %s", path, e)
                _state["done"] = i
                if show and time.time() - last_push > 2.0:
                    _activity("Indexing your files…", "start", f"{i} of {len(pending)}", 6000)
                    last_push = time.time()
                if throttle:
                    time.sleep(min(0.5, max(0.01, time.time() - t1)))   # about half a core at most
            if show and pending:
                _activity("Files indexed", "success", f"{stats['indexed']} updated", 3500)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        log.warning("file index scan failed: %s", e)
        stats["error"] = str(e)
    finally:
        stats["seconds"] = round(time.time() - t0, 2)
        _state.update(phase="idle", current="", last=stats, last_at=time.time())
        _scan_lock.release()
    log.info("file index scan: %s", stats)
    return stats


def _loop() -> None:
    global _force_show
    _lower_priority()
    _stop.wait(START_DELAY_S)
    while not _stop.is_set():
        if _feature_on():
            show, _force_show = _force_show, False
            scan(show=show)
        _wake.wait(RESCAN_S)
        _wake.clear()


def start() -> None:
    """Start the background indexer (call it behind settings_store.feature_on("file_search"))."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="lyra-file-index", daemon=True).start()


def request_reindex() -> bool:
    global _force_show
    if not _started:
        return False
    _force_show = True
    _wake.set()
    return True


def status() -> dict:
    n_files = n_chunks = 0
    try:
        conn = _connect()
        try:
            n_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    return {"ok": True, "enabled": _feature_on(), "running": _started, "phase": _state["phase"],
            "total": _state["total"], "done": _state["done"], "current": _state["current"],
            "files": n_files, "chunks": n_chunks, "last": _state["last"], "last_at": _state["last_at"]}


# ── searching ────────────────────────────────────────────────────────────────────────────────
_STOP_WORDS = set("""a an the of on in at to for from with about regarding mentioning containing related that which
me my mine our all any some find search look locate show open files file documents document docs doc notes note pdf
pdfs word presentation presentations slides slide please can could would you i is are was were what who when where
why how do does did say says tell tells it this those these there their them and or""".split())


def _tokens(text: str, keep_all: bool = False) -> list[str]:
    toks = re.findall(r"[^\W_]+", text.lower())
    kept = [t for t in toks if t not in _STOP_WORDS]
    return toks if keep_all or not kept else kept


def _fts(tokens: list[str], op: str) -> str:
    return f" {op} ".join(f'"{t}"*' if len(t) >= 3 else f'"{t}"' for t in tokens[:10])


def _run(conn, match: str, limit: int) -> list[sqlite3.Row]:
    try:
        return conn.execute(
            "SELECT c.path AS path, c.idx AS idx, c.text AS text,"
            " snippet(chunks_fts, 0, '', '', ' … ', 28) AS snip, bm25(chunks_fts) AS rank"
            " FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid"
            " WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?", (match, limit)).fetchall()
    except sqlite3.OperationalError as e:
        log.debug("file search query failed: %s", e)
        return []


def search_chunks(query: str, max_files: int = 8, per_file: int = 1, any_word: bool = False) -> list[dict]:
    """Best matching chunks, grouped by file (best file first). Files that no longer exist are dropped."""
    toks = _tokens(query)
    if not toks:
        return []
    conn = _connect()
    try:
        rows: list[sqlite3.Row] = []
        if not any_word:
            rows = _run(conn, _fts(toks, "AND"), 80)
        if any_word or len({r["path"] for r in rows}) < 3:
            seen = {(r["path"], r["idx"]) for r in rows}
            rows += [r for r in _run(conn, _fts(toks, "OR"), 120) if (r["path"], r["idx"]) not in seen]
        out: list[dict] = []
        per: dict[str, int] = {}
        missing: list[str] = []
        order: list[str] = []
        for r in rows:
            p = r["path"]
            if p in missing:
                continue
            if p not in per:
                if not os.path.exists(p):
                    missing.append(p)
                    continue
                if len(order) >= max_files:
                    continue
                order.append(p)
                per[p] = 0
            if per[p] >= per_file:
                continue
            per[p] += 1
            out.append({"path": p, "name": os.path.basename(p), "idx": r["idx"], "text": r["text"],
                        "snippet": re.sub(r"\s+", " ", r["snip"] or "").strip(), "rank": r["rank"]})
        if missing:
            _drop(conn, missing)
        return out
    finally:
        conn.close()


def _short_folder(path: str) -> str:
    parts = Path(path).parent.parts[-2:]
    return " › ".join(p.strip("\\/") for p in parts if p.strip("\\/"))


def search_files(query: str, limit: int = 4) -> list[dict]:
    """Island result rows (same shape as web results; ``url`` is the file path: clicking opens it)."""
    rows = []
    for h in search_chunks(query, max_files=limit):
        desc = h["snippet"] if h["idx"] >= 0 else "The file name matches."
        rows.append({"title": h["name"], "description": desc[:220], "url": h["path"],
                     "source": _short_folder(h["path"]), "provider": "files"})
    return rows


# ── commands ─────────────────────────────────────────────────────────────────────────────────
_KIND = (r"(?:files?|documents?|docs?|notes?|pdfs?|word\s+(?:docs?|documents?)|powerpoints?|presentations?|slides?)")
_FIND = [
    re.compile(r"^(?:find|search\s+for|look\s+for|locate|show\s+me|get\s+me)\s+(?:all\s+)?(?:(?:the|my)\s+)?" + _KIND +
               r"\s+(?:about|on|regarding|mentioning|containing|with|for|related\s+to|that\s+(?:mention|contain|talk\s+about))"
               r"\s+(?P<q>.+)$", re.I),
    re.compile(r"^(?:search|look)\s+(?:through\s+|in\s+)?(?:all\s+)?my\s+(?:files?|documents?|docs?|notes|pdfs?|folders?)"
               r"\s+for\s+(?P<q>.+)$", re.I),
    re.compile(r"^(?:search|find)\s+(?P<q>.+?)\s+in\s+my\s+(?:files?|documents?|docs?|notes|pdfs?|folders?)$", re.I),
]
_ASK = [
    re.compile(r"^(?:answer|reply)\s+(?:this\s+|that\s+|it\s+)?from\s+my\s+(?:files?|documents?|docs?|notes)\s*[:,\-]?\s*(?P<q>.+)$", re.I),
    re.compile(r"^(?:from|according\s+to|based\s+on|using)\s+my\s+(?:files?|documents?|docs?|notes)\s*[:,\-]?\s*(?P<q>.+)$", re.I),
    re.compile(r"^ask\s+my\s+(?:files?|documents?|docs?|notes)\s*[:,\-]?\s*(?:about\s+)?(?P<q>.+)$", re.I),
    re.compile(r"^what\s+do\s+my\s+(?:files?|documents?|docs?|notes)\s+(?:say|know)\s+(?:about|on)\s+(?P<q>.+)$", re.I),
]
_OPEN = re.compile(r"^(?:open|show)\s+(?:the\s+)?(?:(?P<w>top|first|1st|second|2nd|third|3rd|fourth|4th|last)"
                   r"(?:\s+(?:one|file|result))?|(?:file|result|number|#)\s*(?P<n>[1-4]))$", re.I)
_ORD = {"top": 1, "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "last": -1}
_POLITE_PRE = re.compile(r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?", re.I)
_POLITE_POST = re.compile(r"(?:[,\s]+(?:please|for\s+me|now|thanks|thank\s+you|sir))+\s*$", re.I)


def _clean(command: str) -> str:
    t = re.sub(r"[.!?\s]+$", "", (command or "").strip())
    t = _POLITE_POST.sub("", t)
    return _POLITE_PRE.sub("", t).strip()


def _remember_rows(rows: list[dict]) -> None:
    global _last_files, _last_files_at
    _last_files, _last_files_at = [dict(r) for r in rows], time.time()


def _find(query: str) -> str:
    try:
        rows = search_files(query)
    except Exception as e:  # noqa: BLE001
        log.warning("file search failed: %s", e)
        return "I couldn't search your files right now."
    if not rows:
        st = status()
        if st.get("ok") and (st["phase"] != "idle" or st["files"] == 0):
            done = f" {st['done']} of {st['total']} done so far." if st["phase"] == "indexing" and st["total"] else ""
            return f"I'm still indexing your files, so I can't find anything about {query} yet.{done}"
        return f"I couldn't find any files about {query}."
    _remember_rows(rows)
    try:
        import ui_bridge
        ui_bridge.push_search_results(query, rows, provider="files")
    except Exception as e:  # noqa: BLE001
        log.debug("file results not shown: %s", e)
    n = len(rows)
    return f"I found {n} file{'s' if n != 1 else ''} about {query}."


def _open_nth(n: int) -> str | None:
    if not _last_files or time.time() - _last_files_at > OPEN_TTL_S:
        return None
    idx = len(_last_files) - 1 if n == -1 else n - 1
    if not 0 <= idx < len(_last_files):
        return f"I only showed {len(_last_files)} files."
    path = _last_files[idx]["url"]
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:  # noqa: BLE001
        return "I couldn't open that file."
    try:
        import ui_bridge
        ui_bridge.push_search_dismiss()
    except Exception:  # noqa: BLE001
        pass
    return f"Opening {_last_files[idx]['title'][:70]}."


_ANSWER_SYSTEM = (
    "You answer a question using ONLY the file excerpts given. Name the file(s) you used in the answer, "
    "like 'According to notes.pdf, ...'. If the excerpts do not contain the answer, say you could not find it "
    "in the user's files. At most four short sentences, plain text, no markdown: it is read aloud."
)


def _llm_complete(question: str, hits: list[dict]) -> str:
    """One LLM call with the excerpts. Raises when no model is configured or the call fails."""
    import brain
    ctx = "\n\n".join(f"[{h['name']}]\n{h['text'][:1100]}" for h in hits)
    messages = [{"role": "system", "content": _ANSWER_SYSTEM},
                {"role": "user", "content": f"Question: {question}\n\nExcerpts:\n{ctx}"}]
    tries = [brain._get_client()]
    try:
        fb = brain._fallback_client()
        if fb:
            tries.append(fb)
    except Exception:  # noqa: BLE001
        pass
    err: Exception | None = None
    for client, model in tries:
        if client is None:
            continue
        try:
            resp = brain._chat_completion(client, model, messages)
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:  # noqa: BLE001
            err = e
    raise RuntimeError(str(err) if err else "no AI key is set")


def answer_from_files(question: str) -> str:
    st = status()
    if st.get("ok") and st["files"] == 0:
        return "I haven't indexed any files yet. Check Settings, Files, to choose folders."
    hits = search_chunks(question, max_files=4, per_file=2, any_word=True)
    if not hits:
        return "I couldn't find anything about that in your files."
    try:
        answer = _llm_complete(question, hits)
    except Exception as e:  # noqa: BLE001
        log.warning("answer from files failed: %s", e)
        return "I found matching files but couldn't get an answer from the AI just now."
    if not answer:
        return "I found matching files but got no answer from the AI."
    names = list(dict.fromkeys(h["name"] for h in hits))
    if not any(n.lower() in answer.lower() for n in names):
        answer += " Sources: " + ", ".join(names[:3]) + "."
    return answer


def _try_file_search(command: str) -> str | None:
    """Handle "find files about X", "open the first one", "answer from my files: ...". None = not mine."""
    if not _feature_on():
        return None
    text = _clean(command)
    if not text:
        return None
    for pat in _ASK:
        m = pat.match(text)
        if m and m.group("q").strip():
            return answer_from_files(m.group("q").strip())
    for pat in _FIND:
        m = pat.match(text)
        if m and m.group("q").strip(" ,"):
            return _find(m.group("q").strip(" ,"))
    m = _OPEN.match(text)
    if m:
        return _open_nth(_ORD[m.group("w").lower()] if m.group("w") else int(m.group("n")))
    return None


def try_file_search(command: str) -> str | None:
    """Hook for actions.try_handle_action: never raises. None = not a file command."""
    try:
        return _try_file_search(command)
    except Exception as e:  # noqa: BLE001
        log.warning("file command failed: %s", e)
        return None


# ── Settings endpoints ───────────────────────────────────────────────────────────────────────
def _folder_error(path: str) -> str:
    p = Path(os.path.expandvars(path)).expanduser()
    if not p.is_dir():
        return "That folder doesn't exist."
    if p.parent == p:
        return "Pick a folder, not a whole drive."
    for prot in (os.environ.get("WINDIR", ""), os.environ.get("ProgramFiles", ""),
                 os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramData", "")):
        if prot and _under(str(p), prot):
            return "System folders can't be indexed."
    return ""


def _pick_folder() -> str:
    script = ("Add-Type -AssemblyName System.Windows.Forms; "
              "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
              "$d.Description = 'Choose a folder for Lyra to search'; "
              "if ($d.ShowDialog() -eq 'OK') { Write-Output $d.SelectedPath }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script], capture_output=True, text=True,
                           timeout=300, creationflags=0x08000000 if sys.platform == "win32" else 0)
        lines = (r.stdout or "").strip().splitlines()
        return lines[-1].strip() if lines else ""
    except Exception as e:  # noqa: BLE001
        log.warning("folder picker: %s", e)
        return ""


def _folders_payload() -> dict:
    explicit = isinstance(_setting("index_folders"), list)
    return {"ok": True, "enabled": _feature_on(), "default": not explicit, "exclude": excludes(),
            "folders": [{"path": f, "exists": os.path.isdir(f)} for f in folders()]}


def http(method: str, path: str, msg: dict) -> tuple[int, dict]:
    """Handle /files/... for ui_bridge. Returns (status code, json body)."""
    try:
        if method == "GET" and path == "/files/status":
            return 200, status()
        if method == "GET" and path == "/files/folders":
            return 200, _folders_payload()
        if method != "POST":
            return 404, {"error": "not found"}
        if path in ("/files/folders/add", "/files/folders/pick"):
            chosen = str(msg.get("path") or "").strip() if path.endswith("add") else _pick_folder()
            if not chosen:
                return 200, {**_folders_payload(), "added": ""}
            err = _folder_error(chosen)
            if err:
                return 400, {"error": err}
            cur = folders()
            norm = os.path.normpath(os.path.expandvars(chosen))
            if norm not in cur:
                _set_settings({"index_folders": cur + [norm]})
                request_reindex()
            return 200, {**_folders_payload(), "added": norm}
        if path == "/files/folders/remove":
            target = os.path.normpath(str(msg.get("path") or ""))
            _set_settings({"index_folders": [f for f in folders() if f != target]})
            request_reindex()
            return 200, _folders_payload()
        if path in ("/files/exclude/add", "/files/exclude/remove"):
            pat = str(msg.get("pattern") or "").strip()[:200]
            if not pat:
                return 400, {"error": "Type a folder name, path or pattern like *.tmp"}
            cur = excludes()
            new = (cur + [pat] if pat not in cur else cur) if path.endswith("add") else [x for x in cur if x != pat]
            _set_settings({"index_exclude": new})
            request_reindex()
            return 200, _folders_payload()
        if path == "/files/reindex":
            if not _feature_on():
                return 409, {"error": "Turn on File search under Features first."}
            if not request_reindex():
                return 409, {"error": "The indexer isn't running yet. Restart Lyra after turning File search on."}
            return 200, {"ok": True}
    except Exception as e:  # noqa: BLE001
        log.warning("file index endpoint %s: %s", path, e)
        return 500, {"error": str(e)}
    return 404, {"error": "not found"}
