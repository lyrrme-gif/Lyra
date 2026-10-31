"""Registers guide mode (guide.py) and the acting guide (guide_act.py) as agent tools.

main.py runs the LLM planner before the direct-command router (config.LLM_FIRST), so the planner needs
to know these exist: otherwise "show me where the volume icon is" would be answered as plain chat.
"""
from tools.base import ToolDefinition, ToolParameter, ToolCategory, ToolResult
from tools.registry import register_tool

P = ToolParameter


def _guide_screen(request: str) -> ToolResult:
    try:
        import guide
        reply = guide.try_guide(request)
        if reply:
            return ToolResult(reply == "Okay, follow me.", reply)
        return ToolResult(False, "That is not something I can point out on screen.")
    except Exception as e:  # noqa: BLE001
        return ToolResult(False, f"{type(e).__name__}: {e}")


def _guide_act(task: str) -> ToolResult:
    """Lyra presses things on screen for the user. Everything that keeps this safe (the guide_level setting,
    asking before steps, Esc, no passwords / payments / admin prompts) is enforced inside guide_act."""
    try:
        import re
        import guide
        t = str(task).strip().rstrip(".?! ")
        reply = guide.try_guide("do it for me" if re.fullmatch(r"(?:do\s+)?(?:it|this|that)", t, re.I)
                                else f"{t} for me on screen")
        return ToolResult(bool(reply), reply or "I can't do that on screen.")
    except Exception as e:  # noqa: BLE001
        return ToolResult(False, f"{type(e).__name__}: {e}")


def _guide_control(action: str) -> ToolResult:
    try:
        import guide
        word = {"next": "next", "repeat": "repeat", "stop": "stop", "yes": "yes", "no": "no"}.get(
            str(action).strip().lower(), "")
        reply = guide.try_guide(word) if word else None
        return ToolResult(bool(reply), reply or "No screen guide is running.")
    except Exception as e:  # noqa: BLE001
        return ToolResult(False, f"{type(e).__name__}: {e}")


register_tool(ToolDefinition(
    "guide_screen", "Screen guide",
    "Lyra's on-screen mascot walks around the screen and points at things while she explains. Use for "
    "'show me where X is', 'how do I ... in <app>', 'explain this screen', 'walk me through ...', "
    "'what is this button'. Looks only: never clicks or types.",
    ToolCategory.SYSTEM,
    [P("request", "string", "The user's request, in their own words")], "SAFE", _guide_screen))

register_tool(ToolDefinition(
    "guide_act", "Screen guide that acts",
    "Lyra clicks, scrolls and switches tabs in the app on screen FOR the user: 'open the privacy tab for me', "
    "'do it for me'. Use only when the user asks her to do it herself. It follows the user's Screen guide level "
    "setting (show only / ask before each step / whole task), shows her cursor, can be stopped with Esc, and "
    "refuses password, payment and administrator screens.",
    ToolCategory.SYSTEM,
    [P("task", "string", "What to do on screen, in the user's words")], "SAFE", _guide_act))

register_tool(ToolDefinition(
    "guide_control", "Screen guide control",
    "Control a screen guide that is running: next step, repeat the step, stop, or answer yes / no when she asks "
    "before a step.",
    ToolCategory.SYSTEM,
    [P("action", "string", "next, repeat, stop, yes or no")], "SAFE", _guide_control))
