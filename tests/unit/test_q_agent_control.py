"""FASE Q — deterministic agent execution & task control (Scenarios A-H).

Agent-level tests with mocked providers verifying that the backend
controls task execution: state transitions, progress coaching, bounded
failure recovery, honest completion, and latency classification.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from personal_ai_secretary.agents.builtin import APPROVAL_REQUIRED_PREFIX, ExecutionAgent
from personal_ai_secretary.agents.contracts import AgentInput, RiskLevel
from personal_ai_secretary.domain.contracts import ProviderResponse


def _make_input(text: str, **context: Any) -> AgentInput:
    return AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test-user",
        text=text,
        correlation_id="test-corr",
        risk_level=RiskLevel.LOW,
        context=dict(context),
    )


def _tool_call_text(tool_name: str, args: dict[str, Any]) -> str:
    return f'```tool\n{{"tool": "{tool_name}", "args": {json.dumps(args)}}}\n```'


def _mock_provider(responses: list[str]) -> MagicMock:
    provider = MagicMock()
    provider.name = "mock"
    provider.model = "test-model"

    async def generate(request: Any) -> ProviderResponse:
        text = responses.pop(0) if responses else "Done."
        return ProviderResponse(text=text, provider="mock")

    provider.generate = AsyncMock(side_effect=generate)
    return provider


def _mock_registry(
    *,
    execute_result: dict[str, Any] | None = None,
    approval_tools: set[str] | None = None,
) -> MagicMock:
    """Registry mock. ``execute_result`` overrides all successful results."""
    registry = MagicMock()
    approval_tools = approval_tools or set()

    async def execute(
        tool_name: str, args: dict[str, Any], approved: bool = False,
    ) -> dict[str, Any]:
        if tool_name in approval_tools and not approved:
            return {"requires_approval": True}
        if execute_result is not None:
            return dict(execute_result)
        if tool_name == "execute_command":
            return {"exit_code": 0, "stdout": "OK", "stderr": ""}
        if tool_name == "create_file":
            return {
                "result": "created", "path": args.get("path", "/tmp/x.py"),
                "verified_exists": True,
            }
        if tool_name == "modify_file":
            return {"result": "modified", "path": args.get("path", "/tmp/x.py")}
        if tool_name == "read_file":
            return {"result": "content...", "path": args.get("path", "")}
        return {"result": "ok"}

    registry.execute = AsyncMock(side_effect=execute)

    tools = {
        name: MagicMock(name=name, requires_explicit_approval=name in approval_tools)
        for name in (
            "execute_command", "create_file", "modify_file",
            "read_file", "list_directory", "search_files", "file_exists",
        )
    }
    registry.get = lambda name: tools.get(name)
    registry.list_definitions = lambda: list(tools.values())
    return registry


# ---------------------------------------------------------------------------
# Scenario A — create-file task completes with evidence (COMPLETED state)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_a_create_completes_with_evidence(tmp_path: Any) -> None:
    target = tmp_path / "notes.txt"
    registry = MagicMock()

    async def execute(
        tool_name: str, args: dict[str, Any], approved: bool = False,
    ) -> dict[str, Any]:
        if tool_name == "create_file":
            target.write_text("hello")
            return {
                "result": "created", "path": str(target),
                "verified_exists": True,
            }
        if tool_name == "file_exists":
            return {"result": target.exists(), "path": str(target)}
        return {"result": "ok"}

    registry.execute = AsyncMock(side_effect=execute)
    tools = {
        name: MagicMock(name=name, requires_explicit_approval=False)
        for name in ("create_file", "file_exists")
    }
    registry.get = lambda name: tools.get(name)
    registry.list_definitions = lambda: list(tools.values())

    provider = _mock_provider([
        _tool_call_text("create_file", {"path": str(target)}),
        _tool_call_text("file_exists", {"path": str(target)}),
        "Created notes.txt successfully.",
    ])
    agent = ExecutionAgent(provider=provider, registry=registry)
    result = await agent.run(_make_input("Create a file named notes.txt"))
    assert "notes.txt" in result.content
    m = agent.last_metrics
    assert m.final_task_state == "completed"
    assert any(t.endswith(">modifying") for t in m.state_transitions)
    assert any(t.endswith(">verifying") for t in m.state_transitions)
    assert "reading>completed" not in m.state_transitions
    assert m.unproductive_loop_detections == 0
    assert m.backend_directives == 0


# ---------------------------------------------------------------------------
# Scenario B — read -> modify flow on a modification task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_b_read_then_modify() -> None:
    provider = _mock_provider([
        _tool_call_text("read_file", {"path": "/tmp/hello.py"}),
        _tool_call_text("modify_file", {"path": "/tmp/hello.py"}),
        "Updated hello.py.",
    ])
    agent = ExecutionAgent(provider=provider, registry=_mock_registry())
    await agent.run(_make_input("Update greeting in hello.py to say hi"))
    m = agent.last_metrics
    assert m.final_task_state == "completed"
    # The machine must show reading BEFORE modifying (no skipping backwards).
    assert "reading>modifying" in m.state_transitions


# ---------------------------------------------------------------------------
# Scenario C — debug task stuck reading: coached once, then stopped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_c_debug_loop_coached_then_stopped() -> None:
    read_call = _tool_call_text("read_file", {"path": "/tmp/app.py"})
    provider = _mock_provider([read_call, read_call, read_call])
    agent = ExecutionAgent(provider=provider, registry=_mock_registry())
    result = await agent.run(_make_input("Fix the bug in app.py"))
    content = result.content.lower()
    assert "stopped" in content or "stopping" in content
    m = agent.last_metrics
    assert m.backend_directives == 1
    assert m.unproductive_loop_detections >= 1
    assert m.final_task_state == "failed"


# ---------------------------------------------------------------------------
# Scenario D — CRUD/project creation completes via batch creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_d_project_create_completes() -> None:
    provider = _mock_provider([
        _tool_call_text("create_project", {"path": "/tmp/newproj"}),
        "Project skeleton created.",
    ])
    agent = ExecutionAgent(provider=provider, registry=_mock_registry())
    await agent.run(_make_input("Create a new project newproj"))
    m = agent.last_metrics
    assert m.final_task_state == "completed"
    assert m.unproductive_loop_detections == 0


# ---------------------------------------------------------------------------
# Scenario E — analysis task terminates cleanly without mutation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_e_analysis_terminates_without_mutation() -> None:
    provider = _mock_provider([
        _tool_call_text("read_file", {"path": "/tmp/mod/main.py"}),
        _tool_call_text("analyze_project", {"path": "/tmp/mod"}),
        "The project has 2 modules and one entry point.",
    ])
    agent = ExecutionAgent(provider=provider, registry=_mock_registry())
    result = await agent.run(
        _make_input("Analyze the project structure of /tmp/mod")
    )
    assert "modules" in result.content.lower()
    m = agent.last_metrics
    assert m.final_task_state == "completed"
    assert m.backend_directives == 0
    assert m.latency_classification in {
        "llm_bound", "tool_bound", "command_bound", "balanced",
    }


# ---------------------------------------------------------------------------
# Scenario F — model failure classified; friendly error + fallback info
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_f_model_failure_classified() -> None:
    provider = MagicMock()
    provider.name = "mock"
    provider.model = "test-model"

    async def generate(request: Any) -> ProviderResponse:
        raise ConnectionError("Provider unavailable")

    provider.generate = AsyncMock(side_effect=generate)
    agent = ExecutionAgent(provider=provider, registry=_mock_registry())
    result = await agent.run(_make_input("Create a file named x.txt"))
    content = result.content.lower()
    assert "error" in content or "unavailable" in content
    m = agent.last_metrics
    assert m.model_failures == 1
    assert m.final_task_state == "failed"
    assert m.state_transitions[-1].endswith(">failed")


# ---------------------------------------------------------------------------
# Scenario G — repeated tool failures stop after MAX_CONSECUTIVE_TOOL_FAILURES
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_g_tool_failures_bounded_at_three() -> None:
    from personal_ai_secretary.agents.task_state import MAX_CONSECUTIVE_TOOL_FAILURES

    provider = _mock_provider([
        _tool_call_text("modify_file", {"path": "/tmp/config.py"}),
        _tool_call_text("modify_file", {"path": "/tmp/config.py.bak"}),
        _tool_call_text("modify_file", {"path": "/tmp/config.py.new"}),
    ])
    registry = _mock_registry(execute_result={"error": "permission denied"})
    agent = ExecutionAgent(provider=provider, registry=registry)
    result = await agent.run(_make_input("Update settings in config.py"))
    content = result.content.lower()
    assert f"stopping after {MAX_CONSECUTIVE_TOOL_FAILURES}" in content
    m = agent.last_metrics
    assert m.consecutive_tool_failures == MAX_CONSECUTIVE_TOOL_FAILURES
    assert m.final_task_state == "failed"


# ---------------------------------------------------------------------------
# Scenario H — false success without evidence is blocked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_h_false_success_blocked() -> None:
    provider = _mock_provider([
        "I have created notes.txt successfully with your shopping list.",
    ])
    agent = ExecutionAgent(provider=provider, registry=_mock_registry())
    result = await agent.run(
        _make_input("Create a file notes.txt with my shopping list")
    )
    content = result.content.lower()
    # Honest-completion guard: no mutation happened, so the task is NOT done.
    assert "not completed" in content
    m = agent.last_metrics
    assert m.final_task_state == "failed"


# ---------------------------------------------------------------------------
# Scenario I — approval-required tool surfaces request and blocks task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_i_approval_required_blocks() -> None:
    provider = _mock_provider([
        _tool_call_text("execute_command", {"command": "rm -rf /tmp/old"}),
    ])
    registry = _mock_registry(approval_tools={"execute_command"})
    agent = ExecutionAgent(provider=provider, registry=registry)
    result = await agent.run(
        _make_input("Run rm -rf /tmp/old now", approval_granted=False)
    )
    assert result.content.startswith(APPROVAL_REQUIRED_PREFIX)
    m = agent.last_metrics
    assert m.final_task_state == "blocked"
