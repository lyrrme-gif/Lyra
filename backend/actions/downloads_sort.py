"""Tidy the Downloads folder by file type - with a preview and a confirmation, and undo.

  "sort my downloads"   -> Lyra says what she would move (nothing moves yet)
  "yes sort them"       -> does it (within 3 minutes), then "undo that" moves everything back

Only loose files directly in Downloads are touched. Folders, unfinished downloads, files changed in
the last 10 minutes and files of unknown type stay where they are. Nothing is ever overwritten
or deleted.
"""
from __future__ import annotations

import ctypes
import os
import re
import shutil
import time
from collections import Counter
from pathlib import Path

_CATS = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".svg", ".tif", ".tiff", ".avif", ".ico"},
    "Videos": {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv", ".m4v"},
    "Audio": {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma"},
    "Documents": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".rtf", ".odt", ".ods", ".csv", ".md", ".epub"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"},
    "Installers": {".exe", ".msi", ".msix", ".appx", ".apk"},
    "Code": {".py", ".js", ".ts", ".json", ".html", ".css", ".java", ".cpp", ".c", ".cs", ".sh", ".bat", ".ps1", ".sql"},
}
_UNFINISHED = {".crdownload", ".part", ".tmp", ".download", ".partial", ".opdownload", ".!ut"}
_RECENT_S = 600
_pending: dict | None = None

_REQUEST = re.compile(r"^(?:sort|organi[sz]e|clean\s+up|tidy(?:\s+up)?|arrange)\s+(?:out\s+)?(?:my\s+|the\s+)?downloads(?:\s+folder)?(?:\s+by\s+(?:type|kind))?$")
_CONFIRM = re.compile(r"^(?:yes[, ]+)?(?:sort\s+them|go\s+ahead\s+and\s+sort(?:\s+them)?|confirm\s+(?:the\s+)?sort(?:ing)?)$")


def _downloads() -> Path:
    try:  # the real Downloads folder, even when it was moved or redirected
        import uuid
        guid = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID("{374DE290-123F-4565-9164-39C4925E467B}").bytes_le)
        out = ctypes.c_wchar_p()
        ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
        if out.value and Path(out.value).is_dir():
            return Path(out.value)
    except Exception:
        pass
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "Downloads"


def _category(p: Path) -> str | None:
    ext = p.suffix.lower()
    for cat, exts in _CATS.items():
        if ext in exts:
            return cat
    return None


def _plan(folder: Path) -> list[tuple[Path, Path]]:
    moves = []
    now = time.time()
    for p in sorted(folder.iterdir()):
        try:
            if not p.is_file() or p.name.startswith(".") or p.suffix.lower() in _UNFINISHED:
                continue
            if now - p.stat().st_mtime < _RECENT_S:
                continue
            cat = _category(p)
            if cat:
                moves.append((p, folder / cat / p.name))
        except OSError:
            continue
    return moves


def _free_name(dst: Path) -> Path:
    if not dst.exists():
        return dst
    stem, ext = dst.stem, dst.suffix
    for i in range(1, 500):
        c = dst.with_name(f"{stem} ({i}){ext}")
        if not c.exists():
            return c
    return dst.with_name(f"{stem} ({int(time.time())}){ext}")


def try_sort(command: str) -> str | None:
    global _pending
    low = command.strip().lower().rstrip("?.! ")
    low = re.sub(r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:please\s+|can\s+you\s+|could\s+you\s+)?", "", low).strip()
    if _REQUEST.match(low):
        folder = _downloads()
        if not folder.is_dir():
            return "I can't find your Downloads folder."
        moves = _plan(folder)
        if not moves:
            _pending = None
            return "Your Downloads folder is already tidy - nothing to sort."
        counts = Counter(_category(src) for src, _ in moves)
        _pending = {"t": time.time(), "folder": str(folder)}
        parts = ", ".join(f"{n} {c.lower()}" for c, n in counts.most_common())
        return (f"I would move {len(moves)} files into folders by type: {parts}. Nothing has moved yet. "
                f"Say 'yes sort them' to do it.")
    if _CONFIRM.match(low):
        if not _pending or time.time() - _pending["t"] > 180:
            _pending = None
            return "There's nothing waiting to be sorted. Say 'sort my downloads' first."
        folder = Path(_pending["folder"])
        _pending = None
        done: list[list[str]] = []
        failed = 0
        for src, dst in _plan(folder):  # re-plan: only files that are still there and still settled
            try:
                dst.parent.mkdir(exist_ok=True)
                final = _free_name(dst)
                shutil.move(str(src), str(final))
                done.append([str(src), str(final)])
            except Exception:
                failed += 1
        if not done:
            return "I couldn't move anything."
        try:
            import activity_log
            activity_log.set_undo({"type": "move_back", "moves": done[:400]})
        except Exception:
            pass
        return f"Sorted {len(done)} files." + (f" {failed} couldn't be moved." if failed else "") + " Say 'undo that' to put them back."
    return None
