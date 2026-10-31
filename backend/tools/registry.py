"""Lyra — Tool registry for managing and calling tools."""

from typing import Optional
import ui
from .base import ToolDefinition, ToolResult

log = ui.log


class ToolRegistry:
    """Central registry for all tools."""

    def __init__(self):
        self.tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition):
        """Register a tool."""
        if tool.id in self.tools:
            log.warning(f"Tool '{tool.id}' already registered, overwriting")
        self.tools[tool.id] = tool
        log.info(f"Registered tool: {tool.id} ({tool.category.value})")

    def get_tool(self, tool_id: str) -> Optional[ToolDefinition]:
        """Get a tool by ID."""
        return self.tools.get(tool_id)

    def call_tool(self, tool_id: str, **kwargs) -> ToolResult:
        """Execute a tool by ID with parameters.

        Args:
            tool_id: Tool identifier
            **kwargs: Tool parameters

        Returns:
            ToolResult with success status and message
        """
        tool = self.get_tool(tool_id)
        if not tool:
            log.error(f"Tool not found: {tool_id}")
            return ToolResult(False, f"Unknown tool: {tool_id}")

        try:
            # Validate required parameters
            for param in tool.parameters:
                if param.required and param.name not in kwargs:
                    return ToolResult(
                        False,
                        f"Missing required parameter: {param.name}"
                    )

            # Execute tool
            log.info(f"Executing tool: {tool_id} with {kwargs}")
            result = tool.execute(**kwargs)

            # Ensure result is ToolResult
            if not isinstance(result, ToolResult):
                # Legacy support for (bool, str) tuples
                if isinstance(result, tuple) and len(result) == 2:
                    success, message = result
                    result = ToolResult(success, message)
                else:
                    result = ToolResult(True, str(result))

            return result

        except Exception as e:
            log.error(f"Tool execution failed: {tool_id} - {e}")
            return ToolResult(False, f"Tool error: {e}")

    def list_tools(self, category: Optional[str] = None) -> list[ToolDefinition]:
        """List all tools, optionally filtered by category."""
        if category:
            return [t for t in self.tools.values() if t.category.value == category]
        return list(self.tools.values())

    def list_tool_ids(self) -> list[str]:
        """Get list of all tool IDs."""
        return list(self.tools.keys())

    def get_tools_compact(self) -> str:
        """One line per tool: `id(param*:type (hint), opt?:type): description`. The planner prompt used the full
        JSON (name, category, permission_level, null defaults...) = ~24 000 characters, which alone exceeded
        Groq's per-minute token limit after two questions. Same information, about half the size."""
        lines = []
        for t in self.tools.values():
            ps = []
            for p in t.parameters:
                s = f"{p.name}{'' if p.required else '?'}:{p.type}"
                if p.default not in (None, ""):
                    s += f"={p.default}"
                if p.description:
                    s += f" ({str(p.description)[:60]})"
                ps.append(s)
            lines.append(f"- {t.id}({', '.join(ps)}): {str(t.description)[:110]}")
        return "\n".join(lines)

    def get_tools_for_agent(self) -> list[dict]:
        """Get tool definitions formatted for Agent/LLM consumption.

        Returns:
            List of tool dicts with id, name, description, parameters
        """
        return [tool.to_dict() for tool in self.tools.values()]


# Global registry instance
_registry = ToolRegistry()



def ensure_registered_tools() -> None:
    """Import built-in tool modules once so the global registry is populated."""
    if getattr(_registry, "_builtins_loaded", False):
        return
    import importlib
    for module in ("tools.app_tools", "tools.agent_tools", "tools.ability_tools", "tools.planner_tools",
                   "tools.guide_tools", "tools.google_tools"):
        try:
            importlib.import_module(module)
        except Exception as e:
            log.warning("tool registry: could not load %s: %s", module, e)
    _registry._builtins_loaded = True

def register_tool(tool: ToolDefinition):
    """Register a tool in the global registry."""
    _registry.register(tool)


def get_tool(tool_id: str) -> Optional[ToolDefinition]:
    ensure_registered_tools()
    return _registry.get_tool(tool_id)


def call_tool(tool_id: str, **kwargs) -> ToolResult:
    ensure_registered_tools()
    return _registry.call_tool(tool_id, **kwargs)


def list_tools(category: Optional[str] = None) -> list[ToolDefinition]:
    ensure_registered_tools()
    return _registry.list_tools(category)


def get_registry() -> ToolRegistry:
    ensure_registered_tools()
    return _registry
