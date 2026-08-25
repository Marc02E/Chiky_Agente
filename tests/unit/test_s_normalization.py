"""FASE S.12 — Deterministic tests for argument normalization.

Tests cover: aliases, unknown stripping, repair, security, anti-hallucination.
No LLM calls — fully deterministic.
"""

import pytest

from personal_ai_secretary.tools.registry import (
    ToolDefinition,
    ToolError,
    ToolRegistry,
    ToolRisk,
    parse_tool_call,
)


async def _echo(args):
    return {"echoed": args}


async def _noop(args):
    return {"ok": True}


@pytest.fixture
def registry():
    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            "modify_file", ToolRisk.HIGH, True, _noop,
            argument_schema={"path": "string", "mode": "string"},
            optional_arguments=frozenset({"search", "replacement", "content"}),
            argument_aliases={
                "p": "path", "operation": "mode", "action": "mode",
                "file": "path", "file_path": "path",
            },
        )
    )
    reg.register(
        ToolDefinition(
            "read_file", ToolRisk.LOW, False, _noop,
            argument_schema={"path": "string"},
            argument_aliases={"p": "path", "file": "path", "file_path": "path"},
        )
    )
    reg.register(
        ToolDefinition(
            "execute_command", ToolRisk.HIGH, True, _noop,
            argument_schema={"command": "string"},
            optional_arguments=frozenset({"working_directory", "timeout"}),
            argument_aliases={"cmd": "command", "shell": "command", "dir": "working_directory"},
        )
    )
    reg.register(
        ToolDefinition(
            "echo", ToolRisk.LOW, False, _echo,
            argument_schema={"value": "string", "count": "integer"},
        )
    )
    return reg


# ── S.2: Alias Resolution ────────────────────────────────────────────


class TestAliasResolution:
    """S.2-S.3: Verify explicit aliases resolve correctly."""

    @pytest.mark.asyncio
    async def test_operation_resolves_to_mode(self, registry):
        """llama3.1 used 'operation' instead of 'mode' for modify_file."""
        result = await registry.execute(
            "modify_file",
            {"path": "/tmp/test.py", "operation": "replace"},
            approved=True,
        )
        assert result["ok"]

    @pytest.mark.asyncio
    async def test_action_resolves_to_mode(self, registry):
        """Some models use 'action' instead of 'mode'."""
        result = await registry.execute(
            "modify_file",
            {"path": "/tmp/test.py", "action": "overwrite"},
            approved=True,
        )
        assert result["ok"]

    @pytest.mark.asyncio
    async def test_p_resolves_to_path(self, registry):
        """Some models use 'p' instead of 'path'."""
        result = await registry.execute(
            "read_file",
            {"p": "/tmp/test.py"},
        )
        assert result["ok"]

    @pytest.mark.asyncio
    async def test_cmd_resolves_to_command(self, registry):
        """Some models use 'cmd' instead of 'command'."""
        result = await registry.execute(
            "execute_command",
            {"cmd": "echo hello"},
            approved=True,
        )
        assert result["ok"]

    @pytest.mark.asyncio
    async def test_shell_resolves_to_command(self, registry):
        """Some models use 'shell' instead of 'command'."""
        result = await registry.execute(
            "execute_command",
            {"shell": "echo hello"},
            approved=True,
        )
        assert result["ok"]

    @pytest.mark.asyncio
    async def test_dir_resolves_to_working_directory(self, registry):
        """Some models use 'dir' instead of 'working_directory'."""
        result = await registry.execute(
            "execute_command",
            {"cmd": "echo hello", "dir": "/tmp"},
            approved=True,
        )
        assert result["ok"]

    @pytest.mark.asyncio
    async def test_multiple_aliases_simultaneously(self, registry):
        """Multiple aliases in the same call."""
        result = await registry.execute(
            "modify_file",
            {"p": "/tmp/test.py", "operation": "replace", "search": "old", "replacement": "new"},
            approved=True,
        )
        assert result["ok"]


# ── S.2: Unknown Argument Stripping ──────────────────────────────────


