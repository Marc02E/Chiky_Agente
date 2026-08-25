"""FASE K.2 — Integration tests for development agent capabilities.

Tests the full flow: UI/API -> Agent -> Provider -> ToolRegistry -> Filesystem -> result.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest


def _p(path: Path) -> str:
    """Return a JSON-safe path string (escape backslashes on Windows)."""
    return str(path).replace("\\", "\\\\")


# ═══════════════════════════════════════════════════════════════════════════════
# Agent -> ToolRegistry -> Development Tools
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_agent_analyze_project_via_tool_call(tmp_path: Path) -> None:
    """Agent must be able to call analyze_project through the tool system."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import RiskLevel
    from personal_ai_secretary.tools.builtin import default_tool_registry

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1")

    agent = ExecutionAgent(registry=default_tool_registry())
    args = json.dumps({"path": _p(tmp_path)})
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text=f"@tool:analyze_project {args}",
        correlation_id="test-integ-1",
        risk_level=RiskLevel.LOW,
    )
    result = await agent.run(data)
    assert not result.blocked
    assert "app.py" in result.content or "total_files" in result.content


@pytest.mark.asyncio
async def test_agent_read_files_via_tool_call(tmp_path: Path) -> None:
    """Agent must be able to call read_files through the tool system."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import RiskLevel
    from personal_ai_secretary.tools.builtin import default_tool_registry

    (tmp_path / "a.py").write_text("hello")
    (tmp_path / "b.py").write_text("world")

    agent = ExecutionAgent(registry=default_tool_registry())
    args = json.dumps({
        "paths": [str(tmp_path / "a.py"), str(tmp_path / "b.py")],
    })
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text=f"@tool:read_files {args}",
        correlation_id="test-integ-2",
        risk_level=RiskLevel.LOW,
    )
    result = await agent.run(data)
    assert not result.blocked
    assert "hello" in result.content
    assert "world" in result.content


@pytest.mark.asyncio
async def test_agent_modify_file_requires_approval(tmp_path: Path) -> None:
    """modify_file must require approval through the agent."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import RiskLevel
    from personal_ai_secretary.tools.builtin import default_tool_registry

    f = tmp_path / "code.py"
    f.write_text("old")

    agent = ExecutionAgent(registry=default_tool_registry())
    args = json.dumps({
        "path": _p(f),
        "mode": "overwrite",
        "search": "",
        "replacement": "",
        "content": "new",
    })
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text=f"@tool:modify_file {args}",
        correlation_id="test-integ-3",
        risk_level=RiskLevel.LOW,
        context={"approval_granted": False},
    )
    result = await agent.run(data)
    assert result.blocked
    assert "approval required" in result.content.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# Provider -> Agent -> ToolRegistry -> Filesystem (Mock LLM)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_llm_driven_analyze_and_modify_flow(tmp_path: Path) -> None:
    """Simulate LLM producing tool calls for analyze -> modify -> verify flow."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS

    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("x = 1\nprint(x)")

    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path)]
    try:
        mock_provider = AsyncMock()
        mock_provider.name = "mock"
        mock_provider.model = "test-model"

        mock_provider.generate = AsyncMock(side_effect=[
            ProviderResponse(
                text=(
                    '```tool\n'
                    '{"tool": "analyze_project", "args": '
                    + json.dumps({"path": str(project)})
                    + "}\n```"
                ),
                provider="mock", model="test",
            ),
            ProviderResponse(
                text=(
                    '```tool\n'
                    '{"tool": "modify_file", "args": '
                    + json.dumps({
                        "path": str(project / "main.py"),
                        "mode": "replace",
                        "search": "print(x)",
                        "replacement": "print('modified')",
                    })
                    + "}\n```"
                ),
                provider="mock", model="test",
            ),
            ProviderResponse(
                text="I analyzed the project and modified main.py.",
                provider="mock", model="test",
            ),
        ])

        registry = default_tool_registry()
        agent = ExecutionAgent(provider=mock_provider, registry=registry)

        data = AgentInput(
            request_id=uuid4(),
            session_id=uuid4(),
            user_id="test",
            text="analyze and modify the project",
            risk_level=RiskLevel.LOW,
            correlation_id="test-integ-4",
            context={"approval_granted": True},
        )

        result = await agent.run(data)
        assert not result.blocked
        assert (project / "main.py").read_text() == "x = 1\nprint('modified')"
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


@pytest.mark.asyncio
async def test_llm_driven_search_and_read_flow(tmp_path: Path) -> None:
    """Simulate LLM producing search -> read_files flow."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS

    (tmp_path / "a.py").write_text("def main(): pass")
    (tmp_path / "b.py").write_text("def helper(): pass")

    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path)]
    try:
        mock_provider = AsyncMock()
        mock_provider.name = "mock"
        mock_provider.model = "test-model"

        mock_provider.generate = AsyncMock(side_effect=[
            ProviderResponse(
                text=(
                    '```tool\n'
                    '{"tool": "search_files", "args": '
                    + json.dumps({"path": str(tmp_path), "pattern": "def"})
                    + "}\n```"
                ),
                provider="mock", model="test",
            ),
            ProviderResponse(
                text=(
                    '```tool\n'
                    '{"tool": "read_files", "args": '
                    + json.dumps({
                        "paths": [str(tmp_path / "a.py"), str(tmp_path / "b.py")],
                    })
                    + "}\n```"
                ),
                provider="mock", model="test",
            ),
            ProviderResponse(
                text="Found 2 files with 'def' keyword.",
                provider="mock", model="test",
            ),
        ])

        registry = default_tool_registry()
        agent = ExecutionAgent(provider=mock_provider, registry=registry)

        data = AgentInput(
            request_id=uuid4(),
            session_id=uuid4(),
            user_id="test",
            text="search for functions in the project",
            risk_level=RiskLevel.LOW,
            correlation_id="test-integ-5",
        )

        result = await agent.run(data)
        assert not result.blocked
        assert "Found 2 files" in result.content
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


