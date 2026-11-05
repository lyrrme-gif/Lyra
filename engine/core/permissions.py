"""Lyra — Permission system.

A safety boundary between command/tool execution and system resources.
Provides a confirmation layer for destructive operations so voice STT errors
cannot shut down the computer or delete files randomly.
"""

from enum import Enum
from engine.bridge import ui
import time
from engine.voice import voice

log = ui.log

_pending_text_confirmation: dict | None = None


class PermissionLevel(Enum):
    SAFE = "safe"        # Executed automatically with no prompt
    CONFIRM = "confirm"  # Requires user confirmation via voice or text input
    BLOCKED = "blocked"  # Never allowed to execute automatically


class PermissionManager:
    """Manages rules for action categorization and handles user confirmation prompts."""

    def __init__(self):
        # Default security mapping for functions
        self._rules = {
            # --- System Safe Actions ---
            "volume_up": PermissionLevel.SAFE,
            "volume_down": PermissionLevel.SAFE,
            "mute": PermissionLevel.SAFE,
            "pause_play": PermissionLevel.SAFE,
            "next_track": PermissionLevel.SAFE,
            "prev_track": PermissionLevel.SAFE,
            "lock_screen": PermissionLevel.SAFE,
            "take_screenshot": PermissionLevel.SAFE,
            "system_status": PermissionLevel.SAFE,
            "current_window": PermissionLevel.SAFE,

            # --- App Safe Actions ---
            "open_spotify": PermissionLevel.SAFE,
            "open_site": PermissionLevel.SAFE,
            "open_local_app": PermissionLevel.SAFE,
            "open_url": PermissionLevel.SAFE,

            # --- File Safe Actions ---
            "create_folder": PermissionLevel.SAFE,
            "create_text_file": PermissionLevel.SAFE,
            "list_directory": PermissionLevel.SAFE,
            "open_explorer": PermissionLevel.SAFE,

            # --- System Destructive Actions (CONFIRM) ---
            "system_power_action": PermissionLevel.SAFE,  # Shutdown is promoted below
            "cancel_shutdown": PermissionLevel.SAFE,        # Free action

            # --- File Destructive Actions (CONFIRM) ---
            "delete_path": PermissionLevel.CONFIRM,
            "rename_path": PermissionLevel.CONFIRM,

            # --- Interactive Actions (CONFIRM from Phase 2) ---
            "click_on_screen": PermissionLevel.CONFIRM,
            "double_click_on_screen": PermissionLevel.CONFIRM,
            "right_click_on_screen": PermissionLevel.CONFIRM,     # Confirm-before-click + preview
            "self_code_write": PermissionLevel.CONFIRM,     # Lyra editing her own source

            # --- Shortcut action types (used by shortcuts/executor.py) ---
            # These are separate ids from the ones above because a shortcut's
            # JSON action "type" field doesn't always match an actions/*.py
            # function name 1:1 (e.g. "open_app" vs "open_local_app").
            "open_app": PermissionLevel.SAFE,
            "open_folder": PermissionLevel.SAFE,
            "set_volume": PermissionLevel.SAFE,
            "wait": PermissionLevel.SAFE,
            "start_timer": PermissionLevel.SAFE,
            "set_app_volume": PermissionLevel.SAFE,
            # --- Agent tool ids (agent.py checks these before every step) ---
            "run_shortcut": PermissionLevel.SAFE,   # the shortcut executor re-checks each of its own actions
            "minimize": PermissionLevel.SAFE,
            "maximize": PermissionLevel.SAFE,
            "switch_window": PermissionLevel.SAFE,
            "snap": PermissionLevel.SAFE,
            "media_play": PermissionLevel.SAFE,
            "media_pause": PermissionLevel.SAFE,
            "close_window": PermissionLevel.CONFIRM,  # closes whatever is focused
            "type_text": PermissionLevel.CONFIRM,     # types into whatever is focused
            # Any shortcut action type NOT listed here (e.g. if someone hand-
            # edits shortcuts.json to add "delete_path" or a future dangerous
            # type) now falls through to the CONFIRM default below instead of
            # silently executing — this only matters once executor.py is
            # actually asking permissions.py first, see the fix there.
        }

    def check_permission(self, action_id: str, action_description: str, engine_context=None) -> tuple[bool, str]:
        """Verify permission for an action/tool.

        Args:
            action_id: Identifier of the action (e.g., "system_power_action")
            action_description: Natural language description of what's about to happen
            engine_context: Dict or object containing current run dependencies (like the voice recognizer)

        Returns:
            (allowed: bool, prompt_msg: str)
            - True if authorized to proceed
            - False if cancelled or denied
        """
        # A confirmed text-chat retry carries this short-lived internal flag.
        # It is only created by PermissionManager.resolve_text_confirmation().
        if isinstance(engine_context, dict) and engine_context.get("confirmed"):
            return True, ""
        # Screen clicks are ordinary UI actions. Handle every click variant
        # before the generic permission table so legacy CONFIRM entries or a
        # stale per-click preference cannot queue a confirmation prompt.
        if action_id in {"click_on_screen", "double_click_on_screen", "right_click_on_screen"}:
            return True, ""
        level = self._rules.get(action_id)
        # Shutdown is the only action that asks for confirmation. Keep explicit
        # BLOCKED rules; all other actions run without a confirmation prompt.
        try:
            from engine.tools.registry import get_tool
            registered_tool = get_tool(action_id)
            if registered_tool is not None and str(getattr(registered_tool, "permission_level", "")).strip().upper() == "BLOCKED":
                level = PermissionLevel.BLOCKED
        except Exception:
            pass
        if level == PermissionLevel.BLOCKED:
            pass
        elif action_id == "system_power_action" and "shut" in (action_description or "").lower():
            level = PermissionLevel.CONFIRM
        else:
            level = PermissionLevel.SAFE
        if level is None:
            # Not in the table above: trust the tool's own registry flag, so read-only tools
            # (get_notifications, media_next, ...) don't ask "Are you sure?". Unknown tools
            # and anything not marked SAFE still fall through to CONFIRM.
            try:
                from engine.tools.registry import get_tool
                t = get_tool(action_id)
                if t is not None and str(getattr(t, "permission_level", "")).strip().upper() == "SAFE":
                    level = PermissionLevel.SAFE
            except Exception:
                pass
            if level is None:
                level = PermissionLevel.CONFIRM

        if level == PermissionLevel.BLOCKED:
            log.warning(f"Blocked action execution request: {action_id}")
            return False, "This action has been blocked for system security, sir."

        if level == PermissionLevel.CONFIRM:
            log.info(f"Confirming action: '{action_id}' ({action_description})")
            return self._prompt_user_confirmation(action_description, engine_context)

        return True, ""

    @staticmethod
    def pending_text_confirmation() -> bool:
        return _pending_text_confirmation is not None and time.time() - _pending_text_confirmation.get("created",0) < 120

    @staticmethod
    def resolve_text_confirmation(answer: str):
        global _pending_text_confirmation
        item=_pending_text_confirmation
        _pending_text_confirmation=None
        if not item:
            return None
        a=(answer or '').strip().lower()
        yes=a in ("yes","y","yeah","yep","sure","ok","okay","confirm","go ahead","do it")
        if not yes:
            return "Okay, cancelled."
        try:
            result=item["callback"]()
            return getattr(result,"message",str(result))
        except Exception as e:
            return f"Confirmation action failed: {e}"

    def _prompt_user_confirmation(self, action_description: str, engine_context=None) -> tuple[bool, str]:
        """Prompt user to confirm a destructive or interactive action."""
        # Phase 2 screen clicks already phrase as “click …”
        if action_description.lower().startswith("click"):
            prompt_text = f"I'm about to {action_description}. Should I go ahead?"
        else:
            prompt_text = f"You're asking to {action_description}. Are you sure, sir?"

        # 1. Print and speak the prompt
        ui.print_chat("lyra", prompt_text)
        voice.speak(prompt_text)

        # 2. Check input mode. Typed input (text mode, or the island UI while the engine
        # is in voice mode) has no mic context, so it must use the typed-confirmation path.
        _typed = ui.INPUT_MODE == "text" or not (
            isinstance(engine_context, dict) and "recognizer" in engine_context and "source" in engine_context)
        if _typed:
            # Tauri/text chat cannot safely block on stdin. Queue the action and let
            # the next typed yes/no resolve it through main.process_command_chain().
            global _pending_text_confirmation
            callback = engine_context.get("confirm_callback") if isinstance(engine_context, dict) else None
            if callback:
                _pending_text_confirmation = {"description": action_description, "callback": callback, "created": time.time()}
                return False, "Awaiting typed confirmation."
            if ui.INPUT_MODE != "text":
                return False, "I can't confirm that from here, sir."
            try:
                raw = input("Confirm [yes/no] > ").strip().lower()
                return raw in ("yes", "y", "confirm", "ok", "go ahead"), "User cancelled the operation."
            except (EOFError, KeyboardInterrupt):
                return False, "Process interrupted."

        else:
            # Voice confirmation using the active mic context
            if not engine_context or "recognizer" not in engine_context or "source" not in engine_context:
                log.error("Permission confirm failed: missing mic context in voice mode.")
                return False, "Could not confirm via voice due to missing microphone context."

            recognizer = engine_context["recognizer"]
            source = engine_context["source"]

            # Listen loop specifically for y/n response
            for attempts in range(2):
                status, response = voice.listen(recognizer, source, timeout_seconds=5)
                if status == "ok":
                    cleaned = response.strip().lower()
                    if any(word in cleaned for word in ("yes", "yeah", "yep", "do it", "confirm", "go ahead")):
                        return True, ""
                    if any(word in cleaned for word in ("no", "nope", "stop", "cancel", "don't")):
                        return False, "Cancelled by user voice command."

                    # Unclear keyword
                    reprompt = "Please say yes to confirm or no to cancel."
                    ui.print_chat("lyra", reprompt)
                    voice.speak(reprompt)
                elif status == "timeout":
                    return False, "Confirmation timed out."
                else:
                    return False, "Voice check failed."

            return False, "Could not hear clear confirmation."
