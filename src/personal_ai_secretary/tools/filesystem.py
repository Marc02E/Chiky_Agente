"""Filesystem tools for Chiky agente.

Provides controlled file and directory operations with security validation.
"""

import contextlib
import contextvars
import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from personal_ai_secretary.shared.config import get_settings
from personal_ai_secretary.tools.registry import ToolDefinition, ToolError, ToolRegistry, ToolRisk

logger = logging.getLogger("personal_ai_secretary.tools.filesystem")

# ---------------------------------------------------------------------------
# Security: allowed roots + per-request workspace
# ---------------------------------------------------------------------------

# Tests pin this list to a temp dir before exercising the file tools. The
# production default is the user's home directory (historical contract of the
# personal assistant); path-traversal safety is enforced by anchoring relative
# paths to the primary root below — a relative path can never silently escape
# into the process CWD.
DEFAULT_ALLOWED_ROOTS: list[str] = [str(Path.home())]

# Per-request workspace set by the agent before executing a tool. While set,
# relative paths are anchored here and this becomes the containment root.
_ACTIVE_WORKSPACE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "chiky_active_workspace", default=None
)


def _default_workspace_root() -> Path:
    """Configured WORKSPACE_ROOT, or <home>/Chiky/workspace when unset."""
    settings = get_settings()
    configured = getattr(settings, "workspace_root", "") or ""
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Chiky" / "workspace"


def _workspace_roots() -> list[str]:
    """Containment roots from the active per-request workspace, if any."""
    active = _ACTIVE_WORKSPACE.get()
    if active:
        return [str(Path(active).expanduser())]
    return []


@contextlib.contextmanager
def routing_workspace(workspace: str | None) -> Iterator[None]:
    """Bind file-tool path resolution to a per-request workspace root.

    While the context is active, file tools anchor relative paths to
    ``workspace`` and reject any access outside it. Safe to nest.
    """
    token = _ACTIVE_WORKSPACE.set(workspace or None)
    try:
        yield
    finally:
        _ACTIVE_WORKSPACE.reset(token)


def _resolve_hallucinated_path(path_str: str) -> str:
    """Resolve hallucinated LLM paths to real filesystem paths.

    LLMs often generate placeholder paths like '/path/to/Desktop/file.txt'
    or '~/file.txt'. This function maps them to the actual user home.
    """
    home = Path.home()
    lower = path_str.lower().replace("\\", "/")

    # Pattern: /path/to/Desktop/... or /path/to/Documents/... etc.
    for subdir in ("Desktop", "Documents", "Downloads"):
        marker = f"/path/to/{subdir.lower()}/"
        if lower.startswith(marker):
            rest = path_str[len(marker):]
            return str(home / subdir / rest)
        # Also handle /path/to/{subdir} (no trailing slash, just the subdir name)
        if lower.rstrip("/") == f"/path/to/{subdir.lower()}":
            return str(home / subdir)

    # Pattern: ~/... → home/...
    if path_str.startswith("~/"):
        return str(home / path_str[2:])

    # Pattern: /path/to/home/... or /path/to/...
    if "/path/to/home/" in lower:
        rest = path_str[lower.index("/path/to/home/") + len("/path/to/home/"):]
        return str(home / rest)
    if lower.rstrip("/") == "/path/to/home":
        return str(home)

    return path_str


def _validate_path(
    path_str: str, allowed_roots: list[str] | None = None, write: bool = False
) -> Path:
    """Validate and resolve a path against allowed roots.

    Raises ToolError on security violations.
    """
    # Precedence: explicit roots (capability tests, tool-level tests) →
    # active per-request workspace (agent/real evidence sessions) → pinned
    # DEFAULT_ALLOWED_ROOTS (test suites) → configured WORKSPACE_ROOT.
    roots = allowed_roots or _workspace_roots() or DEFAULT_ALLOWED_ROOTS
    if not roots:
        roots = [str(_default_workspace_root())]

    # Resolve hallucinated paths first
    resolved_str = _resolve_hallucinated_path(path_str)

    try:
        anchor = Path(roots[0]).resolve()
        candidate = Path(resolved_str)
        if not candidate.is_absolute():
            # FASE AB.6: anchor relative paths to the workspace/primary root
            # instead of the process CWD. Without this, a path like
            # "../../ola.txt" resolves against wherever the server was
            # launched and silently escapes the workspace.
            candidate = anchor / candidate
        target = candidate.resolve()
    except (OSError, ValueError) as exc:
        raise ToolError(f"Invalid path: {exc}") from exc

    # Block path traversal — use separator suffix to prevent sibling-directory bypass.
    # Example: home="C:\Users\alice" must NOT match "C:\Users\alice_evil\file"
    target_str = str(target)
    for root in roots:
        root_resolved = str(Path(root).resolve())
        if target_str == root_resolved or target_str.startswith(root_resolved + os.sep):
            return target

    raise ToolError(
        f"Access denied: path '{path_str}' is outside allowed directories. "
        f"Allowed roots: {', '.join(roots)}"
    )


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


