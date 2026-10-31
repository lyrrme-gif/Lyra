"""Lyra — App control tools (open apps, websites, etc.)"""

from tools.base import ToolDefinition, ToolParameter, ToolCategory, ToolResult
from tools.registry import register_tool
from actions import apps


# Tool: Open Local App
def _open_app_impl(app_name: str) -> ToolResult:
    """Open a local application."""
    app_name = app_name.lower().strip()
    success = apps.open_local_app(app_name)
    if success:
        return ToolResult(True, f"Opened {app_name}")
    return ToolResult(False, f"Failed to open {app_name}")


register_tool(ToolDefinition(
    id="open_app",
    name="Open Application",
    description="Open a local desktop application (e.g., Spotify, Discord, VS Code, Chrome)",
    category=ToolCategory.APP,
    parameters=[
        ToolParameter("app_name", "string", "Name of the app (spotify, discord, code, chrome, etc.)")
    ],
    permission_level="SAFE",
    execute=_open_app_impl
))


# Tool: Open Website
def _open_site_impl(site_name: str, query: str = "") -> ToolResult:
    """Open a website, optionally with a search query."""
    site_name = site_name.lower().strip()
    apps.open_site(site_name, query)
    if query:
        # This tool result is also used as Lyra's spoken reply. Keep it
        # conversational instead of exposing the internal action status.
        return ToolResult(True, f"Searching {site_name.title()} for {query}.")
    return ToolResult(True, f"Opened {site_name}")


register_tool(ToolDefinition(
    id="open_site",
    name="Open Website",
    description="Open a website (YouTube, Google, Gmail, etc.), optionally with a search query",
    category=ToolCategory.WEB,
    parameters=[
        ToolParameter("site_name", "string", "Name of the site (youtube, google, gmail, etc.)"),
        ToolParameter("query", "string", "Optional search query", required=False, default="")
    ],
    permission_level="SAFE",
    execute=_open_site_impl
))


# Tool: Open URL
def _open_url_impl(url: str) -> ToolResult:
    """Open an arbitrary URL."""
    if not url.startswith(("http://", "https://")):
        return ToolResult(False, "URL must start with http:// or https://")
    apps.open_url(url)
    return ToolResult(True, f"Opened {url}")


register_tool(ToolDefinition(
    id="open_url",
    name="Open URL",
    description="Open any web URL in the default browser",
    category=ToolCategory.WEB,
    parameters=[
        ToolParameter("url", "string", "Full URL (must start with http:// or https://)")
    ],
    permission_level="SAFE",
    execute=_open_url_impl
))


# Tool: Open Spotify
def _open_spotify_impl(query: str = "") -> ToolResult:
    """Open Spotify, optionally with a search query."""
    apps.open_spotify(query)
    if query:
        return ToolResult(True, f"Opened Spotify searching for: {query}")
    return ToolResult(True, "Opened Spotify")


register_tool(ToolDefinition(
    id="open_spotify",
    name="Open Spotify",
    description="Open Spotify music app, optionally with a search query",
    category=ToolCategory.MEDIA,
    parameters=[
        ToolParameter("query", "string", "Optional search query (song, artist, playlist)", required=False, default="")
    ],
    permission_level="SAFE",
    execute=_open_spotify_impl
))


# Tool: Click on Screen
def _click_on_screen_impl(description: str) -> ToolResult:
    """Click on screen and preserve the action's verification result."""
    # The agent already asked the user to confirm this click (typed or spoken), so skip
    # the inner prompt, which has no way to hear an answer from here.
    msg = str(apps.click_on_screen(description, engine_context={"confirmed": True}))
    low = msg.lower()
    success = not any(x in low for x in ("couldn't", "could not", "not sure", "failed", "didn't", "did not click", "did not right-click", "did not double-click", "stopped", "isn't configured"))
    verified = True if "verified" in low else False if success else False
    return ToolResult(success, msg, verified=verified)


register_tool(ToolDefinition(
    id="click_on_screen",
    name="Click on Screen",
    description="Click on a UI element visible on screen using vision AI",
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter("description", "string", "Description of what to click (e.g., 'the play button')")
    ],
    permission_level="SAFE",
    execute=_click_on_screen_impl
))
