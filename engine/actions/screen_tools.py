"""Screen and selection helpers.

* "explain this error"            - asks the vision model about what is on screen
* "copy the text from my screen"  - Windows' built-in OCR, result goes to the clipboard
* "read this aloud"               - reads the text you have selected (Ctrl+C trick)
* "remember this folder as work"  - the folder open in the active File Explorer window

Everything returns one short sentence Lyra can speak, or None when the phrase is not for here.
"""
from __future__ import annotations

import asyncio
import ctypes
import io
import re
import subprocess
import time

_CF_UNICODETEXT = 13


# ── clipboard helpers (ctypes, no extra packages) ───────────────────────────────────────────

def _get_clip() -> str:
    try:
        from engine.actions.system import get_clipboard_text
        return get_clipboard_text() or ""
    except Exception:
        return ""


def _set_clip(text: str) -> bool:
    try:
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        k.GlobalAlloc.restype = ctypes.c_void_p
        k.GlobalLock.restype = ctypes.c_void_p
        k.GlobalLock.argtypes = [ctypes.c_void_p]
        k.GlobalUnlock.argtypes = [ctypes.c_void_p]
        u.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        data = text.encode("utf-16le") + b"\x00\x00"
        h = k.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        p = k.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k.GlobalUnlock(h)
        for _ in range(10):
            if u.OpenClipboard(None):
                break
            time.sleep(0.05)
        else:
            return False
        try:
            u.EmptyClipboard()
            u.SetClipboardData(_CF_UNICODETEXT, h)
        finally:
            u.CloseClipboard()
        return True
    except Exception:
        return False


def _ctrl_c() -> None:
    u = ctypes.windll.user32
    VK_CONTROL, VK_C, UP = 0x11, 0x43, 0x0002
    u.keybd_event(VK_CONTROL, 0, 0, 0)
    u.keybd_event(VK_C, 0, 0, 0)
    u.keybd_event(VK_C, 0, UP, 0)
    u.keybd_event(VK_CONTROL, 0, UP, 0)


# ── 1. read the selection aloud ─────────────────────────────────────────────────────────────

_READ_SEL = re.compile(r"^(?:read|say)\s+(?:this|that|it|the\s+(?:selected\s+text|selection))\s*(?:out\s+loud|aloud)?$"
                       r"|^read\s+(?:the\s+)?(?:selected\s+text|selection)(?:\s+(?:out\s+loud|aloud))?$"
                       r"|^read\s+(?:this|that)\s+(?:out\s+loud|aloud)$")


def _read_selection(low: str) -> str | None:
    if not _READ_SEL.match(low):
        return None
    old = _get_clip()
    _set_clip("")
    time.sleep(0.05)
    _ctrl_c()
    text = ""
    for _ in range(8):  # up to ~0.8 s for the app to answer
        time.sleep(0.1)
        text = _get_clip().strip()
        if text:
            break
    if old:
        _set_clip(old)  # put the user's own clipboard back
    if not text:
        return "I didn't see any selected text. Select it first, then ask again."
    return text if len(text) <= 700 else text[:700].rsplit(" ", 1)[0] + "... and so on."


# ── 2. OCR: copy the text from the screen ───────────────────────────────────────────────────

_OCR = re.compile(r"^(?:copy|grab|extract|get|read)\s+(?:all\s+)?(?:the\s+)?text\s+(?:from|on)\s+(?:my\s+|the\s+)?screen$"
                  r"|^(?:ocr|scan)\s+(?:my\s+|the\s+)?screen$")


async def _ocr_png(png: bytes) -> str:
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(bytearray(png))
    await writer.store_async()
    await writer.flush_async()
    writer.detach_stream()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError("no OCR language installed")
    result = await engine.recognize_async(bitmap)
    return result.text or ""


def _screen_text() -> str:
    from PIL import ImageGrab
    shots = []
    try:
        from engine.features import screen_capture
        mons = screen_capture.monitors()
        if len(mons) > 1:                      # every screen, not only the main one
            shots = [screen_capture.capture_monitor(m["index"]) for m in mons]
    except Exception:  # noqa: BLE001
        shots = []
    if not shots:
        shots = [ImageGrab.grab(all_screens=False)]
    texts = []
    for img in shots:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        texts.append(asyncio.run(_ocr_png(buf.getvalue())).strip())
    return "\n\n".join(t for t in texts if t)


def _copy_screen_text(low: str) -> str | None:
    if not _OCR.match(low):
        return None
    try:
        text = _screen_text()
    except Exception as e:  # noqa: BLE001
        return f"I couldn't read the screen text ({type(e).__name__}). Windows OCR may need a language pack."
    if not text:
        return "I couldn't find any text on the screen."
    if _set_clip(text):
        return f"Copied {len(text)} characters of text from your screen to the clipboard."
    return "I read the text but couldn't put it on the clipboard."


# ── 3. explain this error (vision) ──────────────────────────────────────────────────────────

_ERROR = re.compile(r"^(?:explain|what(?:'s|\s+is|\s+does)|help\s+me\s+(?:with|fix)|fix|why\s+(?:am\s+i\s+getting|did\s+i\s+get))\s+"
                    r"(?:this|that|the|my)?\s*(?:error|warning|problem|popup|pop-up|message)(?:\s+(?:on\s+my\s+screen|mean))?$"
                    r"|^what\s+does\s+this\s+(?:error|message|warning)\s+mean$")


def _explain_error(low: str) -> str | None:
    if not _ERROR.match(low):
        return None
    try:
        from engine.actions import apps
        return apps.describe_screen(
            "The user is asking about an error, warning or message visible on the screen. Read it exactly, "
            "then explain in plain words what it means and the most likely fix, in at most four short sentences."
        )
    except Exception as e:  # noqa: BLE001
        return f"I couldn't look at the screen - {e}"


# ── 4. remember this folder ─────────────────────────────────────────────────────────────────

_REMEMBER_HERE = re.compile(r"^(?:remember|save|bookmark|store)\s+(?:this|the\s+current|the\s+open)\s+(?:folder|location|directory|path)"
                            r"\s+(?:as|called|named)\s+(?P<alias>[\w][\w .\-]{0,40})$")


def _explorer_path() -> str | None:
    """Folder shown in the File Explorer window that is in front, or None."""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        ps = (
            "$h = %d; (New-Object -ComObject Shell.Application).Windows() | "
            "Where-Object { $_.HWND -eq $h } | Select-Object -First 1 | "
            "ForEach-Object { $_.Document.Folder.Self.Path }" % int(hwnd)
        )
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, text=True, timeout=8, creationflags=0x08000000).stdout.strip()
        return out or None
    except Exception:
        return None


def _remember_folder(low: str) -> str | None:
    m = _REMEMBER_HERE.match(low)
    if not m:
        return None
    alias = m.group("alias").strip()
    path = _explorer_path()
    if not path:
        return "Open the folder in File Explorer and keep that window in front, then ask me again."
    try:
        from engine.actions import memory_commands
        return memory_commands.remember_path_command(alias, path)
    except Exception as e:  # noqa: BLE001
        return f"I couldn't save it - {e}"


# ── entry point ──────────────────────────────────────────────────────────────────────────────

def try_screen(command: str) -> str | None:
    original = command.strip()
    low = original.lower().rstrip("?.! ")
    low = re.sub(r"^(?:(?:hey|ok|okay)\s+)?(?:lyra[,:]?\s+)?(?:please\s+|can\s+you\s+|could\s+you\s+)?", "", low).strip()
    if not low:
        return None
    return _read_selection(low) or _copy_screen_text(low) or _explain_error(low) or _remember_folder(low)
