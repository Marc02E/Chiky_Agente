"""Development tools for Chiky agente.

Provides advanced tools for project analysis, multi-file reading,
targeted modification, code search, and file verification.
"""

import fnmatch
import os
from pathlib import Path
from typing import Any

from personal_ai_secretary.tools.filesystem import _validate_path
from personal_ai_secretary.tools.registry import ToolDefinition, ToolError, ToolRegistry, ToolRisk

# Directories to skip during project analysis
_SKIP_DIRS = frozenset({
    ".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", "env", ".env", "dist", "build",
    ".eggs", "*.egg-info", ".tox", ".nox", ".idea", ".vscode",
})


def _should_skip(name: str) -> bool:
    """Check if a directory or file should be skipped during analysis."""
    for pattern in _SKIP_DIRS:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


# ---------------------------------------------------------------------------
# analyze_project
# ---------------------------------------------------------------------------


async def _analyze_project(arguments: dict[str, Any]) -> dict[str, Any]:
    """Recursively analyze a project directory and return its structure.

    Returns a tree of files with types, sizes, and optional line counts.
    Respects security boundaries via _validate_path.
    FASE M.2: Uses project_intelligence for smart discovery when available.
    """
    path_str = str(arguments.get("path", "")).strip()
    max_depth = int(arguments.get("max_depth", 5))
    include_content = bool(arguments.get("include_content", False))

    if not path_str:
        return {"error": "path is required"}

    try:
        root = _validate_path(path_str)
    except ToolError as exc:
        return {"error": str(exc)}

    if not root.exists():
        return {"error": f"Directory not found: {path_str}"}
    if not root.is_dir():
        return {"error": f"Not a directory: {path_str}"}

    # FASE M.2: Use project_intelligence for smart discovery
    # (only when not requesting content)
    if not include_content:
        try:
            from personal_ai_secretary.context.project_intelligence import (
                build_project_summary,
                discover_project,
            )
            manifest = discover_project(
                str(root), max_depth=max_depth, read_configs=True,
            )
            summary = build_project_summary(manifest)

            # Build a compact tree from the manifest
            smart_tree: list[dict[str, Any]] = []
            included: set[str] = set()

            def _add(
                paths: list[str], cat: str, limit: int = 10,
            ) -> None:
                for p in paths[:limit]:
                    if p not in included:
                        smart_tree.append({
                            "name": p.split("/")[-1],
                            "path": p,
                            "type": "file",
                            "category": cat,
                        })
                        included.add(p)

            _add(manifest.config_files, "config", 10)
            _add(manifest.entry_points, "entry_point", 5)
            _add(manifest.important_files, "important", 10)
            _add(manifest.test_locations, "test", 5)
            _add(manifest.documentation, "documentation", 5)

            # Only use smart path if we found categorized files; otherwise fall through
            if smart_tree:
                return {
                    "project_path": str(root),
                    "project_name": manifest.name,
                    "summary": summary,
                    "structure": smart_tree,
                    "total_files": manifest.total_files,
                    "total_directories": manifest.total_directories,
                    "languages": manifest.languages,
                    "frameworks": manifest.frameworks,
                    "entry_points": manifest.entry_points,
                    "config_files": manifest.config_files,
                    "source_directories": manifest.source_directories,
                    "file_extensions": manifest.file_extensions,
                    "max_depth": max_depth,
                }
        except ImportError:
            pass  # Fall through to basic walk if project_intelligence unavailable

    # Fallback: basic recursive walk (single traversal, no double-bug)
    tree: list[dict[str, Any]] = []

    def _walk(current: Path, depth: int, rel_prefix: str = "") -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        except PermissionError:
            return
        for entry in entries:
            if _should_skip(entry.name):
                continue
            rel = f"{rel_prefix}{entry.name}" if not rel_prefix else f"{rel_prefix}/{entry.name}"
            node: dict[str, Any] = {"name": entry.name, "path": rel}
            if entry.is_dir():
                node["type"] = "directory"
                try:
                    child_entries = [e for e in entry.iterdir() if not _should_skip(e.name)]
                    node["children_count"] = len(child_entries)
                except PermissionError:
                    pass
                _walk(entry, depth + 1, rel)
            elif entry.is_file():
                node["type"] = "file"
                try:
                    stat = entry.stat()
                    node["size"] = stat.st_size
                    suffix = entry.suffix.lower()
                    if suffix:
                        node["extension"] = suffix
                    if include_content and stat.st_size < 100_000:
                        try:
                            node["content"] = entry.read_text(encoding="utf-8")
                        except (UnicodeDecodeError, OSError):
                            node["content"] = "[binary or unreadable]"
                except OSError:
                    pass
            tree.append(node)

    _walk(root, 0)

    file_count = sum(1 for n in tree if n.get("type") == "file")
    dir_count = sum(1 for n in tree if n.get("type") == "directory")

    return {
        "project_path": str(root),
        "project_name": root.name,
        "structure": tree,
        "total_files": file_count,
        "total_directories": dir_count,
        "max_depth": max_depth,
    }


