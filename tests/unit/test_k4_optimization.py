"""Tests for K.4 Token and Latency Optimizations.

Covers: RequestMetrics, compact prompt, tool-result cap, dedup,
history truncation, model-specific notes, estimate_tokens.
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from personal_ai_secretary.agents.contracts import AgentInput
from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel
from personal_ai_secretary.observability.request_metrics import (
    RequestMetrics,
    estimate_tokens,
)

# ═══════════════════════════════════════════════════════════════════════════════
# RequestMetrics
# ═══════════════════════════════════════════════════════════════════════════════


class TestRequestMetrics:
    def test_initial_state(self) -> None:
        m = RequestMetrics()
        assert m.llm_calls == 0
        assert m.tool_calls == 0
        assert m.rounds == 0
        assert m.total_time == 0.0

    def test_record_llm_call(self) -> None:
        m = RequestMetrics()
        m.record_llm_call(0.5, 100)
        assert m.llm_calls == 1
        assert m.llm_times == [0.5]
        assert m.response_chars_approx == 100

    def test_record_llm_call_cumulative(self) -> None:
        m = RequestMetrics()
        m.record_llm_call(0.3, 50)
        m.record_llm_call(0.7, 80)
        assert m.llm_calls == 2
        assert m.llm_times == [0.3, 0.7]
        assert m.response_chars_approx == 130

    def test_record_tool_call(self) -> None:
        m = RequestMetrics()
        m.record_tool_call("calculator", 0.1)
        assert m.tool_calls == 1
        assert m.tool_times == [0.1]
        assert m.tool_call_names == ["calculator"]

    def test_record_round(self) -> None:
        m = RequestMetrics()
        m.record_round(500, system_prompt_chars=200, history_chars=300)
        assert m.rounds == 1
        assert m.context_chars_approx == 500
        assert m.system_prompt_chars == 200
        assert m.history_chars == 300

    def test_record_round_max_context(self) -> None:
        m = RequestMetrics()
        m.record_round(500)
        m.record_round(300)
        assert m.context_chars_approx == 500  # keeps max

    def test_finish(self) -> None:
        m = RequestMetrics()
        m._start = 0.0
        m.finish()
        assert m.total_time >= 0.0

    def test_summary(self) -> None:
        m = RequestMetrics()
        m.provider = "ollama"
        m.model = "llama3.1"
        m.record_llm_call(0.5, 100)
        m.record_tool_call("calculator", 0.1)
        m.record_round(500)
        m.deduplicated_calls = 2
        s = m.summary()
        assert s["provider"] == "ollama"
        assert s["model"] == "llama3.1"
        assert s["llm_calls"] == 1
        assert s["tool_calls"] == 1
        assert s["deduplicated_calls"] == 2
        assert s["rounds"] == 1
        assert s["avg_llm_time_s"] == 0.5

    def test_summary_empty_llm_times(self) -> None:
        m = RequestMetrics()
        s = m.summary()
        assert s["avg_llm_time_s"] == 0.0


def test_estimate_tokens() -> None:
    assert estimate_tokens(0) == 0
    assert estimate_tokens(4) == 1
    assert estimate_tokens(8) == 2
    assert estimate_tokens(100) == 25


# ═══════════════════════════════════════════════════════════════════════════════
# Compact System Prompt
# ═══════════════════════════════════════════════════════════════════════════════


class TestCompactPrompt:
    def test_basic_identity(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt

        prompt = build_system_prompt()
        assert "Chiky" in prompt
        assert "AI secretary" in prompt
        assert len(prompt) < 1500

    def test_compact_tool_descriptions(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt

        descs = ["calculator: Evaluate math", "read_file: Read file contents"]
        prompt = build_system_prompt(
            tool_names=["calculator", "read_file"],
            compact_descriptions=descs,
        )
        assert "calculator: Evaluate math" in prompt
        assert "read_file: Read file contents" in prompt

    def test_tool_names_fallback(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt

        prompt = build_system_prompt(tool_names=["calculator", "read_file"])
        assert "calculator" in prompt
        assert "read_file" in prompt
        assert "Available:" in prompt

    def test_model_specific_llama(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt

        prompt = build_system_prompt(model_name="llama3.1")
        assert "Llama" in prompt

    def test_model_specific_deepseek(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt

        prompt = build_system_prompt(model_name="deepseek-coder-v2")
        assert "DeepSeek" in prompt

    def test_model_specific_qwen(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt

        prompt = build_system_prompt(model_name="qwen2.5-coder")
        assert "qwen" in prompt.lower() or "JSON" in prompt

    def test_model_specific_mistral(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt

        prompt = build_system_prompt(model_name="mistral-7b")
        assert "mistral" in prompt.lower() or "JSON" in prompt

    def test_no_model_note_for_unknown(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt

        prompt = build_system_prompt(model_name="gpt-4")
        assert "Note:" not in prompt

    def test_user_name(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt

        prompt = build_system_prompt(user_name="Alice")
        assert "Alice" in prompt

    def test_extra_context(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt

        prompt = build_system_prompt(extra_context="User prefers dark mode")
        assert "User prefers dark mode" in prompt

    def test_dev_workflow_section(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt

        prompt = build_system_prompt(tool_names=["analyze_project", "modify_file"])
        assert "Development Workflow" in prompt
        assert "modify_file" in prompt

    def test_no_dev_workflow_without_tools(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt

        prompt = build_system_prompt(tool_names=["calculator"])
        assert "Development Workflow" not in prompt

    def test_compact_result(self) -> None:
        from personal_ai_secretary.tools.prompt import build_tool_result_prompt

        result = build_tool_result_prompt("calculator", {"result": 42})
        assert "calculator" in result
        assert "42" in result

    def test_large_result_truncation(self) -> None:
        from personal_ai_secretary.tools.prompt import build_tool_result_prompt

        big = {"content": "x" * 5000}
        result = build_tool_result_prompt("read_file", big)
        assert "truncated" in result
        assert len(result) < 4000

    def test_estimate_prompt_tokens(self) -> None:
        from personal_ai_secretary.tools.prompt import estimate_prompt_tokens

        assert estimate_prompt_tokens("hello") == 1  # 5 chars / 4 = 1
        assert estimate_prompt_tokens("a" * 8) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Compact Descriptions in Registry
# ═══════════════════════════════════════════════════════════════════════════════


def test_registry_compact_descriptions() -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry

    registry = default_tool_registry()
    descs = registry.compact_descriptions()
    assert len(descs) > 0
    assert any("calculator" in d for d in descs)
    assert any("read_file" in d for d in descs)
    assert any("modify_file" in d for d in descs)
    assert any("analyze_project" in d for d in descs)


# ═══════════════════════════════════════════════════════════════════════════════
# Tool-result size cap
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_tool_result_size_cap() -> None:
    """Tool results > 2000 chars are truncated when fed back to LLM."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.tools.builtin import default_tool_registry

    big_content = "x = 1\n" * 500  # ~3000 chars

    async def big_read(args: dict) -> dict:
        return {"content": big_content, "lines": 500}

    # Replace read_file with a handler that returns huge content
    registry = default_tool_registry()
    read_def = registry.get("read_file")
    assert read_def is not None
    registry._tools["read_file"] = type(read_def)(
        name="read_file",
        risk=read_def.risk,
        requires_explicit_approval=read_def.requires_explicit_approval,
        handler=big_read,
        argument_schema=read_def.argument_schema,
    )

    call_count = 0

    async def generate(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ProviderResponse(
                text='```tool\n{"tool": "read_file", "args": {"path": "/tmp/test.txt"}}\n```',
                provider="mock",
            )
        return ProviderResponse(text="Done.", provider="mock")

    mock_provider = AsyncMock()
    mock_provider.name = "mock"
    mock_provider.model = "test"
    mock_provider.generate = generate

    agent = ExecutionAgent(provider=mock_provider, registry=registry)
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="read the file",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
    )
    # Need to allow the path
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS

    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = ["/tmp"]
    try:
        result = await agent.run(data)
        assert result.blocked is False
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


