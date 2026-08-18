from uuid import uuid4

import pytest

from personal_ai_secretary.agents.builtin import ExecutionAgent, PlannerAgent
from personal_ai_secretary.agents.contracts import AgentInput
from personal_ai_secretary.domain.contracts import RiskLevel
from personal_ai_secretary.tools.builtin import default_tool_registry
from personal_ai_secretary.tools.registry import (
    ToolDefinition,
    ToolError,
    ToolRegistry,
    ToolRisk,
    parse_tool_call,
)


def test_parse_tool_call_returns_none_without_marker() -> None:
    assert parse_tool_call("calculate 2 plus 2") is None


def test_parse_tool_call_parses_invocation() -> None:
    call = parse_tool_call('@tool:calculator {"expression": "2+2"}')
    assert call is not None
    assert call.name == "calculator"
    assert call.arguments == {"expression": "2+2"}


def test_parse_tool_call_allows_empty_arguments() -> None:
    call = parse_tool_call("@tool:list_tools")
    assert call is not None
    assert call.name == "list_tools"
    assert call.arguments == {}


def test_parse_tool_call_rejects_missing_name() -> None:
    with pytest.raises(ToolError):
        parse_tool_call("@tool:")


def test_parse_tool_call_rejects_malformed_name() -> None:
    with pytest.raises(ToolError):
        parse_tool_call("@tool:bad-name {}")


def test_parse_tool_call_rejects_invalid_json() -> None:
    with pytest.raises(ToolError):
        parse_tool_call("@tool:calculator not-json")


def test_parse_tool_call_rejects_non_object_json() -> None:
    with pytest.raises(ToolError):
        parse_tool_call("@tool:calculator [1, 2]")


def test_parse_tool_call_ignores_leading_whitespace() -> None:
    call = parse_tool_call('  @tool:calculator {"expression": "1+1"}')
    assert call is not None
    assert call.name == "calculator"


async def _echo(arguments: dict[str, object]) -> dict[str, object]:
    return {"echoed": arguments}


@pytest.mark.asyncio
async def test_registry_validates_argument_types() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "echo",
            ToolRisk.LOW,
            False,
            _echo,
            argument_schema={"value": "string", "count": "integer"},
        )
    )

    with pytest.raises(ToolError, match="Missing argument"):
        await registry.execute("echo", {"value": "x"})
    with pytest.raises(ToolError, match="Unknown argument"):
        await registry.execute("echo", {"value": "x", "count": 1, "extra": True})
    with pytest.raises(ToolError, match="must be"):
        await registry.execute("echo", {"value": "x", "count": "1"})
    assert await registry.execute("echo", {"value": "x", "count": 1}) == {
        "echoed": {"value": "x", "count": 1}
    }


@pytest.mark.asyncio
async def test_registry_rejects_unknown_tool() -> None:
    registry = ToolRegistry()
    with pytest.raises(KeyError):
        await registry.execute("nope", {})


def test_registry_forbids_critical_tools() -> None:
    registry = ToolRegistry()
    with pytest.raises(ValueError):
        registry.register(ToolDefinition("bad", ToolRisk.CRITICAL, False, _echo))


@pytest.mark.asyncio
async def test_registry_requires_approval_for_sensitive_tools() -> None:
    async def send(_: dict[str, object]) -> dict[str, object]:
        return {"sent": True}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition("send_email", ToolRisk.HIGH, True, send, argument_schema={"to": "string"})
    )

    with pytest.raises(PermissionError):
        await registry.execute("send_email", {"to": "boss@example.com"})
    assert await registry.execute(
        "send_email", {"to": "boss@example.com"}, approved=True
    ) == {"sent": True}


def test_default_registry_has_safe_tools() -> None:
    registry = default_tool_registry()
    assert registry.names() == ["calculator", "list_tools"]
    assert all(
        not registry.get(name).requires_explicit_approval  # type: ignore[union-attr]
        for name in registry.names()
    )


@pytest.mark.asyncio
async def test_calculator_solves_arithmetic() -> None:
    registry = default_tool_registry()
    result = await registry.execute("calculator", {"expression": "2 + 3 * 4"})
    assert result == {"result": 14}


@pytest.mark.asyncio
async def test_calculator_rejects_division_by_zero() -> None:
    registry = default_tool_registry()
    result = await registry.execute("calculator", {"expression": "1 / 0"})
    assert "error" in result


