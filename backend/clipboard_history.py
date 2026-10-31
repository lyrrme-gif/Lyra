"""Windows clipboard snapshot helpers for Lyra's session-only Clipboard tab."""
from __future__ import annotations
import ctypes
import os
from ctypes import wintypes

CF_UNICODETEXT = 13
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002


def _open():
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.user32.OpenClipboard(None))
    except Exception:
        return False


def _close():
    try:
        ctypes.windll.user32.CloseClipboard()
    except Exception:
        pass


def snapshot() -> dict:
    """Return the current text and/or file paths on the Windows clipboard."""
    if not _open():
        return {"kind": "empty", "text": "", "files": []}
    try:
        user32 = ctypes.windll.user32
        files: list[str] = []
        if user32.IsClipboardFormatAvailable(CF_HDROP):
            hdrop = user32.GetClipboardData(CF_HDROP)
            if hdrop:
                count = user32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
                buf = ctypes.create_unicode_buffer(32768)
                for i in range(int(count)):
                    n = user32.DragQueryFileW(hdrop, i, buf, len(buf))
                    if n:
                        files.append(buf.value[:n])
        text = ""
        if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if handle:
                user32.GlobalLock.restype = ctypes.c_void_p
                ptr = user32.GlobalLock(handle)
                if ptr:
                    try:
                        text = ctypes.wstring_at(ptr)
                    finally:
                        user32.GlobalUnlock(handle)
        if files:
            return {"kind": "files", "text": text[:4000], "files": files[:100]}
        if text:
            return {"kind": "text", "text": text[:8000], "files": []}
        return {"kind": "empty", "text": "", "files": []}
    except Exception:
        return {"kind": "empty", "text": "", "files": []}
    finally:
        _close()


def set_text(text: str) -> bool:
    if os.name != "nt":
        return False
    if not _open():
        return False
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        data = (str(text) + "\x00").encode("utf-16-le")
        user32.EmptyClipboard()
        hmem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not hmem:
            return False
        ptr = kernel32.GlobalLock(hmem)
        if not ptr:
            kernel32.GlobalFree(hmem)
            return False
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(hmem)
        if not user32.SetClipboardData(CF_UNICODETEXT, hmem):
            kernel32.GlobalFree(hmem)
            return False
        return True
    except Exception:
        return False
    finally:
        _close()
