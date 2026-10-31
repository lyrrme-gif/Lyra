"""Session-only clipboard: Windows clipboard history + files dropped on the Clipboard tab.

Nothing here is persisted. Every file lives in %TEMP%\\LyraClipboardSession\\<id>\\<name>
and the whole folder is wiped when the backend starts and when it exits, so items stay
exactly until the user removes them or Lyra restarts.
"""
from __future__ import annotations

import atexit
import ctypes
import hashlib
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import BinaryIO, Callable

Publisher = Callable[[dict], None]

CF_UNICODETEXT = 13
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002

MAX_UPLOAD = 2 * 1024 ** 3   # per dropped file
MAX_RECENT = 30              # auto-captured clipboard entries kept (dropped files are never auto-evicted)
MAX_ITEMS = 200

_items: list[dict] = []
_lock = threading.RLock()
_stop = threading.Event()
_thread: threading.Thread | None = None
_publisher: Publisher | None = None
_last = ""
_seq = 0
_SESSION_DIR = Path(tempfile.gettempdir()) / "LyraClipboardSession"
_KEEP_FLAG = Path(tempfile.gettempdir()) / "LyraClipboardKeep.flag"
_STORE = _SESSION_DIR / "items.json"


def _keeping() -> bool:
    return _KEEP_FLAG.exists()


def set_keep(keep: bool) -> None:
    """Settings: keep the clipboard (items and dropped files) across restarts."""
    try:
        if keep:
            _KEEP_FLAG.write_text("1")
            _persist()
        else:
            _KEEP_FLAG.unlink(missing_ok=True)
    except OSError:
        pass


def _persist() -> None:
    if not _keeping():
        return
    try:
        import json
        _SESSION_DIR.mkdir(parents=True, exist_ok=True)
        with _lock:
            data = list(_items)
        _STORE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def _load_kept() -> None:
    global _seq
    try:
        import json
        data = json.loads(_STORE.read_text(encoding="utf-8"))
    except Exception:
        return
    with _lock:
        _items[:] = [i for i in data if all(os.path.exists(f) for f in (i.get("files") or []))]
        _seq = max([int(i.get("id", 0)) for i in _items] or [0])


class UploadError(Exception):
    """A dropped file could not be stored (too large, cut short, ...)."""


# ── Win32 plumbing (private DLL handles so nothing global is mutated) ─────────

_win_cache = None


