"""Date/time tools for Chiky agente."""

from datetime import UTC, datetime
from typing import Any

from personal_ai_secretary.tools.registry import ToolDefinition, ToolRegistry, ToolRisk


async def _now(_: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "result": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "day_of_week": now.strftime("%A"),
    }


async def _format_date(arguments: dict[str, Any]) -> dict[str, Any]:
    iso_str = str(arguments.get("iso_datetime", ""))
    fmt = str(arguments.get("format", "%Y-%m-%d %H:%M:%S"))
    try:
        dt = datetime.fromisoformat(iso_str)
        return {"result": dt.strftime(fmt)}
    except ValueError as exc:
        return {"error": f"Invalid datetime: {exc}"}


def register_datetime_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolDefinition(
            "datetime_now",
            ToolRisk.LOW,
            False,
            _now,
            argument_schema={},
            compact_description="Get current date/time",
        )
    )
    registry.register(
        ToolDefinition(
            "format_date",
            ToolRisk.LOW,
            False,
            _format_date,
            argument_schema={"iso_datetime": "string", "format": "string"},
            compact_description="Format datetime string",
        )
    )
