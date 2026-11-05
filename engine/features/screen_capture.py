"""Lyra — reusable screen capture service.

All screenshot / vision / screen-watch features must use this module instead of
rolling their own ImageGrab / mss calls.

Supports:
  - full virtual desktop
  - specific monitor (0-based index)
  - arbitrary region (left, top, width, height) in virtual-desktop coords

Returns PIL Images; helpers can write to disk or keep an in-memory agent context.
"""

from __future__ import annotations

import io
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from engine.bridge import ui

log = ui.log

_lock = threading.Lock()
_last_context: dict[str, Any] | None = None


def _grab_pil(bbox: tuple[int, int, int, int] | None = None):
    """bbox = (left, top, right, bottom) in virtual-screen coordinates, or None for all."""
    from PIL import ImageGrab
    if bbox is None:
        return ImageGrab.grab(all_screens=True)
    return ImageGrab.grab(bbox=bbox, all_screens=True)


def monitors() -> list[dict]:
    """List displays: [{index, left, top, width, height}, ...].

    Index 0 is the primary when Windows reports it first; otherwise order is
    whatever the OS gives. Virtual desktop bounds are included as index -1 style
    via full capture.
    """
    try:
        from PIL import ImageGrab
        # Pillow 10+ : ImageGrab doesn't list monitors; use Windows API
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        monitors_out: list[dict] = []

        def _cb(hmon, hdc, lprect, lparam):
            r = lprect.contents
            monitors_out.append({
                "index": len(monitors_out),
                "left": int(r.left),
                "top": int(r.top),
                "width": int(r.right - r.left),
                "height": int(r.bottom - r.top),
            })
            return 1

        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(wintypes.RECT), ctypes.c_double
        )
        user32.EnumDisplayMonitors(0, 0, MONITORENUMPROC(_cb), 0)
        if monitors_out:
            return monitors_out
    except Exception as e:
        log.debug("monitor enum failed: %s", e)
    # Fallback: single primary
    try:
        img = _grab_pil()
        return [{"index": 0, "left": 0, "top": 0, "width": img.width, "height": img.height}]
    except Exception:
        return []


def capture_full():
    """Full virtual desktop as a PIL Image."""
    return _grab_pil(None)


def virtual_bounds() -> tuple[int, int, int, int]:
    """(left, top, width, height) of the whole desktop, all screens together. `left`/`top` are negative when a
    screen sits left of / above the main one: a full-desktop picture starts there, not at (0, 0)."""
    try:
        import ctypes
        u = ctypes.windll.user32
        w, h = int(u.GetSystemMetrics(78)), int(u.GetSystemMetrics(79))
        if w > 0 and h > 0:
            return int(u.GetSystemMetrics(76)), int(u.GetSystemMetrics(77)), w, h
    except Exception:  # noqa: BLE001
        pass
    mons = monitors()
    if not mons:
        return 0, 0, 0, 0
    l = min(m["left"] for m in mons)
    t = min(m["top"] for m in mons)
    return l, t, max(m["left"] + m["width"] for m in mons) - l, max(m["top"] + m["height"] for m in mons) - t


def primary_monitor(mons: list[dict] | None = None) -> dict | None:
    """The main screen (it always sits at 0,0); the first one if Windows does not say."""
    mons = monitors() if mons is None else mons
    return next((m for m in mons if m["left"] == 0 and m["top"] == 0), mons[0] if mons else None)


def screen_label(m: dict, mons: list[dict] | None = None) -> str:
    """\"Screen 1 (main)\", \"Screen 2 (right of the main screen)\" ..."""
    mons = monitors() if mons is None else mons
    main = primary_monitor(mons)
    name = f"Screen {m['index'] + 1}"
    if main is None or m is main or (m["left"], m["top"]) == (main["left"], main["top"]):
        return f"{name} (main)" if len(mons) > 1 else name
    dx = (m["left"] + m["width"] / 2) - (main["left"] + main["width"] / 2)
    dy = (m["top"] + m["height"] / 2) - (main["top"] + main["height"] / 2)
    side = ("right of" if dx > 0 else "left of") if abs(dx) >= abs(dy) else ("below" if dy > 0 else "above")
    return f"{name} ({side} the main screen)"


def describe_layout() -> str:
    """One sentence for Lyra: how many screens there are and how they sit. Empty with a single screen."""
    mons = monitors()
    if len(mons) < 2:
        return ""
    parts = [f"{screen_label(m, mons)} {m['width']}x{m['height']}" for m in mons]
    return f"This PC has {len(mons)} screens: " + "; ".join(parts) + "."


def capture_monitor(index: int = 0):
    """Capture one monitor by index (see monitors())."""
    mons = monitors()
    if not mons:
        return capture_full()
    index = max(0, min(index, len(mons) - 1))
    m = mons[index]
    bbox = (m["left"], m["top"], m["left"] + m["width"], m["top"] + m["height"])
    return _grab_pil(bbox)


def capture_region(left: int, top: int, width: int, height: int):
    """Capture a rectangle in virtual-desktop coordinates."""
    width = max(1, int(width))
    height = max(1, int(height))
    bbox = (int(left), int(top), int(left) + width, int(top) + height)
    return _grab_pil(bbox)


def to_png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def save(img, path: str | Path | None = None) -> str:
    """Write PNG to path (or Pictures/Screenshots) and return the path string."""
    if path is None:
        out_dir = Path.home() / "Pictures" / "Screenshots"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"screenshot_{datetime.now():%Y%m%d_%H%M%S}.png"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG")
    return str(path)


def capture(
    mode: str = "full",
    *,
    monitor: int = 0,
    region: tuple[int, int, int, int] | None = None,
    save_path: str | Path | None = None,
    keep_context: bool = True,
) -> dict:
    """Unified capture entry point.

    mode: "full" | "monitor" | "region"
    region: (left, top, width, height) when mode == "region"
    keep_context: store as last agent screen context (in-memory + optional path)

    Returns {ok, path?, width, height, mode, message?}
    """
    try:
        if mode == "region" and region is not None:
            img = capture_region(*region)
        elif mode == "monitor":
            img = capture_monitor(monitor)
        else:
            img = capture_full()
            mode = "full"
    except Exception as e:
        return {"ok": False, "message": f"Screen capture failed: {e}", "mode": mode}

    path = None
    # Keeping context must not silently create a permanent screenshot.
    # Persistence is explicit through save_path (or the screenshot action).
    if save_path is not None:
        try:
            path = save(img, save_path)
        except Exception as e:
            log.warning("could not save screenshot: %s", e)

    result = {
        "ok": True,
        "path": path,
        "width": img.width,
        "height": img.height,
        "mode": mode,
        "ts": int(time.time() * 1000),
    }
    if keep_context:
        with _lock:
            global _last_context
            _last_context = {
                **result,
                # Keep a small in-memory PNG so vision can reuse without re-grab
                "png_bytes": to_png_bytes(img),
            }
        try:
            from engine.brain import session_context
            session_context.get().note_screen_capture(path or "", mode=mode)
        except Exception:
            pass
    return result


def last_context() -> dict | None:
    """Most recent capture kept for the Agent/vision (may include png_bytes)."""
    with _lock:
        if _last_context is None:
            return None
        # Return a copy without forcing callers to hold the lock
        return dict(_last_context)


