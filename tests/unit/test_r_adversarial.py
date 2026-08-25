"""FASE R — R.11: Adversarial testing.

Tests backend security controls directly (no LLM, no approval blocking).
Verifies path traversal, sibling bypass, dangerous commands, protected files,
prompt injection handling, and false completion prevention.
"""
import os
from pathlib import Path

import pytest

from personal_ai_secretary.tools import filesystem as _fs
from personal_ai_secretary.tools.registry import (
    ToolRegistry,
    ToolError,
    ToolRisk,
)
from personal_ai_secretary.tools.filesystem import (
    register_filesystem_tools,
    PROTECTED_FILES,
    DEFAULT_ALLOWED_ROOTS,
)
from personal_ai_secretary.tools.command import register_command_tools
from personal_ai_secretary.tools.development import register_development_tools


@pytest.fixture
def narrow_root(tmp_path):
    """Narrow root directory — only this dir is allowed."""
    p = tmp_path / "narrow"
    p.mkdir()
    (p / "data").mkdir()
    (p / "data" / "secret.txt").write_text("password123", encoding="utf-8")
    return p


@pytest.fixture
def narrow_registry(narrow_root):
    """Registry with only narrow_root as allowed root."""
    reg = ToolRegistry()
    register_filesystem_tools(reg, allowed_roots=[str(narrow_root)])
    register_command_tools(reg)
    return reg


@pytest.fixture
def full_registry():
    """Registry with default roots."""
    reg = ToolRegistry()
    register_filesystem_tools(reg)
    register_development_tools(reg)
    register_command_tools(reg)
    return reg


# --- Path Traversal (raises ToolError directly) ---

@pytest.mark.asyncio
async def test_path_traversal_create_blocked(narrow_registry, narrow_root):
    """R.11: Path traversal with ../ should raise ToolError."""
    outside = narrow_root.parent / "escaped.txt"
    with pytest.raises(ToolError, match="Access denied"):
        await narrow_registry.execute(
            "create_file",
            {"path": str(outside), "content": "pwned"},
            approved=True,
        )
    assert not outside.exists(), "File created outside allowed root!"


@pytest.mark.asyncio
async def test_path_traversal_read_blocked(narrow_registry, narrow_root):
    """R.11: Path traversal in read should raise ToolError."""
    malicious = narrow_root / ".." / ".." / "Windows" / "System32" / "config" / "SAM"
    with pytest.raises(ToolError, match="Access denied"):
        await narrow_registry.execute(
            "read_file",
            {"path": str(malicious)},
            approved=True,
        )


@pytest.mark.asyncio
async def test_sibling_bypass_blocked(narrow_registry, narrow_root):
    """R.11: Sibling directory bypass should raise ToolError."""
    sibling = narrow_root.parent / "sibling_attack.txt"
    with pytest.raises(ToolError, match="Access denied"):
        await narrow_registry.execute(
            "create_file",
            {"path": str(sibling), "content": "attack"},
            approved=True,
        )
    assert not sibling.exists(), "File created outside allowed root!"


@pytest.mark.asyncio
async def test_system32_creation_blocked(narrow_registry):
    """R.11: Creating files in Windows System32 should be blocked."""
    system32 = Path(r"C:\Windows\System32\chiky_test_adversarial.txt")
    with pytest.raises(ToolError, match="Access denied"):
        await narrow_registry.execute(
            "create_file",
            {"path": str(system32), "content": "pwned"},
            approved=True,
        )
    assert not system32.exists(), "File created in System32!"


# --- Protected Files ---

@pytest.mark.asyncio
async def test_protected_files_blocklist():
    """R.11: PROTECTED_FILES should contain critical config files."""
    critical = {"pyproject.toml", "setup.py", "setup.cfg", "package.json",
                ".gitignore", "Dockerfile", "docker-compose.yml"}
    for name in critical:
        assert name in PROTECTED_FILES, f"{name} missing from PROTECTED_FILES"


@pytest.mark.asyncio
async def test_protected_file_delete_blocked(narrow_registry, narrow_root):
    """R.11: Deleting a protected file should return error."""
    target = narrow_root / "pyproject.toml"
    target.write_text("[project]\nname='test'", encoding="utf-8")
    result = await narrow_registry.execute(
        "file_delete",
        {"path": str(target)},
        approved=True,
    )
    assert result.get("error"), f"Protected file delete NOT blocked: {result}"
    assert target.exists(), "Protected file was deleted!"


# --- Dangerous Commands ---

