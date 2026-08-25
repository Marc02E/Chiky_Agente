"""FASE L.3 - Unit tests for Autonomous Development Loop.

Tests the autonomous workflow prompt, observability metrics, approval flow,
and integration with existing K.1.1-K.6, L.1-L.2 capabilities.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from personal_ai_secretary.tools.prompt import (
    build_system_prompt,
    build_tool_result_prompt,
    estimate_prompt_tokens,
)

# ---------------------------------------------------------------------------
# Prompt: Autonomous Development Loop Section
# ---------------------------------------------------------------------------


class TestPromptAutonomousLoop:
    """Verify that the system prompt includes L.3 autonomous loop guidance."""

    def test_autonomous_section_present(self) -> None:
        prompt = build_system_prompt(
            tool_names=[
                "analyze_project", "read_files", "modify_file", "execute_command",
            ],
            compact_descriptions=["analyze_project: Analyze project structure"],
        )
        assert "## Development Workflow" in prompt

    def test_autonomous_steps(self) -> None:
        prompt = build_system_prompt(
            tool_names=[
                "analyze_project", "read_files", "modify_file", "execute_command",
            ],
            compact_descriptions=["analyze_project: Analyze project structure"],
        )
        for step in ("DISCOVER", "READ", "UNDERSTAND", "MODIFY",
                      "TEST", "FIX", "VERIFY", "REPORT"):
            assert step in prompt

    def test_correction_guidance(self) -> None:
        prompt = build_system_prompt(
            tool_names=[
                "analyze_project", "read_files", "modify_file", "execute_command",
            ],
            compact_descriptions=["analyze_project: Analyze project structure"],
        )
        assert "When command fails" in prompt
        assert "5 cycles" in prompt

    def test_verification_guidance(self) -> None:
        prompt = build_system_prompt(
            tool_names=[
                "analyze_project", "read_files", "modify_file", "execute_command",
            ],
            compact_descriptions=["analyze_project: Analyze project structure"],
        )
        assert "verify" in prompt.lower()
        assert "MODIFIED" in prompt

    def test_no_autonomous_section_without_tools(self) -> None:
        prompt = build_system_prompt(
            tool_names=["calculator"],
            compact_descriptions=["calculator: Evaluate math"],
        )
        assert "## Development Workflow" not in prompt

    def test_l1_l2_sections_still_present(self) -> None:
        prompt = build_system_prompt(
            tool_names=[
                "analyze_project", "read_files", "modify_file",
                "execute_command", "search_files",
            ],
            compact_descriptions=["analyze_project: Analyze project structure"],
        )
        assert "## Development Workflow" in prompt
        assert "## Project Intelligence" in prompt

    def test_skip_steps_guidance(self) -> None:
        prompt = build_system_prompt(
            tool_names=[
                "analyze_project", "read_files", "modify_file", "execute_command",
            ],
            compact_descriptions=["analyze_project: Analyze project structure"],
        )
        assert "Simple file ops: create_file -> verify exists -> report." in prompt


# ---------------------------------------------------------------------------
# RequestMetrics: L.3 Extensions
# ---------------------------------------------------------------------------


class TestRequestMetricsL3:
    def test_l3_defaults(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        assert m.correction_attempts == 0
        assert m.correction_tools_used == []
        assert m.final_outcome == "unknown"
        assert m.verification_status == "pending"
        assert m.max_corrections_reached is False

    def test_record_correction(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        m.record_correction("modify_file")
        assert m.correction_attempts == 1
        assert m.correction_tools_used == ["modify_file"]

    def test_multiple_corrections(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        m.record_correction("modify_file")
        m.record_correction("execute_command")
        m.record_correction("modify_file")
        assert m.correction_attempts == 3
        assert m.correction_tools_used == [
            "modify_file", "execute_command", "modify_file",
        ]

    def test_set_final_outcome(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        m.set_final_outcome("success")
        assert m.final_outcome == "success"

    def test_set_verification_status(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        m.set_verification_status("VERIFIED")
        assert m.verification_status == "VERIFIED"

    def test_max_corrections_reached(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        m.max_corrections_reached = True
        assert m.max_corrections_reached is True

    def test_summary_includes_l3_fields(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        m.record_correction("modify_file")
        m.set_final_outcome("success")
        m.set_verification_status("VERIFIED")
        m.max_corrections_reached = False
        s = m.summary()
        assert "correction_attempts" in s
        assert s["correction_attempts"] == 1
        assert s["final_outcome"] == "success"
        assert s["verification_status"] == "VERIFIED"
        assert s["max_corrections_reached"] is False


# ---------------------------------------------------------------------------
# ExecutionAgent: Approval Flow Preservation
# ---------------------------------------------------------------------------


class TestExecutionAgentApprovalPreserved:
    @pytest.mark.asyncio
    async def test_approval_required_stops_loop(self) -> None:
        from uuid import uuid4

        from personal_ai_secretary.agents.builtin import (
            APPROVAL_REQUIRED_PREFIX,
            ExecutionAgent,
        )
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
            context={},
        )
        result = await agent.run(data)
        assert APPROVAL_REQUIRED_PREFIX in result.content

    @pytest.mark.asyncio
    async def test_approval_granted_executes(self) -> None:
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
        assert metrics.tool_calls >= 1

    @pytest.mark.asyncio
    async def test_single_use_approval(self) -> None:
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
        # First request: no approval
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="run", correlation_id="test",
            risk_level=RiskLevel.LOW,
            context={},
        )
        result = await agent.run(data)
        from personal_ai_secretary.agents.builtin import APPROVAL_REQUIRED_PREFIX
        assert APPROVAL_REQUIRED_PREFIX in result.content


# ---------------------------------------------------------------------------
# ExecutionAgent: Tool Loop Limits
# ---------------------------------------------------------------------------


class TestExecutionAgentLoopLimits:
    @pytest.mark.asyncio
    async def test_max_tool_rounds_respected(self) -> None:
        from uuid import uuid4

        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput
        from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel
        from personal_ai_secretary.tools.builtin import default_tool_registry

        registry = default_tool_registry()
        call_count = 0

        async def generate(request: object) -> ProviderResponse:
            nonlocal call_count
            call_count += 1
            # Always return a tool call to test loop limits
            text = _tool_response("analyze_project", {"path": "."})
            return ProviderResponse(text=text, provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="analyze", correlation_id="test",
            risk_level=RiskLevel.LOW,
            context={"approval_granted": True},
        )
        await agent.run(data)
        metrics = agent.last_metrics
        assert metrics is not None
        # Should not exceed MAX_TOOL_ROUNDS
        assert metrics.rounds <= ExecutionAgent.MAX_TOOL_ROUNDS

    @pytest.mark.asyncio
    async def test_dedup_prevents_repeated_calls(self) -> None:
        from uuid import uuid4

        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput
        from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel
        from personal_ai_secretary.tools.builtin import default_tool_registry

        registry = default_tool_registry()

        async def generate(request: object) -> ProviderResponse:
            # Return the same tool call every time
            text = _tool_response("analyze_project", {"path": "."})
            return ProviderResponse(text=text, provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="analyze", correlation_id="test",
            risk_level=RiskLevel.LOW,
            context={"approval_granted": True},
        )
        await agent.run(data)
        metrics = agent.last_metrics
        assert metrics is not None
        # Dedup should have caught some calls
        assert metrics.deduplicated_calls > 0


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


# ---------------------------------------------------------------------------
# ExecutionAgent: Provider Failure Handling
# ---------------------------------------------------------------------------


class TestExecutionAgentProviderFailure:
    @pytest.mark.asyncio
    async def test_connection_error_returns_error(self) -> None:
        from uuid import uuid4

        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput
        from personal_ai_secretary.domain.contracts import RiskLevel

        async def generate(request: object) -> None:
            raise ConnectionError("Provider unavailable")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=None)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="hello", correlation_id="test",
            risk_level=RiskLevel.LOW, context={},
        )
        result = await agent.run(data)
        assert "error" in result.content.lower() or "unavailable" in result.content.lower()

    @pytest.mark.asyncio
    async def test_empty_response_returns_message(self) -> None:
        from uuid import uuid4

        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput
        from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel

        async def generate(request: object) -> ProviderResponse:
            return ProviderResponse(text="", provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=None)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="hello", correlation_id="test",
            risk_level=RiskLevel.LOW, context={},
        )
        result = await agent.run(data)
        assert "empty" in result.content.lower() or "response" in result.content.lower()


# ---------------------------------------------------------------------------
# ExecutionAgent: Authorization Block
# ---------------------------------------------------------------------------


class TestExecutionAgentAuthorization:
    @pytest.mark.asyncio
    async def test_unauthorized_request_blocked(self) -> None:
        from uuid import uuid4

        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput
        from personal_ai_secretary.domain.contracts import RiskLevel

        agent = ExecutionAgent(provider=None, registry=None)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="hello", correlation_id="test",
            risk_level=RiskLevel.LOW,
            context={"authorized": False},
        )
        result = await agent.run(data)
        assert result.blocked is True
        assert "authorization" in result.content.lower()


# ---------------------------------------------------------------------------
# ExecutionAgent: Security Regression
# ---------------------------------------------------------------------------


class TestSecurityRegression:
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

    def test_modify_requires_approval(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        registry = default_tool_registry()
        for name in ("create_file", "write_file", "modify_file",
                      "create_project", "execute_command", "create_directory"):
            defn = registry.get(name)
            assert defn is not None
            assert defn.requires_explicit_approval is True

    def test_read_only_tools_no_approval(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        registry = default_tool_registry()
        for name in ("read_file", "list_directory", "file_exists",
                      "analyze_project", "read_files", "search_files"):
            defn = registry.get(name)
            assert defn is not None
            assert defn.requires_explicit_approval is False


# ---------------------------------------------------------------------------
# Acceptance Scenarios
# ---------------------------------------------------------------------------


def _tool_response(tool_name: str, args: dict) -> str:
    block = json.dumps({"tool": tool_name, "args": args})
    return "```tool\n" + block + "\n```"


class TestAcceptanceScenarioA:
    """User: 'Encuentra y corrige el error de este proyecto.'"""

    def test_prompt_guides_diagnosis_workflow(self) -> None:
        prompt = build_system_prompt(
            tool_names=[
                "analyze_project", "read_files", "modify_file",
                "execute_command", "search_files",
            ],
            compact_descriptions=["analyze_project: Analyze project structure"],
        )
        assert "## Development Workflow" in prompt
        assert "Locate error" in prompt
        assert "FIX" in prompt
        assert "When command fails" in prompt

    def test_modify_file_exists(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        registry = default_tool_registry()
        assert "modify_file" in registry.names()


class TestAcceptanceScenarioB:
    """User: 'Ejecuta los tests y corrige los errores.'"""

    def test_prompt_guides_test_fix_cycle(self) -> None:
        prompt = build_system_prompt(
            tool_names=[
                "analyze_project", "read_files", "modify_file", "execute_command",
            ],
            compact_descriptions=["execute_command: Execute safe shell command"],
        )
        assert "TEST" in prompt
        assert "Locate error" in prompt

    def test_execute_command_exists(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        registry = default_tool_registry()
        assert "execute_command" in registry.names()


class TestAcceptanceScenarioC:
    """User: 'Créame un CRUD de productos con FastAPI.'"""

    def test_prompt_guides_project_creation(self) -> None:
        prompt = build_system_prompt(
            tool_names=[
                "create_project", "create_file", "execute_command",
                "analyze_project", "read_files",
            ],
            compact_descriptions=[
                "create_project: Create multi-file project",
                "execute_command: Execute safe shell command",
            ],
        )
        assert "## Development Workflow" in prompt
        assert "REPORT" in prompt


class TestAcceptanceScenarioD:
    """User: 'Créame un juego Snake.'"""

    def test_prompt_guides_full_workflow(self) -> None:
        prompt = build_system_prompt(
            tool_names=[
                "create_project", "create_file", "execute_command",
                "analyze_project", "read_files", "verify_files",
            ],
            compact_descriptions=[
                "create_project: Create multi-file project",
                "execute_command: Execute safe shell command",
            ],
        )
        assert "## Development Workflow" in prompt
        assert "VERIFY" in prompt
        assert "REPORT" in prompt


class TestAcceptanceScenarioE:
    """User: 'Analiza este proyecto y dime qué cambiarías.'"""

    def test_prompt_guides_readonly_analysis(self) -> None:
        prompt = build_system_prompt(
            tool_names=["analyze_project", "read_files", "search_files"],
            compact_descriptions=["analyze_project: Analyze project structure"],
        )
        assert "## Development Workflow" in prompt
        assert "DISCOVER" in prompt
        assert "READ progressively" in prompt

    def test_analyze_project_is_low_risk(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry
        from personal_ai_secretary.tools.registry import ToolRisk
        registry = default_tool_registry()
        defn = registry.get("analyze_project")
        assert defn is not None
        assert defn.risk == ToolRisk.LOW


# ---------------------------------------------------------------------------
# Prompt: Existing Section Preservation
# ---------------------------------------------------------------------------


class TestExistingSectionsPreserved:
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

    def test_verification_section(self) -> None:
        prompt = build_system_prompt(
            tool_names=["execute_command"],
            compact_descriptions=["execute_command: Execute safe shell command"],
        )
        assert "## Truth & Verification" in prompt

    def test_response_section(self) -> None:
        prompt = build_system_prompt()
        assert "## Response" in prompt

    def test_self_correction_section(self) -> None:
        prompt = build_system_prompt(
            tool_names=["execute_command"],
            compact_descriptions=["execute_command: Execute safe shell command"],
        )
        assert "## Error Recovery" in prompt
        assert "max 5 cycles" in prompt


# ---------------------------------------------------------------------------
# Prompt: Token Efficiency
# ---------------------------------------------------------------------------


class TestPromptTokenEfficiency:
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
        assert tokens < 2100


# ---------------------------------------------------------------------------
# Tool Result Prompt
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