async def _create_file(arguments: dict[str, Any]) -> dict[str, Any]:
    path_str = str(arguments.get("path", ""))
    content = str(arguments.get("content", ""))
    if not path_str:
        return {"error": "path is required"}

    target = _validate_path(path_str)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return {"error": f"Failed to create file: {exc}"}

    verified = target.exists() and target.is_file()
    return {
        "result": "created",
        "path": str(target),
        "name": target.name,
        "size": len(content),
        "verified_exists": verified,
    }


async def _read_file(arguments: dict[str, Any]) -> dict[str, Any]:
    path_str = str(arguments.get("path", ""))
    if not path_str:
        return {"error": "path is required"}

    target = _validate_path(path_str)
    if not target.exists():
        return {"error": f"File not found: {target.name}"}
    if not target.is_file():
        return {"error": f"Not a file: {target.name}"}

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = target.read_text(encoding="latin-1")
        except OSError as exc:
            return {"error": f"Failed to read file: {exc}"}
    except OSError as exc:
        return {"error": f"Failed to read file: {exc}"}

    max_chars = 50_000
    truncated = len(content) > max_chars
    if truncated:
        content = content[:max_chars] + "\n... [truncated]"

    return {
        "result": content,
        "path": str(target),
        "name": target.name,
        "size": target.stat().st_size,
        "truncated": truncated,
    }


async def _write_file(arguments: dict[str, Any]) -> dict[str, Any]:
    path_str = str(arguments.get("path", ""))
    content = str(arguments.get("content", ""))
    mode = str(arguments.get("mode", "overwrite")).lower()
    if not path_str:
        return {"error": "path is required"}

    target = _validate_path(path_str)
    existed = target.exists()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append" and existed:
            with target.open("a", encoding="utf-8") as f:
                f.write(content)
        else:
            target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return {"error": f"Failed to write file: {exc}"}

    verified = target.exists() and target.is_file()
    return {
        "result": "updated" if existed else "created",
        "path": str(target),
        "name": target.name,
        "size": len(content),
        "mode": mode,
        "verified_exists": verified,
    }


async def _list_directory(arguments: dict[str, Any]) -> dict[str, Any]:
    path_str = str(arguments.get("path", ""))
    if not path_str:
        return {"error": "path is required"}

    target = _validate_path(path_str)
    if not target.exists():
        return {"error": f"Directory not found: {path_str}"}
    if not target.is_dir():
        return {"error": f"Not a directory: {path_str}"}

    try:
        entries: list[dict[str, str]] = []
        for entry in sorted(target.iterdir()):
            kind = "dir" if entry.is_dir() else "file"
            entries.append({"name": entry.name, "type": kind})
    except OSError as exc:
        return {"error": f"Failed to list directory: {exc}"}

    return {
        "result": entries,
        "path": str(target),
        "count": len(entries),
    }


async def _create_directory(arguments: dict[str, Any]) -> dict[str, Any]:
    path_str = str(arguments.get("path", ""))
    if not path_str:
        return {"error": "path is required"}

    target = _validate_path(path_str)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"error": f"Failed to create directory: {exc}"}

    verified = target.exists() and target.is_dir()
    return {
        "result": "created",
        "path": str(target),
        "name": target.name,
        "verified_exists": verified,
    }


async def _file_exists(arguments: dict[str, Any]) -> dict[str, Any]:
    path_str = str(arguments.get("path", ""))
    if not path_str:
        return {"error": "path is required"}

    try:
        target = _validate_path(path_str)
    except ToolError as exc:
        return {"error": str(exc)}

    return {
        "result": target.exists(),
        "path": str(target),
        "name": target.name,
    }


# ---------------------------------------------------------------------------
# FASE O: File delete and copy
# ---------------------------------------------------------------------------

