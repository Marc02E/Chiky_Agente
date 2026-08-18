import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ToolRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ToolError(Exception):
    """Raised when a tool invocation cannot be parsed or its arguments are invalid."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


_TOOL_MARKER = "@tool:"


def parse_tool_call(text: str) -> ToolCall | None:
    """Parse a '@tool:<name> <json-object>' invocation from the input.

    Returns None when the text does not contain a tool invocation.
    """
    stripped = text.lstrip()
    if not stripped.startswith(_TOOL_MARKER):
        return None
    rest = stripped[len(_TOOL_MARKER):].strip()
    if not rest:
        raise ToolError("Tool invocation is missing a tool name")
    name, _, payload = rest.partition(" ")
    if not name.replace("_", "").isalnum():
        raise ToolError("Malformed tool name")
    arguments: dict[str, Any] = {}
    if payload.strip():
        try:
            parsed = json.loads(payload.strip())
        except json.JSONDecodeError as exc:
            raise ToolError("Tool arguments must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ToolError("Tool arguments must be a JSON object")
        arguments = parsed
    return ToolCall(name, arguments)


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    risk: ToolRisk
    requires_explicit_approval: bool
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
    argument_schema: dict[str, str] = field(default_factory=dict)


def _argument_matches(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return True


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.risk == ToolRisk.CRITICAL:
            raise ValueError("Critical-risk tools are forbidden by default")
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def _validate_arguments(
        self, definition: ToolDefinition, arguments: dict[str, Any]
    ) -> None:
        unknown = [key for key in arguments if key not in definition.argument_schema]
        if unknown:
            raise ToolError(f"Unknown argument(s): {', '.join(sorted(unknown))}")
        missing = [key for key in definition.argument_schema if key not in arguments]
        if missing:
            raise ToolError(f"Missing argument(s): {', '.join(sorted(missing))}")
        for key, expected in definition.argument_schema.items():
            if key in arguments and not _argument_matches(arguments[key], expected):
                raise ToolError(f"Argument '{key}' must be {expected}")

    async def execute(
        self, name: str, arguments: dict[str, Any], *, approved: bool = False
    ) -> dict[str, Any]:
        definition = self.get(name)
        if definition is None:
            raise KeyError(f"Unknown tool: {name}")
        if definition.requires_explicit_approval and not approved:
            raise PermissionError("Explicit approval required")
        self._validate_arguments(definition, arguments)
        return await definition.handler(arguments)