class TestUnknownStripping:
    """S.2: Unknown arguments are stripped, not raised."""

    @pytest.mark.asyncio
    async def test_extra_args_stripped(self, registry):
        """Extra unknown arguments should be stripped silently."""
        result = await registry.execute(
            "echo",
            {"value": "x", "count": 1, "extra": True, "noise": "ignored"},
        )
        assert result == {"echoed": {"value": "x", "count": 1}}

    @pytest.mark.asyncio
    async def test_only_canonical_args_pass(self, registry):
        """After normalization, only canonical args remain."""
        norm = registry.normalize_arguments(
            registry.get("modify_file"),
            {"p": "/tmp/f.py", "operation": "replace", "junk": 42},
        )
        assert "path" in norm.normalized_args
        assert "mode" in norm.normalized_args
        assert "junk" not in norm.normalized_args
        assert ("p", "path") in norm.aliases_resolved
        assert ("operation", "mode") in norm.aliases_resolved
        assert "junk" in norm.unknown_stripped


# ── S.4: Validation Pipeline ─────────────────────────────────────────


class TestValidationPipeline:
    """S.4: EXTRACT -> NORMALIZE -> VALIDATE -> EXECUTE."""

    @pytest.mark.asyncio
    async def test_missing_required_still_raises(self, registry):
        """Missing required argument should still raise after normalization."""
        with pytest.raises(ToolError, match="Missing argument"):
            await registry.execute("echo", {})

    @pytest.mark.asyncio
    async def test_wrong_type_still_raises(self, registry):
        """Wrong type should still raise after normalization."""
        with pytest.raises(ToolError, match="must be"):
            await registry.execute("echo", {"value": "x", "count": "not_int"})

    @pytest.mark.asyncio
    async def test_canonical_args_work(self, registry):
        """Canonical arguments should work without normalization."""
        result = await registry.execute("echo", {"value": "hello", "count": 2})
        assert result == {"echoed": {"value": "hello", "count": 2}}


# ── S.5: Argument Repair ─────────────────────────────────────────────


class TestArgumentRepair:
    """S.5: Maximum 1 repair attempt."""

    @pytest.mark.asyncio
    async def test_repair_missing_mode_when_search_present(self, registry):
        """If search is present but mode is missing, add mode=replace."""
        result = await registry.execute(
            "modify_file",
            {"path": "/tmp/f.py", "search": "old", "replacement": "new"},
            approved=True,
        )
        assert result["ok"]

    @pytest.mark.asyncio
    async def test_repair_missing_mode_when_content_present(self, registry):
        """If content is present but mode is missing, add mode=overwrite."""
        result = await registry.execute(
            "modify_file",
            {"path": "/tmp/f.py", "content": "new content"},
            approved=True,
        )
        assert result["ok"]

    @pytest.mark.asyncio
    async def test_repair_int_to_string(self, registry):
        """If an int is given where string expected, convert."""
        result = await registry.execute("read_file", {"path": 12345})
        assert result["ok"]

    @pytest.mark.asyncio
    async def test_repair_unrepairable_error(self, registry):
        """If repair fails, the original error propagates."""
        with pytest.raises(ToolError, match="Missing argument"):
            await registry.execute("modify_file", {}, approved=True)

    @pytest.mark.asyncio
    async def test_repair_metrics_increment(self, registry):
        """Repair attempts should be tracked in metrics."""
        registry.reset_metrics()
        await registry.execute(
            "modify_file",
            {"path": "/tmp/f.py", "search": "old", "replacement": "new"},
            approved=True,
        )
        assert registry.get_metrics()["argument_repairs"] >= 1

    @pytest.mark.asyncio
    async def test_repair_failure_metrics_increment(self, registry):
        """Failed repairs should increment failure counter."""
        registry.reset_metrics()
        with pytest.raises(ToolError):
            await registry.execute("modify_file", {}, approved=True)
        assert registry.get_metrics()["argument_repair_failures"] >= 1


# ── S.8: Security — Aliases Cannot Bypass Controls ───────────────────


