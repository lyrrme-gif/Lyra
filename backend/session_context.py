"""Lyra — short-lived session context (in-memory only).

Holds transient state for the *current process run* so that follow-up
phrases such as "play it again", "pause that", or "click it again" still
make sense after the user has done other things in the same session.

Design notes:
- Nothing here is written to disk. Long-term memory lives in memory.py / SQLite.
- A single global SessionContext instance is created at import time.
- The brain (brain.py) can pull a short text summary via summary_for_brain()
  and inject it into the LLM prompt so the model understands recent context.
"""

from __future__ import annotations  # Enables modern type hints (str | None)

import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SessionContext:
    """Holds all the short-lived facts about the current run.

    Fields are grouped by domain (media, focus, clicks, generic log).
    """

    # ----- Media playback state -----
    last_media_action: Optional[str] = None  # "play" | "pause" | "toggle" | "next" | "prev"
    last_media_state: Optional[str] = None   # "playing" | "paused" | "unknown"
    last_media_window: Optional[str] = None  # Title of the window that was playing media
    last_media_at: float = 0.0               # Unix timestamp of the last media action

    # ----- Window / app focus -----
    last_window_title: Optional[str] = None  # Title of the last focused window
    last_app_opened: Optional[str] = None    # Name of the last app we launched

    # ----- Screen clicks (vision-assisted) -----
    last_click_label: Optional[str] = None   # Short label of the last clicked UI element
    last_click_desc: Optional[str] = None    # Longer description of that element

    # ----- Screen capture context for Agent/vision -----
    last_screen_path: Optional[str] = None
    last_screen_mode: Optional[str] = None
    last_screen_at: float = 0.0

    # ----- Rolling log of recent actions (for LLM / debugging) -----
    recent: list[dict[str, Any]] = field(default_factory=list)
    _max_recent: int = 20  # Hard limit; oldest entries are dropped beyond this

    # ----- Pending clarification (so "spotify" after "where should I play X?" works) -----
    pending_intent: Optional[str] = None   # original user request awaiting detail
    pending_kind: Optional[str] = None     # "play_where" | "general" | ...
    pending_at: float = 0.0
    pending_question: str = ""            # what Lyra actually asked (so a bare 'yes' has context)

    def note(self, kind: str, **kwargs) -> None:
        """Append a generic event to the recent-actions log.

        Args:
            kind: Short category string ("media", "window", "app", "click", …)
            **kwargs: Arbitrary extra fields stored with the event
        """
        entry = {"kind": kind, "ts": time.time(), **kwargs}
        self.recent.append(entry)
        # Keep the list bounded so memory cannot grow without limit
        if len(self.recent) > self._max_recent:
            del self.recent[: len(self.recent) - self._max_recent]

    def note_media(self, action: str, window_title: str | None = None, state: str | None = None) -> None:
        """Record a media-related action and update derived state.

        Automatically infers playing/paused state from the action when
        an explicit state is not supplied.
        """
        self.last_media_action = action
        if state:
            self.last_media_state = state
        elif action == "play":
            self.last_media_state = "playing"
        elif action == "pause":
            self.last_media_state = "paused"
        elif action == "toggle":
            # Flip the known state; if unknown leave it unknown
            if self.last_media_state == "playing":
                self.last_media_state = "paused"
            elif self.last_media_state == "paused":
                self.last_media_state = "playing"
            else:
                self.last_media_state = "unknown"
        if window_title:
            self.last_media_window = window_title
            self.last_window_title = window_title
        self.last_media_at = time.time()
        # Also push into the generic recent log
        self.note("media", action=action, window=window_title, state=self.last_media_state)

    def set_pending(self, intent: str, kind: str = "general", question: str = "") -> None:
        """Remember that we asked the user a follow-up about `intent`."""
        self.pending_intent = (intent or "").strip() or None
        self.pending_kind = kind if self.pending_intent else None
        self.pending_at = time.time() if self.pending_intent else 0.0
        self.pending_question = (question or "").strip()[:240] if self.pending_intent else ""
        if self.pending_intent:
            self.note("pending", intent=self.pending_intent, kind=kind)

    def clear_pending(self) -> None:
        self.pending_intent = None
        self.pending_kind = None
        self.pending_at = 0.0
        self.pending_question = ""

    def take_pending(self, max_age_sec: float = 120.0) -> tuple[Optional[str], Optional[str]]:
        """Return and clear pending if still fresh."""
        if not self.pending_intent:
            return None, None
        if time.time() - self.pending_at > max_age_sec:
            self.clear_pending()
            return None, None
        intent, kind = self.pending_intent, self.pending_kind
        self.clear_pending()
        return intent, kind

    def has_pending(self, max_age_sec: float = 120.0) -> bool:
        if not self.pending_intent:
            return False
        if time.time() - self.pending_at > max_age_sec:
            self.clear_pending()
            return False
        return True

    def note_window(self, title: str | None) -> None:
        """Record that a particular window title is now focused."""
        if title:
            self.last_window_title = title
            self.note("window", title=title)

    def note_app(self, name: str) -> None:
        """Record that we just opened (or focused) an application."""
        self.last_app_opened = name
        self.note("app", name=name)

    def note_click(self, label: str, desc: str = "") -> None:
        """Record a vision-assisted screen click target."""
        self.last_click_label = label
        self.last_click_desc = desc or label
        self.note("click", label=label, desc=desc)

    def note_screen_capture(self, path: str, mode: str = "full") -> None:
        """Remember the latest screenshot for Agent/vision follow-ups."""
        self.last_screen_path = path or None
        self.last_screen_mode = mode
        self.last_screen_at = time.time()
        self.note("screen_capture", path=path, mode=mode)


    def summary_for_brain(self, max_items: int = 8) -> str:
        """Produce a short plain-text block the LLM can use for
        resolving pronouns / references like 'again', 'that', 'it'.

        Returns an empty string when there is nothing useful to report,
        so the caller can simply skip injecting context.
        """
        lines = []
        if self.last_media_window or self.last_media_action:
            lines.append(
                f"- Last media: action={self.last_media_action or '?'}, "
                f"state={self.last_media_state or '?'}, "
                f"window={self.last_media_window or 'unknown'}"
            )
        if self.last_window_title:
            lines.append(f"- Last focused window: {self.last_window_title}")
        if self.last_app_opened:
            lines.append(f"- Last app opened: {self.last_app_opened}")
        if self.last_click_label:
            lines.append(f"- Last click target: {self.last_click_label}")
        # Add a few of the most recent generic events (skip media — already covered)
        for e in self.recent[-max_items:]:
            kind = e.get("kind")
            if kind == "media":
                continue
            lines.append(f"- Recent {kind}: { {k: v for k, v in e.items() if k not in ('kind', 'ts')} }")
        if not lines:
            return ""
        return "Session context (same run only):\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level singleton — every other module imports get() to access it
# ---------------------------------------------------------------------------
_ctx = SessionContext()


def get() -> SessionContext:
    """Return the global SessionContext instance for the current process."""
    return _ctx


def current_window_title() -> str:
    """Best-effort query of the currently focused window title.

    Uses pygetwindow. Returns an empty string on any failure so callers
    never have to handle exceptions themselves.
    """
    try:
        import pygetwindow as gw
        win = gw.getActiveWindow()
        if win and win.title:
            return (win.title or "").strip()
    except Exception:
        pass
    return ""


def refocus_media_window() -> bool:
    """Try to bring the last media window (or a known media player) to the front.

    Returns True if a window was successfully focused, False otherwise.
    Used when the user says "play it again" after the media window lost focus.
    """
    title = _ctx.last_media_window or ""
    try:
        from actions.apps import focus_window, _find_window
        # First try the exact title we recorded earlier
        if title and focus_window(title):
            return True
        # Heuristic fallbacks for common media apps / sites
        for hint in ("YouTube", "Netflix", "VLC", "Movies", "Spotify", "Chrome", "Edge", "Firefox"):
            if _find_window(hint):
                return focus_window(hint)
    except Exception:
        pass
    return False
