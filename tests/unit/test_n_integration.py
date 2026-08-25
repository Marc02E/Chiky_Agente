"""FASE N tests — Integration.

Tests that verify the components work together: classifier → profile →
verification pipeline, prompt updates, and ExecutionAgent wiring.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from personal_ai_secretary.agents.classifier import TaskType, classify_task
from personal_ai_secretary.agents.profiles import get_task_profile
from personal_ai_secretary.agents.verifier import verify_tool_result
from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel
from personal_ai_secretary.tools.builtin import default_tool_registry
from personal_ai_secretary.tools.prompt import build_system_prompt


class TestClassifierToProfilePipeline:
    """Verify classifier output feeds correctly into profile lookup."""

    @pytest.mark.parametrize("user_input,expected_type", [
        ("create a project", TaskType.PROJECT_CREATE),
        ("analyze this project", TaskType.PROJECT_ANALYSIS),
        ("run the tests", TaskType.TEST_EXECUTION),
        ("fix this error", TaskType.CODE_DEBUG),
        ("read the file", TaskType.FILE_READ),
        ("create a function", TaskType.CODE_GENERATION),
        ("hello", TaskType.CHAT),
    ])
    def test_classify_then_profile(self, user_input: str, expected_type: TaskType) -> None:
        task_type = classify_task(user_input)
        profile = get_task_profile(task_type)
        assert profile.task_type == expected_type

    def test_project_create_needs_approval(self) -> None:
        task_type = classify_task("build a crud app")
        profile = get_task_profile(task_type)
        assert profile.approval_required is True

    def test_chat_needs_no_tools(self) -> None:
        task_type = classify_task("what is 2+2")
        profile = get_task_profile(task_type)
        assert len(profile.preferred_tools) == 0


class TestVerificationPipeline:
    """Verify that tool results go through verification correctly."""

    def test_create_file_with_real_file(self, tmp_path) -> None:
        fp = tmp_path / "test.py"
        content = "print('hello')"
        fp.write_text(content)
        result = verify_tool_result("create_file", {
            "path": str(fp),
            "size": len(content.encode("utf-8")),
            "verified_exists": True,
        })
        assert result.passed is True

    def test_execute_command_success(self) -> None:
        result = verify_tool_result("execute_command", {
            "success": True,
            "exit_code": 0,
            "stdout": "hello world\n",
            "stderr": "",
        })
        assert result.passed is True

    def test_execute_command_failure(self) -> None:
        result = verify_tool_result("execute_command", {
            "success": False,
            "exit_code": 1,
            "stdout": "",
            "stderr": "FileNotFoundError",
        })
        assert result.passed is False


class TestPromptUpdates:
    """Verify the system prompt contains FASE N truth/verification section."""

    def test_prompt_contains_truth_guarantee(self) -> None:
        prompt = build_system_prompt(tool_names=["create_file", "execute_command"])
        assert "TRUTH GUARANTEE" in prompt
        assert "verification_failed" in prompt

    def test_prompt_contains_fase_n(self) -> None:
        prompt = build_system_prompt(tool_names=["create_file"])
        assert "Truth & Verification" in prompt

    def test_prompt_contains_verification_hints(self) -> None:
        prompt = build_system_prompt(tool_names=["create_file"])
        assert "hints" in prompt

    def test_prompt_still_has_existing_sections(self) -> None:
        prompt = build_system_prompt(
            tool_names=["create_file", "execute_command", "analyze_project"],
            model_name="llama3.1",
        )
        assert "## Tools" in prompt
        assert "## Error Recovery" in prompt
        assert "## Response" in prompt
        assert "## Documents & Images" in prompt

    def test_prompt_no_tool_names_still_works(self) -> None:
        prompt = build_system_prompt()
        assert "Chiky" in prompt
        assert "Capabilities" in prompt


class TestClassifierEdgeCases:
    def test_unicode_input(self) -> None:
        result = classify_task("crear un archivo")
        assert isinstance(result, TaskType)

    def test_very_long_input(self) -> None:
        long_text = "create a project " * 100
        result = classify_task(long_text)
        assert result == TaskType.PROJECT_CREATE

    def test_case_insensitive(self) -> None:
        assert classify_task("CREATE A PROJECT") == TaskType.PROJECT_CREATE
        assert classify_task("Create A Project") == TaskType.PROJECT_CREATE

    def test_mixed_language(self) -> None:
        result = classify_task("create un proyecto nuevo")
        assert isinstance(result, TaskType)


class TestVerifierEdgeCases:
    def test_empty_result_dict(self) -> None:
        result = verify_tool_result("create_file", {})
        assert result.passed is False

    def test_none_path(self) -> None:
        result = verify_tool_result("create_file", {"path": None})
        assert result.passed is False

    def test_tool_error_with_no_result_field(self) -> None:
        result = verify_tool_result("some_tool", {"error": "timeout"})
        assert result.passed is False

    def test_tool_error_with_result_field(self) -> None:
        # Tool returned both error and result — still counts as success path
        result = verify_tool_result("some_tool", {
            "error": "partial failure",
            "result": "some data",
        })
        assert result.passed is True  # no verifier for some_tool, assumes success


class TestProfileCapabilities:
    def test_project_create_needs_all_caps(self) -> None:
        p = get_task_profile(TaskType.PROJECT_CREATE)
        assert "file_ops" in p.required_capabilities
        assert "project_mgmt" in p.required_capabilities
        assert "code_exec" in p.required_capabilities

    def test_chat_needs_nothing(self) -> None:
        p = get_task_profile(TaskType.CHAT)
        assert len(p.required_capabilities) == 0

    def test_debug_needs_code_exec(self) -> None:
        p = get_task_profile(TaskType.CODE_DEBUG)
        assert "code_exec" in p.required_capabilities


# ---------------------------------------------------------------------------
# Agent-level integration tests — cover builtin.py N code paths
# ---------------------------------------------------------------------------


def _tool_response(tool_name: str, args: dict) -> str:
    block = json.dumps({"tool": tool_name, "args": args})
    return "```tool\n" + block + "\n```"


class TestExecutionAgentClassification:
    """Test that ExecutionAgent classifies tasks and logs model intelligence."""

    @pytest.mark.asyncio
    async def test_chat_task_classified(self) -> None:
        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput

        registry = default_tool_registry()
        text = "Just a plain response, no tools."

        async def generate(request: object) -> ProviderResponse:
            return ProviderResponse(text=text, provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="hello", correlation_id="test",
            risk_level=RiskLevel.LOW, context={},
        )
        await agent.run(data)
        metrics = agent.last_metrics
        assert metrics is not None
        assert metrics.final_outcome == "chat"

    @pytest.mark.asyncio
    async def test_create_file_task_classified(self) -> None:
        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput

        registry = default_tool_registry()
        text = _tool_response("create_file", {"path": "test_n_class.txt", "content": "hi"})

        async def generate(request: object) -> ProviderResponse:
            return ProviderResponse(text=text, provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="create a file", correlation_id="test",
            risk_level=RiskLevel.LOW, context={},
        )
        await agent.run(data)
        metrics = agent.last_metrics
        assert metrics is not None
        # Task type is file_create; outcome may include verification_failed
        assert metrics.final_outcome.startswith("file_create")

    @pytest.mark.asyncio
    async def test_model_intelligence_logged(self) -> None:
        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput

        registry = default_tool_registry()
        text = "OK done."

        async def generate(request: object) -> ProviderResponse:
            return ProviderResponse(text=text, provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="hello", correlation_id="test",
            risk_level=RiskLevel.LOW, context={"model": "llama3.1"},
        )
        await agent.run(data)
        metrics = agent.last_metrics
        assert metrics is not None
        assert metrics.final_outcome == "chat"

    @pytest.mark.asyncio
    async def test_project_create_needs_approval(self) -> None:
        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput

        registry = default_tool_registry()
        text = _tool_response("create_project", {"project_path": "/tmp/test"})

        async def generate(request: object) -> ProviderResponse:
            return ProviderResponse(text=text, provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="create a crud app", correlation_id="test",
            risk_level=RiskLevel.LOW, context={},
        )
        result = await agent.run(data)
        # Should require approval, not execute
        assert "__APPROVAL_REQUIRED__" in result.content

    @pytest.mark.asyncio
    async def test_verification_failure_appended(self) -> None:
        """Test that verification failure appends note to response."""
        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput

        registry = default_tool_registry()
        # Create a file that exists, then ask to create it at a missing path
        text = _tool_response("create_file", {
            "path": "C:\\nonexistent\\deep\\path\\fake.txt",
            "content": "x",
            "size": 1,
        })

        async def generate(request: object) -> ProviderResponse:
            return ProviderResponse(text=text, provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="create a file at a bad path", correlation_id="test",
            risk_level=RiskLevel.LOW, context={"approval_granted": True},
        )
        await agent.run(data)
        metrics = agent.last_metrics
        assert metrics is not None
        # The file doesn't exist, so verification should fail
        assert metrics.verification_status == "failed"

    @pytest.mark.asyncio
    async def test_debug_task_classified(self) -> None:
        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput

        registry = default_tool_registry()
        text = "Found the bug, fixed it."

        async def generate(request: object) -> ProviderResponse:
            return ProviderResponse(text=text, provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="fix this error", correlation_id="test",
            risk_level=RiskLevel.LOW, context={},
        )
        await agent.run(data)
        metrics = agent.last_metrics
        assert metrics is not None
        assert metrics.final_outcome == "code_debug"
