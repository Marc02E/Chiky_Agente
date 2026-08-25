"""Path helper tools for Chiky agente.

Provides safe path resolution for common user directories.
"""

from pathlib import Path
from typing import Any

from personal_ai_secretary.tools.registry import ToolDefinition, ToolRegistry, ToolRisk


def _get_user_dir(name: str) -> str | None:
    """Resolve common user directories safely."""
    home = Path.home()
    mapping: dict[str, Path] = {
        "desktop": home / "Desktop",
        "documents": home / "Documents",
        "downloads": home / "Downloads",
        "home": home,
    }
    path = mapping.get(name.lower())
    if path is not None and path.exists():
        return str(path)
    return None


async def _get_directory(arguments: dict[str, Any]) -> dict[str, Any]:
    name = str(arguments.get("name", "desktop")).lower()
    result = _get_user_dir(name)
    if result is None:
        return {"error": f"Directory '{name}' not found or does not exist."}
    return {"result": result, "name": name}


def register_path_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolDefinition(
            "get_directory",
            ToolRisk.LOW,
            False,
            _get_directory,
            argument_schema={"name": "string"},
            compact_description="Get user directory path (desktop/documents/downloads/home)",
        )
    )
