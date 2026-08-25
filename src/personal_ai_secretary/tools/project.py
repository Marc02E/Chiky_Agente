"""Project creation tool for Chiky agente.

Provides a high-level tool to create multi-file projects efficiently.
"""

from typing import Any

from personal_ai_secretary.tools.filesystem import _validate_path
from personal_ai_secretary.tools.registry import ToolDefinition, ToolError, ToolRegistry, ToolRisk


async def _create_project(arguments: dict[str, Any]) -> dict[str, Any]:
    """Create a complete project with multiple files and directories.

    Args:
        project_name: Name of the project directory.
        base_path: Where to create the project (e.g., Desktop path).
        files: List of dicts with 'path' (relative to project) and 'content'.
        directories: List of directory paths relative to project (optional).

    Returns:
        Summary of created files and directories.
    """
    project_name = str(arguments.get("project_name", "")).strip()
    base_path = str(arguments.get("base_path", "")).strip()
    files = arguments.get("files", [])
    directories = arguments.get("directories", [])

    if not project_name:
        return {"error": "project_name is required"}
    if not base_path:
        return {"error": "base_path is required"}
    if not isinstance(files, list):
        return {"error": "files must be a list"}

    # Validate base path
    try:
        base = _validate_path(base_path)
    except ToolError as exc:
        return {"error": str(exc)}

    # Create project root
    project_root = base / project_name
    try:
        project_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"error": f"Failed to create project directory: {exc}"}

    created_files: list[str] = []
    created_dirs: list[str] = []
    errors: list[str] = []

    # Create additional directories first
    for dir_path in directories:
        if not isinstance(dir_path, str) or not dir_path.strip():
            continue
        full_dir = project_root / dir_path.strip()
        try:
            # Validate against allowed roots
            _validate_path(str(full_dir))
            full_dir.mkdir(parents=True, exist_ok=True)
            created_dirs.append(dir_path.strip())
        except (ToolError, OSError) as exc:
            errors.append(f"Failed to create directory '{dir_path}': {exc}")

    # Create files
    for file_spec in files:
        if not isinstance(file_spec, dict):
            errors.append(f"Invalid file spec: {file_spec}")
            continue

        rel_path = str(file_spec.get("path", "")).strip()
        content = str(file_spec.get("content", ""))

        if not rel_path:
            errors.append("File spec missing 'path'")
            continue

        full_path = project_root / rel_path
        try:
            # Validate against allowed roots
            _validate_path(str(full_path))
            # Create parent directories
            full_path.parent.mkdir(parents=True, exist_ok=True)
            # Write content
            full_path.write_text(content, encoding="utf-8")
            created_files.append(rel_path)
        except (ToolError, OSError) as exc:
            errors.append(f"Failed to create file '{rel_path}': {exc}")

    result: dict[str, Any] = {
        "project_name": project_name,
        "project_path": str(project_root),
        "created_files": created_files,
        "files_count": len(created_files),
        "errors": errors,
        "errors_count": len(errors),
    }

    if created_dirs:
        result["created_directories"] = created_dirs
        result["directories_count"] = len(created_dirs)

    return result


def register_project_tools(registry: ToolRegistry) -> None:
    """Register project creation tools."""
    registry.register(
        ToolDefinition(
            "create_project",
            ToolRisk.HIGH,
            True,  # requires approval
            _create_project,
            argument_schema={
                "project_name": "string",
                "base_path": "string",
                "files": "list",
                "directories": "list",
            },
            compact_description="Create multi-file project with directories",
            argument_aliases={
                "name": "project_name",
                "path": "base_path",
                "dir": "base_path",
                "directory": "base_path",
            },
        )
    )
