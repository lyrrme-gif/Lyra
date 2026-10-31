"""The keyboard shortcuts that run an ACTION (Settings -> Shortcuts). No imports: settings_store, hotkeys, the tests
and (as a mirror) ui/src/design/shortcuts.ts all read this one list.

Each of them works ONLY through its own key combination (modifier(s) + one key): there is no voice command, no typed
command and no "work mode" behind them. The combo of each lives in the features settings as shortcut_<id>_combo
(\"\" = switched off), exactly like the recording / wheel / circle shortcuts."""
from __future__ import annotations

# id -> (label, default combo)
ACTIONS: dict[str, tuple[str, str]] = {
    "media_play":     ("Play / pause music", "ctrl+alt+space"),
    "media_next":     ("Next song", "ctrl+alt+right"),
    "media_prev":     ("Previous song", "ctrl+alt+left"),
    "volume_mute":    ("Mute / unmute", "ctrl+alt+m"),
    "open_music":     ("Open Music tab", "ctrl+alt+s"),
    "open_todo":      ("Open To-do tab", "ctrl+alt+t"),
    "open_calendar":  ("Open Calendar tab", "ctrl+alt+c"),
    "open_clipboard": ("Open Clipboard tab", "ctrl+alt+v"),
}

# actions that open a tab: id -> island view
OPEN_VIEW = {"open_music": "music", "open_todo": "todo", "open_calendar": "calendar", "open_clipboard": "clipboard"}


def key_of(action: str) -> str:
    return f"shortcut_{action}_combo"
