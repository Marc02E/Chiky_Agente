"""FASE K.6 - Unit tests for secure command execution."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from personal_ai_secretary.tools.command import (
    ALLOWED_EXECUTABLES,
    BLOCKED_EXECUTABLES,
    _execute_command,
    _extract_executable_name,
    _has_chaining,
    _has_path_traversal,
    _strip_quoted_portions,
    _truncate_output,
    register_command_tools,
    validate_command,
    validate_working_directory,
)
from personal_ai_secretary.tools.registry import ToolError, ToolRegistry, ToolRisk


def _tool_response(tool_name: str, args: dict) -> str:
    block = json.dumps({"tool": tool_name, "args": args})
    return "```tool\n" + block + "\n```"


class TestAllowlist:
    def test_allowed_executables_present(self) -> None:
        for name in ("python", "pytest", "node", "git", "where"):
            assert name in ALLOWED_EXECUTABLES

    def test_blocked_executables_present(self) -> None:
        for name in ("powershell", "cmd", "shutdown", "del", "rmdir", "diskpart", "net", "sc"):
            assert name in BLOCKED_EXECUTABLES

    def test_disjoint(self) -> None:
        assert ALLOWED_EXECUTABLES.isdisjoint(BLOCKED_EXECUTABLES)


class TestExtractExecutableName:
    def test_bare_name(self) -> None:
        assert _extract_executable_name("python --version") == "python"

    def test_full_path_windows(self) -> None:
        assert _extract_executable_name("C:\\Python313\\python.exe --version") == "python"

    def test_extension_stripped(self) -> None:
        assert _extract_executable_name("git.exe status") == "git"
        assert _extract_executable_name("npm.cmd install") == "npm"

    def test_empty(self) -> None:
        assert _extract_executable_name("") == ""
        assert _extract_executable_name("   ") == ""

    def test_case_insensitive(self) -> None:
        assert _extract_executable_name("PYTHON --version") == "python"


class TestChainingDetection:
    def test_simple_commands(self) -> None:
        for cmd in ("python --version", "pytest tests/", "git status"):
            assert not _has_chaining(cmd)

    def test_double_ampersand(self) -> None:
        assert _has_chaining("python --version && pytest")

    def test_double_pipe(self) -> None:
        assert _has_chaining("python script.py || echo failed")

    def test_single_pipe(self) -> None:
        assert _has_chaining("dir | findstr .py")

    def test_semicolon(self) -> None:
        assert _has_chaining("python --version; pytest")

    def test_redirect(self) -> None:
        assert _has_chaining("dir > output.txt")
        assert _has_chaining("echo test >> log.txt")
        assert _has_chaining("type < input.txt")

    def test_backtick(self) -> None:
        assert _has_chaining("`whoami`")

    def test_dollar_paren(self) -> None:
        assert _has_chaining("$(whoami)")

    def test_dollar_brace(self) -> None:
        assert _has_chaining("echo ${HOME}")

    def test_malformed_quotes(self) -> None:
        assert _has_chaining('echo "unclosed')

    def test_semicolon_inside_quoted_arg(self) -> None:
        assert not _has_chaining('python -c "import sys; sys.exit(42)"')

    def test_semicolon_inside_quoted_arg_stderr(self) -> None:
        assert not _has_chaining(
            'python -c "import sys; sys.stderr.write(\'err\'); sys.exit(1)"'
        )

    def test_pipe_inside_quoted_arg(self) -> None:
        assert not _has_chaining('python -c "print(\'a | b\')"')


class TestStripQuotedPortions:
    def test_no_quotes(self) -> None:
        assert _strip_quoted_portions("python --version") == "python --version"

    def test_double_quoted_arg(self) -> None:
        assert _strip_quoted_portions('python -c "print(1)"') == "python -c "

    def test_single_quoted_arg(self) -> None:
        assert _strip_quoted_portions("python -c 'print(1)'") == "python -c "

    def test_mixed_quotes(self) -> None:
        assert _strip_quoted_portions('python -c "import sys; sys.exit(1)"') == "python -c "

    def test_metachar_outside_quotes(self) -> None:
        assert _strip_quoted_portions('python -c "code" && echo hi') == "python -c  && echo hi"

    def test_semicolon_outside_quotes(self) -> None:
        assert _strip_quoted_portions("python; pytest") == "python; pytest"

    def test_empty_string(self) -> None:
        assert _strip_quoted_portions("") == ""


class TestPathTraversalDetection:
    def test_no_traversal(self) -> None:
        assert not _has_path_traversal("python script.py")
        assert not _has_path_traversal("pytest tests/test_foo.py")

    def test_dot_dot_in_arg(self) -> None:
        assert _has_path_traversal("python ../escape.py")
        assert _has_path_traversal("type ../../etc/passwd")

    def test_dot_dot_not_in_executable(self) -> None:
        assert not _has_path_traversal("python --version")


class TestValidateCommand:
    def test_valid_simple(self) -> None:
        assert validate_command("python --version") == "python --version"

    def test_valid_pytest(self) -> None:
        assert validate_command("pytest tests/") == "pytest tests/"

    def test_valid_git(self) -> None:
        assert validate_command("git status") == "git status"

    def test_empty_raises(self) -> None:
        with pytest.raises(ToolError, match="Empty command"):
            validate_command("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ToolError, match="Empty command"):
            validate_command("   ")

    def test_chaining_rejected(self) -> None:
        with pytest.raises(ToolError, match="chaining"):
            validate_command("python && pytest")

    def test_pipe_rejected(self) -> None:
        with pytest.raises(ToolError, match="chaining"):
            validate_command("dir | findstr .py")

    def test_semicolon_rejected(self) -> None:
        with pytest.raises(ToolError, match="chaining"):
            validate_command("python; pytest")

    def test_redirect_rejected(self) -> None:
        with pytest.raises(ToolError, match="chaining"):
            validate_command("dir > output.txt")

    def test_backtick_rejected(self) -> None:
        with pytest.raises(ToolError, match="chaining"):
            validate_command("`whoami`")

    def test_dollar_paren_rejected(self) -> None:
        with pytest.raises(ToolError, match="chaining"):
            validate_command("$(whoami)")

    def test_path_traversal_rejected(self) -> None:
        with pytest.raises(ToolError, match="path traversal"):
            validate_command("python ../escape.py")

    def test_powershell_blocked(self) -> None:
        with pytest.raises(ToolError, match="blocked"):
            validate_command("powershell -Command Get-Date")

    def test_cmd_blocked(self) -> None:
        with pytest.raises(ToolError, match="blocked"):
            validate_command("cmd /c dir")

    def test_shutdown_blocked(self) -> None:
        with pytest.raises(ToolError, match="blocked"):
            validate_command("shutdown /s /t 0")

    def test_unlisted_rejected(self) -> None:
        with pytest.raises(ToolError, match="not in the allowlist"):
            validate_command("curl http://example.com")

    def test_strip_whitespace(self) -> None:
        assert validate_command("  python --version  ") == "python --version"

    def test_full_path_allowed(self) -> None:
        result = validate_command("C:\\Python313\\python.exe --version")
        assert result == "C:\\Python313\\python.exe --version"


class TestValidateWorkingDirectory:
    def test_none_returns_cwd(self) -> None:
        result = validate_working_directory(None)
        assert result == str(Path.cwd())

    def test_empty_returns_cwd(self) -> None:
        result = validate_working_directory("")
        assert result == str(Path.cwd())

    def test_existing_directory(self, tmp_path: Path) -> None:
        result = validate_working_directory(str(tmp_path))
        assert result == str(tmp_path)

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        fake = tmp_path / "nonexistent"
        with pytest.raises(ToolError, match="does not exist"):
            validate_working_directory(str(fake))

    def test_file_not_directory(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("data")
        with pytest.raises(ToolError, match="not a directory"):
            validate_working_directory(str(f))

    def test_outside_allowed_roots(self, tmp_path: Path) -> None:
        with pytest.raises(ToolError, match="outside allowed"):
            validate_working_directory(
                str(tmp_path), allowed_roots=["C:\\Users\\other"]
            )

    def test_within_allowed_roots(self, tmp_path: Path) -> None:
        result = validate_working_directory(
            str(tmp_path), allowed_roots=[str(tmp_path.parent)]
        )
        assert result == str(tmp_path)


class TestTruncateOutput:
    def test_short_not_truncated(self) -> None:
        text, truncated = _truncate_output("hello", 100)
        assert text == "hello"
        assert not truncated

    def test_exact_limit(self) -> None:
        _, truncated = _truncate_output("x" * 100, 100)
        assert not truncated

    def test_long_truncated(self) -> None:
        result, truncated = _truncate_output("x" * 10_000, 1000)
        assert truncated
        assert len(result) <= 1200
        assert "truncated" in result.lower()

    def test_head_tail_preserved(self) -> None:
        text = "HEAD" + "M" * 1000 + "TAIL"
        result, truncated = _truncate_output(text, 200)
        assert truncated
        assert result.startswith("HEAD")
        assert result.endswith("TAIL")


class TestExecuteCommand:
    @pytest.mark.asyncio
    async def test_empty_command(self) -> None:
        result = await _execute_command({"command": ""})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_blocked_command(self) -> None:
        result = await _execute_command({"command": "powershell -Command Get-Date"})
        assert "error" in result
        assert "blocked" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_chaining_rejected(self) -> None:
        result = await _execute_command({"command": "python && pytest"})
        assert "error" in result
        assert "chaining" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_unlisted_executable(self) -> None:
        result = await _execute_command({"command": "curl http://example.com"})
        assert "error" in result
        assert "not in the allowlist" in result["error"]

    @pytest.mark.asyncio
    async def test_python_success(self) -> None:
        result = await _execute_command(
            {"command": 'python -c "print(42)"'}
        )
        assert result.get("success") is True
        assert result["exit_code"] == 0
        assert "42" in result["stdout"]

    @pytest.mark.asyncio
    async def test_nonexistent_working_dir(self) -> None:
        result = await _execute_command(
            {"command": 'python -c "print(1)"', "working_directory": "Z:\\nonexistent"}
        )
        assert "error" in result
        assert "does not exist" in result["error"]

    @pytest.mark.asyncio
    async def test_exit_code_nonzero(self) -> None:
        result = await _execute_command(
            {"command": 'python -c "import sys; sys.exit(42)"'}
        )
        assert result.get("success") is False
        assert result["exit_code"] == 42

    @pytest.mark.asyncio
    async def test_output_truncation(self, tmp_path: Path) -> None:
        script = tmp_path / "big.py"
        script.write_text('print("x" * 50000)')
        result = await _execute_command(
            {"command": f"python {script}", "working_directory": str(tmp_path)}
        )
        assert result.get("success") is True
        assert result.get("output_truncated") is True

    @pytest.mark.asyncio
    async def test_stderr_captured(self) -> None:
        result = await _execute_command(
            {"command": 'python -c "import sys; sys.stderr.write(chr(101)+chr(114)+chr(114)+chr(95)+chr(109)+chr(115)+chr(103)); sys.exit(1)"'}
        )
        assert result.get("success") is False
        assert "err_msg" in result["stderr"]

    @pytest.mark.asyncio
    async def test_working_directory_used(self, tmp_path: Path) -> None:
        result = await _execute_command(
            {
                "command": 'python -c "import os; print(os.getcwd())"',
                "working_directory": str(tmp_path),
            }
        )
        assert result.get("success") is True
        assert result["working_directory"] == str(tmp_path)

    @pytest.mark.asyncio
    async def test_duration_recorded(self) -> None:
        result = await _execute_command(
            {"command": 'python -c "print(1)"'}
        )
        assert result["duration"] >= 0.0

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self) -> None:
        result = await _execute_command({"command": "python ../escape.py"})
        assert "error" in result
        assert "path traversal" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_timeout_exceeded(self, tmp_path: Path) -> None:
        script = tmp_path / "slow.py"
        script.write_text("import time; time.sleep(10)")
        result = await _execute_command(
            {
                "command": f"python {script}",
                "working_directory": str(tmp_path),
                "timeout": 1,
            }
        )
        assert result.get("success") is False
        assert result.get("timeout") is True
        assert result["exit_code"] == -1

    @pytest.mark.asyncio
    async def test_result_structure(self) -> None:
        result = await _execute_command(
            {"command": 'python -c "print(1)"'}
        )
        for key in ("success", "exit_code", "stdout", "stderr", "duration", "working_directory"):
            assert key in result


class TestToolRegistryIntegration:
    def test_register(self) -> None:
        registry = ToolRegistry()
        register_command_tools(registry)
        defn = registry.get("execute_command")
        assert defn is not None
        assert defn.risk == ToolRisk.HIGH
        assert defn.requires_explicit_approval is True
        assert "working_directory" in defn.optional_arguments
        assert "timeout" in defn.optional_arguments

    def test_compact_description(self) -> None:
        registry = ToolRegistry()
        register_command_tools(registry)
        descs = registry.compact_descriptions()
        assert any("execute_command" in d for d in descs)

    def test_in_default_registry(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        assert "execute_command" in default_tool_registry().names()

    @pytest.mark.asyncio
    async def test_requires_approval(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        registry = default_tool_registry()
        with pytest.raises(PermissionError):
            await registry.execute(
                "execute_command",
                {"command": 'python -c "print(1)"'},
                approved=False,
            )

    @pytest.mark.asyncio
    async def test_approved_execution(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        registry = default_tool_registry()
        result = await registry.execute(
            "execute_command",
            {"command": 'python -c "print(1)"'},
            approved=True,
        )
        assert result.get("success") is True


class TestRequestMetricsK6:
    def test_defaults(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        assert m.command_count == 0
        assert m.command_timeout_count == 0
        assert m.command_output_chars == 0

    def test_record_command(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        m.record_command(duration=1.5, exit_code=0, output_chars=500)
        assert m.command_count == 1
        assert m.command_durations == [1.5]

    def test_record_timeout(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        m.record_command(duration=30.0, exit_code=-1, timed_out=True)
        assert m.command_timeout_count == 1

    def test_summary_keys(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        m.record_command(duration=1.0, exit_code=0)
        s = m.summary()
        assert "command_count" in s
        assert "command_avg_duration_s" in s
        assert s["command_count"] == 1


class TestPromptIntegration:
    def test_command_in_prompt(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt
        prompt = build_system_prompt(
            tool_names=["execute_command", "read_file"],
            compact_descriptions=["execute_command: Execute safe shell command"],
        )
        assert "Commands" in prompt

    def test_not_in_prompt_without_tool(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt
        prompt = build_system_prompt(
            tool_names=["read_file"],
            compact_descriptions=["read_file: Read a file"],
        )
        assert "## Commands" not in prompt


class TestExecutionAgentK6:
    @pytest.mark.asyncio
    async def test_approval_required(self) -> None:
        from uuid import uuid4

        from personal_ai_secretary.agents.builtin import (
            APPROVAL_REQUIRED_PREFIX,
            ExecutionAgent,
        )
        from personal_ai_secretary.agents.contracts import AgentInput
        from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel
        from personal_ai_secretary.tools.builtin import default_tool_registry

        registry = default_tool_registry()
        text = _tool_response("execute_command", {"command": "echo hello"})

        async def generate(request: object) -> ProviderResponse:
            return ProviderResponse(text=text, provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="run echo", correlation_id="test",
            risk_level=RiskLevel.LOW, context={},
        )
        result = await agent.run(data)
        assert result.content.startswith(APPROVAL_REQUIRED_PREFIX)
        assert "execute_command" in result.content

    @pytest.mark.asyncio
    async def test_command_metrics(self) -> None:
        from uuid import uuid4

        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput
        from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel
        from personal_ai_secretary.tools.builtin import default_tool_registry

        registry = default_tool_registry()
        text = _tool_response(
            "execute_command", {"command": 'python -c "print(1)"'}
        )

        async def generate(request: object) -> ProviderResponse:
            return ProviderResponse(text=text, provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="run", correlation_id="test",
            risk_level=RiskLevel.LOW,
            context={"approval_granted": True},
        )
        await agent.run(data)
        metrics = agent.last_metrics
        assert metrics is not None
        assert metrics.command_count >= 1
