"""FASE L.1 - Unit tests for Development Agent Core.

Tests the enhanced prompt sections, workflow stage tracking, and
acceptance scenarios for the development agent workflow.
"""

import json
from unittest.mock import AsyncMock

import pytest

from personal_ai_secretary.tools.prompt import (
    build_system_prompt,
    build_tool_result_prompt,
    estimate_prompt_tokens,
)

# ---------------------------------------------------------------------------
# Prompt: Development Workflow Section
# ---------------------------------------------------------------------------


class TestPromptDevelopmentWorkflow:
    """Verify that the system prompt includes all L.1 workflow sections."""

    def test_dev_workflow_section_present(self) -> None:
        prompt = build_system_prompt(
            tool_names=["analyze_project", "read_files", "modify_file", "execute_command"],
            compact_descriptions=["analyze_project: Analyze project structure"],
        )
        assert "## Development Workflow" in prompt

    def test_dev_workflow_steps(self) -> None:
        prompt = build_system_prompt(
            tool_names=["analyze_project", "read_files", "modify_file", "execute_command"],
            compact_descriptions=["analyze_project: Analyze project structure"],
        )
        for step in ("DISCOVER", "READ", "UNDERSTAND", "MODIFY",
                      "TEST", "FIX", "VERIFY", "REPORT"):
            assert step in prompt

    def test_project_creation_section(self) -> None:
        prompt = build_system_prompt(
            tool_names=["create_project", "create_file", "execute_command"],
            compact_descriptions=["create_project: Create multi-file project"],
        )
        assert "## Development Workflow" in prompt
        assert "create_project" in prompt
        assert "install/test" in prompt.lower()

    def test_project_modification_section(self) -> None:
        prompt = build_system_prompt(
            tool_names=["analyze_project", "read_files", "modify_file"],
            compact_descriptions=["modify_file: Modify existing files"],
        )
        assert "## Development Workflow" in prompt
        assert "FINAL REPORT must include" in prompt

    def test_self_correction_section(self) -> None:
        prompt = build_system_prompt(
            tool_names=["execute_command"],
            compact_descriptions=["execute_command: Execute safe shell command"],
        )
        assert "## Error Recovery" in prompt
        assert "max 5 cycles" in prompt

    def test_verification_section(self) -> None:
        prompt = build_system_prompt(
            tool_names=["execute_command", "read_file"],
            compact_descriptions=["execute_command: Execute safe shell command"],
        )
        assert "## Truth & Verification" in prompt
        assert "CREATED" in prompt
        assert "EXECUTED" in prompt
        assert "TESTED" in prompt
        assert "MODIFIED" in prompt

    def test_command_execution_enhanced(self) -> None:
        prompt = build_system_prompt(
            tool_names=["execute_command"],
            compact_descriptions=["execute_command: Execute safe shell command"],
        )
        assert "## Commands" in prompt
        assert "3. Read file → 4. Fix (minimal) → 5. Retest" in prompt

    def test_no_dev_sections_without_tools(self) -> None:
        prompt = build_system_prompt(
            tool_names=["calculator"],
            compact_descriptions=["calculator: Evaluate math"],
        )
        assert "## Development Workflow" not in prompt
        assert "## Error Recovery" not in prompt

    def test_identity_includes_development_agent(self) -> None:
        prompt = build_system_prompt()
        assert "development agent" in prompt.lower()

    def test_capabilities_include_testing(self) -> None:
        prompt = build_system_prompt()
        assert "testing" in prompt.lower()
        assert "debugging" in prompt.lower()


class TestPromptWorkflowMinimal:
    """For simple tasks, the prompt should guide minimal workflow."""

    def test_single_file_task_guidance(self) -> None:
        prompt = build_system_prompt(
            tool_names=["create_file", "execute_command"],
            compact_descriptions=["create_file: Create a file"],
        )
        assert "simple file ops" in prompt.lower()


# ---------------------------------------------------------------------------
# Prompt: Existing section preservation
# ---------------------------------------------------------------------------


class TestPromptExistingSectionsPreserved:
    """Verify K.4/K.5/K.6 prompt sections still present."""

    def test_tools_section(self) -> None:
        prompt = build_system_prompt(
            tool_names=["read_file"],
            compact_descriptions=["read_file: Read a file"],
        )
        assert "## Tools" in prompt
        assert "## Tool Call Format" in prompt

    def test_rules_section(self) -> None:
        prompt = build_system_prompt(
            tool_names=["read_file"],
            compact_descriptions=["read_file: Read a file"],
        )
        assert "## Rules" in prompt

    def test_response_section(self) -> None:
        prompt = build_system_prompt()
        assert "## Response" in prompt

    def test_model_specific_guidance(self) -> None:
        prompt = build_system_prompt(model_name="llama3.1")
        assert "Llama" in prompt

    def test_memory_context(self) -> None:
        prompt = build_system_prompt(extra_context="User prefers dark mode")
        assert "## Context" in prompt
        assert "dark mode" in prompt


