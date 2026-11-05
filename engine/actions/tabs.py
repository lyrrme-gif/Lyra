"""Voice: open an island tab, or the tab wheel.

  "show the tab wheel" / "open the radial menu" / "all tabs"     -> the wheel appears in the middle of the screen
  "open the timer tab" / "show me the stats page" / "go to the calendar tab"
  "switch to chat" / "go to wallpaper"                            -> that tab

Deliberately strict. "open music" or "open camera" may mean "launch that app", which other
handlers own, so "open/show" needs the word tab, page, view or panel after the name. Only "switch
to" and "go to" work without it, and only for the names below. The ids are the island's view names
(ui/src/core/destinations.ts); ui_bridge.OPEN_VIEWS lists the ones the island accepts.
Study, Sounds, Controls and Translate are no longer tabs: they cannot be opened by name here, but their commands
("set Spotify volume to 50", "translate hello to French", "quiz me", "turn off bluetooth") still run and
the backend still opens those views to show the result (ui_bridge.OPEN_VIEWS keeps them).
"""

from __future__ import annotations

import re

# view id -> (name Lyra says back, spoken names)
TABS: dict[str, tuple[str, tuple[str, ...]]] = {
    "overview": ("Mochi", ("mochi", "home", "my mochi", "mochi home")),
    "music": ("Music", ("music", "player", "now playing")),
    "prompt": ("Chat", ("chat", "typing", "type")),
    "timer": ("Timer", ("timer", "timers", "pomodoro")),
    "calendar": ("Calendar", ("calendar", "agenda", "schedule")),
    "todo": ("To-do", ("to-do", "todo", "to do", "tasks", "task list", "checklist",
                       "to-do list", "todo list", "to do list")),
    "weather": ("Weather", ("weather", "forecast")),
    "record": ("Record", ("record", "recording", "screen record", "screen recording")),
    "stats": ("Stats", ("stats", "statistics", "performance")),
    "recognition": ("Recognize", ("recognize", "recognition", "song recognition")),
    "clipboard": ("Clipboard", ("clipboard",)),
    "camera": ("Camera", ("camera", "mirror")),
    "wallpaper": ("Wallpaper", ("wallpaper", "wallpapers", "background")),
    "apps": ("Apps", ("apps", "my apps", "whatsapp", "spotify library")),
}

_NAME_TO_VIEW = {name: view for view, (_, names) in TABS.items() for name in names}

_WHEEL = re.compile(
    r"^(?:(?:show|open|bring\s+up|pull\s+up)(?:\s+me)?\s+(?:the\s+|my\s+)?)?"
    r"(?:(?:tab|radial|island|lyra)\s+(?:wheel|menu|circle)|wheel|all\s+(?:the\s+|my\s+)?tabs|tabs)$"
)
_TAB = re.compile(
    r"^(?:open|show|bring\s+up|pull\s+up|go\s+to|switch\s+to|take\s+me\s+to)(?:\s+me)?\s+(?:the\s+|my\s+)?"
    r"(?P<name>[a-z][a-z\- ]{1,24}?)\s+(?:tab|page|view|panel)$"
)
# "open my todo list", "open the timer", "open weather", "open my calendar", "open clipboard", "open stats",
# "open wallpaper", "open home": these are Lyra's own
# tabs, so no "tab" is needed (not Music / Camera / Record / Apps: those names are also real apps) ("show ..." is left alone: "show me the weather" is a question to answer).
_OWN = re.compile(
    r"^(?:open|go\s+to|switch\s+to|bring\s+up|pull\s+up|take\s+me\s+to)(?:\s+me)?\s+(?:the\s+|my\s+)?"
    r"(?P<name>to-?do(?:\s+list)?|to\s+do(?:\s+list)?|task\s+list|timer|weather|calendar|clipboard|stats|statistics|"
    r"wallpapers?|home|mochi)$"
)
_BARE = re.compile(r"^(?:go\s+to|switch\s+to)\s+(?:the\s+|my\s+)?(?P<name>[a-z][a-z\- ]{1,24}?)$")


def resolve(low: str) -> tuple[str, str | None] | None:
    """('wheel', None) or ('tab', view id) for an already-lowercased command, else None."""
    if _WHEEL.match(low):
        return ("wheel", None)
    if re.match(r"^(?:go|take\s+me)\s+home$", low):
        return ("tab", "overview")
    m = _TAB.match(low) or _BARE.match(low) or _OWN.match(low)
    if m:
        view = _NAME_TO_VIEW.get(m.group("name").strip())
        if view:
            return ("tab", view)
    return None


def try_tabs(low: str) -> str | None:
    """Handle a spoken tab command; returns what Lyra says, or None if it is not one."""
    hit = resolve(low)
    if not hit:
        return None
    kind, view = hit
    try:
        from engine.bridge import ui_bridge
        if kind == "wheel":
            ui_bridge.push_wheel("show")
            return "Here are your tabs."
        ui_bridge.push_open_view(view or "")
        return f"Here's the {TABS[view or ''][0]} tab."
    except Exception:
        return "I couldn't open that tab."