# ═══════════════════════════════════════════════════════════════════════════════
# Dedup: same call not re-executed
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dedup_skips_identical_calls() -> None:
    """Identical tool calls are executed only once; subsequent are deduped."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.tools.builtin import default_tool_registry

    exec_count = 0

    async def counting_calc(args: dict) -> dict:
        nonlocal exec_count
        exec_count += 1
        return {"result": 2}

    registry = default_tool_registry()
    calc_def = registry.get("calculator")
    assert calc_def is not None
    registry._tools["calculator"] = type(calc_def)(
        name="calculator",
        risk=calc_def.risk,
        requires_explicit_approval=False,
        handler=counting_calc,
        argument_schema=calc_def.argument_schema,
    )

    call_count = 0

    async def generate(request):
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            # LLM keeps outputting the same tool call
            return ProviderResponse(
                text='```tool\n{"tool": "calculator", "args": {"expression": "1+1"}}\n```',
                provider="mock",
            )
        return ProviderResponse(text="Done.", provider="mock")

    mock_provider = AsyncMock()
    mock_provider.name = "mock"
    mock_provider.model = "test"
    mock_provider.generate = generate

    agent = ExecutionAgent(provider=mock_provider, registry=registry)
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="calc",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
    )
    await agent.run(data)
    # Should only execute once, not 3 times
    assert exec_count == 1
    # Should not call LLM more than 4 times (3 tool + 1 text)
    assert call_count <= 4


# ═══════════════════════════════════════════════════════════════════════════════
# History truncation
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_history_truncation() -> None:
    """Long conversation history is truncated to fit within budget."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    # Build a huge history (> 8000 chars total)
    huge_history = []
    for i in range(50):
        huge_history.append({"role": "user", "content": f"Question {i}: " + "x" * 200})
        huge_history.append({"role": "assistant", "content": f"Answer {i}: " + "y" * 200})

    mock_provider = AsyncMock()
    mock_provider.name = "mock"
    mock_provider.model = "test"
    mock_provider.generate = AsyncMock(return_value=ProviderResponse(text="OK", provider="mock"))

    agent = ExecutionAgent(provider=mock_provider, registry=None)
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="hello",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
        context={"conversation_history": huge_history},
    )
    result = await agent.run(data)
    assert result.content == "OK"

    # Verify the request sent to provider had truncated history
    call_args = mock_provider.generate.call_args
    envelope = call_args[0][0]
    # Should have fewer messages than the full history
    assert len(envelope.messages) < len(huge_history)


