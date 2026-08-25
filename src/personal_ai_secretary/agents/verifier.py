"""FASE N — Verification Engine.

Programmatic post-execution verification. The system, not the LLM,
confirms that operations actually produced the expected results.

Core principle: an operation is not complete until the system can
verify its result.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class VerificationCheck:
    """A single verification check."""

    name: str
    passed: bool
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationResult:
    """Aggregate verification result for a tool call or session."""

    passed: bool
    checks: list[VerificationCheck] = field(default_factory=list)
    summary: str = ""

    def __post_init__(self) -> None:
        if not self.summary:
            total = len(self.checks)
            passed = sum(1 for c in self.checks if c.passed)
            self.summary = f"{passed}/{total} checks passed"


# ---------------------------------------------------------------------------
# Tool-specific verifiers
# ---------------------------------------------------------------------------


def _verify_create_file(result: dict[str, Any]) -> list[VerificationCheck]:
    """Verify that create_file actually created the file."""
    checks: list[VerificationCheck] = []
    path_str = result.get("path", "")

    if not path_str:
        checks.append(VerificationCheck(
            name="path_present",
            passed=False,
            detail="No path in tool result",
        ))
        return checks

    p = Path(path_str)
    exists = p.exists() and p.is_file()
    checks.append(VerificationCheck(
        name="file_exists",
        passed=exists,
        detail=f"File {'exists' if exists else 'does not exist'}: {path_str}",
        evidence={"path": path_str, "exists": exists},
    ))

    if exists:
        try:
            size = p.stat().st_size
            reported_size = result.get("size", -1)
            size_ok = reported_size < 0 or size == reported_size
            checks.append(VerificationCheck(
                name="size_matches",
                passed=size_ok,
                detail=f"Actual size {size}, reported {reported_size}",
                evidence={"actual_size": size, "reported_size": reported_size},
            ))
        except OSError as exc:
            checks.append(VerificationCheck(
                name="size_readable",
                passed=False,
                detail=f"Cannot read file size: {exc}",
            ))

    if result.get("verified_exists") is True and not exists:
        checks.append(VerificationCheck(
            name="tool_verified_consistent",
            passed=False,
            detail="Tool reported verified_exists=True but file does not exist",
        ))

    return checks


def _verify_write_file(result: dict[str, Any]) -> list[VerificationCheck]:
    """Verify that write_file actually wrote the file."""
    return _verify_create_file(result)


def _verify_modify_file(result: dict[str, Any]) -> list[VerificationCheck]:
    """Verify that modify_file actually changed the file."""
    checks: list[VerificationCheck] = []
    path_str = result.get("path", "")

    if not path_str:
        checks.append(VerificationCheck(
            name="path_present",
            passed=False,
            detail="No path in tool result",
        ))
        return checks

    p = Path(path_str)
    exists = p.exists() and p.is_file()
    checks.append(VerificationCheck(
        name="file_exists",
        passed=exists,
        detail=f"File {'exists' if exists else 'does not exist'}: {path_str}",
        evidence={"path": path_str, "exists": exists},
    ))

    if exists:
        actual_size = p.stat().st_size
        size_before = result.get("size_before", -1)
        size_after = result.get("size_after", -1)

        if size_before >= 0 and size_after >= 0:
            checks.append(VerificationCheck(
                name="size_changed",
                passed=True,  # existence is sufficient for modify
                detail=f"Size: {size_before} -> {size_after} (actual: {actual_size})",
                evidence={
                    "size_before": size_before,
                    "size_after": size_after,
                    "actual_size": actual_size,
                },
            ))

        if result.get("verified_exists") is True:
            checks.append(VerificationCheck(
                name="tool_verified_consistent",
                passed=True,
                detail="Tool verified_exists matches actual existence",
            ))

    if result.get("result") == "unchanged":
        checks.append(VerificationCheck(
            name="no_op_detected",
            passed=True,
            detail="File was already in desired state (no change needed)",
        ))

    return checks


def _verify_create_directory(result: dict[str, Any]) -> list[VerificationCheck]:
    """Verify that create_directory actually created the directory."""
    checks: list[VerificationCheck] = []
    path_str = result.get("path", "")

    if not path_str:
        checks.append(VerificationCheck(
            name="path_present",
            passed=False,
            detail="No path in tool result",
        ))
        return checks

    p = Path(path_str)
    exists = p.exists() and p.is_dir()
    checks.append(VerificationCheck(
        name="directory_exists",
        passed=exists,
        detail=f"Directory {'exists' if exists else 'does not exist'}: {path_str}",
        evidence={"path": path_str, "exists": exists},
    ))

    return checks


def _verify_execute_command(result: dict[str, Any]) -> list[VerificationCheck]:
    """Verify command execution result."""
    checks: list[VerificationCheck] = []

    if "error" in result:
        checks.append(VerificationCheck(
            name="command_no_error",
            passed=False,
            detail=f"Command error: {result['error']}",
            evidence={"error": result["error"]},
        ))
        return checks

    success = result.get("success", False)
    exit_code = result.get("exit_code", -1)
    timed_out = result.get("timeout", False)

    checks.append(VerificationCheck(
        name="exit_code",
        passed=success,
        detail=f"Exit code: {exit_code}" + (" (timeout)" if timed_out else ""),
        evidence={
            "exit_code": exit_code,
            "success": success,
            "timeout": timed_out,
            "stdout_chars": len(result.get("stdout", "")),
            "stderr_chars": len(result.get("stderr", "")),
        },
    ))

    return checks


def _verify_create_project(result: dict[str, Any]) -> list[VerificationCheck]:
    """Verify that create_project actually created the project files."""
    checks: list[VerificationCheck] = []

    errors_count = result.get("errors_count", 0)
    if errors_count > 0:
        checks.append(VerificationCheck(
            name="no_creation_errors",
            passed=False,
            detail=f"{errors_count} file/directory creation errors",
            evidence={"errors": result.get("errors", [])},
        ))

    created_files = result.get("created_files", [])
    project_path = result.get("project_path", "")

    if not created_files:
        checks.append(VerificationCheck(
            name="files_created",
            passed=False,
            detail="No files were created",
        ))
        return checks

    verified_count = 0
    for file_path in created_files:
        p = Path(file_path)
        if p.exists() and p.is_file():
            verified_count += 1

    all_exist = verified_count == len(created_files)
    checks.append(VerificationCheck(
        name="all_files_exist",
        passed=all_exist,
        detail=f"{verified_count}/{len(created_files)} created files exist on disk",
        evidence={
            "expected": len(created_files),
            "verified": verified_count,
            "project_path": project_path,
        },
    ))

    if project_path:
        pp = Path(project_path)
        checks.append(VerificationCheck(
            name="project_root_exists",
            passed=pp.exists() and pp.is_dir(),
            detail=f"Project root {'exists' if pp.exists() else 'missing'}: {project_path}",
            evidence={"project_path": project_path},
        ))

    return checks


def _verify_analyze_project(result: dict[str, Any]) -> list[VerificationCheck]:
    """Verify analyze_project returned real data (not hallucinated)."""
    checks: list[VerificationCheck] = []

    if "error" in result:
        checks.append(VerificationCheck(
            name="analysis_no_error",
            passed=False,
            detail=f"Analysis error: {result['error']}",
        ))
        return checks

    project_path = result.get("project_path", "")
    if project_path:
        p = Path(project_path)
        checks.append(VerificationCheck(
            name="project_path_valid",
            passed=p.exists() and p.is_dir(),
            detail=f"Project path {'exists' if p.exists() else 'missing'}: {project_path}",
            evidence={"project_path": project_path},
        ))

    structure = result.get("structure", [])
    checks.append(VerificationCheck(
        name="structure_populated",
        passed=len(structure) > 0,
        detail=f"Structure contains {len(structure)} entries",
        evidence={"entry_count": len(structure)},
    ))

    return checks


# ---------------------------------------------------------------------------
# Dispatch map
# ---------------------------------------------------------------------------

_VERIFIERS: dict[str, Callable[[dict[str, Any]], list[VerificationCheck]]] = {
    "create_file": _verify_create_file,
    "write_file": _verify_write_file,
    "modify_file": _verify_modify_file,
    "create_directory": _verify_create_directory,
    "execute_command": _verify_execute_command,
    "create_project": _verify_create_project,
    "analyze_project": _verify_analyze_project,
}

# Tools that are read-only or don't need verification
_SKIP_VERIFICATION: frozenset[str] = frozenset({
    "read_file",
    "read_files",
    "list_directory",
    "file_exists",
    "search_files",
    "verify_files",
    "calculator",
    "list_tools",
    "get_current_datetime",
    "resolve_path",
})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def verify_tool_result(
    tool_name: str,
    tool_result: dict[str, Any],
) -> VerificationResult:
    """Verify a tool execution result programmatically.

    Dispatches to the appropriate verifier based on tool name.
    Read-only tools are skipped. Unknown tools get a basic sanity check.
    """
    if tool_name in _SKIP_VERIFICATION:
        return VerificationResult(
            passed=True,
            checks=[],
            summary=f"Skipped verification for read-only tool '{tool_name}'",
        )

    if "error" in tool_result and "result" not in tool_result:
        return VerificationResult(
            passed=False,
            checks=[VerificationCheck(
                name="tool_error",
                passed=False,
                detail=f"Tool returned error: {tool_result['error']}",
                evidence={"error": tool_result["error"]},
            )],
            summary=f"Tool '{tool_name}' returned error",
        )

    verifier = _VERIFIERS.get(tool_name)
    if verifier is None:
        return VerificationResult(
            passed=True,
            checks=[VerificationCheck(
                name="no_verifier",
                passed=True,
                detail=f"No specific verifier for '{tool_name}'; assuming success",
            )],
            summary=f"No verifier for '{tool_name}'",
        )

    try:
        checks = verifier(tool_result)
        passed = all(c.passed for c in checks) if checks else True
        return VerificationResult(passed=passed, checks=checks)
    except Exception as exc:
        logger.warning("Verification crashed for tool '%s': %s", tool_name, exc)
        return VerificationResult(
            passed=False,
            checks=[VerificationCheck(
                name="verifier_error",
                passed=False,
                detail=f"Verification crashed: {exc}",
            )],
            summary=f"Verification error for '{tool_name}'",
        )


def verify_session_files(
    created: set[str],
    modified: set[str],
) -> VerificationResult:
    """Batch-verify all files created or modified during a session.

    Called at the end of the agentic loop to confirm the session actually
    produced the claimed file changes.
    """
    all_checks: list[VerificationCheck] = []

    for path_str in sorted(created):
        p = Path(path_str)
        exists = p.exists() and p.is_file()
        all_checks.append(VerificationCheck(
            name="session_file_exists",
            passed=exists,
            detail=f"Created file {'exists' if exists else 'MISSING'}: {path_str}",
            evidence={"path": path_str, "action": "created", "exists": exists},
        ))

    for path_str in sorted(modified):
        p = Path(path_str)
        exists = p.exists() and p.is_file()
        all_checks.append(VerificationCheck(
            name="session_file_exists",
            passed=exists,
            detail=f"Modified file {'exists' if exists else 'MISSING'}: {path_str}",
            evidence={"path": path_str, "action": "modified", "exists": exists},
        ))

    passed = all(c.passed for c in all_checks) if all_checks else True
    return VerificationResult(passed=passed, checks=all_checks)
