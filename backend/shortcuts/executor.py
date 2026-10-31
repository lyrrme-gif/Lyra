"""Lyra — Shortcut executor for running multi-action sequences."""

import time
import ui
from actions import apps, files, system
from permissions import PermissionManager

log = ui.log

_permission_manager = PermissionManager()


class ShortcutExecutor:
    """Executes shortcut action sequences."""

    def __init__(self):
        # Map action types to their execution functions
        self.action_handlers = {
            "open_app": self._execute_open_app,
            "open_site": self._execute_open_site,
            "open_url": self._execute_open_url,
            "open_spotify": self._execute_open_spotify,
            "open_folder": self._execute_open_folder,
            "set_volume": self._execute_set_volume,
            "volume_up": self._execute_volume_up,
            "volume_down": self._execute_volume_down,
            "mute": self._execute_mute,
            "lock_screen": self._execute_lock_screen,
            "take_screenshot": self._execute_take_screenshot,
            "wait": self._execute_wait,
            "start_timer": self._execute_start_timer,
            "set_app_volume": self._execute_set_app_volume,
        }

    def execute_shortcut(self, shortcut_name: str, actions: list, engine_context=None) -> tuple[bool, str]:
        """Execute a shortcut's action sequence.

        Args:
            shortcut_name: Name of the shortcut for logging
            actions: List of action dicts
            engine_context: Dict with {'recognizer', 'source'} for voice
                confirmations, passed straight through from main.py — same
                object actions/__init__.py already gets for its own
                permission checks. Without it, any CONFIRM-level action in a
                shortcut is refused rather than silently skipped or run.

        Returns:
            (success, message) tuple
        """
        log.info(f"Executing shortcut: {shortcut_name} ({len(actions)} actions)")
        ui.set_state("thinking")

        results = []
        failed_actions = []

        for i, action in enumerate(actions, 1):
            action_type = action.get("type")
            if not action_type:
                log.warning(f"Action {i} has no type, skipping")
                continue

            handler = self.action_handlers.get(action_type)
            if not handler:
                log.warning(f"Unknown action type: {action_type}")
                failed_actions.append(f"Action {i}: unknown type '{action_type}'")
                continue

            # Every action goes through the same permission check the direct
            # command path uses (actions/__init__.py) — previously shortcuts
            # called these handlers straight through, bypassing SAFE/CONFIRM/
            # BLOCKED entirely. Today's 3 built-in shortcuts are all SAFE so
            # this is a no-op for them, but it matters the moment any
            # shortcut (built-in or user-created) includes something like
            # delete_path or system_power_action.
            allowed, reason = _permission_manager.check_permission(
                action_type, f"run '{action_type}' as part of the '{shortcut_name}' shortcut",
                engine_context,
            )
            if not allowed:
                log.info(f"Action {i} ({action_type}) denied by permission check: {reason}")
                results.append(f"✗ Skipped {action_type}: {reason or 'not confirmed'}")
                failed_actions.append(f"Action {i} ({action_type}) not confirmed")
                continue

            try:
                success, message = handler(action)
                if success:
                    results.append(f"✓ {message}")
                else:
                    results.append(f"✗ {message}")
                    failed_actions.append(message)

                # Small delay between actions for stability
                time.sleep(0.3)
            except Exception as e:
                error_msg = f"Action {i} ({action_type}) failed: {e}"
                log.error(error_msg)
                results.append(f"✗ {error_msg}")
                failed_actions.append(error_msg)

        ui.set_state("idle")

        # Build summary message
        total = len(actions)
        succeeded = total - len(failed_actions)

        if not failed_actions:
            summary = f"Shortcut '{shortcut_name}' completed successfully, sir. {succeeded}/{total} actions executed."
        else:
            summary = f"Shortcut '{shortcut_name}' completed with {len(failed_actions)} error(s), sir. {succeeded}/{total} actions succeeded."

        return len(failed_actions) == 0, summary

    # Action handlers
    def _execute_open_app(self, action: dict) -> tuple[bool, str]:
        """Open a local application."""
        target = action.get("target", "").lower()
        if apps.open_local_app(target):
            return True, f"Opened {target}"
        return False, f"Failed to open {target}"

    def _execute_open_site(self, action: dict) -> tuple[bool, str]:
        """Open a website."""
        target = action.get("target", "").lower()
        query = action.get("query", "")
        apps.open_site(target, query)
        return True, f"Opened {target}"

    def _execute_open_url(self, action: dict) -> tuple[bool, str]:
        """Open a URL."""
        url = action.get("url", "")
        if not url:
            return False, "No URL provided"
        apps.open_url(url)
        return True, f"Opened {url}"

    def _execute_open_spotify(self, action: dict) -> tuple[bool, str]:
        """Open Spotify, optionally with a search query."""
        query = action.get("query", "")
        apps.open_spotify(query)
        if query:
            return True, f"Opened Spotify with query: {query}"
        return True, "Opened Spotify"

    def _execute_open_folder(self, action: dict) -> tuple[bool, str]:
        """Open a folder in Explorer."""
        path = action.get("path", "")
        if not path:
            return False, "No path provided"
        files.open_explorer_at(path)
        return True, f"Opened folder: {path}"

    def _execute_set_volume(self, action: dict) -> tuple[bool, str]:
        """Set volume to a specific percentage (0–100)."""
        level = int(action.get("level", 50))
        ok, msg = system.set_volume_level(level)
        return ok, msg

    def _execute_volume_up(self, action: dict) -> tuple[bool, str]:
        """Increase volume."""
        steps = action.get("steps", 5)
        system.adjust_volume("up", steps=steps)
        return True, "Volume up"

    def _execute_volume_down(self, action: dict) -> tuple[bool, str]:
        """Decrease volume."""
        steps = action.get("steps", 5)
        system.adjust_volume("down", steps=steps)
        return True, "Volume down"

    def _execute_mute(self, action: dict) -> tuple[bool, str]:
        """Toggle mute."""
        system._send_media_key(system.VK_VOLUME_MUTE)
        return True, "Toggled mute"

    def _execute_lock_screen(self, action: dict) -> tuple[bool, str]:
        """Lock the workstation."""
        system.lock_screen()
        return True, "Locked screen"

    def _execute_take_screenshot(self, action: dict) -> tuple[bool, str]:
        """Take a screenshot."""
        path = system.take_screenshot()
        if not path.startswith("failed"):
            return True, f"Screenshot: {path}"
        return False, path

    def _execute_start_timer(self, action: dict) -> tuple[bool, str]:
        """Run a timer / pomodoro / reminder phrase through Lyra's timer engine."""
        import timers
        phrase = str(action.get("command") or "").strip()
        if not phrase:
            return False, "No timer command given"
        reply = timers.handle_command(phrase)
        return (True, reply) if reply else (False, f"Timers did not understand '{phrase}'")

    def _execute_set_app_volume(self, action: dict) -> tuple[bool, str]:
        """Set one app's volume (0-100 percent), e.g. Spotify to 20."""
        import app_volume
        app = str(action.get("app") or "").strip()
        if not app:
            return False, "No app given"
        try:
            level = max(0, min(100, int(action.get("level", 50))))
            name, _ = app_volume.set_app_by_name(app, level / 100.0)  # takes 0.0-1.0, returns (name, level)
            return True, f"Set {name} to {level} percent"
        except Exception as e:  # not making sound right now, unknown app...
            return False, f"Couldn't set {app}: {e}"

    def _execute_wait(self, action: dict) -> tuple[bool, str]:
        """Wait for a specified duration."""
        seconds = action.get("seconds", 1)
        time.sleep(seconds)
        return True, f"Waited {seconds}s"