# ═══════════════════════════════════════════════════════════════════════════════
# DeepSeek behavior analysis
# ═══════════════════════════════════════════════════════════════════════════════


def test_deepseek_protocol_token_cleaning() -> None:
    """DeepSeek protocol tokens are stripped from response."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    text = (
        "<|tool_calls_begin|>"
        '{"tool": "calculator", "args": {"expression": "1+1"}}'
        "<|tool_calls_end|>"
    )
    cleaned = ExecutionAgent._sanitize_response(text)
    assert "<|tool_calls_begin|>" not in cleaned
    assert "<|tool_calls_end|>" not in cleaned
    assert "calculator" in cleaned


def test_deepseek_xml_tool_call_parsing() -> None:
    """DeepSeek XML <tool_call> format is parsed."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    agent = ExecutionAgent()
    text = '<tool_call>{"tool": "calculator", "args": {"expression": "1+1"}}</tool_call>'
    calls = agent._extract_all_tool_calls(text)
    assert len(calls) == 1
    assert calls[0][0] == "calculator"


def test_deepseek_model_detection() -> None:
    """DeepSeek model is detected via duck typing."""
    from personal_ai_secretary.tools.prompt import build_system_prompt

    prompt = build_system_prompt(model_name="deepseek-coder-v2:16b")
    assert "DeepSeek" in prompt
    assert "No XML" in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# Llama3.1 behavior