@pytest.mark.asyncio
async def test_calculator_rejects_unsupported_syntax() -> None:
    registry = default_tool_registry()
    result = await registry.execute("calculator", {"expression": "__import__('os')"})
    assert "error" in result


@pytest.mark.asyncio
async def test_list_tools_reports_registry_names() -> None:
    registry = default_tool_registry()
    result = await registry.execute("list_tools", {})
    assert result == {"tools": ["calculator", "list_tools"]}


@pytest.mark.asyncio
async def test_registry_validates_float_type() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "measure",
            ToolRisk.LOW,
            False,
            _echo,
            argument_schema={"value": "float"},
        )
    )
    assert await registry.execute("measure", {"value": 3.14}) == {
        "echoed": {"value": 3.14}
    }
    assert await registry.execute("measure", {"value": 42}) == {
        "echoed": {"value": 42}
    }
    with pytest.raises(ToolError, match="must be"):
        await registry.execute("measure", {"value": "not-a-float"})


@pytest.mark.asyncio
async def test_registry_validates_boolean_type() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "flag",
            ToolRisk.LOW,
            False,
            _echo,
            argument_schema={"enabled": "boolean"},
        )
    )
    assert await registry.execute("flag", {"enabled": True}) == {
        "echoed": {"enabled": True}
    }
    assert await registry.execute("flag", {"enabled": False}) == {
        "echoed": {"enabled": False}
    }
    with pytest.raises(ToolError, match="must be"):
        await registry.execute("flag", {"enabled": "yes"})


@pytest.mark.asyncio
async def test_registry_allows_unknown_type() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "flex",
            ToolRisk.LOW,
            False,
            _echo,
            argument_schema={"data": "list"},
        )
    )
    assert await registry.execute("flex", {"data": [1, 2, 3]}) == {
        "echoed": {"data": [1, 2, 3]}
    }


def _tool_input(text: str, **context: object) -> AgentInput:
    return AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="user-1",
        text=text,
        correlation_id="corr-tool",
        context=context,
    )


@pytest.mark.asyncio
async def test_execution_agent_runs_tool() -> None:
    registry = default_tool_registry()
    artifact = await ExecutionAgent(registry=registry).run(
        _tool_input('@tool:calculator {"expression": "2+2"}')
    )
    assert not artifact.blocked
    assert artifact.content == "Tool 'calculator' returned: 4"
    assert artifact.metadata["tools"][0]["user_id"] == "user-1"
    assert artifact.metadata["tools"][0]["correlation_id"] == "corr-tool"
    assert artifact.metadata["tools"][0]["approved"] is False


@pytest.mark.asyncio
async def test_execution_agent_reports_unknown_tool() -> None:
    registry = default_tool_registry()
    artifact = await ExecutionAgent(registry=registry).run(_tool_input("@tool:ghost {}"))
    assert not artifact.blocked
    assert artifact.content == "Tool 'ghost' is not available."


@pytest.mark.asyncio
async def test_execution_agent_rejects_invalid_arguments() -> None:
    registry = default_tool_registry()
    artifact = await ExecutionAgent(registry=registry).run(_tool_input("@tool:calculator {}"))
    assert not artifact.blocked
    assert "rejected arguments" in artifact.content
    assert "Missing argument" in artifact.content


@pytest.mark.asyncio
async def test_execution_agent_reports_malformed_call() -> None:
    registry = default_tool_registry()
    artifact = await ExecutionAgent(registry=registry).run(_tool_input("@tool:calculator not-json"))
    assert not artifact.blocked
    assert artifact.content.startswith("Invalid tool call:")


@pytest.mark.asyncio
async def test_execution_agent_blocks_unapproved_sensitive_tool() -> None:
    async def send(_: dict[str, object]) -> dict[str, object]:
        return {"sent": True}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition("send_email", ToolRisk.HIGH, True, send, argument_schema={"to": "string"})
    )

    artifact = await ExecutionAgent(registry=registry).run(
        _tool_input('@tool:send_email {"to": "boss@example.com"}')
    )
    assert artifact.blocked
    assert "approval required" in artifact.content
    assert artifact.metadata["tools"][0]["approved"] is False


