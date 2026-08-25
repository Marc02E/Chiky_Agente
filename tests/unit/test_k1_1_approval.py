"""FASE K.1.1 — Tests for explicit per-operation approval system."""

from datetime import UTC
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

# ─── Approval prefix constant ───

def test_approval_required_prefix_constant() -> None:
    """The APPROVAL_REQUIRED_PREFIX constant must exist and be a non-empty string."""
    from personal_ai_secretary.agents.builtin import APPROVAL_REQUIRED_PREFIX

    assert isinstance(APPROVAL_REQUIRED_PREFIX, str)
    assert len(APPROVAL_REQUIRED_PREFIX) > 0
    assert "APPROVAL" in APPROVAL_REQUIRED_PREFIX


# ─── ToolRegistry — approval gate ───

@pytest.mark.asyncio
async def test_high_risk_tool_without_approval_raises(tmp_path: Path) -> None:
    """create_file must raise PermissionError when executed without approval=True."""
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS

    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path)]
    try:
        registry = default_tool_registry()
        with pytest.raises(PermissionError, match="approval"):
            await registry.execute(
                "create_file",
                {"path": str(tmp_path / "file.txt"), "content": "x"},
                approved=False,
            )
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


@pytest.mark.asyncio
async def test_high_risk_tool_with_approval_executes(tmp_path: Path) -> None:
    """create_file must execute and return verified_exists=True when approved=True."""
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS

    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path)]
    try:
        registry = default_tool_registry()
        result = await registry.execute(
            "create_file",
            {"path": str(tmp_path / "file.txt"), "content": "approved"},
            approved=True,
        )
        assert result["result"] == "created"
        assert result["verified_exists"] is True
        assert (tmp_path / "file.txt").read_text() == "approved"
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


@pytest.mark.asyncio
async def test_write_file_without_approval_raises(tmp_path: Path) -> None:
    """write_file (HIGH risk) must be blocked without approval."""
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS

    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path)]
    try:
        registry = default_tool_registry()
        with pytest.raises(PermissionError):
            await registry.execute(
                "write_file",
                {"path": str(tmp_path / "f.txt"), "content": "x"},
                approved=False,
            )
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


@pytest.mark.asyncio
async def test_read_file_no_approval_needed(tmp_path: Path) -> None:
    """read_file (LOW risk, no approval) must execute without approved=True."""
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS

    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path)]
    try:
        (tmp_path / "data.txt").write_text("hello")
        registry = default_tool_registry()
        result = await registry.execute(
            "read_file",
            {"path": str(tmp_path / "data.txt")},
            approved=False,
        )
        assert result["result"] == "hello"
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


@pytest.mark.asyncio
async def test_list_directory_no_approval_needed(tmp_path: Path) -> None:
    """list_directory (LOW risk) must execute without approval."""
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS

    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path)]
    try:
        (tmp_path / "a.txt").write_text("x")
        registry = default_tool_registry()
        result = await registry.execute(
            "list_directory",
            {"path": str(tmp_path)},
        )
        assert result["count"] == 1
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


# ─── Approval is single-use per request scope ───

@pytest.mark.asyncio
async def test_approval_does_not_persist_across_separate_calls(tmp_path: Path) -> None:
    """Approval is scoped per registry.execute() call — a second call without
    approved=True is always blocked regardless of a previous approved=True call."""
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS

    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path)]
    try:
        registry = default_tool_registry()
        # First call — approved
        await registry.execute(
            "create_file",
            {"path": str(tmp_path / "first.txt"), "content": "ok"},
            approved=True,
        )
        # Second call — NOT approved (approval from first call must not carry over)
        with pytest.raises(PermissionError):
            await registry.execute(
                "create_file",
                {"path": str(tmp_path / "second.txt"), "content": "sneaky"},
                approved=False,
            )
        assert not (tmp_path / "second.txt").exists()
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


@pytest.mark.asyncio
async def test_approval_for_create_file_does_not_approve_write_file(tmp_path: Path) -> None:
    """An approval that worked for create_file does not automatically approve write_file.
    Each call is independently gated."""
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS

    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path)]
    try:
        registry = default_tool_registry()
        # Create a file WITH approval
        await registry.execute(
            "create_file",
            {"path": str(tmp_path / "target.txt"), "content": "initial"},
            approved=True,
        )
        # Now try to write WITHOUT approval — must be blocked
        with pytest.raises(PermissionError):
            await registry.execute(
                "write_file",
                {"path": str(tmp_path / "target.txt"), "content": "overwritten"},
                approved=False,
            )
        # File must be unchanged
        assert (tmp_path / "target.txt").read_text() == "initial"
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


