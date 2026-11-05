"""Lyra — Shortcuts system (Phase 1)

Multi-action voice sequences triggered by user-defined phrases.
"""

from .manager import ShortcutManager
from .executor import ShortcutExecutor
from .builder import ShortcutWizard

# Global instances — the wizard is handed the SAME manager instance used
# for matching, so a shortcut created/edited/deleted through the wizard is
# usable immediately, with no restart needed to reload shortcuts.json.
_manager = ShortcutManager()
_executor = ShortcutExecutor()
_wizard = ShortcutWizard(_manager)


# ── built-in routines (installed once; edit or delete them in Settings > Shortcuts) ─────────
_DEFAULT_ROUTINES = {
    "gaming_mode": {
        "name": "Gaming mode",
        "triggers": ["gaming mode", "game mode", "start gaming mode", "lets game", "let's game"],
        "actions": [
            {"type": "open_app", "target": "riot client"},
            {"type": "open_app", "target": "discord"},
            {"type": "set_volume", "level": 70},
        ],
    },
    "focus_mode": {
        "name": "Focus mode",
        "triggers": ["focus mode", "start focus mode", "study mode", "let me focus"],
        "actions": [
            {"type": "set_volume", "level": 25},
            {"type": "start_timer", "command": "start a pomodoro"},
        ],
    },
    "good_night": {
        "name": "Good night",
        "triggers": ["good night", "goodnight", "night night", "i'm going to sleep"],
        "actions": [
            {"type": "set_volume", "level": 0},
            {"type": "lock_screen"},
        ],
    },
}


def _install_default_routines() -> None:
    """Create the built-in routines the first time only (a flag file remembers it, so a routine
    you delete or edit is never brought back)."""
    try:
        from engine.core import paths
        flag = paths.data_path("shortcuts", "routines_v1.flag")
        if flag.exists():
            return
        for sid, data in _DEFAULT_ROUTINES.items():
            _manager.shortcuts.setdefault(sid, data)
        _manager.save_shortcuts()
        flag.write_text("installed", encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        from engine.bridge.ui import log
        log.warning("default routines not installed: %s", e)


_install_default_routines()


def try_handle_shortcut_exact(command: str, engine_context=None) -> str | None:
    """Run a shortcut only if the phrase is exactly one of its triggers (no fuzzy guessing)."""
    shortcut = _manager.match_exact(command.strip().rstrip("?.! "))
    if not shortcut:
        return None
    _ok, message = _executor.execute_shortcut(shortcut["name"], shortcut["actions"], engine_context)
    return message


def try_handle_shortcut(command: str, engine_context=None) -> str | None:
    """Try to match and execute a shortcut.

    Priority:
    1. Exact match
    2. Fuzzy match (>75% similarity), unless the top two candidates are
       too close to call — then ask instead of guessing.

    Returns:
        Response message if shortcut matched and executed, None otherwise
    """
    # Try exact match first
    shortcut = _manager.match_exact(command)
    if shortcut:
        success, message = _executor.execute_shortcut(shortcut["name"], shortcut["actions"], engine_context)
        return message

    # Try fuzzy match
    shortcut = _manager.match_fuzzy(command, cutoff=0.75)
    if shortcut and shortcut.get("ambiguous"):
        names = " or ".join(f"'{n}'" for n, _ in shortcut["candidates"])
        return f"Did you mean {names}, sir? Say the exact name to run it, or try again."
    if shortcut:
        success, message = _executor.execute_shortcut(shortcut["name"], shortcut["actions"], engine_context)
        return message

    return None


def wizard_active() -> bool:
    """True while a create/edit/delete shortcut conversation is in progress."""
    return _wizard.active


def try_start_wizard(command: str) -> str | None:
    """Checks if `command` is a shortcut-management phrase ("create a
    shortcut", "edit shortcut X", "delete shortcut X", "list shortcuts").
    Returns the first reply if so, None otherwise. Only call when
    wizard_active() is False."""
    return _wizard.try_start(command)


def handle_wizard_turn(command: str) -> str:
    """Advances an in-progress wizard session by one turn. Only call when
    wizard_active() is True."""
    return _wizard.handle_turn(command)


def add_shortcut(shortcut_id: str, name: str, triggers: list, actions: list):
    """Add or update a shortcut."""
    _manager.add_shortcut(shortcut_id, name, triggers, actions)


def remove_shortcut(shortcut_id: str) -> bool:
    """Remove a shortcut."""
    return _manager.remove_shortcut(shortcut_id)


def list_shortcuts() -> list:
    """List all shortcuts."""
    return _manager.list_shortcuts()


def get_shortcut(shortcut_id: str) -> dict | None:
    """Get a shortcut by ID."""
    return _manager.get_shortcut(shortcut_id)