@pytest.mark.asyncio
async def test_execution_agent_executes_approved_sensitive_tool() -> None:
    async def send(_: dict[str, object]) -> dict[str, object]:
        return {"sent": True}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition("send_email", ToolRisk.HIGH, True, send, argument_schema={"to": "string"})
    )

    artifact = await ExecutionAgent(registry=registry).run(
        _tool_input('@tool:send_email {"to": "boss@example.com"}', approval_granted=True)
    )
    assert not artifact.blocked
    assert artifact.content == 'Tool \'send_email\' returned: {"sent": true}'
    assert artifact.metadata["tools"][0]["approved"] is True


@pytest.mark.asyncio
async def test_execution_agent_propagates_handler_failure() -> None:
    async def explode(_: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("handler crashed")

    registry = ToolRegistry()
    registry.register(ToolDefinition("boom", ToolRisk.LOW, False, explode, argument_schema={}))

    with pytest.raises(RuntimeError):
        await ExecutionAgent(registry=registry).run(_tool_input("@tool:boom {}"))


@pytest.mark.asyncio
async def test_planner_flags_approval_required_tool() -> None:
    async def send(_: dict[str, object]) -> dict[str, object]:
        return {"sent": True}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition("send_email", ToolRisk.HIGH, True, send, argument_schema={"to": "string"})
    )

    artifact = await PlannerAgent(registry).run(
        _tool_input('@tool:send_email {"to": "boss@example.com"}')
    )
    assert artifact.metadata["tool"] == "send_email"
    assert artifact.metadata["requires_approval"] is True


@pytest.mark.asyncio
async def test_planner_does_not_flag_low_risk_tool() -> None:
    registry = default_tool_registry()
    artifact = await PlannerAgent(registry).run(
        _tool_input('@tool:calculator {"expression": "1+1"}')
    )
    assert artifact.metadata["tool"] == "calculator"
    assert artifact.metadata["requires_approval"] is False


@pytest.mark.asyncio
async def test_governed_workflow_executes_tool() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    workflow = GovernedWorkflow(tools=default_tool_registry())
    result = await workflow.run(
        uuid4(), uuid4(), "user-1", '@tool:calculator {"expression": "6*7"}', "corr-wf-tool"
    )
    assert result.status == "completed"
    assert result.response == "Tool 'calculator' returned: 42"
    assert [a.role.value for a in result.artifacts] == [
        "planner", "research", "execution", "reviewer", "compliance"
    ]


@pytest.mark.asyncio
async def test_governed_workflow_blocks_unapproved_sensitive_tool() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    async def send(_: dict[str, object]) -> dict[str, object]:
        return {"sent": True}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition("send_email", ToolRisk.HIGH, True, send, argument_schema={"to": "string"})
    )

    workflow = GovernedWorkflow(tools=registry)
    result = await workflow.run(
        uuid4(), uuid4(), "user-1", '@tool:send_email {"to": "boss@example.com"}', "corr-wf-block"
    )
    assert result.status == "blocked"
    assert result.response is None
    assert "approval required" in (result.blocked_reason or "")


@pytest.mark.asyncio
async def test_governed_workflow_executes_sensitive_tool_with_approval() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    async def send(_: dict[str, object]) -> dict[str, object]:
        return {"sent": True}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition("send_email", ToolRisk.HIGH, True, send, argument_schema={"to": "string"})
    )

    workflow = GovernedWorkflow(tools=registry)
    result = await workflow.run(
        uuid4(),
        uuid4(),
        "user-1",
        '@tool:send_email {"to": "boss@example.com"}',
        "corr-wf-ok",
        context={"approval_granted": True},
    )
    assert result.status == "completed"
    assert result.response == 'Tool \'send_email\' returned: {"sent": true}'


@pytest.mark.asyncio
async def test_governed_workflow_reports_unknown_tool() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    workflow = GovernedWorkflow(tools=default_tool_registry())
    result = await workflow.run(uuid4(), uuid4(), "user-1", "@tool:ghost {}", "corr-wf-ghost")
    assert result.status == "completed"
    assert result.response == "Tool 'ghost' is not available."


def test_high_risk_input_still_blocks_via_risk_classifier() -> None:
    from personal_ai_secretary.application.risk import classify_risk

    assert classify_risk("@tool:calculator {\"expression\": \"1+1\"}") is RiskLevel.LOW