class TestSecurityViaAliases:
    """S.8: Alias normalization NEVER bypasses security controls."""

    @pytest.mark.asyncio
    async def test_alias_cannot_bypass_approval(self, registry):
        """Using aliases should not bypass approval requirement."""
        with pytest.raises(PermissionError):
            await registry.execute(
                "modify_file",
                {"p": "/tmp/f.py", "operation": "overwrite"},
                approved=False,
            )

    @pytest.mark.asyncio
    async def test_alias_cannot_bypass_command_approval(self, registry):
        """cmd alias should not bypass command approval."""
        with pytest.raises(PermissionError):
            await registry.execute(
                "execute_command",
                {"cmd": "echo pwned"},
                approved=False,
            )

    @pytest.mark.asyncio
    async def test_alias_cannot_create_new_tool(self, registry):
        """An alias should never map to a different tool name."""
        with pytest.raises(KeyError):
            await registry.execute("cmd", {"command": "echo hi"}, approved=True)


# ── S.9: Anti-Hallucination ──────────────────────────────────────────


class TestAntiHallucination:
    """S.9: No success signal without evidence."""

    @pytest.mark.asyncio
    async def test_no_success_without_approval(self, registry):
        """If approval required and not granted, tool should NOT execute."""
        with pytest.raises(PermissionError):
            await registry.execute(
                "execute_command",
                {"cmd": "echo done"},
                approved=False,
            )

    @pytest.mark.asyncio
    async def test_no_success_with_invalid_args(self, registry):
        """If args are invalid after repair, tool should NOT execute."""
        with pytest.raises(ToolError):
            await registry.execute("echo", {})  # missing required 'value'


# ── S.10: Observability Metrics ──────────────────────────────────────


class TestObservability:
    """S.10: Normalization metrics are tracked."""

    def test_metrics_initialized(self, registry):
        metrics = registry.get_metrics()
        assert "tool_calls_received" in metrics
        assert "tool_calls_normalized" in metrics
        assert "argument_repairs" in metrics
        assert "unknown_tool_arguments" in metrics

    @pytest.mark.asyncio
    async def test_metrics_tracked_on_normalized_call(self, registry):
        registry.reset_metrics()
        await registry.execute(
            "modify_file",
            {"p": "/tmp/f.py", "operation": "replace", "junk": True},
            approved=True,
        )
        metrics = registry.get_metrics()
        assert metrics["tool_calls_received"] == 1
        assert metrics["tool_calls_normalized"] == 1
        assert metrics["unknown_tool_arguments"] == 1

    @pytest.mark.asyncio
    async def test_metrics_tracked_on_repair(self, registry):
        registry.reset_metrics()
        await registry.execute(
            "modify_file",
            {"path": "/tmp/f.py", "search": "old", "replacement": "new"},
            approved=True,
        )
        metrics = registry.get_metrics()
        assert metrics["argument_repairs"] >= 1

    @pytest.mark.asyncio
    async def test_metrics_reset(self, registry):
        registry.reset_metrics()
        await registry.execute("echo", {"value": "x", "count": 1})
        registry.reset_metrics()
        metrics = registry.get_metrics()
        assert all(v == 0 for v in metrics.values())


# ── NormalizationResult ──────────────────────────────────────────────


class TestNormalizationResult:
    """Verify NormalizationResult dataclass."""

    def test_no_modification(self, registry):
        norm = registry.normalize_arguments(
            registry.get("echo"), {"value": "x", "count": 1}
        )
        assert not norm.was_modified
        assert norm.aliases_resolved == []
        assert norm.unknown_stripped == []
        assert norm.normalized_args == {"value": "x", "count": 1}

    def test_alias_modification(self, registry):
        norm = registry.normalize_arguments(
            registry.get("read_file"), {"p": "/tmp/f.py", "extra": True}
        )
        assert norm.was_modified
        assert ("p", "path") in norm.aliases_resolved
        assert "extra" in norm.unknown_stripped
        assert norm.normalized_args == {"path": "/tmp/f.py"}

    def test_ambiguity_resolves_to_canonical(self, registry):
        """If both alias and canonical are present, canonical wins."""
        norm = registry.normalize_arguments(
            registry.get("modify_file"),
            {"path": "/tmp/f.py", "p": "/other.py", "mode": "replace"},
        )
        assert norm.normalized_args["path"] == "/tmp/f.py"
        assert "p" in norm.unknown_stripped