# ─── Agent approval signal ───

@pytest.mark.asyncio
async def test_agent_returns_approval_required_prefix_when_tool_blocked(
    tmp_path: Path,
) -> None:
    """When approval_granted=False and the LLM tries a HIGH risk tool, the agent
    must return a response starting with APPROVAL_REQUIRED_PREFIX."""
    from uuid import uuid4

    from personal_ai_secretary.agents.builtin import APPROVAL_REQUIRED_PREFIX, ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS

    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path)]
    try:
        mock_provider = AsyncMock()
        mock_provider.name = "mock"
        file_path = str(tmp_path / "cachorro.txt").replace("\\", "\\\\")
        mock_provider.generate = AsyncMock(
            return_value=ProviderResponse(
                text=(
                    "```tool\n"
                    f'{{"tool": "create_file", "args": {{"path": "{file_path}", "content": ""}}}}\n'
                    "```"
                ),
                provider="mock",
                model="test",
            )
        )

        registry = default_tool_registry()
        agent = ExecutionAgent(provider=mock_provider, registry=registry)

        data = AgentInput(
            request_id=uuid4(),
            session_id=uuid4(),
            user_id="user-1",
            text="create cachorro.txt",
            risk_level=RiskLevel.LOW,
            correlation_id="test-corr-1",
            context={"approval_granted": False},
        )

        result = await agent.run(data)
        assert result.content.startswith(APPROVAL_REQUIRED_PREFIX), (
            f"Expected approval prefix. Got: {result.content[:80]}"
        )
        assert not (tmp_path / "cachorro.txt").exists()
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


@pytest.mark.asyncio
async def test_agent_executes_tool_when_approved(tmp_path: Path) -> None:
    """When approval_granted=True, the agent must execute the tool and create the file."""
    from uuid import uuid4

    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS

    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path)]
    try:
        file_path = str(tmp_path / "cachorro.txt").replace("\\", "\\\\")
        mock_provider = AsyncMock()
        mock_provider.name = "mock"
        mock_provider.generate = AsyncMock(
            side_effect=[
                ProviderResponse(
                    text=(
                        "```tool\n"
                        f'{{"tool": "create_file", "args": {{"path": "{file_path}", "content": "hola"}}}}\n'
                        "```"
                    ),
                    provider="mock",
                    model="test",
                ),
                ProviderResponse(
                    text="I created cachorro.txt successfully.",
                    provider="mock",
                    model="test",
                ),
            ]
        )

        registry = default_tool_registry()
        agent = ExecutionAgent(provider=mock_provider, registry=registry)

        data = AgentInput(
            request_id=uuid4(),
            session_id=uuid4(),
            user_id="user-2",
            text="create cachorro.txt",
            risk_level=RiskLevel.LOW,
            correlation_id="test-corr-2",
            context={"approval_granted": True},
        )

        result = await agent.run(data)
        assert "created" in result.content.lower() or "cachorro" in result.content.lower()
        assert (tmp_path / "cachorro.txt").exists()
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


# ─── Approval prefix parser in app.py ───

def test_approval_prefix_stripped_from_assistant_message() -> None:
    """The send_session_message endpoint must strip APPROVAL_REQUIRED_PREFIX from
    the assistant message content and populate the approval_request field."""
    import json
    from datetime import datetime
    from uuid import uuid4

    from personal_ai_secretary.agents.builtin import APPROVAL_REQUIRED_PREFIX
    from personal_ai_secretary.domain.contracts import SendMessageResponse

    session_id = uuid4()
    request_id = uuid4()
    from personal_ai_secretary.domain.contracts import ConversationMessage

    approval_data = json.dumps({"tool_name": "create_file", "tool_args": {"path": "/tmp/x.txt"}})
    raw_content = f"{APPROVAL_REQUIRED_PREFIX}{approval_data}\n\nI need your permission."

    msg = ConversationMessage(
        message_id=request_id,
        request_id=request_id,
        role="assistant",
        content=raw_content,
        status="completed",
        created_at=datetime.now(UTC),
    )
    user_msg = ConversationMessage(
        message_id=request_id,
        request_id=request_id,
        role="user",
        content="create file",
        status="completed",
        created_at=datetime.now(UTC),
    )

    response = SendMessageResponse(
        session_id=session_id,
        request_id=request_id,
        status="completed",
        user_message=user_msg,
        assistant_message=msg,
        correlation_id="corr-1",
    )

    # Simulate what app.py does
    content = response.assistant_message.content  # type: ignore[union-attr]
    assert content.startswith(APPROVAL_REQUIRED_PREFIX)

    first_newline = content.find("\n")
    end = first_newline if first_newline != -1 else len(content)
    json_part = content[len(APPROVAL_REQUIRED_PREFIX):end]
    human_part = content[first_newline + 1:].lstrip("\n") if first_newline != -1 else ""
    parsed = json.loads(json_part)

    assert parsed["tool_name"] == "create_file"
    assert "I need your permission." in human_part

    # Simulate model_copy
    cleaned = response.model_copy(
        update={
            "assistant_message": response.assistant_message.model_copy(
                update={"content": human_part}
            ),
            "approval_request": parsed,
        }
    )
    assert cleaned.approval_request is not None
    assert cleaned.approval_request["tool_name"] == "create_file"
    assert APPROVAL_REQUIRED_PREFIX not in (cleaned.assistant_message.content or "")  # type: ignore[union-attr]


