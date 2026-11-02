"""Lyra — Phase 1 live screen watch.

Keeps a recent screenshot of the focused window in memory so describe/click
can use a near-live frame without calling the vision API until the user asks.

Design:
  - Local capture only on an interval (CPU cost is small; API cost is zero)
  - no background capture is started any more: Lyra only looks when she is asked
  - get_latest() returns (PIL.Image, meta) if the frame is still fresh
  - grab_now() forces an immediate capture (used when watch is off or stale)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image

import ui

log = ui.log


def _eco() -> bool:
    try:
        import settings_store
        return settings_store.eco_on()
    except Exception:
        return False

try:
    from config import SCREEN_WATCH_INTERVAL, SCREEN_WATCH_MAX_AGE
    _DEFAULT_INTERVAL = float(SCREEN_WATCH_INTERVAL)
    _DEFAULT_MAX_AGE = float(SCREEN_WATCH_MAX_AGE)
except Exception:
    _DEFAULT_INTERVAL = 1.0
    _DEFAULT_MAX_AGE = 2.5

_MIN_WINDOW_PX = 80
_CHANGE_THRESHOLD = 3.0   # mean 0-255 luminance diff on a 32x18 thumbnail
_ALERT_COOLDOWN = 8.0     # min seconds between vision alert checks


def _default_alert_handler(phrase: str) -> None:
    msg = f"Heads up — I can see “{phrase}” on your screen."
    try:
        ui.print_chat("lyra", msg)
    except Exception:
        pass
    try:
        import voice
        voice.speak(msg)
    except Exception as e:
        log.warning("Alert speak failed: %s", e)


@dataclass
class FrameMeta:
    timestamp: float
    window_title: str = ""
    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0
    source: str = "focused"  # focused | full


@dataclass
class ScreenFrame:
    image: Image.Image
    meta: FrameMeta


class ScreenWatch:
    """Background focused-window capture loop."""

    def __init__(
        self,
        interval: float = _DEFAULT_INTERVAL,
        max_age: float = _DEFAULT_MAX_AGE,
    ):
        self.interval = max(0.3, float(interval))
        self.max_age = max(0.5, float(max_age))
        self._lock = threading.Lock()
        self._frame: Optional[ScreenFrame] = None
        self._watching = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_hash: Optional[str] = None
        self._change_count = 0
        self._alert_phrase: Optional[str] = None
        self._last_error: Optional[str] = None
        self._last_tiny: Optional[bytes] = None
        self._alert_set_at = 0.0
        self._alert_busy = False
        self._last_alert_check = 0.0
        self._alert_handler = None
        self._mode = "off"
        self._previous_image: Optional[Image.Image] = None
        self._last_change_at = 0.0

    # ----- public API -----
    def set_mode(self, mode: str) -> str:
        """Lyra only looks at the screen when she is asked to (or when she is explaining something).

        There is no background capture any more, so whatever mode is requested (an old saved
        "always", a voice command) ends up as "ask": a frame is grabbed only when visual context
        is requested, and a loop that is somehow still running is stopped.
        """
        mode = "ask"
        if self.is_watching():
            self.stop()
        self._mode = mode
        try:
            import settings_store
            settings_store.set_many({"screen_watch_mode": mode})
        except Exception:
            pass
        return mode

    def mode(self) -> str:
        return self._mode

    def ensure_context_frame(self) -> Optional[ScreenFrame]:
        """Return a fresh visual frame without enabling continuous watch."""
        if self.is_watching():
            return self.get_latest() or self.grab_now()
        return self.grab_now()

    def last_change_age(self) -> Optional[float]:
        with self._lock:
            if not self._last_change_at:
                return None
            return max(0.0, time.time() - self._last_change_at)

    def previous_image(self) -> Optional[Image.Image]:
        with self._lock:
            return self._previous_image.copy() if self._previous_image is not None else None

    def compare_with_previous(self, image: Image.Image) -> bool:
        """Local-only coarse comparison used by "what changed?"."""
        import hashlib
        tiny = image.copy().convert("L")
        tiny.thumbnail((96, 54))
        sig = hashlib.sha256(tiny.tobytes()).digest()
        with self._lock:
            prev = self._previous_image
            self._previous_image = image.copy()
        if prev is None:
            return False
        prev = prev.convert("L")
        prev.thumbnail((96, 54))
        if prev.size != tiny.size:
            prev = prev.resize(tiny.size)
        diff = sum(abs(a - b) for a, b in zip(tiny.tobytes(), prev.tobytes())) / max(1, len(tiny.tobytes()))
        return diff > _CHANGE_THRESHOLD

    def start(self) -> bool:
        """Start continuous capture. Idempotent."""
        if self._watching and self._thread and self._thread.is_alive():
            return True
        self._mode = "always"
        self._stop_event.clear()
        self._watching = True
        self._thread = threading.Thread(
            target=self._loop,
            name="LyraScreenWatch",
            daemon=True,
        )
        self._thread.start()
        # Immediate first frame so "what do you see" works right away
        self._capture_once()
        log.info("ScreenWatch started (interval=%.2fs)", self.interval)
        return True

    def stop(self) -> None:
        self._alert_phrase = None
        self._mode = "off"
        self._watching = False
        self._stop_event.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=2.0)
        self._thread = None
        log.info("ScreenWatch stopped")

    def is_watching(self) -> bool:
        return bool(self._watching)

    def get_latest(self, max_age: Optional[float] = None) -> Optional[ScreenFrame]:
        """Return the latest frame if it is fresher than max_age, else None."""
        # A frame is only refreshed every `interval` seconds, so the freshness
        # window must be longer than that or the buffer is ALWAYS "stale"
        # (settings.json ships interval=3.0 vs max_age=2.5).
        age_limit = (max(self.max_age, self.interval * 1.5 + 0.5)
                     if max_age is None else max_age)
        with self._lock:
            if not self._frame:
                return None
            age = time.time() - self._frame.meta.timestamp
            if age > age_limit:
                return None
            # Return a copy of the image so callers can draw on it safely
            return ScreenFrame(
                image=self._frame.image.copy(),
                meta=self._frame.meta,
            )

    def grab_now(self, full_screen: bool = False) -> Optional[ScreenFrame]:
        """Force a capture right now (also updates the buffer)."""
        return self._capture_once(full_screen=full_screen)

    def status_text(self) -> str:
        with self._lock:
            if not self._watching:
                return "I only look at your screen when you ask me to; nothing is captured in the background."
            if not self._frame:
                return "Screen watch is on, but no frame yet."
            age = time.time() - self._frame.meta.timestamp
            title = self._frame.meta.window_title or "unknown window"
            alert = f" Watching for “{self._alert_phrase}”." if self._alert_phrase else ""
            error = f" Last capture error: {self._last_error}." if self._last_error else ""
            return (
                f"Screen watch is on. Last frame {age:.1f}s ago "
                f"({self._frame.meta.width}x{self._frame.meta.height}, “{title}”)."
                f" Changes seen: {self._change_count}.{alert}{error}"
            )

    def watch_for(self, phrase: str) -> str:
        """Phase 5: remember a phrase; on significant local change, describe_screen may be used by caller."""
        self._alert_phrase = None
        return ("I only look at your screen when you ask me to, so I can't keep watching for that. "
                "Ask me what's on your screen whenever you want me to look.")

    def clear_watch_for(self) -> None:
        self._alert_phrase = None

    def set_alert_handler(self, fn) -> None:
        """fn(phrase) is called once when a watched phrase is seen."""
        self._alert_handler = fn

    def _maybe_check_alert(self, image: Image.Image) -> None:
        """Run a cheap vision yes/no only when the screen really changed.

        Guards: needs an active phrase, 8s cooldown, one check at a time,
        auto-expires after 30 min. One-shot: clears itself after firing.
        """
        phrase = self._alert_phrase
        if not phrase:
            return
        now = time.time()
        if now - self._alert_set_at > 1800:
            self._alert_phrase = None
            return
        if self._alert_busy or now - self._last_alert_check < _ALERT_COOLDOWN:
            return
        self._alert_busy = True
        self._last_alert_check = now
        snap = image.copy()

        def _work():
            try:
                from actions.apps import screen_contains
                if screen_contains(snap, phrase) and self._alert_phrase == phrase:
                    self._alert_phrase = None  # one-shot
                    handler = self._alert_handler or _default_alert_handler
                    handler(phrase)
            except Exception as e:
                log.warning("ScreenWatch alert check failed: %s", e)
            finally:
                self._alert_busy = False

        threading.Thread(target=_work, name="LyraScreenAlert", daemon=True).start()

    def consume_change(self) -> bool:
        """Return True once if the screen changed since last consume."""
        with self._lock:
            if self._change_count > 0:
                self._change_count = 0
                return True
            return False

    def get_alert_phrase(self) -> Optional[str]:
        return self._alert_phrase

    # ----- internals -----
    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                try:
                    import dbg
                    dbg.loop("screen_watch", 15, f"capturing the active window every {self.interval:.1f}s (this line is shown every 15s)")
                except Exception:
                    pass
                self._capture_once()
            except Exception as e:
                self._last_error = str(e)
                log.warning("ScreenWatch capture error: %s", e)
            self._stop_event.wait(self.interval * (3 if _eco() else 1))   # Eco mode: a third of the captures

    def _capture_once(self, full_screen: bool = False) -> Optional[ScreenFrame]:
        import screen_capture as _sc

        def _sc_full():
            return _sc.capture_full().convert("RGB")

        try:
            import pygetwindow as gw
        except Exception:
            gw = None

        left = top = width = height = 0
        title = ""
        img: Optional[Image.Image] = None

        if full_screen or gw is None:
            try:
                img = _sc_full().convert("RGB")
            except TypeError:
                img = _sc.capture_full().convert("RGB")
            width, height = img.size
            left, top = _virtual_origin()
            title = "full screen"
            source = "full"
        else:
            win = None
            try:
                win = gw.getActiveWindow()
            except Exception:
                win = None
            if not win or getattr(win, "width", 0) < _MIN_WINDOW_PX:
                # Fallback to full screen if no usable focused window
                try:
                    img = _sc_full().convert("RGB")
                except TypeError:
                    img = _sc.capture_full().convert("RGB")
                width, height = img.size
                left, top = _virtual_origin()
                title = "full screen"
                source = "full"
            else:
                title = (win.title or "").strip()
                left, top, width, height = _physical_window_rect(win)
                if width < _MIN_WINDOW_PX or height < _MIN_WINDOW_PX:
                    return None
                img = _sc.capture_region(left, top, width, height).convert("RGB")
                # Trust grab size if it differs
                iw, ih = img.size
                if abs(iw - width) > 2 or abs(ih - height) > 2:
                    width, height = iw, ih
                source = "focused"

        if img is None:
            return None

        frame = ScreenFrame(
            image=img,
            meta=FrameMeta(
                timestamp=time.time(),
                window_title=title,
                left=left,
                top=top,
                width=width,
                height=height,
                source=source,
            ),
        )
        # Phase 5: perceptual-ish hash via tiny resize + tobytes
        changed = False
        try:
            tiny = img.resize((32, 18)).convert("L").tobytes()
        except Exception:
            tiny = None
        with self._lock:
            self._last_error = None
            if tiny and self._last_tiny and len(tiny) == len(self._last_tiny):
                diff = sum(abs(a - b) for a, b in zip(tiny, self._last_tiny)) / len(tiny)
                if diff > _CHANGE_THRESHOLD:
                    self._change_count += 1
                    changed = True
            if tiny:
                self._last_tiny = tiny
            if self._frame is not None:
                self._previous_image = self._frame.image.copy()
            self._frame = frame
        if changed:
            with self._lock:
                self._last_change_at = time.time()
            if self._alert_phrase:
                self._maybe_check_alert(img)
        return ScreenFrame(image=img.copy(), meta=frame.meta)


def _virtual_origin() -> tuple[int, int]:
    """Where a full-desktop picture starts on screen. Not (0, 0) when a second screen is left of / above the
    main one, and a click located in that picture has to add this back."""
    try:
        import screen_capture as _sc
        l, t, _w, _h = _sc.virtual_bounds()
        return l, t
    except Exception:  # noqa: BLE001
        return 0, 0


def _physical_window_rect(win) -> tuple[int, int, int, int]:
    """Match apps.py DPI-aware rect logic so clicks align with the buffer."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = getattr(win, "_hWnd", None) or getattr(win, "hWnd", None)
        if hwnd:
            rect = wintypes.RECT()
            if user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
                left, top = int(rect.left), int(rect.top)
                width = int(rect.right - rect.left)
                height = int(rect.bottom - rect.top)
                if width > 50 and height > 50:
                    return left, top, width, height
    except Exception:
        pass

    left, top, width, height = win.left, win.top, win.width, win.height
    try:
        import ctypes
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
        ctypes.windll.user32.ReleaseDC(0, hdc)
        scale = dpi / 96.0
        if abs(scale - 1.0) > 0.05:
            left = int(left * scale)
            top = int(top * scale)
            width = int(width * scale)
            height = int(height * scale)
    except Exception:
        pass
    return left, top, width, height


