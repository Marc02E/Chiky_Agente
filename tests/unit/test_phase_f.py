"""Tests for Phase F: Provider Resilience."""

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from personal_ai_secretary.agents.builtin import ExecutionAgent
from personal_ai_secretary.agents.contracts import AgentInput
from personal_ai_secretary.domain.contracts import RiskLevel


class MockProvider:
    name = "mock"

    def __init__(self, responses=None, fail_with=None):
        self.responses = list(responses or [])
        self.call_count = 0
        self.fail_with = fail_with

    async def health(self):
        from personal_ai_secretary.domain.contracts import ProviderInfo
        return ProviderInfo(
            name=self.name, mode="test", available=True,
            is_ai=False, detail="test provider",
        )

    async def generate(self, request):
        from personal_ai_secretary.domain.contracts import ProviderResponse
        if self.fail_with is not None:
            raise self.fail_with
        if self.call_count < len(self.responses):
            text = self.responses[self.call_count]
        else:
            text = "Done."
        self.call_count += 1
        return ProviderResponse(text=text, provider=self.name)


def _tc(tool: str, args: dict) -> str:
    return f"```tool\n{json.dumps({'tool': tool, 'args': args})}\n```"


# === OllamaProvider Error Handling ===


@pytest.mark.asyncio
async def test_ollama_provider_connection_error() -> None:
    from personal_ai_secretary.domain.contracts import RequestEnvelope
    from personal_ai_secretary.providers.ollama import OllamaProvider

    provider = OllamaProvider(model="nonexistent")
    provider._base_url = "http://localhost:99999"
    envelope = RequestEnvelope(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        input="hello", risk_level=RiskLevel.LOW, correlation_id="test",
    )
    with pytest.raises(ConnectionError, match="Cannot connect to Ollama"):
        await provider.generate(envelope)


@pytest.mark.asyncio
async def test_ollama_health_connection_error() -> None:
    from personal_ai_secretary.providers.ollama import OllamaProvider

    provider = OllamaProvider(model="nonexistent")
    provider._base_url = "http://localhost:99999"
    info = await provider.health()
    assert info.available is False
    assert "not running" in info.detail.lower()


@pytest.mark.asyncio
async def test_ollama_health_timeout() -> None:
    from unittest.mock import AsyncMock, patch

    from personal_ai_secretary.providers.ollama import OllamaProvider

    provider = OllamaProvider(model="test")
    with patch("httpx.AsyncClient") as mock_client:
        instance = mock_client.return_value.__aenter__.return_value
        instance.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        info = await provider.health()
    assert info.available is False
    assert "timed out" in info.detail.lower()


@pytest.mark.asyncio
async def test_ollama_generate_empty_response() -> None:
    mock = MockProvider(responses=[""])
    agent = ExecutionAgent(provider=mock)
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="hello", correlation_id="test", risk_level=RiskLevel.LOW,
    )
    artifact = await agent.run(data)
    assert "empty response" in artifact.content.lower()


# === Agentic Loop Resilience ===


def test_max_tool_rounds_is_15() -> None:
    assert ExecutionAgent.MAX_TOOL_ROUNDS == 15


@pytest.mark.asyncio
async def test_max_tool_rounds_limits_execution() -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry

    responses = [_tc("calculator", {"expression": f"{i}+1"}) for i in range(20)]
    mock = MockProvider(responses=responses)
    agent = ExecutionAgent(provider=mock, registry=default_tool_registry())
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="calc", correlation_id="test", risk_level=RiskLevel.LOW,
    )
    await agent.run(data)
    assert mock.call_count <= ExecutionAgent.MAX_TOOL_ROUNDS + 1


@pytest.mark.asyncio
async def test_repeated_tool_call_breaks_loop() -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry

    responses = [_tc("calculator", {"expression": "1+1"})] * 10
    mock = MockProvider(responses=responses)
    agent = ExecutionAgent(provider=mock, registry=default_tool_registry())
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="calc", correlation_id="test", risk_level=RiskLevel.LOW,
    )
    await agent.run(data)
    assert mock.call_count <= 5


@pytest.mark.asyncio
async def test_connection_error_returns_user_message(manual_routing_manager) -> None:
    mock = MockProvider(fail_with=ConnectionError("Cannot connect to Ollama"))
    agent = ExecutionAgent(provider=mock)
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="hello", correlation_id="test", risk_level=RiskLevel.LOW,
    )
    artifact = await agent.run(data)
    assert "Ollama" in artifact.content
    assert mock.call_count == 0


@pytest.mark.asyncio
async def test_timeout_returns_user_message(manual_routing_manager) -> None:
    mock = MockProvider(fail_with=TimeoutError("timeout"))
    agent = ExecutionAgent(provider=mock)
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="hello", correlation_id="test", risk_level=RiskLevel.LOW,
    )
    artifact = await agent.run(data)
    assert "timed out" in artifact.content.lower() or "timeout" in artifact.content.lower()


