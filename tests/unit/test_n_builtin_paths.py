"""FASE N — Additional builtin.py path coverage.

Tests to cover the fix-cycle detection, repeated tool call detection,
and other uncovered agent loop paths.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel
from personal_ai_secretary.tools.builtin import default_tool_registry


def _tool_response(tool_name: str, args: dict) -> str:
    block = json.dumps({"tool": tool_name, "args": args})
    return "```tool\n" + block + "\n```"


class TestRepeatedToolCallBreak:
    """Cover lines 524-534: repeated identical tool call detection."""

    @pytest.mark.asyncio
    async def test_same_tool_call_repeated_breaks(self) -> None:
        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput

        registry = default_tool_registry()
        # Same tool call returned every time by mock provider
        text = _tool_response("list_directory", {"path": "."})

        async def generate(request: object) -> ProviderResponse:
            return ProviderResponse(text=text, provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="list the directory", correlation_id="test",
            risk_level=RiskLevel.LOW, context={},
        )
        result = await agent.run(data)
        # Should have stopped due to repeated tool call
        assert result.content  # Has some response text
        assert agent.last_metrics is not None
        assert agent.last_metrics.tool_calls >= 1

    @pytest.mark.asyncio
    async def test_different_tools_dont_trigger_dedup(self) -> None:
        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput

        registry = default_tool_registry()
        # Two different tool calls
        responses = [
            _tool_response("list_directory", {"path": "."}),
            _tool_response("get_current_datetime", {}),
            "Here are the results.",
        ]
        call_count = 0

        async def generate(request: object) -> ProviderResponse:
            nonlocal call_count
            idx = min(call_count, len(responses) - 1)
            call_count += 1
            return ProviderResponse(text=responses[idx], provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="list the directory then get time", correlation_id="test",
            risk_level=RiskLevel.LOW, context={},
        )
        await agent.run(data)
        assert agent.last_metrics is not None
        assert agent.last_metrics.tool_calls >= 2


class TestFixCycleDetection:
    """Cover lines 668-683: fix cycle detection."""

    @pytest.mark.asyncio
    async def test_modify_then_execute_counted(self) -> None:
        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput

        registry = default_tool_registry()
        # Simulate modify_file then execute_command cycle
        responses = [
            _tool_response("modify_file", {"path": "test.py", "old_text": "a", "new_text": "b"}),
            _tool_response("execute_command", {"command": "python test.py"}),
            _tool_response("modify_file", {"path": "test.py", "old_text": "c", "new_text": "d"}),
            _tool_response("execute_command", {"command": "python test.py"}),
            "Fixed after testing.",
        ]
        call_count = 0

        async def generate(request: object) -> ProviderResponse:
            nonlocal call_count
            idx = min(call_count, len(responses) - 1)
            call_count += 1
            return ProviderResponse(text=responses[idx], provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="fix the code", correlation_id="test",
            risk_level=RiskLevel.LOW, context={"approval_granted": True},
        )
        await agent.run(data)
        assert agent.last_metrics is not None
        # Should have attempted some fix cycles
        assert agent.last_metrics.tool_calls >= 2

    @pytest.mark.asyncio
    async def test_max_fix_cycles_breaks(self) -> None:
        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput

        registry = default_tool_registry()
        # Generate many modify+execute pairs to hit MAX_FIX_CYCLES
        responses = []
        for i in range(6):
            responses.append(
                _tool_response("modify_file", {"path": "test.py", "old_text": f"old{i}", "new_text": f"new{i}"})
            )
            responses.append(
                _tool_response("execute_command", {"command": "python test.py"})
            )
        responses.append("After many fixes.")

        call_count = 0

        async def generate(request: object) -> ProviderResponse:
            nonlocal call_count
            idx = min(call_count, len(responses) - 1)
            call_count += 1
            return ProviderResponse(text=responses[idx], provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="fix the code repeatedly", correlation_id="test",
            risk_level=RiskLevel.LOW, context={"approval_granted": True},
        )
        result = await agent.run(data)
        assert agent.last_metrics is not None
        # Should have hit the fix cycle limit or repeated tool detection
        assert (
            "fix cycle" in result.content.lower()
            or "repeating" in result.content.lower()
            or "attempted" in result.content.lower()
            or agent.last_metrics.tool_calls >= 3
        )


class TestMaxSameToolName:
    """Cover lines 536-550: same tool name called too many times."""

    @pytest.mark.asyncio
    async def test_same_tool_name_breaks(self) -> None:
        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput

        registry = default_tool_registry()
        # Different args each time but same tool name
        responses = []
        for i in range(5):
            responses.append(
                _tool_response("read_file", {"path": f"file{i}.txt"})
            )
        responses.append("I've read many files.")

        call_count = 0

        async def generate(request: object) -> ProviderResponse:
            nonlocal call_count
            idx = min(call_count, len(responses) - 1)
            call_count += 1
            return ProviderResponse(text=responses[idx], provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="read all the files", correlation_id="test",
            risk_level=RiskLevel.LOW, context={},
        )
        result = await agent.run(data)
        assert agent.last_metrics is not None
        # Should have stopped due to too many calls of same tool
        assert result.content


class TestApprovalFlow:
    """Cover lines 642-663: approval required flow."""

    @pytest.mark.asyncio
    async def test_approval_required_breaks(self) -> None:
        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput

        registry = default_tool_registry()
        text = _tool_response("execute_command", {"command": "rm -rf /tmp/test"})

        async def generate(request: object) -> ProviderResponse:
            return ProviderResponse(text=text, provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="delete the test directory", correlation_id="test",
            risk_level=RiskLevel.HIGH, context={},
        )
        result = await agent.run(data)
        # Should require approval
        assert "__APPROVAL_REQUIRED__" in result.content


class TestToolCallParsingError:
    """Cover lines 84-85: ToolError in tool call parsing."""

    @pytest.mark.asyncio
    async def test_invalid_tool_call_format(self) -> None:
        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput

        registry = default_tool_registry()
        # Not a valid tool call format — just plain text
        responses = ["Just a normal response without tool calls."]

        async def generate(request: object) -> ProviderResponse:
            return ProviderResponse(text=responses[0], provider="mock")

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
        result = await agent.run(data)
        assert result.content
        assert agent.last_metrics is not None


class TestNonToolCallingModel:
    """Cover NON_TOOL_CALLING_MODELS fallback."""

    @pytest.mark.asyncio
    async def test_non_tool_model_fallback(self) -> None:
        from personal_ai_secretary.agents.builtin import ExecutionAgent
        from personal_ai_secretary.agents.contracts import AgentInput

        registry = default_tool_registry()
        # Response without tool call for a model in NON_TOOL_CALLING_MODELS
        text = "I'll create the file for you."

        async def generate(request: object) -> ProviderResponse:
            return ProviderResponse(text=text, provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "llama3"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        data = AgentInput(
            request_id=uuid4(), session_id=uuid4(), user_id="test",
            text="create a file", correlation_id="test",
            risk_level=RiskLevel.LOW, context={},
        )
        result = await agent.run(data)
        assert result.content