# ---------------------------------------------------------------------------
# Prompt: Token efficiency
# ---------------------------------------------------------------------------


class TestPromptTokenEfficiency:
    """L.1 prompt additions should not bloat token usage."""

    def test_full_prompt_token_count(self) -> None:
        prompt = build_system_prompt(
            tool_names=[
                "calculator", "list_tools", "datetime_now", "format_date",
                "get_directory", "create_file", "read_file", "write_file",
                "list_directory", "create_directory", "file_exists",
                "create_project", "analyze_project", "read_files",
                "modify_file", "search_files", "verify_files", "execute_command",
            ],
            compact_descriptions=[
                "calculator: Evaluate math",
                "read_file: Read a file",
                "execute_command: Execute safe shell command",
                "analyze_project: Analyze project structure",
                "create_project: Create multi-file project",
                "modify_file: Modify existing files",
            ],
            model_name="llama3.1",
            extra_context="User: Test",
        )
        tokens = estimate_prompt_tokens(prompt)
        assert tokens < 1800


# ---------------------------------------------------------------------------
# Tool result prompt
# ---------------------------------------------------------------------------


class TestToolResultPrompt:
    def test_basic(self) -> None:
        result = build_tool_result_prompt("read_file", {"content": "hello"})
        assert "read_file" in result
        assert "hello" in result

    def test_truncation(self) -> None:
        big = {"content": "x" * 5000}
        result = build_tool_result_prompt("read_file", big)
        assert "truncated" in result


# ---------------------------------------------------------------------------
# RequestMetrics: Workflow Stage Tracking
# ---------------------------------------------------------------------------