# Files that should never be deleted
PROTECTED_FILES: frozenset[str] = frozenset({
    "pyproject.toml", "setup.py", "setup.cfg", "package.json",
    ".gitignore", "Dockerfile", "docker-compose.yml",
})


async def _file_delete(arguments: dict[str, Any]) -> dict[str, Any]:
    """Delete a file. Requires approval. Verifies deletion."""
    path_str = str(arguments.get("path", ""))
    if not path_str:
        return {"error": "path is required"}

    try:
        target = _validate_path(path_str, write=True)
    except ToolError as exc:
        return {"error": str(exc)}

    if not target.exists():
        return {"error": f"File not found: {target.name}"}

    if not target.is_file():
        return {"error": f"Not a file: {target.name} (use create_directory for dirs)"}

    if target.name in PROTECTED_FILES:
        return {"error": f"Protected file: {target.name} cannot be deleted"}

    try:
        target.unlink()
    except OSError as exc:
        return {"error": f"Failed to delete: {exc}"}

    verified_absent = not target.exists()
    return {
        "result": "deleted",
        "path": str(target),
        "name": target.name,
        "verified_absent": verified_absent,
    }


async def _file_copy(arguments: dict[str, Any]) -> dict[str, Any]:
    """Copy a file to a new location. Verifies destination exists with same size."""
    src_str = str(arguments.get("source", ""))
    dst_str = str(arguments.get("destination", ""))
    if not src_str:
        return {"error": "source is required"}
    if not dst_str:
        return {"error": "destination is required"}

    try:
        src = _validate_path(src_str)
    except ToolError as exc:
        return {"error": f"source: {exc}"}

    if not src.exists():
        return {"error": f"Source not found: {src.name}"}

    if not src.is_file():
        return {"error": f"Source is not a file: {src.name}"}

    try:
        dst = _validate_path(dst_str, write=True)
    except ToolError as exc:
        return {"error": f"destination: {exc}"}

    import shutil

    try:
        shutil.copy2(str(src), str(dst))
    except OSError as exc:
        return {"error": f"Failed to copy: {exc}"}

    verified_exists = dst.exists() and dst.is_file()
    same_size = verified_exists and dst.stat().st_size == src.stat().st_size
    return {
        "result": "copied",
        "source": str(src),
        "destination": str(dst),
        "source_size": src.stat().st_size,
        "destination_size": dst.stat().st_size if verified_exists else 0,
        "verified_exists": verified_exists,
        "same_size": same_size,
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_filesystem_tools(
    registry: ToolRegistry,
    allowed_roots: list[str] | None = None,
) -> None:
    roots = allowed_roots or DEFAULT_ALLOWED_ROOTS

    async def _create(arguments: dict[str, Any]) -> dict[str, Any]:
        import personal_ai_secretary.tools.filesystem as _fs
        original = _fs.DEFAULT_ALLOWED_ROOTS[:]
        if roots:
            _fs.DEFAULT_ALLOWED_ROOTS[:] = roots
        try:
            return await _create_file(arguments)
        finally:
            _fs.DEFAULT_ALLOWED_ROOTS[:] = original

    async def _read(arguments: dict[str, Any]) -> dict[str, Any]:
        import personal_ai_secretary.tools.filesystem as _fs
        original = _fs.DEFAULT_ALLOWED_ROOTS[:]
        if roots:
            _fs.DEFAULT_ALLOWED_ROOTS[:] = roots
        try:
            return await _read_file(arguments)
        finally:
            _fs.DEFAULT_ALLOWED_ROOTS[:] = original

    async def _write(arguments: dict[str, Any]) -> dict[str, Any]:
        import personal_ai_secretary.tools.filesystem as _fs
        original = _fs.DEFAULT_ALLOWED_ROOTS[:]
        if roots:
            _fs.DEFAULT_ALLOWED_ROOTS[:] = roots
        try:
            return await _write_file(arguments)
        finally:
            _fs.DEFAULT_ALLOWED_ROOTS[:] = original

    async def _list(arguments: dict[str, Any]) -> dict[str, Any]:
        import personal_ai_secretary.tools.filesystem as _fs
        original = _fs.DEFAULT_ALLOWED_ROOTS[:]
        if roots:
            _fs.DEFAULT_ALLOWED_ROOTS[:] = roots
        try:
            return await _list_directory(arguments)
        finally:
            _fs.DEFAULT_ALLOWED_ROOTS[:] = original

    async def _mkdir(arguments: dict[str, Any]) -> dict[str, Any]:
        import personal_ai_secretary.tools.filesystem as _fs
        original = _fs.DEFAULT_ALLOWED_ROOTS[:]
        if roots:
            _fs.DEFAULT_ALLOWED_ROOTS[:] = roots
        try:
            return await _create_directory(arguments)
        finally:
            _fs.DEFAULT_ALLOWED_ROOTS[:] = original

    async def _exists(arguments: dict[str, Any]) -> dict[str, Any]:
        import personal_ai_secretary.tools.filesystem as _fs
        original = _fs.DEFAULT_ALLOWED_ROOTS[:]
        if roots:
            _fs.DEFAULT_ALLOWED_ROOTS[:] = roots
        try:
            return await _file_exists(arguments)
        finally:
            _fs.DEFAULT_ALLOWED_ROOTS[:] = original

    registry.register(
        ToolDefinition(
            "create_file",
            ToolRisk.MEDIUM,
            True,
            _create,
            argument_schema={"path": "string", "content": "string"},
            compact_description="Create a file with content",
            argument_aliases={
                "p": "path", "file": "path", "file_path": "path",
                "text": "content", "data": "content",
            },
        )
    )
    registry.register(
        ToolDefinition(
            "read_file",
            ToolRisk.LOW,
            False,
            _read,
            argument_schema={"path": "string"},
            compact_description="Read file contents",
            argument_aliases={"p": "path", "file": "path", "file_path": "path"},
        )
    )
    registry.register(
        ToolDefinition(
            "write_file",
            ToolRisk.HIGH,
            True,
            _write,
            argument_schema={"path": "string", "content": "string"},
            compact_description="Write/overwrite file content",
            argument_aliases={
                "p": "path", "file": "path", "file_path": "path",
                "text": "content", "data": "content",
            },
        )
    )
    registry.register(
        ToolDefinition(
            "list_directory",
            ToolRisk.LOW,
            False,
            _list,
            argument_schema={"path": "string"},
            compact_description="List directory contents",
            argument_aliases={"p": "path", "dir": "path", "directory": "path"},
        )
    )
    registry.register(
        ToolDefinition(
            "create_directory",
            ToolRisk.MEDIUM,
            True,
            _mkdir,
            argument_schema={"path": "string"},
            compact_description="Create a directory",
            argument_aliases={"p": "path", "dir": "path", "directory": "path"},
        )
    )
    registry.register(
        ToolDefinition(
            "file_exists",
            ToolRisk.LOW,
            False,
            _exists,
            argument_schema={"path": "string"},
            compact_description="Check if file/directory exists",
            argument_aliases={"p": "path", "file": "path", "file_path": "path"},
        )
    )

    # FASE O: File delete and copy tools
    async def _delete(arguments: dict[str, Any]) -> dict[str, Any]:
        import personal_ai_secretary.tools.filesystem as _fs
        original = _fs.DEFAULT_ALLOWED_ROOTS[:]
        if roots:
            _fs.DEFAULT_ALLOWED_ROOTS[:] = roots
        try:
            return await _file_delete(arguments)
        finally:
            _fs.DEFAULT_ALLOWED_ROOTS[:] = original

    async def _copy(arguments: dict[str, Any]) -> dict[str, Any]:
        import personal_ai_secretary.tools.filesystem as _fs
        original = _fs.DEFAULT_ALLOWED_ROOTS[:]
        if roots:
            _fs.DEFAULT_ALLOWED_ROOTS[:] = roots
        try:
            return await _file_copy(arguments)
        finally:
            _fs.DEFAULT_ALLOWED_ROOTS[:] = original

    registry.register(
        ToolDefinition(
            "file_delete",
            ToolRisk.HIGH,
            True,
            _delete,
            argument_schema={"path": "string"},
            compact_description="Delete a file (requires approval, verifies deletion)",
            argument_aliases={"p": "path", "file": "path", "file_path": "path"},
        )
    )
    registry.register(
        ToolDefinition(
            "file_copy",
            ToolRisk.MEDIUM,
            True,
            _copy,
            argument_schema={"source": "string", "destination": "string"},
            compact_description="Copy a file to a new location (verifies destination)",
            argument_aliases={
                "src": "source", "from": "source",
                "dest": "destination", "to": "destination",
            },
        )
    )
