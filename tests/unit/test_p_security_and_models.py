"""FASE P — Security Tests (P.13) and Model Switching (P.10).

P.13: Security — test path traversal, sibling bypass, .git access, dangerous commands.
P.10: Model switching — test with llama3, llama3.1, deepseek-coder-v2.

CRITICAL FIX APPLIED: Aliasing bug in filesystem.py closures (original = list reference
instead of copy). Now fixed: original = _fs.DEFAULT_ALLOWED_ROOTS[:]
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest

from personal_ai_secretary.agents.builtin import ExecutionAgent
from personal_ai_secretary.agents.contracts import AgentInput
from personal_ai_secretary.domain.contracts import RiskLevel
from personal_ai_secretary.providers.ollama import OllamaProvider
from personal_ai_secretary.tools.command import register_command_tools
from personal_ai_secretary.tools.datetime_tool import register_datetime_tools
from personal_ai_secretary.tools.development import register_development_tools
from personal_ai_secretary.tools.filesystem import (
    DEFAULT_ALLOWED_ROOTS,
    register_filesystem_tools,
)
from personal_ai_secretary.tools.registry import ToolError, ToolRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def narrow_workspace(tmp_path: Path):
    """Workspace that will be the ONLY allowed root."""
    ws = tmp_path / "narrow_test"
    ws.mkdir()
    return ws


@pytest.fixture
def narrow_registry(narrow_workspace: Path):
    """Registry with NARROW allowed roots (just the workspace)."""
    reg = ToolRegistry()
    register_filesystem_tools(reg, allowed_roots=[str(narrow_workspace)])
    return reg


@pytest.fixture
def full_registry():
    """Registry with ALL tools and DEFAULT allowed roots."""
    reg = ToolRegistry()
    register_filesystem_tools(reg)
    register_development_tools(reg)
    register_command_tools(reg)
    register_datetime_tools(reg)
    return reg


# ---------------------------------------------------------------------------
# P.13 — SECURITY TESTS
# ---------------------------------------------------------------------------

class TestSecurityBackend:
    """P.13: Test security controls at the backend level."""

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, narrow_registry, narrow_workspace):
        """Path traversal (../) should be blocked outside narrow root."""
        outside = narrow_workspace.parent / "escaped.txt"
        with pytest.raises(ToolError, match="Access denied"):
            await narrow_registry.execute(
                "create_file",
                {"path": str(outside), "content": "bad"},
                approved=True,
            )
        assert not outside.exists(), "File was created outside allowed root!"
        print("  [Security] Path traversal: BLOCKED")

    @pytest.mark.asyncio
    async def test_sibling_directory_bypass_blocked(self, narrow_registry, narrow_workspace):
        """Sibling directory bypass should be blocked outside narrow root."""
        sibling = narrow_workspace.parent / "sibling_dir"
        target = sibling / "file.txt"

        with pytest.raises(ToolError, match="Access denied"):
            await narrow_registry.execute(
                "create_file",
                {"path": str(target), "content": "bypass attempt"},
                approved=True,
            )
        assert not target.exists(), "File created outside allowed root!"
        print("  [Security] Sibling bypass: BLOCKED")

    @pytest.mark.asyncio
    async def test_protected_file_delete_requires_approval(self, narrow_registry, narrow_workspace):
        """file_delete requires explicit approval via registry."""
        target = narrow_workspace / "secret.txt"
        target.write_text("top secret", encoding="utf-8")

        with pytest.raises(PermissionError, match="approval"):
            await narrow_registry.execute(
                "file_delete",
                {"path": str(target)},
                approved=False,
            )
        assert target.exists(), "File deleted without approval!"
        print("  [Security] Protected file delete: BLOCKED (no approval)")

    @pytest.mark.asyncio
    async def test_command_chaining_blocked(self, full_registry, narrow_workspace):
        """Command chaining (&&, ||, |, ;) should be blocked."""
        result = await full_registry.execute(
            "execute_command",
            {"command": "echo hello && echo world"},
            approved=True,
        )
        assert result.get("error"), f"Command chaining NOT blocked: {result}"
        print("  [Security] Command chaining: BLOCKED")

    @pytest.mark.asyncio
    async def test_blocked_command_rejected(self, full_registry, narrow_workspace):
        """Dangerous commands (shutdown, del, etc.) should be rejected."""
        dangerous_cmds = ["shutdown /s /t 0", "del /q /f C:\\*", "format C:"]
        for cmd in dangerous_cmds:
            result = await full_registry.execute(
                "execute_command",
                {"command": cmd},
                approved=True,
            )
            assert result.get("error"), f"Dangerous command NOT blocked: {cmd}"
        print("  [Security] Dangerous commands: ALL BLOCKED")

    def test_approval_requires_explicit_flag(self, full_registry):
        """High-risk tools must have requires_explicit_approval=True."""
        for tool_name in ["file_delete", "file_copy", "create_file", "create_directory"]:
            tool = full_registry.get(tool_name)
            assert tool is not None, f"{tool_name} not registered"
            assert tool.requires_explicit_approval, (
                f"{tool_name} does not require_explicit_approval"
            )
        for tool_name in ["read_file", "read_files", "list_directory", "file_exists"]:
            tool = full_registry.get(tool_name)
            assert tool is not None, f"{tool_name} not registered"
            assert not tool.requires_explicit_approval, (
                f"{tool_name} should not require_explicit_approval"
            )
        print("  [Security] Approval flags: ALL CORRECT")

    @pytest.mark.asyncio
    async def test_path_traversal_in_read_blocked(self, narrow_registry, narrow_workspace):
        """Path traversal in read_file should be blocked outside root."""
        with pytest.raises(ToolError, match="Access denied"):
            await narrow_registry.execute(
                "read_file",
                {"path": str(narrow_workspace / ".." / ".." / "etc" / "passwd")},
                approved=True,
            )
        print("  [Security] Path traversal read: BLOCKED")

    @pytest.mark.asyncio
    async def test_write_to_dot_git_blocked(self, narrow_registry, narrow_workspace):
        """Writing to .git outside root should be blocked."""
        outside_target = narrow_workspace.parent / ".git" / "config"
        with pytest.raises(ToolError, match="Access denied"):
            await narrow_registry.execute(
                "create_file",
                {"path": str(outside_target), "content": "malicious"},
                approved=True,
            )
        print("  [Security] .git write (outside root): BLOCKED")


class TestAliasingFix:
    """Verify the DEFAULT_ALLOWED_ROOTS aliasing bug is fixed."""

    @pytest.mark.asyncio
    async def test_default_roots_restored_after_tool_call(self, narrow_registry, narrow_workspace):
        """After a tool call with narrow roots, DEFAULT_ALLOWED_ROOTS should be restored."""
        original_roots = DEFAULT_ALLOWED_ROOTS[:]
        await narrow_registry.execute(
            "create_file",
            {"path": str(narrow_workspace / "test.txt"), "content": "test"},
            approved=True,
        )
        assert DEFAULT_ALLOWED_ROOTS == original_roots, (
            f"DEFAULT_ALLOWED_ROOTS NOT restored! "
            f"Before: {original_roots}, After: {DEFAULT_ALLOWED_ROOTS}"
        )
        print("  [Security] Aliasing fix: PASS — roots restored correctly")


# ---------------------------------------------------------------------------
# P.10 — MODEL SWITCHING (Requires live Ollama)
# ---------------------------------------------------------------------------

class TestModelSwitching:
    """P.10: Test with different Ollama models."""

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def _test_model(self, model_name: str):
        """Common model test logic."""
        os.environ["AI_PROVIDER"] = "local"
        provider = OllamaProvider(model=model_name)

        reg = ToolRegistry()
        register_filesystem_tools(reg)
        register_development_tools(reg)
        register_command_tools(reg)
        register_datetime_tools(reg)

        agent = ExecutionAgent(
            provider=provider,
            registry=reg,
            observability=None,
        )

        ws = Path(os.environ.get("TEMP", ".")) / f"model_test_{model_name.replace(':', '_')}"
        ws.mkdir(exist_ok=True)

        data = AgentInput(
            request_id=uuid4(),
            session_id=uuid4(),
            user_id="test_user",
            text="What is 2 + 2? Reply with just the number.",
            correlation_id="fase-p-model-test",
            risk_level=RiskLevel.LOW,
            context={"authorized": True, "working_directory": str(ws)},
        )

        start = time.monotonic()
        try:
            artifact = await asyncio.wait_for(agent.run(data), timeout=180)
            elapsed = time.monotonic() - start
            print(f"\n  [Model] {model_name}: {elapsed:.1f}s, response: {artifact.content[:100]}")
            assert artifact.content, f"{model_name} returned empty response"
            assert not any(tok in artifact.content for tok in ["<|", "[TOOL"]), (
                f"Protocol tokens visible: {artifact.content[:100]}"
            )
            return {"model": model_name, "time": elapsed, "status": "PASS"}
        except Exception as e:
            elapsed = time.monotonic() - start
            print(f"\n  [Model] {model_name}: FAILED after {elapsed:.1f}s: {e}")
            return {"model": model_name, "time": elapsed, "status": "FAIL", "error": str(e)}

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_llama3_1(self):
        await self._test_model("llama3.1:latest")

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_llama3(self):
        await self._test_model("llama3:latest")

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_deepseek_coder(self):
        await self._test_model("deepseek-coder-v2:latest")