def _win():
    global _win_cache
    if os.name != "nt":
        return None
    if _win_cache is None:
        from ctypes import wintypes
        u = ctypes.WinDLL("user32")
        k = ctypes.WinDLL("kernel32")
        sh = ctypes.WinDLL("shell32")
        vp = ctypes.c_void_p
        u.OpenClipboard.argtypes = [wintypes.HWND]; u.OpenClipboard.restype = wintypes.BOOL
        u.CloseClipboard.argtypes = []; u.CloseClipboard.restype = wintypes.BOOL
        u.EmptyClipboard.argtypes = []; u.EmptyClipboard.restype = wintypes.BOOL
        u.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]; u.IsClipboardFormatAvailable.restype = wintypes.BOOL
        u.GetClipboardData.argtypes = [wintypes.UINT]; u.GetClipboardData.restype = vp
        u.SetClipboardData.argtypes = [wintypes.UINT, vp]; u.SetClipboardData.restype = vp
        u.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]; u.RegisterClipboardFormatW.restype = wintypes.UINT
        k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]; k.GlobalAlloc.restype = vp
        k.GlobalLock.argtypes = [vp]; k.GlobalLock.restype = vp
        k.GlobalUnlock.argtypes = [vp]; k.GlobalUnlock.restype = wintypes.BOOL
        k.GlobalFree.argtypes = [vp]; k.GlobalFree.restype = vp
        sh.DragQueryFileW.argtypes = [vp, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
        sh.DragQueryFileW.restype = wintypes.UINT
        _win_cache = (u, k, sh)
    return _win_cache


def _open() -> bool:
    w = _win()
    if not w:
        return False
    for _ in range(6):  # another app may hold the clipboard for a few ms
        if w[0].OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def _close() -> None:
    w = _win()
    if w:
        try:
            w[0].CloseClipboard()
        except Exception:
            pass


def read_current():
    """Text or file paths currently on the Windows clipboard, or None."""
    w = _win()
    if not w or not _open():
        return None
    u, k, sh = w
    try:
        files: list[str] = []
        if u.IsClipboardFormatAvailable(CF_HDROP):
            h = u.GetClipboardData(CF_HDROP)
            if h:
                n = int(sh.DragQueryFileW(h, 0xFFFFFFFF, None, 0))
                for i in range(n):
                    buf = ctypes.create_unicode_buffer(32768)
                    sh.DragQueryFileW(h, i, buf, len(buf))
                    if buf.value:
                        files.append(buf.value)
        if files:
            return {"kind": "files", "text": "", "files": files[:100]}
        if u.IsClipboardFormatAvailable(CF_UNICODETEXT):
            h = u.GetClipboardData(CF_UNICODETEXT)
            if h:
                p = k.GlobalLock(h)
                if p:
                    try:
                        text = ctypes.wstring_at(p)
                    finally:
                        k.GlobalUnlock(h)
                    return {"kind": "text", "text": text[:8000], "files": []}
    except Exception:
        pass
    finally:
        _close()
    return None


def set_clipboard_files(paths: list[str]) -> bool:
    """Put real files on the Windows clipboard (CF_HDROP) so Ctrl+V pastes them anywhere."""
    w = _win()
    paths = [str(p) for p in paths if p]
    if not w or not paths:
        return False
    u, k, _sh = w

    def make(blob: bytes):
        h = k.GlobalAlloc(GMEM_MOVEABLE, len(blob))
        if not h:
            return None
        p = k.GlobalLock(h)
        if not p:
            k.GlobalFree(h)
            return None
        ctypes.memmove(p, blob, len(blob))
        k.GlobalUnlock(h)
        return h

    # DROPFILES: pFiles=20, pt=(0,0), fNC=0, fWide=1, then UTF-16 paths, double-NUL terminated.
    hdrop = make(struct.pack("<IiiII", 20, 0, 0, 0, 1) + ("\0".join(paths) + "\0\0").encode("utf-16-le"))
    if not hdrop:
        return False
    if not _open():
        k.GlobalFree(hdrop)
        return False
    try:
        u.EmptyClipboard()
        if not u.SetClipboardData(CF_HDROP, hdrop):
            k.GlobalFree(hdrop)
            return False
        fmt = u.RegisterClipboardFormatW("Preferred DropEffect")
        eff = make(struct.pack("<I", 1)) if fmt else None  # 1 = copy, never move out of the session
        if eff and not u.SetClipboardData(fmt, eff):
            k.GlobalFree(eff)
        return True
    finally:
        _close()


# ── Helpers ───────────────────────────────────────────────────────────────────

_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_name(name: str) -> str:
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        name = "file"
    if name.split(".")[0].lower() in _RESERVED:
        name = "_" + name
    if len(name) > 200:
        stem, dot, ext = name.rpartition(".")
        name = (stem[:180] + dot + ext[:15]) if dot and stem else name[:200]
    return name


def _is_ours(p: str) -> bool:
    try:
        Path(p).resolve().relative_to(_SESSION_DIR.resolve())
        return True
    except Exception:
        return False


def _next_seq() -> int:
    global _seq
    with _lock:
        _seq += 1
        return _seq


def _size(p: str) -> int:
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def _make_item(seq: int, kind: str, text: str, files: list[str], label: str, source: str, sig: str = "") -> dict:
    return {
        "id": seq, "kind": kind, "text": text[:8000], "files": files,
        "names": [Path(f).name for f in files], "sizes": [_size(f) for f in files],
        "label": label, "source": source, "sig": sig, "ts": int(time.time() * 1000),
    }


def _store(src: str, seq: int, index: int = 0) -> str:
    """Copy a file into this item's own folder, keeping its real name."""
    folder = _SESSION_DIR / f"{seq}_{index}"
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / safe_name(Path(src).name)
    try:
        shutil.copy2(src, dst)
        return str(dst)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        return src  # fall back to a reference to the original; never deleted (not ours)


def _delete_files(item: dict) -> None:
    for p in item.get("files", []):
        if _is_ours(p):
            try:
                Path(p).unlink(missing_ok=True)
                parent = Path(p).parent
                if parent != _SESSION_DIR:
                    shutil.rmtree(parent, ignore_errors=True)
            except Exception:
                pass


def _sig(v) -> str:
    if not v:
        return ""
    raw = v.get("kind", "") + "\0" + v.get("text", "") + "\0" + "\0".join(v.get("files", []))
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def _trim() -> None:
    """Caller holds the lock. Newest first, so the tail is the oldest."""
    recent = [i for i in _items if i.get("source") != "drop"]
    for old in recent[MAX_RECENT:]:
        _items.remove(old)
        _delete_files(old)
    while len(_items) > MAX_ITEMS:
        _delete_files(_items.pop())


def _emit() -> None:
    _persist()
    if _publisher:
        try:
            _publisher({"type": "clipboard", "items": snapshot(), "ts": int(time.time() * 1000)})
        except Exception:
            pass


# ── Public API ────────────────────────────────────────────────────────────────

def capture(v=None) -> bool:
    """Record what is on the Windows clipboard right now (also what the watcher calls)."""
    global _last
    v = v or read_current()
    sig = _sig(v)
    if not v or not sig:
        return False
    if sig == _last:
        return True
    _last = sig
    if v["kind"] == "files" and all(_is_ours(p) for p in v["files"]):
        return True  # we put these there ourselves (Copy button); already in the list
    seq = _next_seq()
    if v["kind"] == "files":
        files = [_store(p, seq, i) for i, p in enumerate(v["files"][:100])]
        label = Path(v["files"][0]).name + (f" +{len(files) - 1}" if len(files) > 1 else "")
    else:
        files = []
        label = (v.get("text") or "(empty)").replace("\n", " ").strip()[:100]
    item = _make_item(seq, v["kind"], v.get("text", ""), files, label, "clip", sig)
    with _lock:
        for old in [o for o in _items if o.get("source") == "clip" and o.get("sig") == sig]:
            _items.remove(old)
            _delete_files(old)
        _items.insert(0, item)
        _trim()
    _emit()
    return True


def add_upload(name: str, stream: BinaryIO, length: int | None) -> dict:
    """Store one dropped file streamed from an HTTP request body."""
    if length is None:
        raise UploadError("Missing file size.")
    if length > MAX_UPLOAD:
        raise UploadError("That file is too large (2 GB limit).")
    seq = _next_seq()
    folder = _SESSION_DIR / f"{seq}_0"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / safe_name(name)
    try:
        remaining = length
        with open(dest, "wb") as f:
            while remaining > 0:
                chunk = stream.read(min(1 << 20, remaining))
                if not chunk:
                    raise UploadError("The upload was cut short.")
                f.write(chunk)
                remaining -= len(chunk)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    item = _make_item(seq, "files", "", [str(dest)], dest.name, "drop")
    with _lock:
        _items.insert(0, item)
        _trim()
    _emit()
    return item


def capture_files(paths):
    """Add existing files by path (one item each). Kept for callers that already have paths."""
    added = False
    for p in paths or []:
        try:
            if not (p and Path(p).is_file()):
                continue
        except Exception:
            continue
        seq = _next_seq()
        stored = _store(str(p), seq)
        item = _make_item(seq, "files", "", [stored], Path(stored).name, "drop")
        with _lock:
            _items.insert(0, item)
            _trim()
        added = True
    if added:
        _emit()
    return added, snapshot()


def snapshot():
    with _lock:
        return [{k: v for k, v in i.items() if k != "sig"} for i in _items]


def file_path(iid: int, index: int = 0) -> Path | None:
    """Path of a stored file, only ever inside the session folder (used to serve drag-out)."""
    with _lock:
        item = next((x for x in _items if int(x["id"]) == int(iid)), None)
    if not item or not (0 <= index < len(item.get("files", []))):
        return None
    p = item["files"][index]
    return Path(p) if _is_ours(p) and Path(p).is_file() else None


def remove(iid: int) -> bool:
    with _lock:
        item = next((x for x in _items if int(x["id"]) == int(iid)), None)
        if not item:
            return False
        _items.remove(item)
    _delete_files(item)
    _emit()
    return True


def clear() -> None:
    with _lock:
        old = list(_items)
        _items.clear()
    for item in old:
        _delete_files(item)
    _emit()


def restore(iid, action="copy"):
    """action: copy | open | reveal | remove. Items are only ever removed by 'remove'."""
    iid = int(iid)
    if action == "remove":
        return (True, "Removed.") if remove(iid) else (False, "Clipboard item not found.")
    with _lock:
        item = next((x for x in _items if int(x["id"]) == iid), None)
    if not item:
        return False, "Clipboard item not found."
    files = [p for p in item.get("files", []) if os.path.exists(p)]
    if action == "open":
        if not files:
            return False, "Nothing to open."
        try:
            os.startfile(files[0])  # type: ignore[attr-defined]
            return True, "Opened."
        except Exception as e:
            return False, str(e)
    if action == "reveal":
        if not files:
            return False, "Nothing to show."
        try:
            subprocess.Popen(f'explorer /select,"{files[0]}"')
            return True, "Shown in Explorer."
        except Exception as e:
            return False, str(e)
    # copy / restore → back onto the Windows clipboard
    global _last
    if item["kind"] == "files":
        if not files:
            return False, "The file is no longer there."
        if set_clipboard_files(files):
            _last = _sig({"kind": "files", "text": "", "files": files})  # so the watcher doesn't re-add them
            return True, "Copied. Paste it anywhere with Ctrl+V."
        return False, "Could not put the files on the clipboard."
    try:
        from actions.system import set_clipboard_text
        ok = set_clipboard_text(item.get("text", ""))
        return (True, "Copied.") if ok else (False, "Could not restore the clipboard text.")
    except Exception as e:
        return False, str(e)


def cleanup_all() -> None:
    if _keeping():
        _persist()
        return
    shutil.rmtree(_SESSION_DIR, ignore_errors=True)


def start(publisher: Publisher):
    global _thread, _publisher, _last
    if _thread and _thread.is_alive():
        return
    if _keeping():
        _load_kept()  # "Keep clipboard" is on: carry the last session over
    else:
        shutil.rmtree(_SESSION_DIR, ignore_errors=True)  # a restart starts with an empty clipboard
    atexit.register(cleanup_all)
    _publisher = publisher
    _stop.clear()
    _last = _sig(read_current())

    def run():
        # The clipboard has a change counter. Reading the clipboard (opening it, copying the text,
        # hashing it) 4 times a second is the expensive part, so only read when the counter moved.
        last_seq, tries = None, 0
        while not _stop.wait(0.25):
            cur = _clip_seq()
            if cur is not None and cur == last_seq:
                continue
            ok = capture()
            if cur is None or ok or tries >= 3:      # unreadable right now (another app has it open): retry a few times
                last_seq, tries = cur, 0
            else:
                tries += 1

    _thread = threading.Thread(target=run, name="lyra-clipboard-watch", daemon=True)
    _thread.start()


def _clip_seq():
    """Windows' clipboard change counter, or None when it cannot be read (then every poll reads, as before)."""
    try:
        w = _win()
        if not w:
            return None
        return int(w[0].GetClipboardSequenceNumber())
    except Exception:
        return None


def stop():
    _stop.set()