# ---------------------------------------------------------------------------
# read_files
# ---------------------------------------------------------------------------

_MAX_FILES_PER_CALL = 20
_MAX_CONTENT_PER_FILE = 50_000


async def _read_files(arguments: dict[str, Any]) -> dict[str, Any]:
    """Read multiple files in a single call for efficient context gathering.

    Args:
        paths: List of file paths to read.

    Returns:
        Dict mapping each path to its content or error.
    """
    paths = arguments.get("paths", [])
    if not isinstance(paths, list) or not paths:
        return {"error": "paths must be a non-empty list"}

    if len(paths) > _MAX_FILES_PER_CALL:
        return {"error": f"Too many files: {len(paths)} (max {_MAX_FILES_PER_CALL})"}

    results: dict[str, Any] = {}
    errors: list[str] = []
    read_count = 0

    for raw_path in paths:
        path_str = str(raw_path).strip()
        if not path_str:
            errors.append("Empty path in list")
            continue

        try:
            target = _validate_path(path_str)
        except ToolError as exc:
            errors.append(f"{path_str}: {exc}")
            results[path_str] = {"error": str(exc)}
            continue

        if not target.exists():
            errors.append(f"{path_str}: not found")
            results[path_str] = {"error": f"File not found: {path_str}"}
            continue

        if not target.is_file():
            errors.append(f"{path_str}: not a file")
            results[path_str] = {"error": f"Not a file: {path_str}"}
            continue

        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = target.read_text(encoding="latin-1")
            except OSError as exc:
                errors.append(f"{path_str}: {exc}")
                results[path_str] = {"error": str(exc)}
                continue
        except OSError as exc:
            errors.append(f"{path_str}: {exc}")
            results[path_str] = {"error": str(exc)}
            continue

        truncated = len(content) > _MAX_CONTENT_PER_FILE
        if truncated:
            content = content[:_MAX_CONTENT_PER_FILE] + "\n... [truncated]"

        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)

        results[path_str] = {
            "content": content,
            "size": target.stat().st_size,
            "lines": line_count,
            "truncated": truncated,
        }
        read_count += 1

    return {
        "files": results,
        "read_count": read_count,
        "error_count": len(errors),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# modify_file
# ---------------------------------------------------------------------------


async def _modify_file(arguments: dict[str, Any]) -> dict[str, Any]:
    """Apply targeted modifications to an existing file.

    Supports modes:
      - 'replace': Replace all occurrences of 'search' with 'replacement'.
      - 'replace_first': Replace only the first occurrence.
      - 'append': Append content to the end of the file.
      - 'prepend': Prepend content to the beginning of the file.
      - 'insert_after': Insert content after the first occurrence of 'search'.
      - 'insert_before': Insert content before the first occurrence of 'search'.
      - 'overwrite': Overwrite the entire file with 'content'.
    """
    path_str = str(arguments.get("path", "")).strip()
    mode = str(arguments.get("mode", "replace")).lower()
    search = str(arguments.get("search", ""))
    replacement = str(arguments.get("replacement", ""))
    content = str(arguments.get("content", ""))

    if not path_str:
        return {"error": "path is required"}

    try:
        target = _validate_path(path_str)
    except ToolError as exc:
        return {"error": str(exc)}

    if not target.exists():
        return {"error": f"File not found: {path_str}"}
    if not target.is_file():
        return {"error": f"Not a file: {path_str}"}

    # Read original content
    try:
        original = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            original = target.read_text(encoding="latin-1")
        except OSError as exc:
            return {"error": f"Failed to read file: {exc}"}
    except OSError as exc:
        return {"error": f"Failed to read file: {exc}"}

    lines_before = original.count("\n") + (1 if original and not original.endswith("\n") else 0)

    modified = original
    changes_made = 0

    if mode == "overwrite":
        modified = content
        changes_made = 1 if original != content else 0
    elif mode == "append":
        if content:
            sep = "" if original.endswith("\n") or not original else "\n"
            modified = original + sep + content
            changes_made = 1
    elif mode == "prepend":
        if content:
            sep = "" if content.endswith("\n") else "\n"
            modified = content + sep + original
            changes_made = 1
    elif mode in ("replace", "replace_first"):
        if not search:
            return {"error": "search is required for replace mode"}
        if search in modified:
            count = 1 if mode == "replace_first" else -1
            modified = modified.replace(search, replacement, count)
            changes_made = 1 if modified != original else 0
        else:
            return {"error": f"Search string not found in {path_str}"}
    elif mode == "insert_after":
        if not search:
            return {"error": "search is required for insert_after mode"}
        idx = modified.find(search)
        if idx == -1:
            return {"error": f"Search string not found in {path_str}"}
        insert_pos = idx + len(search)
        sep = "" if modified[insert_pos - 1:insert_pos] == "\n" else "\n"
        modified = modified[:insert_pos] + sep + content + modified[insert_pos:]
        changes_made = 1
    elif mode == "insert_before":
        if not search:
            return {"error": "search is required for insert_before mode"}
        idx = modified.find(search)
        if idx == -1:
            return {"error": f"Search string not found in {path_str}"}
        sep = "" if idx > 0 and modified[idx - 1:idx] == "\n" else "\n"
        modified = modified[:idx] + content + sep + modified[idx:]
        changes_made = 1
    else:
        return {"error": f"Unknown mode: {mode}. Use: replace, replace_first, "
                        "append, prepend, insert_after, insert_before, overwrite"}

    if changes_made == 0:
        return {
            "result": "unchanged",
            "path": str(target),
            "name": target.name,
            "message": "No changes needed; file is already in the desired state.",
        }

    # Write modified content
    try:
        target.write_text(modified, encoding="utf-8")
    except OSError as exc:
        return {"error": f"Failed to write file: {exc}"}

    lines_after = modified.count("\n") + (1 if modified and not modified.endswith("\n") else 0)
    verified = target.exists() and target.is_file()

    return {
        "result": "modified",
        "path": str(target),
        "name": target.name,
        "mode": mode,
        "size_before": len(original),
        "size_after": len(modified),
        "lines_before": lines_before,
        "lines_after": lines_after,
        "verified_exists": verified,
    }


# ---------------------------------------------------------------------------
# search_files
# ---------------------------------------------------------------------------

_MAX_SEARCH_RESULTS = 50
_MAX_SEARCH_FILES = 500


async def _search_files(arguments: dict[str, Any]) -> dict[str, Any]:
    """Search for a text pattern across files in a directory.

    Returns matching file paths and line numbers with context.
    """
    path_str = str(arguments.get("path", "")).strip()
    pattern = str(arguments.get("pattern", "")).strip()
    include = str(arguments.get("include", "")).strip()
    max_results = min(int(arguments.get("max_results", 20)), _MAX_SEARCH_RESULTS)

    if not path_str:
        return {"error": "path is required"}
    if not pattern:
        return {"error": "pattern is required"}

    try:
        root = _validate_path(path_str)
    except ToolError as exc:
        return {"error": str(exc)}

    if not root.exists():
        return {"error": f"Directory not found: {path_str}"}
    if not root.is_dir():
        return {"error": f"Not a directory: {path_str}"}

    matches: list[dict[str, Any]] = []
    files_searched = 0
    total_matches = 0

    for dirpath, dirnames, filenames in os.walk(str(root)):
        # Filter out skipped directories in-place
        dirnames[:] = [d for d in dirnames if not _should_skip(d)]

        for filename in filenames:
            if _should_skip(filename):
                continue
            if include and not fnmatch.fnmatch(filename, include):
                continue
            if files_searched >= _MAX_SEARCH_FILES:
                break

            file_path = Path(dirpath) / filename
            files_searched += 1

            try:
                text = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            for line_num, line in enumerate(text.splitlines(), start=1):
                if pattern.lower() in line.lower():
                    total_matches += 1
                    if len(matches) < max_results:
                        rel = str(file_path.relative_to(root))
                        matches.append({
                            "file": rel,
                            "line": line_num,
                            "text": line.strip()[:200],
                        })

        if files_searched >= _MAX_SEARCH_FILES:
            break

    return {
        "pattern": pattern,
        "root": str(root),
        "matches": matches,
        "match_count": total_matches,
        "files_searched": files_searched,
        "truncated": total_matches > max_results,
    }


# ---------------------------------------------------------------------------
# verify_files
# ---------------------------------------------------------------------------


async def _verify_files(arguments: dict[str, Any]) -> dict[str, Any]:
    """Verify that files exist and optionally contain expected content.

    Args:
        verifications: List of dicts with:
          - path: file path
          - should_exist: bool (default True)
          - content_contains: optional string to check for
          - min_size: optional minimum size in bytes
          - max_size: optional maximum size in bytes
    """
    verifications = arguments.get("verifications", [])
    if not isinstance(verifications, list) or not verifications:
        return {"error": "verifications must be a non-empty list"}

    results: list[dict[str, Any]] = []
    all_passed = True

    for spec in verifications:
        if not isinstance(spec, dict):
            results.append({"error": "Invalid verification spec"})
            all_passed = False
            continue

        path_str = str(spec.get("path", "")).strip()
        should_exist = bool(spec.get("should_exist", True))
        content_contains = spec.get("content_contains")
        min_size = spec.get("min_size")
        max_size = spec.get("max_size")

        if not path_str:
            results.append({"error": "path is required"})
            all_passed = False
            continue

        try:
            target = _validate_path(path_str)
        except ToolError as exc:
            results.append({"path": path_str, "passed": False, "error": str(exc)})
            all_passed = False
            continue

        exists = target.exists() and target.is_file()
        checks: dict[str, Any] = {"path": path_str, "exists": exists}

        if should_exist and not exists:
            checks["passed"] = False
            checks["failure_reason"] = "File does not exist"
            results.append(checks)
            all_passed = False
            continue

        if not should_exist and exists:
            checks["passed"] = False
            checks["failure_reason"] = "File exists but should not"
            results.append(checks)
            all_passed = False
            continue

        if not should_exist and not exists:
            checks["passed"] = True
            results.append(checks)
            continue

        # File exists — run content/size checks
        try:
            content = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            checks["passed"] = False
            checks["failure_reason"] = f"Cannot read file: {exc}"
            results.append(checks)
            all_passed = False
            continue

        file_size = target.stat().st_size
        checks["size"] = file_size

        if content_contains is not None:
            found = content_contains in content
            checks["content_contains"] = content_contains
            checks["content_found"] = found
            if not found:
                checks["passed"] = False
                checks["failure_reason"] = f"Content '{content_contains[:100]}' not found"
                results.append(checks)
                all_passed = False
                continue

        if min_size is not None and file_size < int(min_size):
            checks["passed"] = False
            checks["failure_reason"] = f"File too small: {file_size} < {min_size}"
            results.append(checks)
            all_passed = False
            continue

        if max_size is not None and file_size > int(max_size):
            checks["passed"] = False
            checks["failure_reason"] = f"File too large: {file_size} > {max_size}"
            results.append(checks)
            all_passed = False
            continue

        checks["passed"] = True
        results.append(checks)

    return {
        "all_passed": all_passed,
        "results": results,
        "total": len(results),
    }


# ---------------------------------------------------------------------------
# FASE O: Test generation
# ---------------------------------------------------------------------------


async def _generate_test_file(
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Generate a test file for a Python module.

    This tool creates a skeleton test file with common test patterns.
    The LLM can then fill in the actual test logic.
    """
    module_path = str(arguments.get("module_path", "")).strip()
    framework = str(arguments.get("framework", "pytest")).strip().lower()
    test_dir = str(arguments.get("test_dir", "")).strip() or None
    test_name = str(arguments.get("test_name", "")).strip() or None

    if not module_path:
        return {"error": "module_path is required"}

    # Validate source exists
    try:
        src = _validate_path(module_path)
    except ToolError as exc:
        return {"error": str(exc)}

    if not src.exists():
        return {"error": f"Module not found: {module_path}"}

    if not src.is_file():
        return {"error": f"Not a file: {module_path}"}

    # Determine test file location
    if test_dir:
        try:
            test_path = _validate_path(test_dir, write=True)
        except ToolError as exc:
            return {"error": f"test_dir: {exc}"}
    else:
        # Default: tests/ directory next to the module
        test_path = src.parent.parent / "tests"
        if not test_path.exists():
            test_path = src.parent / "tests"

    # Determine test filename
    if test_name:
        test_filename = test_name if test_name.endswith(".py") else f"{test_name}.py"
    else:
        stem = src.stem
        test_filename = f"test_{stem}.py"

    test_file = test_path / test_filename

    # Generate skeleton test
    module_name = src.stem
    class_name = "".join(word.capitalize() for word in module_name.split("_"))

    if framework == "unittest":
        skeleton = f'''"""Tests for {module_name}."""
import unittest
from unittest.mock import AsyncMock, patch


class Test{class_name}(unittest.TestCase):
    """Test cases for {module_name}."""

    def test_placeholder(self) -> None:
        """TODO: Add real tests."""
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
'''
    else:
        skeleton = f'''"""Tests for {module_name}."""
import pytest
from unittest.mock import AsyncMock, patch


class Test{class_name}:
    """Test cases for {module_name}."""

    def test_placeholder(self) -> None:
        """TODO: Add real tests."""
        assert True

    @pytest.mark.asyncio
    async def test_async_placeholder(self) -> None:
        """TODO: Add async tests."""
        assert True
'''

    # Write test file
    try:
        test_path.mkdir(parents=True, exist_ok=True)
        test_file.write_text(skeleton, encoding="utf-8")
    except OSError as exc:
        return {"error": f"Failed to write test file: {exc}"}

    verified_exists = test_file.exists() and test_file.is_file()
    return {
        "result": "created",
        "test_file": str(test_file),
        "module_tested": str(src),
        "framework": framework,
        "verified_exists": verified_exists,
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_development_tools(registry: ToolRegistry) -> None:
    """Register development tools."""
    registry.register(
        ToolDefinition(
            "analyze_project",
            ToolRisk.LOW,
            False,
            _analyze_project,
            argument_schema={"path": "string"},
            optional_arguments=frozenset({"max_depth", "include_content"}),
            compact_description="Analyze project structure (types, sizes, counts)",
            argument_aliases={"directory": "path", "dir": "path", "project_path": "path"},
        )
    )
    registry.register(
        ToolDefinition(
            "read_files",
            ToolRisk.LOW,
            False,
            _read_files,
            argument_schema={"paths": "list"},
            compact_description="Read multiple files at once",
            argument_aliases={"file_paths": "paths", "files": "paths"},
        )
    )
    registry.register(
        ToolDefinition(
            "modify_file",
            ToolRisk.HIGH,
            True,
            _modify_file,
            argument_schema={
                "path": "string",
                "mode": "string",
            },
            optional_arguments=frozenset({"search", "replacement", "content"}),
            compact_description="Targeted file modification (replace/append/prepend/overwrite)",
            argument_aliases={
                "p": "path",
                "operation": "mode",
                "action": "mode",
                "type": "mode",
                "file": "path",
                "file_path": "path",
            },
        )
    )
    registry.register(
        ToolDefinition(
            "search_files",
            ToolRisk.LOW,
            False,
            _search_files,
            argument_schema={
                "path": "string",
                "pattern": "string",
            },
            optional_arguments=frozenset({"include", "max_results"}),
            compact_description="Search for pattern across files",
            argument_aliases={
                "query": "pattern",
                "search": "pattern",
                "regex": "pattern",
                "directory": "path",
                "dir": "path",
            },
        )
    )
    registry.register(
        ToolDefinition(
            "verify_files",
            ToolRisk.LOW,
            False,
            _verify_files,
            argument_schema={"verifications": "list"},
            compact_description="Verify files exist with expected content/size",
        )
    )

    # FASE O: Test generation tool
    async def _generate_tests(arguments: dict[str, Any]) -> dict[str, Any]:
        return await _generate_test_file(arguments)

    registry.register(
        ToolDefinition(
            "generate_tests",
            ToolRisk.MEDIUM,
            True,
            _generate_tests,
            argument_schema={"module_path": "string", "framework": "string"},
            optional_arguments=frozenset({"test_dir", "test_name"}),
            compact_description="Generate test file for a module (requires approval)",
            argument_aliases={"module": "module_path", "file": "module_path", "src": "module_path"},
        )
    )