# ── parse_tool_call ──────────────────────────────────────────────────


class TestParseToolCall:
    """Test the @tool: parser used by the agent."""

    def test_parse_valid(self):
        result = parse_tool_call('@tool:read_file {"path": "/tmp/f.py"}')
        assert result is not None
        assert result.name == "read_file"
        assert result.arguments == {"path": "/tmp/f.py"}

    def test_parse_no_args(self):
        result = parse_tool_call("@tool:list_tools")
        assert result is not None
        assert result.name == "list_tools"
        assert result.arguments == {}

    def test_parse_not_tool(self):
        result = parse_tool_call("Hello, how are you?")
        assert result is None

    def test_parse_empty_name(self):
        with pytest.raises(ToolError, match="missing a tool name"):
            parse_tool_call("@tool:")

    def test_parse_malformed_name(self):
        with pytest.raises(ToolError, match="Malformed"):
            parse_tool_call("@tool:bad-name!{}")

    def test_parse_invalid_json(self):
        with pytest.raises(ToolError, match="valid JSON"):
            parse_tool_call("@tool:test {invalid}")

    def test_parse_non_dict_args(self):
        with pytest.raises(ToolError, match="JSON object"):
            parse_tool_call("@tool:test [1, 2, 3]")


# ── Edge Cases ───────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases for normalization."""

    def test_empty_arguments(self, registry):
        """Empty args should pass normalization (validation catches missing)."""
        norm = registry.normalize_arguments(
            registry.get("echo"), {}
        )
        assert not norm.was_modified
        assert norm.normalized_args == {}

    def test_no_aliases_tool(self, registry):
        """Tool with no aliases should pass through unchanged."""
        norm = registry.normalize_arguments(
            registry.get("echo"), {"value": "x", "count": 1}
        )
        assert not norm.was_modified
        assert norm.normalized_args == {"value": "x", "count": 1}

    def test_all_aliases_resolved(self, registry):
        """All args are aliases — all should resolve."""
        norm = registry.normalize_arguments(
            registry.get("execute_command"),
            {"cmd": "echo hi", "dir": "/tmp"},
        )
        assert norm.was_modified
        assert norm.normalized_args == {"command": "echo hi", "working_directory": "/tmp"}
        assert len(norm.aliases_resolved) == 2

    @pytest.mark.asyncio
    async def test_full_pipeline_with_aliases(self, registry):
        """Full pipeline: normalize -> validate -> execute with aliases."""
        result = await registry.execute(
            "execute_command",
            {"cmd": "echo test", "dir": "/tmp"},
            approved=True,
        )
        assert result["ok"]


# ---------------------------------------------------------------------------
# S.1.3 / S.1.10 — Task-aware prompt & simple task fast path
# ---------------------------------------------------------------------------