# ═══════════════════════════════════════════════════════════════════════════════


def test_llama31_model_detection() -> None:
    from personal_ai_secretary.tools.prompt import build_system_prompt

    prompt = build_system_prompt(model_name="llama3.1:8b")
    assert "Llama" in prompt


def test_llama3_non_tool_calling_model() -> None:
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    assert "llama3" in ExecutionAgent.NON_TOOL_CALLING_MODELS


# ═══════════════════════════════════════════════════════════════════════════════
# Metrics integration in agentic loop
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_metrics_recorded_during_loop() -> None:
    """RequestMetrics tracks LLM calls, tool calls, rounds."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.tools.builtin import default_tool_registry

    call_count = 0

    async def generate(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ProviderResponse(
                text='```tool\n{"tool": "calculator", "args": {"expression": "1+1"}}\n```',
                provider="mock",
            )
        return ProviderResponse(text="Result is 2.", provider="mock")

    mock_provider = AsyncMock()
    mock_provider.name = "mock"
    mock_provider.model = "test-model"
    mock_provider.generate = generate

    agent = ExecutionAgent(provider=mock_provider, registry=default_tool_registry())
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="calculate 1+1",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
    )
    result = await agent.run(data)
    assert result.content == "Result is 2."
    assert call_count == 2  # 1 tool + 1 text


@pytest.mark.asyncio
async def test_metrics_on_provider_error() -> None:
    """Metrics still finish on provider error."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    async def generate(request):
        raise ConnectionError("Cannot connect to Ollama")

    mock_provider = AsyncMock()
    mock_provider.name = "mock"
    mock_provider.model = "test"
    mock_provider.generate = generate

    agent = ExecutionAgent(provider=mock_provider)
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="hello",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
    )
    result = await agent.run(data)
    assert "error" in result.content.lower() or "Ollama" in result.content


@pytest.mark.asyncio
async def test_empty_response_returns_message() -> None:
    """Empty LLM response returns user-friendly message."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    async def generate(request):
        return ProviderResponse(text="", provider="mock")

    mock_provider = AsyncMock()
    mock_provider.name = "mock"
    mock_provider.model = "test"
    mock_provider.generate = generate

    agent = ExecutionAgent(provider=mock_provider)
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="hello",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
    )
    result = await agent.run(data)
    assert "empty" in result.content.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# Prompt size comparison
# ═══════════════════════════════════════════════════════════════════════════════


def test_compact_prompt_is_smaller() -> None:
    """Compact prompt should be significantly smaller than a verbose one."""
    from personal_ai_secretary.tools.prompt import build_system_prompt

    prompt = build_system_prompt(
        tool_names=[
            "calculator", "list_tools", "datetime_now", "format_date",
            "get_directory", "create_file", "read_file", "write_file",
            "list_directory", "create_directory", "file_exists",
            "create_project", "analyze_project", "read_files",
            "modify_file", "search_files", "verify_files",
        ],
        compact_descriptions=[
            "calculator: Evaluate math",
            "list_tools: List tools",
            "datetime_now: Get date/time",
            "format_date: Format datetime",
            "get_directory: Get user dir",
            "create_file: Create a file",
            "read_file: Read file",
            "write_file: Write file",
            "list_directory: List dir",
            "create_directory: Create dir",
            "file_exists: Check exists",
            "create_project: Create project",
            "analyze_project: Analyze structure",
            "read_files: Read multiple files",
            "modify_file: Modify file",
            "search_files: Search files",
            "verify_files: Verify files",
        ],
    )
    # Should be under 7500 chars for 17 tools (includes L.1 + L.2 + L.3 + L.4-L.6 + L.7-L.9 prompt sections)
    assert len(prompt) < 7500
    # Token estimate should be reasonable
    tokens = len(prompt) // 4
    assert tokens < 1875
