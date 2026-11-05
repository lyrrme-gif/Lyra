"""Registers Gmail/Calendar (google_workspace.py) as an agent tool. main.py runs the LLM planner before
the direct-command router, so the planner must know this exists or "what's my next event" is chat."""
from engine.tools.base import ToolDefinition, ToolParameter, ToolCategory, ToolResult
from engine.tools.registry import register_tool


def _google(request: str) -> ToolResult:
    try:
        from engine.features import google_workspace
        reply = google_workspace.try_google(request)
        return ToolResult(bool(reply), reply or "That isn't something Google can answer here.")
    except Exception as e:  # noqa: BLE001
        return ToolResult(False, f"{type(e).__name__}: {e}")


register_tool(ToolDefinition(
    "google_workspace", "Google Calendar and Gmail (read-only)",
    "Read the user's Google Calendar and Gmail: next event, today's agenda, latest/unread emails, summarize an "
    "email, draft a reply (text only, never sent), connect/disconnect Google.",
    ToolCategory.SYSTEM,
    [ToolParameter("request", "string", "The user's request, in their own words")], "SAFE", _google))