class TestTaskAwarePrompt:
    """Tests for FASE S.1.3 task-aware prompt building."""

    def test_simple_chat_prompt_smaller(self):
        """Chat task should produce a smaller prompt than full."""
        from personal_ai_secretary.tools.command import register_command_tools
        from personal_ai_secretary.tools.development import register_development_tools
        from personal_ai_secretary.tools.filesystem import register_filesystem_tools
        from personal_ai_secretary.tools.prompt import build_system_prompt
        from personal_ai_secretary.tools.registry import ToolRegistry

        reg = ToolRegistry()
        register_filesystem_tools(reg)
        register_development_tools(reg)
        register_command_tools(reg)

        full = build_system_prompt(
            tool_names=reg.names(),
            compact_descriptions=reg.compact_descriptions(),
        )
        simple = build_system_prompt(
            tool_names=reg.names(),
            compact_descriptions=reg.compact_descriptions(),
            task_type="chat",
        )
        assert len(simple) < len(full)

    def test_full_prompt_for_file_create(self):
        """file_create should get the full prompt."""
        from personal_ai_secretary.tools.filesystem import register_filesystem_tools
        from personal_ai_secretary.tools.prompt import build_system_prompt
        from personal_ai_secretary.tools.registry import ToolRegistry

        reg = ToolRegistry()
        register_filesystem_tools(reg)

        full = build_system_prompt(
            tool_names=reg.names(),
            compact_descriptions=reg.compact_descriptions(),
        )
        file_create = build_system_prompt(
            tool_names=reg.names(),
            compact_descriptions=reg.compact_descriptions(),
            task_type="file_create",
        )
        assert len(file_create) == len(full)

    def test_task_type_none_gives_full_prompt(self):
        """None task_type should give full prompt (backward compat)."""
        from personal_ai_secretary.tools.filesystem import register_filesystem_tools
        from personal_ai_secretary.tools.prompt import build_system_prompt
        from personal_ai_secretary.tools.registry import ToolRegistry

        reg = ToolRegistry()
        register_filesystem_tools(reg)

        full = build_system_prompt(
            tool_names=reg.names(),
            compact_descriptions=reg.compact_descriptions(),
        )
        none_type = build_system_prompt(
            tool_names=reg.names(),
            compact_descriptions=reg.compact_descriptions(),
            task_type=None,
        )
        assert len(none_type) == len(full)

    def test_simple_prompt_has_identity(self):
        """Simple prompt should still have identity."""
        from personal_ai_secretary.tools.prompt import build_system_prompt

        prompt = build_system_prompt(
            tool_names=["create_file", "read_file"],
            compact_descriptions=["create_file: Create a file", "read_file: Read a file"],
            task_type="chat",
        )
        assert "Chiky" in prompt
        assert "Tools" in prompt
        assert "create_file" in prompt
        assert "read_file" in prompt

    def test_simple_prompt_omits_dev_workflow(self):
        """Simple prompt should omit development workflow."""
        from personal_ai_secretary.tools.prompt import build_system_prompt

        prompt = build_system_prompt(
            tool_names=["create_file", "read_file", "modify_file", "execute_command"],
            compact_descriptions=[
                "create_file: Create a file",
                "read_file: Read a file",
                "modify_file: Modify files",
                "execute_command: Execute command",
            ],
            task_type="chat",
        )
        assert "Development Workflow" not in prompt

    def test_non_simple_prompt_has_dev_workflow(self):
        """Non-simple prompt should have development workflow."""
        from personal_ai_secretary.tools.prompt import build_system_prompt

        prompt = build_system_prompt(
            tool_names=["create_file", "read_file", "modify_file", "execute_command"],
            compact_descriptions=[
                "create_file: Create a file",
                "read_file: Read a file",
                "modify_file: Modify files",
                "execute_command: Execute command",
            ],
            task_type="file_create",
        )
        assert "Development Workflow" in prompt

    @pytest.mark.parametrize("simple_type", [
        "chat", "file_read", "file_exists", "list_directory", "datetime",
    ])
    def test_all_simple_types_produce_smaller_prompt(self, simple_type):
        """All simple task types should produce a smaller prompt."""
        from personal_ai_secretary.tools.command import register_command_tools
        from personal_ai_secretary.tools.development import register_development_tools
        from personal_ai_secretary.tools.filesystem import register_filesystem_tools
        from personal_ai_secretary.tools.prompt import build_system_prompt
        from personal_ai_secretary.tools.registry import ToolRegistry

        reg = ToolRegistry()
        register_filesystem_tools(reg)
        register_development_tools(reg)
        register_command_tools(reg)

        full = build_system_prompt(
            tool_names=reg.names(),
            compact_descriptions=reg.compact_descriptions(),
        )
        simple = build_system_prompt(
            tool_names=reg.names(),
            compact_descriptions=reg.compact_descriptions(),
            task_type=simple_type,
        )
        assert len(simple) < len(full), (
            f"task_type={simple_type} should produce smaller prompt"
        )