class TestRequestMetricsWorkflowStages:
    def test_defaults(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        assert m.workflow_stages == []
        assert m.workflow_stage_times == []

    def test_record_stage(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        m.record_stage("analyzing")
        assert m.workflow_stages == ["analyzing"]

    def test_multiple_stages(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        m.record_stage("analyzing")
        m.record_stage("creating")
        m.record_stage("executing")
        m.record_stage("completed")
        assert m.workflow_stages == ["analyzing", "creating", "executing", "completed"]
        assert len(m.workflow_stage_times) == 3

    def test_summary_includes_stages(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        m.record_stage("analyzing")
        m.record_stage("completed")
        s = m.summary()
        assert "workflow_stages" in s
        assert s["workflow_stages"] == ["analyzing", "completed"]
        assert "workflow_stage_times" in s


# ---------------------------------------------------------------------------
# ExecutionAgent: Workflow Stage Emission
# ---------------------------------------------------------------------------


class TestExecutionAgentWorkflowStages:
    @pytest.mark.asyncio
    async def test_analyze_project_records_analyzing(self) -> None:
        from uuid import uuid4

        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput
        from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel
        from personal_ai_secretary.tools.builtin import default_tool_registry

        registry = default_tool_registry()
        text = _tool_response("analyze_project", {"path": "."})

        async def generate(request: object) -> ProviderResponse:
            return ProviderResponse(text=text, provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="analyze", correlation_id="test",
            risk_level=RiskLevel.LOW, context={},
        )
        await agent.run(data)
        metrics = agent.last_metrics
        assert metrics is not None
        assert "analyzing" in metrics.workflow_stages
        assert "completed" in metrics.workflow_stages

    @pytest.mark.asyncio
    async def test_execute_command_records_executing(self) -> None:
        from uuid import uuid4

        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput
        from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel
        from personal_ai_secretary.tools.builtin import default_tool_registry

        registry = default_tool_registry()
        text = _tool_response("execute_command", {"command": "python -c \"print(1)\""})

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
        assert "executing" in metrics.workflow_stages

    @pytest.mark.asyncio
    async def test_create_file_records_creating(self) -> None:
        from uuid import uuid4

        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput
        from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel
        from personal_ai_secretary.tools.builtin import default_tool_registry

        registry = default_tool_registry()
        text = _tool_response("create_file", {"path": "test.txt", "content": "hello"})

        async def generate(request: object) -> ProviderResponse:
            return ProviderResponse(text=text, provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="create", correlation_id="test",
            risk_level=RiskLevel.LOW,
            context={"approval_granted": True},
        )
        await agent.run(data)
        metrics = agent.last_metrics
        assert metrics is not None
        assert "creating" in metrics.workflow_stages


# ---------------------------------------------------------------------------
# Acceptance Scenarios
# ---------------------------------------------------------------------------


def _tool_response(tool_name: str, args: dict) -> str:
    block = json.dumps({"tool": tool_name, "args": args})
    return "```tool\n" + block + "\n```"


class TestAcceptanceScenarioA:
    """User: 'Create a hola.py that prints Hola Esteban'."""

    def test_prompt_guides_single_file_workflow(self) -> None:
        prompt = build_system_prompt(
            tool_names=["create_file", "execute_command"],
            compact_descriptions=["create_file: Create a file", "execute_command: Execute safe shell command"],
        )
        assert "simple file ops" in prompt.lower()
        assert "create_file -> verify exists" in prompt

    def test_create_file_tool_exists(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        registry = default_tool_registry()
        assert "create_file" in registry.names()

    def test_execute_command_tool_exists(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        registry = default_tool_registry()
        assert "execute_command" in registry.names()


class TestAcceptanceScenarioB:
    """User: 'Create a CRUD of products with FastAPI'."""

    def test_prompt_guides_project_creation(self) -> None:
        prompt = build_system_prompt(
            tool_names=["create_project", "create_file", "execute_command"],
            compact_descriptions=["create_project: Create multi-file project"],
        )
        assert "## Development Workflow" in prompt
        assert "install/test" in prompt.lower()
        assert "test" in prompt.lower()

    def test_create_project_tool_exists(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        registry = default_tool_registry()
        assert "create_project" in registry.names()


class TestAcceptanceScenarioC:
    """User: 'Analyze this project and tell me how it is organized'."""

    def test_prompt_guides_readonly_analysis(self) -> None:
        prompt = build_system_prompt(
            tool_names=["analyze_project", "read_files", "search_files"],
            compact_descriptions=["analyze_project: Analyze project structure"],
        )
        assert "## Development Workflow" in prompt
        assert "DISCOVER" in prompt
        assert "READ" in prompt

    def test_analyze_project_is_low_risk(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        from personal_ai_secretary.tools.registry import ToolRisk
        registry = default_tool_registry()
        defn = registry.get("analyze_project")
        assert defn is not None
        assert defn.risk == ToolRisk.LOW

    def test_read_files_is_low_risk(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        from personal_ai_secretary.tools.registry import ToolRisk
        registry = default_tool_registry()
        defn = registry.get("read_files")
        assert defn is not None
        assert defn.risk == ToolRisk.LOW


class TestAcceptanceScenarioD:
    """User: 'Fix this error in the project'."""

    def test_prompt_guides_diagnosis_workflow(self) -> None:
        prompt = build_system_prompt(
            tool_names=["read_files", "modify_file", "execute_command", "search_files"],
            compact_descriptions=["modify_file: Modify existing files", "execute_command: Execute safe shell command"],
        )
        assert "## Error Recovery" in prompt
        assert "## Development Workflow" in prompt
        assert "FINAL REPORT must include" in prompt

    def test_modify_file_requires_approval(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        registry = default_tool_registry()
        defn = registry.get("modify_file")
        assert defn is not None
        assert defn.requires_explicit_approval is True


class TestAcceptanceScenarioE:
    """User: 'Create a Snake game'."""

    def test_prompt_guides_full_project_workflow(self) -> None:
        prompt = build_system_prompt(
            tool_names=[
                "create_project", "create_file", "execute_command",
                "analyze_project", "read_files", "verify_files",
            ],
            compact_descriptions=[
                "create_project: Create multi-file project",
                "execute_command: Execute safe shell command",
                "verify_files: Verify file assertions",
            ],
        )
        assert "## Development Workflow" in prompt
        assert "## Truth & Verification" in prompt
        assert "REPORT" in prompt


# ---------------------------------------------------------------------------
# Security: Approval requirements
# ---------------------------------------------------------------------------


class TestSecurityApprovalsPreserved:
    """All destructive operations still require approval."""

    def test_create_file_approval(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        registry = default_tool_registry()
        defn = registry.get("create_file")
        assert defn is not None
        assert defn.requires_explicit_approval is True

    def test_write_file_approval(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        registry = default_tool_registry()
        defn = registry.get("write_file")
        assert defn is not None
        assert defn.requires_explicit_approval is True

    def test_modify_file_approval(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        registry = default_tool_registry()
        defn = registry.get("modify_file")
        assert defn is not None
        assert defn.requires_explicit_approval is True

    def test_create_project_approval(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        registry = default_tool_registry()
        defn = registry.get("create_project")
        assert defn is not None
        assert defn.requires_explicit_approval is True

    def test_execute_command_approval(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        registry = default_tool_registry()
        defn = registry.get("execute_command")
        assert defn is not None
        assert defn.requires_explicit_approval is True

    def test_create_directory_approval(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        registry = default_tool_registry()
        defn = registry.get("create_directory")
        assert defn is not None
        assert defn.requires_explicit_approval is True


# ---------------------------------------------------------------------------
# Tool count and registry integrity
# ---------------------------------------------------------------------------


class TestRegistryIntegrity:
    def test_all_expected_tools_present(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        registry = default_tool_registry()
        expected = {
            "calculator", "list_tools", "datetime_now", "format_date",
            "get_directory", "create_file", "read_file", "write_file",
            "list_directory", "create_directory", "file_exists",
            "create_project", "analyze_project", "read_files",
            "modify_file", "search_files", "verify_files", "execute_command",
        }
        assert expected <= set(registry.names())

    def test_no_critical_risk_tools(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        from personal_ai_secretary.tools.registry import ToolRisk
        registry = default_tool_registry()
        for name in registry.names():
            defn = registry.get(name)
            assert defn is not None
            assert defn.risk != ToolRisk.CRITICAL
