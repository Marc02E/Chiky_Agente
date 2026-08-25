"""Integration tests for Phase E: Code Generation & Project Creation.

Tests the full flow from agent execution through tool calls to file creation.
"""

from uuid import uuid4

import pytest

from personal_ai_secretary.agents.builtin import ExecutionAgent
from personal_ai_secretary.agents.contracts import AgentInput
from personal_ai_secretary.domain.contracts import RiskLevel
from personal_ai_secretary.tools.builtin import default_tool_registry


class MockProvider:
    """Mock LLM provider for testing agentic loops."""

    name = "mock"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.call_count = 0

    async def health(self):
        from personal_ai_secretary.domain.contracts import ProviderInfo
        return ProviderInfo(
            name=self.name, mode="test", available=True,
            is_ai=False, detail="mock provider",
        )

    async def generate(self, request):
        from personal_ai_secretary.domain.contracts import ProviderResponse
        if self.call_count < len(self.responses):
            text = self.responses[self.call_count]
        else:
            text = "Done! Project created successfully."
        self.call_count += 1
        return ProviderResponse(text=text, provider=self.name)


def _tool_call(tool: str, args: dict) -> str:
    """Helper to build a tool call string."""
    import json
    return f"```tool\n{json.dumps({'tool': tool, 'args': args})}\n```"


@pytest.mark.asyncio
async def test_agent_creates_single_file(tmp_path) -> None:
    """Test agent creates a single file via tool call."""
    responses = [
        _tool_call("create_file", {"path": str(tmp_path / "hello.txt"), "content": "Hello World!"}),
        "File created successfully!",
    ]
    provider = MockProvider(responses)
    registry = default_tool_registry()
    agent = ExecutionAgent(provider=provider, registry=registry)

    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="Create a file called hello.txt with Hello World!",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
        context={"approval_granted": True},
    )

    await agent.run(data)
    assert provider.call_count >= 1
    assert (tmp_path / "hello.txt").exists()
    assert (tmp_path / "hello.txt").read_text() == "Hello World!"


@pytest.mark.asyncio
async def test_agent_creates_project_with_multiple_files(tmp_path) -> None:
    """Test agent creates multiple files in sequence."""
    project_dir = str(tmp_path / "my_project")
    responses = [
        _tool_call("create_directory", {"path": project_dir}),
        _tool_call("create_file", {"path": f"{project_dir}/main.py", "content": "print(1)"}),
        _tool_call("create_file", {"path": f"{project_dir}/README.md", "content": "# My Project"}),
        "Project created with 2 files!",
    ]
    provider = MockProvider(responses)
    registry = default_tool_registry()
    agent = ExecutionAgent(provider=provider, registry=registry)

    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="Create a project called my_project",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
        context={"approval_granted": True},
    )

    await agent.run(data)
    assert (tmp_path / "my_project" / "main.py").exists()
    assert (tmp_path / "my_project" / "README.md").exists()


@pytest.mark.asyncio
async def test_agent_handles_tool_error_gracefully(tmp_path) -> None:
    """Test agent handles tool errors and continues."""
    responses = [
        _tool_call("read_file", {"path": str(tmp_path / "nonexistent.txt")}),
        _tool_call("create_file", {"path": str(tmp_path / "new_file.txt"), "content": "created"}),
        "File created after error!",
    ]
    provider = MockProvider(responses)
    registry = default_tool_registry()
    agent = ExecutionAgent(provider=provider, registry=registry)

    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="Read and then create a file",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
        context={"approval_granted": True},
    )

    await agent.run(data)
    assert provider.call_count >= 2


@pytest.mark.asyncio
async def test_agent_max_rounds_limits_execution() -> None:
    """Test that MAX_TOOL_ROUNDS limits the agentic loop."""
    responses = []
    for i in range(20):
        responses.append(_tool_call("calculator", {"expression": f"{i}+1"}))
    provider = MockProvider(responses)
    registry = default_tool_registry()
    agent = ExecutionAgent(provider=provider, registry=registry)

    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="Calculate many things",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
    )

    await agent.run(data)
    assert provider.call_count <= ExecutionAgent.MAX_TOOL_ROUNDS + 1


@pytest.mark.asyncio
async def test_agent_no_tool_call_ends_loop() -> None:
    """Test that LLM response without tool call ends the loop."""
    responses = [
        "I'll help you with that. Let me think about it...",
        "Here's the solution: just run the code.",
    ]
    provider = MockProvider(responses)
    registry = default_tool_registry()
    agent = ExecutionAgent(provider=provider, registry=registry)

    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="How do I sort a list in Python?",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
    )

    await agent.run(data)
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_agent_multiple_tool_calls_per_round(tmp_path) -> None:
    """Test agent handles multiple tool calls in a single response."""
    responses = [
        (
            _tool_call("create_directory", {"path": str(tmp_path / "proj")})
            + "\n"
            + _tool_call("create_file", {"path": str(tmp_path / "proj" / "a.py"), "content": "a"})
            + "\n"
            + _tool_call("create_file", {"path": str(tmp_path / "proj" / "b.py"), "content": "b"})
        ),
        "Created project with 2 files!",
    ]
    provider = MockProvider(responses)
    registry = default_tool_registry()
    agent = ExecutionAgent(provider=provider, registry=registry)

    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="Create a project",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
        context={"approval_granted": True},
    )

    await agent.run(data)
    assert (tmp_path / "proj" / "a.py").exists()
    assert (tmp_path / "proj" / "b.py").exists()


@pytest.mark.asyncio
async def test_agent_approval_blocks_tool(tmp_path) -> None:
    """Test that approval requirement blocks tool execution."""
    responses = [
        _tool_call("create_file", {"path": str(tmp_path / "blocked.txt"), "content": "x"}),
        "File created!",
    ]
    provider = MockProvider(responses)
    registry = default_tool_registry()
    agent = ExecutionAgent(provider=provider, registry=registry)

    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="Create a file",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
    )

    await agent.run(data)
    assert not (tmp_path / "blocked.txt").exists()


@pytest.mark.asyncio
async def test_agent_read_before_modify(tmp_path) -> None:
    """Test agent reads file before modifying it."""
    (tmp_path / "existing.py").write_text("def old(): pass")

    responses = [
        _tool_call("read_file", {"path": str(tmp_path / "existing.py")}),
        _tool_call("write_file", {"path": str(tmp_path / "existing.py"), "content": "def new(): pass"}),
        "File modified!",
    ]
    provider = MockProvider(responses)
    registry = default_tool_registry()
    agent = ExecutionAgent(provider=provider, registry=registry)

    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="Modify the existing.py file",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
        context={"approval_granted": True},
    )

    await agent.run(data)
    assert (tmp_path / "existing.py").read_text() == "def new(): pass"
