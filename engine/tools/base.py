"""Lyra — Tool system base classes and interfaces.

Tools are the standardized, reusable units of work that can be invoked by:
- Direct command matching (actions/ package)
- Multi-action shortcuts (shortcuts/ package)
- Future LLM agent tool-calling (Phase 3)

This module only defines the data structures and interfaces.
Concrete tools are registered in tools/registry.py and tools/app_tools.py.
"""

from typing import Any, Callable
from dataclasses import dataclass
from enum import Enum


class ToolCategory(Enum):
    """High-level categories used to organize tools in the registry
    and (later) to help the LLM choose the right tool.
    """
    APP = "app"           # Application launch / focus / control
    FILE = "file"         # Filesystem create / delete / rename / list
    SYSTEM = "system"     # Volume, lock, screenshot, power, status
    WEB = "web"           # Browser / URL related
    MEDIA = "media"       # Play / pause / next / previous track
    MEMORY = "memory"     # Remember / recall facts and paths


@dataclass
class ToolParameter:
    """Describes a single parameter that a tool accepts.

    Used both for documentation and for future LLM function-calling schemas.
    """
    name: str                 # Parameter name as it appears in the function signature
    type: str                 # Logical type: "string", "number", "boolean"
    description: str          # Human-readable explanation of what the param does
    required: bool = True     # Whether the caller must supply this value
    default: Any = None       # Fallback value when the parameter is omitted


@dataclass
class ToolDefinition:
    """Complete metadata for one tool, plus a reference to the callable
    that actually performs the work.

    The Agent (Phase 3) will receive a list of these (via to_dict()) so it
    can decide which tool to call and with which arguments.
    """
    id: str                       # Unique machine id, e.g. "open_local_app"
    name: str                     # Human-friendly display name
    description: str              # Longer explanation for humans / LLM
    category: ToolCategory        # One of the ToolCategory enum values
    parameters: list[ToolParameter]  # Ordered list of accepted parameters
    permission_level: str         # "SAFE", "CONFIRM", or "BLOCKED"
    execute: Callable             # The real Python function that does the work

    def to_dict(self) -> dict:
        """Serialize the tool definition into a plain dict suitable for
        sending to an LLM as a function/tool schema.

        Note: the 'execute' callable itself is intentionally omitted —
        the agent only needs the metadata; the registry looks up the
        callable by id when it is time to run the tool.
        """
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category.value,  # enum → string
            "parameters": [
                {
                    "name": p.name,
                    "type": p.type,
                    "description": p.description,
                    "required": p.required,
                    "default": p.default
                }
                for p in self.parameters
            ],
            "permission_level": self.permission_level
        }


class ToolResult:
    """Standardized result object returned by every tool execution.

    Success/failure is carried in the boolean 'success' flag.
    'message' is always a human-readable string suitable for speaking
    or showing in the chat transcript.
    'data' is an optional structured payload for callers that need it.
    """

    def __init__(self, success: bool, message: str, data: Any = None, verified: bool | None = None):
        self.success = success
        self.message = message
        self.data = data
        # Execution success is not the same as outcome verification.
        # Outward-facing tools should set this explicitly.
        self.verified = verified

    def __str__(self):
        """Allow str(result) to yield the message directly."""
        return self.message

    def __bool__(self):
        """Allow 'if result:' to mean 'if the tool succeeded'."""
        return self.success