@pytest.mark.asyncio
async def test_value_error_returns_user_message(manual_routing_manager) -> None:
    mock = MockProvider(fail_with=ValueError("Model not found"))
    agent = ExecutionAgent(provider=mock)
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="hello", correlation_id="test", risk_level=RiskLevel.LOW,
    )
    artifact = await agent.run(data)
    assert "not found" in artifact.content.lower()


@pytest.mark.asyncio
async def test_unexpected_error_returns_user_message(manual_routing_manager) -> None:
    mock = MockProvider(fail_with=RuntimeError("something broke"))
    agent = ExecutionAgent(provider=mock)
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="hello", correlation_id="test", risk_level=RiskLevel.LOW,
    )
    artifact = await agent.run(data)
    assert "unexpected" in artifact.content.lower() or "error" in artifact.content.lower()


@pytest.mark.asyncio
async def test_error_preserves_correlation_id() -> None:
    observation = MagicMock()
    observation.inc = MagicMock()
    observation.emit = AsyncMock()
    observation.record_duration = MagicMock()

    mock = MockProvider(fail_with=ConnectionError("connection failed"))
    agent = ExecutionAgent(provider=mock, observability=observation)
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="hello", correlation_id="corr-123", risk_level=RiskLevel.LOW,
    )
    await agent.run(data)
    assert observation.emit.called
    calls = [str(c) for c in observation.emit.call_args_list]
    assert any("corr-123" in c for c in calls)


# === Tool Call Parser Hardening ===


def test_extract_malformed_json() -> None:
    agent = ExecutionAgent()
    assert agent._extract_tool_call("```tool\n{not json\n```") is None


def test_extract_empty_block() -> None:
    agent = ExecutionAgent()
    assert agent._extract_tool_call("```tool\n\n```") is None


def test_extract_missing_tool_key() -> None:
    agent = ExecutionAgent()
    assert agent._extract_tool_call('```tool\n{"args": {"x": 1}}\n```') is None


def test_extract_nested_args() -> None:
    agent = ExecutionAgent()
    text = '```tool\n{"tool": "create_file", "args": {"path": "/t.txt", "content": "hi"}}\n```'
    result = agent._extract_tool_call(text)
    assert result is not None
    assert result[0] == "create_file"


def test_extract_inline_nested() -> None:
    agent = ExecutionAgent()
    text = 'Text {"tool": "create_file", "args": {"path": "/t.txt", "content": "hi"}} end'
    result = agent._extract_tool_call(text)
    assert result is not None
    assert result[0] == "create_file"


def test_extract_text_around_block() -> None:
    agent = ExecutionAgent()
    text = 'Here\n```tool\n{"tool": "calc", "args": {"expression": "1"}}\n```\nDone'
    result = agent._extract_tool_call(text)
    assert result is not None
    assert result[0] == "calc"


def test_extract_all_multiple() -> None:
    agent = ExecutionAgent()
    text = (
        '```tool\n{"tool": "create_directory", "args": {"path": "/p"}}\n```\n'
        '```tool\n{"tool": "create_file", "args": {"path": "/p/a.py", "content": "x"}}\n```'
    )
    assert len(agent._extract_all_tool_calls(text)) == 2


def test_extract_all_plain_text() -> None:
    agent = ExecutionAgent()
    assert agent._extract_all_tool_calls("Hello") == []


def test_extract_all_invalid_then_valid() -> None:
    agent = ExecutionAgent()
    text = '```tool\nbad\n```\n```tool\n{"tool": "calc", "args": {"expression": "1"}}\n```'
    calls = agent._extract_all_tool_calls(text)
    assert len(calls) == 1
    assert calls[0][0] == "calc"


def test_extract_openai_style_parameters() -> None:
    agent = ExecutionAgent()
    text = '{"name": "create_file", "parameters": {"path": "/p/a.py", "content": "x"}}'
    calls = agent._extract_all_tool_calls(text)
    assert len(calls) == 1
    assert calls[0][0] == "create_file"
    assert calls[0][1]["path"] == "/p/a.py"


def test_extract_openai_style_arguments() -> None:
    agent = ExecutionAgent()
    text = '{"name": "calculator", "arguments": {"expression": "1+1"}}'
    calls = agent._extract_all_tool_calls(text)
    assert len(calls) == 1
    assert calls[0][0] == "calculator"
    assert calls[0][1]["expression"] == "1+1"


def test_extract_function_wrapper_with_json_string() -> None:
    agent = ExecutionAgent()
    text = (
        '{"function": {"name": "create_directory", '
        '"arguments": "{\\"path\\": \\"/p\\"}"}}'
    )
    calls = agent._extract_all_tool_calls(text)
    assert len(calls) == 1
    assert calls[0][0] == "create_directory"
    assert calls[0][1]["path"] == "/p"


def test_extract_openai_plain_name_only_is_ignored() -> None:
    agent = ExecutionAgent()
    text = '{"name": "create_file"}'
    assert agent._extract_all_tool_calls(text) == []


def test_balanced_json_valid() -> None:
    result = ExecutionAgent._extract_balanced_json('{"tool": "x", "args": {"a": 1}}', 0)
    assert result is not None
    assert json.loads(result)["tool"] == "x"