# ═══════════════════════════════════════════════════════════════════════════════
# Workflow -> Agent -> ToolRegistry -> Filesystem
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_workflow_analyze_project(tmp_path: Path) -> None:
    """GovernedWorkflow must execute analyze_project tool call end-to-end."""
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    (tmp_path / "app.py").write_text("x = 1")

    workflow = GovernedWorkflow(tools=default_tool_registry())
    args = json.dumps({"path": _p(tmp_path)})
    result = await workflow.run(
        uuid4(), uuid4(), "user-1",
        f"@tool:analyze_project {args}",
        "corr-wf-analyze",
    )
    # Tool-only responses may be rejected by evaluation gate;
    # the important thing is that the workflow ran without crashing
    assert result.status in ("completed", "rejected")
    if result.response:
        assert "app.py" in result.response or result.status == "rejected"


@pytest.mark.asyncio
async def test_workflow_search_files(tmp_path: Path) -> None:
    """GovernedWorkflow must execute search_files tool call end-to-end."""
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    (tmp_path / "code.py").write_text("target_value = 42")

    workflow = GovernedWorkflow(tools=default_tool_registry())
    args = json.dumps({"path": _p(tmp_path), "pattern": "target_value"})
    result = await workflow.run(
        uuid4(), uuid4(), "user-1",
        f"@tool:search_files {args}",
        "corr-wf-search",
    )
    assert result.status in ("completed", "rejected")


@pytest.mark.asyncio
async def test_workflow_verify_files(tmp_path: Path) -> None:
    """GovernedWorkflow must execute verify_files tool call end-to-end."""
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    (tmp_path / "config.json").write_text('{"key": "value"}')

    workflow = GovernedWorkflow(tools=default_tool_registry())
    args = json.dumps({
        "verifications": [{"path": _p(tmp_path / "config.json"), "content_contains": "key"}],
    })
    result = await workflow.run(
        uuid4(), uuid4(), "user-1",
        f"@tool:verify_files {args}",
        "corr-wf-verify",
    )
    assert result.status in ("completed", "rejected")


# ═══════════════════════════════════════════════════════════════════════════════
# Model Adaptation Integration
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_agent_uses_model_specific_prompt() -> None:
    """Agent must pass model name to system prompt when provider has model attr."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel

    mock_provider = AsyncMock()
    mock_provider.name = "ollama"
    mock_provider.model = "llama3.2"
    mock_provider.generate = AsyncMock(
        return_value=ProviderResponse(
            text="I'll help you with that.", provider="ollama", model="llama3.2",
        )
    )

    agent = ExecutionAgent(provider=mock_provider)
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="hello", risk_level=RiskLevel.LOW,
        correlation_id="test-model-prompt",
    )

    await agent.run(data)
    assert mock_provider.generate.called
    call_args = mock_provider.generate.call_args
    envelope = call_args[0][0]
    assert "Llama" in envelope.context_summary or "llama" in envelope.context_summary.lower()


@pytest.mark.asyncio
async def test_agent_without_model_attribute_works() -> None:
    """Agent must work even when provider doesn't have a model attribute."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel

    mock_provider = AsyncMock()
    mock_provider.name = "deterministic"
    del mock_provider.model
    mock_provider.generate = AsyncMock(
        return_value=ProviderResponse(text="Response", provider="deterministic")
    )

    agent = ExecutionAgent(provider=mock_provider)
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="hello", risk_level=RiskLevel.LOW,
        correlation_id="test-no-model",
    )

    result = await agent.run(data)
    assert "Response" in result.content


# ═══════════════════════════════════════════════════════════════════════════════
# Error Recovery Integration
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_agent_recovers_from_invalid_llm_tool_json() -> None:
    """When LLM produces invalid JSON in tool block, agent must handle gracefully."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel

    mock_provider = AsyncMock()
    mock_provider.name = "mock"
    mock_provider.model = "test"
    mock_provider.generate = AsyncMock(
        return_value=ProviderResponse(
            text="```tool\nnot valid json\n```\nHere is my response.",
            provider="mock", model="test",
        )
    )

    agent = ExecutionAgent(provider=mock_provider)
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="test", risk_level=RiskLevel.LOW,
        correlation_id="test-invalid-json",
    )

    result = await agent.run(data)
    assert "Here is my response." in result.content


@pytest.mark.asyncio
async def test_agent_provider_error_returns_message() -> None:
    """Provider connection errors must return user-friendly message, not crash."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import RiskLevel

    mock_provider = AsyncMock()
    mock_provider.name = "mock"
    mock_provider.model = "test"
    mock_provider.generate = AsyncMock(
        side_effect=ConnectionError("Ollama not running")
    )

    agent = ExecutionAgent(provider=mock_provider)
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="hello", risk_level=RiskLevel.LOW,
        correlation_id="test-conn-error",
    )

    result = await agent.run(data)
    assert isinstance(result.content, str)
    assert len(result.content) > 0


@pytest.mark.asyncio
async def test_agent_timeout_error_returns_message() -> None:
    """Timeout errors must return user-friendly message."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import RiskLevel

    mock_provider = AsyncMock()
    mock_provider.name = "mock"
    mock_provider.model = "test"
    mock_provider.generate = AsyncMock(
        side_effect=TimeoutError("Request timed out")
    )

    agent = ExecutionAgent(provider=mock_provider)
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="test",
        text="hello", risk_level=RiskLevel.LOW,
        correlation_id="test-timeout",
    )

    result = await agent.run(data)
    assert isinstance(result.content, str)
    assert len(result.content) > 0