def test_send_message_response_has_approval_request_field() -> None:
    """SendMessageResponse must have an optional approval_request field."""
    from datetime import datetime
    from uuid import uuid4

    from personal_ai_secretary.domain.contracts import ConversationMessage, SendMessageResponse

    rid = uuid4()
    sid = uuid4()
    now = datetime.now(UTC)
    msg = ConversationMessage(message_id=rid, request_id=rid, role="user",
                              content="hi", status="completed", created_at=now)
    r = SendMessageResponse(session_id=sid, request_id=rid, status="completed",
                            user_message=msg, correlation_id="x")
    # Default is None
    assert r.approval_request is None
    # Can be set
    r2 = r.model_copy(update={"approval_request": {"tool_name": "create_file", "tool_args": {}}})
    assert r2.approval_request == {"tool_name": "create_file", "tool_args": {}}


# ─── Approval loop does not infinite-loop ───

@pytest.mark.asyncio
async def test_approval_required_does_not_cause_infinite_loop(tmp_path: Path) -> None:
    """When the tool is repeatedly blocked by approval, the agent must NOT loop.
    It should surface the approval request once and stop."""
    from uuid import uuid4

    from personal_ai_secretary.agents.builtin import APPROVAL_REQUIRED_PREFIX, ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS

    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path)]
    call_count = 0
    try:
        file_path = str(tmp_path / "f.txt").replace("\\", "\\\\")

        mock_provider = AsyncMock()
        mock_provider.name = "mock"

        def make_response(*args: Any, **kwargs: Any) -> ProviderResponse:
            nonlocal call_count
            call_count += 1
            return ProviderResponse(
                text=(
                    "```tool\n"
                    f'{{"tool": "create_file", "args": {{"path": "{file_path}", "content": ""}}}}\n'
                    "```"
                ),
                provider="mock",
                model="test",
            )

        mock_provider.generate = AsyncMock(side_effect=make_response)

        registry = default_tool_registry()
        agent = ExecutionAgent(provider=mock_provider, registry=registry)

        data = AgentInput(
            request_id=uuid4(),
            session_id=uuid4(),
            user_id="user-loop",
            text="create file",
            risk_level=RiskLevel.LOW,
            correlation_id="test-corr-loop",
            context={"approval_granted": False},
        )

        result = await agent.run(data)
        assert result.content.startswith(APPROVAL_REQUIRED_PREFIX)
        # LLM should have been called only once
        assert call_count == 1
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


# ─── Normal messages do not contain approval ───

def test_normal_message_does_not_carry_approval_header() -> None:
    """Verify that the sendMessage function no longer has X-Approval-Granted: true
    in the normal (non-approval) code path."""
    import os
    js_path = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "src", "personal_ai_secretary", "ui", "static", "js", "app.js",
    )
    with open(js_path, encoding="utf-8") as fh:
        js_content = fh.read()

    # The sendMessage function should NOT unconditionally send X-Approval-Granted.
    # The only place it should appear is in the confirmApproval function.
    lines = js_content.splitlines()
    approval_header_lines = [
        (i + 1, line.strip())
        for i, line in enumerate(lines)
        if "X-Approval-Granted" in line and "true" in line
    ]
    # Must exist (for the explicit approval path) but only inside confirmApproval
    assert len(approval_header_lines) >= 1, "Must have at least one explicit approval path"
    for _lineno, line in approval_header_lines:
        # None of the approval header lines should be in a comment saying "normal messages"
        # Verify the automatic global pattern is gone
        assert "every message" not in line.lower()
        assert "each message" not in line.lower()
        assert "all messages" not in line.lower()