def test_balanced_json_not_at_brace() -> None:
    assert ExecutionAgent._extract_balanced_json("hello", 0) is None


def test_balanced_json_nested() -> None:
    text = '{"tool": "x", "args": {"nested": {"deep": true}}}'
    result = ExecutionAgent._extract_balanced_json(text, 0)
    assert result is not None
    assert json.loads(result)["args"]["nested"]["deep"] is True


# === Error Messages ===


@pytest.mark.asyncio
async def test_error_no_internal_paths() -> None:
    mock = MockProvider(fail_with=RuntimeError("Error in /home/user/.ollama"))
    agent = ExecutionAgent(provider=mock)
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="hello", correlation_id="test", risk_level=RiskLevel.LOW,
    )
    artifact = await agent.run(data)
    assert "/home/user" not in artifact.content


@pytest.mark.asyncio
async def test_error_no_traceback() -> None:
    mock = MockProvider(fail_with=RuntimeError("test"))
    agent = ExecutionAgent(provider=mock)
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="hello", correlation_id="test", risk_level=RiskLevel.LOW,
    )
    artifact = await agent.run(data)
    assert "Traceback" not in artifact.content


# === Tool Execution Errors ===


@pytest.mark.asyncio
async def test_unknown_tool_returns_error() -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry
    agent = ExecutionAgent(registry=default_tool_registry())
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="test", correlation_id="test", risk_level=RiskLevel.LOW,
    )
    result = await agent._execute_tool_from_llm("nonexistent_tool", {}, data)
    assert "error" in result


@pytest.mark.asyncio
async def test_bad_args_returns_error() -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry
    agent = ExecutionAgent(registry=default_tool_registry())
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="test", correlation_id="test", risk_level=RiskLevel.LOW,
    )
    result = await agent._execute_tool_from_llm("calculator", {"bad_arg": "x"}, data)
    assert "error" in result


@pytest.mark.asyncio
async def test_no_registry_returns_error() -> None:
    agent = ExecutionAgent(provider=None, registry=None)
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="test", correlation_id="test", risk_level=RiskLevel.LOW,
    )
    result = await agent._execute_tool_from_llm("calc", {}, data)
    assert "error" in result


@pytest.mark.asyncio
async def test_approval_required_returns_error() -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry
    agent = ExecutionAgent(registry=default_tool_registry())
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="test", correlation_id="test", risk_level=RiskLevel.LOW,
    )
    result = await agent._execute_tool_from_llm(
        "create_file", {"path": "/tmp/t.txt", "content": "x"}, data
    )
    assert "approval" in result.get("error", "").lower() or result.get("requires_approval")


# === Deterministic Provider ===


@pytest.mark.asyncio
async def test_deterministic_provider_unchanged() -> None:
    from personal_ai_secretary.domain.contracts import RequestEnvelope
    from personal_ai_secretary.providers.deterministic import DeterministicProvider
    provider = DeterministicProvider()
    envelope = RequestEnvelope(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        input="hello", risk_level=RiskLevel.LOW, correlation_id="test",
    )
    response = await provider.generate(envelope)
    assert response.text.startswith("DETERMINISTIC_RESPONSE:")


@pytest.mark.asyncio
async def test_deterministic_provider_health() -> None:
    from personal_ai_secretary.providers.deterministic import DeterministicProvider
    info = await DeterministicProvider().health()
    assert info.available is True


# === Model Selection ===


def test_model_selection_works() -> None:
    from personal_ai_secretary.providers.factory import get_provider, set_current_model
    from personal_ai_secretary.providers.ollama import OllamaProvider
    from personal_ai_secretary.shared.config import get_settings

    settings = get_settings()
    original = settings.ai_provider
    try:
        settings.ai_provider = "local"
        for model in ["deepseek-coder-v2:latest", "llama3.1:latest", "llama3:latest"]:
            set_current_model(model)
            provider = get_provider()
            assert isinstance(provider, OllamaProvider)
            assert provider.model == model
    finally:
        settings.ai_provider = original


# === No Tool Call Ends Loop ===


@pytest.mark.asyncio
async def test_no_tool_call_ends_loop() -> None:
    mock = MockProvider(responses=["Hello! How can I help?"])
    agent = ExecutionAgent(provider=mock)
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="What's 2+2?", correlation_id="test", risk_level=RiskLevel.LOW,
    )
    await agent.run(data)
    assert mock.call_count == 1


# === Agentic Loop Integration ===


@pytest.mark.asyncio
async def test_agent_creates_file_and_verifies(tmp_path) -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry

    p = str(tmp_path / "hello.txt")
    responses = [
        _tc("create_file", {"path": p, "content": "Hello!"}),
        _tc("read_file", {"path": p}),
        "Done!",
    ]
    mock = MockProvider(responses=responses)
    agent = ExecutionAgent(provider=mock, registry=default_tool_registry())
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="Create and verify", correlation_id="test",
        risk_level=RiskLevel.LOW, context={"approval_granted": True},
    )
    await agent.run(data)
    assert (tmp_path / "hello.txt").read_text() == "Hello!"
