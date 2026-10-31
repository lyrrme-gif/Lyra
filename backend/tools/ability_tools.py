"""Registers Lyra's local abilities as agent tools.

The LLM agent (agent.py) picks tools from this registry for phrasings no pattern catches, e.g.
"yo how much juice does my laptop have left" -> battery_status. Each tool reuses the same code
as the direct commands (actions/quick.py, actions/notes.py), so behaviour is identical.
"""
from tools.base import ToolDefinition, ToolParameter, ToolCategory, ToolResult
from tools.registry import register_tool

P = ToolParameter


def _quick(phrase: str) -> ToolResult:
    try:
        from actions import quick
        reply = quick.try_quick(phrase)
        if reply:
            return ToolResult(True, reply, verified=True)
        return ToolResult(False, "That did not produce an answer.")
    except Exception as e:  # noqa: BLE001
        return ToolResult(False, f"{type(e).__name__}: {e}")


def _simple(tid, name, desc, phrase, cat=ToolCategory.SYSTEM):
    register_tool(ToolDefinition(tid, name, desc, cat, [], "SAFE", lambda: _quick(phrase)))


_simple("battery_status", "Battery", "Battery percentage, charging state and time left.", "what's my battery")
_simple("disk_space", "Disk space", "Free and total space on every drive.", "how much disk space do I have")
_simple("active_window", "Active window", "Title of the window the user is in right now.", "which window is active")
_simple("time_left", "Timer time left", "How much time is left on running timers.", "how much time is left")
_simple("pc_load", "PC load", "Why the PC is slow: CPU and memory use and the busiest programs.", "why is my pc slow")
_simple("morning_brief", "Morning brief", "Greeting with weather, timers, to-do list and battery warning.", "give me my morning brief")

register_tool(ToolDefinition(
    "weather", "Weather", "Current weather and today's high/low; optional city (default: the user's own).",
    ToolCategory.WEB, [P("city", "string", "City name; empty for the user's location", False, "")], "SAFE",
    lambda city="": _quick(f"what's the weather in {city}" if city else "what's the weather")))

register_tool(ToolDefinition(
    "calculate", "Calculate", "Do arithmetic or a percentage, e.g. '15 percent of 240' or '12 times 7'.",
    ToolCategory.SYSTEM, [P("expression", "string", "The calculation in words or symbols")], "SAFE",
    lambda expression: _quick(expression if "percent" in str(expression).lower() or "%" in str(expression) else f"calculate {expression}")))

register_tool(ToolDefinition(
    "convert_units", "Convert", "Convert units (miles/km, kg/lb, C/F...) or currency (USD/EUR/MAD...).",
    ToolCategory.SYSTEM,
    [P("value", "number", "The amount"), P("from_unit", "string", "e.g. miles, kg, usd"), P("to_unit", "string", "e.g. km, lb, eur")], "SAFE",
    lambda value, from_unit, to_unit: _quick(f"convert {value} {from_unit} to {to_unit}")))


def _notes(phrase):
    try:
        from actions import notes
        r = notes.try_notes(phrase)
        return ToolResult(bool(r), r or "Could not do that.", verified=bool(r))
    except Exception as e:  # noqa: BLE001
        return ToolResult(False, f"{type(e).__name__}: {e}")


register_tool(ToolDefinition("add_note", "Add note", "Save a quick note for the user.", ToolCategory.MEMORY,
                             [P("text", "string", "The note")], "SAFE", lambda text: _notes(f"take a note: {text}")))
register_tool(ToolDefinition("read_notes", "Read notes", "Read back the user's latest notes.", ToolCategory.MEMORY,
                             [], "SAFE", lambda: _notes("what are my notes")))
register_tool(ToolDefinition("list_add", "Add to list", "Add an item to the user's to-do or shopping list.", ToolCategory.MEMORY,
                             [P("item", "string", "What to add"), P("list", "string", "'to-do' or 'shopping' (or another list name)", False, "to-do")],
                             "SAFE", lambda item, list="to-do": _notes(f"add {item} to my {list} list")))
register_tool(ToolDefinition("list_read", "Read list", "Read an item list (to-do, shopping...).", ToolCategory.MEMORY,
                             [P("list", "string", "List name", False, "to-do")], "SAFE", lambda list="to-do": _notes(f"what's on my {list} list")))
register_tool(ToolDefinition("list_remove", "Remove from list", "Remove an item from a list.", ToolCategory.MEMORY,
                             [P("item", "string", "Item to remove"), P("list", "string", "List name", False, "to-do")],
                             "SAFE", lambda item, list="to-do": _notes(f"remove {item} from my {list} list")))
register_tool(ToolDefinition("clipboard_ai", "Clipboard AI",
                             "Translate, summarize, fix, rewrite, shorten, explain or simplify the text the user copied.",
                             ToolCategory.SYSTEM,
                             [P("action", "string", "translate | summarize | fix | rewrite | shorten | explain | simplify"),
                              P("language", "string", "Target language when translating", False, "")], "SAFE",
                             lambda action, language="": _notes(f"{action} what i copied" + (f" to {language}" if language else ""))))
