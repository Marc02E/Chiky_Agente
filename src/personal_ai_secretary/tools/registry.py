"""Tool registry with argument normalization for model compatibility.

FASE S: Adds explicit argument aliases and normalization so that models
like DeepSeek and llama3.1 can use variant argument names without failing.

All normalization is deterministic — only declared aliases are resolved.
Security controls (approval, sandbox, path validation) are NEVER bypassed.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


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
    optional_arguments: frozenset[str] = field(default_factory=frozenset)
    compact_description: str = ""
    # FASE S: explicit aliases — alias -> canonical argument name
    argument_aliases: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class NormalizationResult:
    """Result of argument normalization — for observability."""

    original_args: dict[str, Any]
    normalized_args: dict[str, Any]
    aliases_resolved: list[tuple[str, str]]  # (alias, canonical)
    unknown_stripped: list[str]
    was_modified: bool = False


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
        # FASE S.10: observability counters
        self._metrics: dict[str, int] = {
            "tool_calls_received": 0,
            "tool_calls_normalized": 0,
            "argument_repairs": 0,
            "argument_repair_failures": 0,
            "unknown_tool_arguments": 0,
            "invalid_tool_arguments": 0,
        }

    def register(self, definition: ToolDefinition) -> None:
        if definition.risk == ToolRisk.CRITICAL:
            raise ValueError("Critical-risk tools are forbidden by default")
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def compact_descriptions(self) -> list[str]:
        """Return compact 'name: description' lines for tools that have descriptions."""
        result = []
        for name in sorted(self._tools):
            desc = self._tools[name].compact_description
            if desc:
                result.append(f"{name}: {desc}")
        return result

    def get_metrics(self) -> dict[str, int]:
        """FASE S.10: Return observability metrics (copy)."""
        return dict(self._metrics)

    def reset_metrics(self) -> None:
        """Reset observability counters."""
        for key in self._metrics:
            self._metrics[key] = 0

    # ── FASE S.2-S.3: Normalization ──────────────────────────────────

    def normalize_arguments(
        self, definition: ToolDefinition, arguments: dict[str, Any]
    ) -> NormalizationResult:
        """Normalize tool arguments: resolve aliases, strip unknowns.

        This is a DETERMINISTIC step — only declared aliases are resolved.
        No heuristics, no fuzzy matching, no "if it looks similar".
        """
        aliases_resolved: list[tuple[str, str]] = []
        unknown_stripped: list[str] = []
        normalized: dict[str, Any] = {}

        allowed = set(definition.argument_schema) | definition.optional_arguments

        for key, value in arguments.items():
            if key in allowed:
                # Canonical name — pass through
                normalized[key] = value
            elif key in definition.argument_aliases:
                # Known alias — resolve to canonical name
                canonical = definition.argument_aliases[key]
                if canonical in normalized:
                    # Ambiguity: canonical already present — skip alias
                    logger.debug(
                        "Alias '%s' -> '%s' skipped: canonical already present",
                        key, canonical,
                    )
                    unknown_stripped.append(key)
                    self._metrics["unknown_tool_arguments"] += 1
                else:
                    normalized[canonical] = value
                    aliases_resolved.append((key, canonical))
                    logger.debug("Alias resolved: '%s' -> '%s'", key, canonical)
            else:
                # Unknown argument — strip it
                unknown_stripped.append(key)
                self._metrics["unknown_tool_arguments"] += 1
                logger.debug("Unknown argument stripped: '%s'", key)

        was_modified = bool(aliases_resolved) or bool(unknown_stripped)
        if was_modified:
            self._metrics["tool_calls_normalized"] += 1

        return NormalizationResult(
            original_args=dict(arguments),
            normalized_args=normalized,
            aliases_resolved=aliases_resolved,
            unknown_stripped=unknown_stripped,
            was_modified=was_modified,
        )

    # ── S.4-S.5: Validation + Repair ─────────────────────────────────

    def _validate_arguments(
        self, definition: ToolDefinition, arguments: dict[str, Any]
    ) -> None:
        # Build the set of allowed keys: required + optional
        allowed = set(definition.argument_schema) | definition.optional_arguments
        unknown = [key for key in arguments if key not in allowed]
        if unknown:
            raise ToolError(f"Unknown argument(s): {', '.join(sorted(unknown))}")
        # Only required arguments (in argument_schema, not in optional_arguments)
        required = set(definition.argument_schema) - definition.optional_arguments
        missing = [key for key in required if key not in arguments]
        if missing:
            raise ToolError(f"Missing argument(s): {', '.join(sorted(missing))}")
        # Validate types for arguments present in the schema
        for key, expected in definition.argument_schema.items():
            if key in arguments and not _argument_matches(arguments[key], expected):
                raise ToolError(f"Argument '{key}' must be {expected}")

    def _attempt_argument_repair(
        self,
        definition: ToolDefinition,
        arguments: dict[str, Any],
        error: ToolError,
    ) -> dict[str, Any] | None:
        """Attempt a single repair for common argument errors.

        S.5: Maximum 1 repair attempt. Returns repaired args or None.
        """
        error_msg = str(error).lower()

        # Repair: missing required argument
        if "missing argument" in error_msg:
            missing_part = error_msg.split("missing argument(s):")[-1].strip()
            missing_names = [n.strip() for n in missing_part.split(",")]

            if len(missing_names) == 1:
                missing_name = missing_names[0]

                # modify_file: if 'search' present but 'mode' missing -> mode=replace
                if missing_name == "mode" and "search" in arguments:
                    repaired = dict(arguments)
                    repaired["mode"] = "replace"
                    self._metrics["argument_repairs"] += 1
                    logger.info("Repaired: added mode=replace (search present)")
                    return repaired

                # modify_file: if 'content' present but 'mode' missing -> mode=overwrite
                if missing_name == "mode" and "content" in arguments:
                    repaired = dict(arguments)
                    repaired["mode"] = "overwrite"
                    self._metrics["argument_repairs"] += 1
                    logger.info("Repaired: added mode=overwrite (content present)")
                    return repaired

                # modify_file: if 'search' present but 'content' missing -> ok
                # (search mode doesn't need content)
                if missing_name == "content" and "search" in arguments:
                    # Model probably meant mode=replace but forgot mode key
                    if "mode" not in arguments:
                        repaired = dict(arguments)
                        repaired["mode"] = "replace"
                        self._metrics["argument_repairs"] += 1
                        logger.info("Repaired: added mode=replace (search present)")
                        return repaired

        # Repair: wrong type — int where string expected (common with list index)
        if "must be string" in error_msg:
            for key, expected in definition.argument_schema.items():
                if expected == "string" and key in arguments:
                    val = arguments[key]
                    if isinstance(val, (int, float)):
                        repaired = dict(arguments)
                        repaired[key] = str(val)
                        self._metrics["argument_repairs"] += 1
                        logger.info("Repaired: %s=%r -> str(%r)", key, val, val)
                        return repaired

        self._metrics["argument_repair_failures"] += 1
        return None

    # ── S.4: Execute with normalization ───────────────────────────────

    async def execute(
        self, name: str, arguments: dict[str, Any], *, approved: bool = False
    ) -> dict[str, Any]:
        """Execute a tool with normalization, validation, and repair.

        Pipeline: EXTRACT -> NORMALIZE -> VALIDATE -> REPAIR -> APPROVAL -> EXECUTE
        """
        self._metrics["tool_calls_received"] += 1

        definition = self.get(name)
        if definition is None:
            raise KeyError(f"Unknown tool: {name}")

        # S.2-S.3: Normalize arguments (resolve aliases, strip unknowns)
        norm = self.normalize_arguments(definition, arguments)
        args = norm.normalized_args

        if norm.aliases_resolved:
            logger.info(
                "Tool '%s': %d alias(es) resolved: %s",
                name, len(norm.aliases_resolved),
                norm.aliases_resolved,
            )

        # S.4: Validate
        try:
            self._validate_arguments(definition, args)
        except ToolError as exc:
            # S.5: Attempt single repair
            repaired = self._attempt_argument_repair(definition, args, exc)
            if repaired is not None:
                args = repaired
                # Re-validate after repair
                try:
                    self._validate_arguments(definition, args)
                except ToolError as repair_exc:
                    self._metrics["invalid_tool_arguments"] += 1
                    raise repair_exc from exc
            else:
                self._metrics["invalid_tool_arguments"] += 1
                raise

        # Approval gate — NEVER bypassed by normalization
        if definition.requires_explicit_approval and not approved:
            raise PermissionError("Explicit approval required")

        return await definition.handler(args)