# Module-level singleton used by actions / apps
_screen_settings = None
try:
    import settings_store as _screen_settings
    _saved_interval = float(_screen_settings.get("screen_watch_interval", _DEFAULT_INTERVAL))
except Exception:
    _saved_interval = _DEFAULT_INTERVAL
_watch = ScreenWatch(interval=_saved_interval)
try:
    _watch._mode = "ask"
except Exception:
    _watch._mode = "ask"


def get_watch() -> ScreenWatch:
    return _watch


_ONLY_WHEN_ASKED = "I only look at your screen when you ask me to, for example “what's on my screen?”."


def start_watching() -> str:
    _watch.set_mode("ask")
    return _ONLY_WHEN_ASKED + " I don't watch it in the background."


def stop_watching() -> str:
    _watch.set_mode("ask")
    return _ONLY_WHEN_ASKED + " Nothing is being watched in the background."


def set_mode(mode: str) -> str:
    _watch.set_mode("ask")
    return _ONLY_WHEN_ASKED + " There is no background screen watching."


def is_watching() -> bool:
    return _watch.is_watching()


def latest_frame(max_age: Optional[float] = None) -> Optional[ScreenFrame]:
    return _watch.get_latest(max_age=max_age)


def grab_now(full_screen: bool = False) -> Optional[ScreenFrame]:
    return _watch.grab_now(full_screen=full_screen)


def watch_for(phrase: str) -> str:
    return _watch.watch_for(phrase)