@pytest.mark.asyncio
async def test_dangerous_command_blocked(full_registry):
    """R.11: Dangerous commands should return error."""
    dangerous = [
        "del /f /q C:\\test.txt",
        "format C: /y",
        "shutdown /s /t 0",
        "rd /s /q C:\\test",
    ]
    for cmd in dangerous:
        result = await full_registry.execute(
            "execute_command",
            {"command": cmd},
            approved=True,
        )
        assert result.get("error"), f"Dangerous command NOT blocked: {cmd}"


@pytest.mark.asyncio
async def test_command_chaining_blocked(full_registry):
    """R.11: Command chaining operators should be blocked."""
    chaining = [
        "echo hello && echo world",
        "echo hello || echo world",
        "echo hello | more",
        "echo hello; echo world",
    ]
    for cmd in chaining:
        result = await full_registry.execute(
            "execute_command",
            {"command": cmd},
            approved=True,
        )
        assert result.get("error"), f"Command chaining NOT blocked: {cmd}"


# --- False Completion Prevention ---

@pytest.mark.asyncio
async def test_read_nonexistent_file_returns_error(narrow_registry, narrow_root):
    """R.11: Reading nonexistent file should return error dict."""
    result = await narrow_registry.execute(
        "read_file",
        {"path": str(narrow_root / "nonexistent.py")},
        approved=True,
    )
    assert result.get("error"), f"Read nonexistent NOT blocked: {result}"


@pytest.mark.asyncio
async def test_delete_nonexistent_file_returns_error(narrow_registry, narrow_root):
    """R.11: Deleting nonexistent file should return error."""
    result = await narrow_registry.execute(
        "file_delete",
        {"path": str(narrow_root / "nonexistent.py")},
        approved=True,
    )
    assert result.get("error"), f"Delete nonexistent NOT blocked: {result}"


# --- Approval Enforcement ---

@pytest.mark.asyncio
async def test_create_file_requires_approval(narrow_registry, narrow_root):
    """R.11: File creation should require approval."""
    with pytest.raises(PermissionError):
        await narrow_registry.execute(
            "create_file",
            {"path": str(narrow_root / "test.txt"), "content": "test"},
            approved=False,
        )


@pytest.mark.asyncio
async def test_delete_requires_approval(narrow_registry, narrow_root):
    """R.11: File deletion should require approval."""
    target = narrow_root / "to_delete.txt"
    target.write_text("delete me", encoding="utf-8")
    with pytest.raises(PermissionError):
        await narrow_registry.execute(
            "file_delete",
            {"path": str(target)},
            approved=False,
        )


@pytest.mark.asyncio
async def test_command_requires_approval(full_registry):
    """R.11: Command execution should require approval."""
    with pytest.raises(PermissionError):
        await full_registry.execute(
            "execute_command",
            {"command": "echo hello"},
            approved=False,
        )


# --- Tool Registry Integrity ---

@pytest.mark.asyncio
async def test_read_tools_no_approval(full_registry):
    """R.11: Read-only tools should not require approval."""
    for name in ["read_file", "list_directory", "file_exists"]:
        tool = full_registry.get(name)
        assert tool is not None, f"{name} not registered"
        assert not tool.requires_explicit_approval, f"{name} should not require approval"


@pytest.mark.asyncio
async def test_write_tools_require_approval(full_registry):
    """R.11: Write tools should require approval."""
    for name in ["create_file", "write_file", "file_delete", "file_copy", "create_directory"]:
        tool = full_registry.get(name)
        assert tool is not None, f"{name} not registered"
        assert tool.requires_explicit_approval, f"{name} should require approval"


@pytest.mark.asyncio
async def test_all_registered_tools(full_registry):
    """R.11: Verify core tools are registered."""
    expected = {
        "create_file", "read_file", "write_file", "list_directory",
        "create_directory", "file_exists", "file_delete", "file_copy",
        "execute_command", "generate_tests",
    }
    registered = set(full_registry.names())
    missing = expected - registered
    assert not missing, f"Missing tools: {missing}"


@pytest.mark.asyncio
async def test_default_allowed_roots_not_mutated(narrow_registry, narrow_root):
    """R.11: DEFAULT_ALLOWED_ROOTS should not be mutated after tool calls."""
    original = DEFAULT_ALLOWED_ROOTS[:]
    await narrow_registry.execute(
        "list_directory",
        {"path": str(narrow_root)},
        approved=True,
    )
    assert DEFAULT_ALLOWED_ROOTS == original, (
        f"DEFAULT_ALLOWED_ROOTS was mutated! "
        f"Before: {original}, After: {DEFAULT_ALLOWED_ROOTS}"
    )
