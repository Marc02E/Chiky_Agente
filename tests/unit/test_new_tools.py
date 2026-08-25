"""Tests for new Chiky agente tools: datetime, path helper, filesystem, system prompt."""

from pathlib import Path

import pytest

# ─── DateTime Tools ───


@pytest.mark.asyncio
async def test_datetime_now_returns_current_time() -> None:
    from personal_ai_secretary.tools.datetime_tool import _now

    result = await _now({})
    assert "result" in result
    assert "date" in result
    assert "time" in result
    assert "day_of_week" in result
    assert len(result["date"]) == 10  # YYYY-MM-DD
    assert len(result["time"]) == 8  # HH:MM:SS


@pytest.mark.asyncio
async def test_format_date_valid() -> None:
    from personal_ai_secretary.tools.datetime_tool import _format_date

    result = await _format_date({
        "iso_datetime": "2025-03-15T14:30:00",
        "format": "%B %d, %Y",
    })
    assert result == {"result": "March 15, 2025"}


@pytest.mark.asyncio
async def test_format_date_invalid() -> None:
    from personal_ai_secretary.tools.datetime_tool import _format_date

    result = await _format_date({
        "iso_datetime": "not-a-date",
        "format": "%Y-%m-%d",
    })
    assert "error" in result


# ─── Path Helper Tools ───


@pytest.mark.asyncio
async def test_get_directory_desktop() -> None:
    from personal_ai_secretary.tools.path_helper import _get_directory

    result = await _get_directory({"name": "desktop"})
    assert "result" in result
    assert Path(result["result"]).name == "Desktop"


@pytest.mark.asyncio
async def test_get_directory_unknown() -> None:
    from personal_ai_secretary.tools.path_helper import _get_directory

    result = await _get_directory({"name": "nonexistent_dir_xyz"})
    assert "error" in result


# ─── Filesystem Tools ───


@pytest.mark.asyncio
async def test_create_and_read_file(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.filesystem import (
        _create_file,
        _read_file,
    )

    file_path = str(tmp_path / "test.txt")
    create_result = await _create_file({
        "path": file_path,
        "content": "Hello Chiky!",
    })
    assert create_result["result"] == "created"
    assert create_result["name"] == "test.txt"
    assert create_result["size"] == 12

    read_result = await _read_file({"path": file_path})
    assert read_result["result"] == "Hello Chiky!"
    assert read_result["name"] == "test.txt"


@pytest.mark.asyncio
async def test_read_file_not_found(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.filesystem import _read_file

    result = await _read_file({"path": str(tmp_path / "nonexistent.txt")})
    assert "error" in result
    assert "not found" in result["error"].lower()


@pytest.mark.asyncio
async def test_read_file_not_a_file(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.filesystem import _read_file

    result = await _read_file({"path": str(tmp_path)})
    assert "error" in result
    assert "not a file" in result["error"].lower()


@pytest.mark.asyncio
async def test_write_file_creates_new(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.filesystem import _write_file

    file_path = str(tmp_path / "new.txt")
    result = await _write_file({"path": file_path, "content": "content"})
    assert result["result"] == "created"
    assert Path(file_path).read_text() == "content"


@pytest.mark.asyncio
async def test_write_file_updates_existing(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.filesystem import _write_file

    file_path = str(tmp_path / "existing.txt")
    Path(file_path).write_text("old")
    result = await _write_file({"path": file_path, "content": "new"})
    assert result["result"] == "updated"
    assert Path(file_path).read_text() == "new"


@pytest.mark.asyncio
async def test_list_directory(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.filesystem import _list_directory

    (tmp_path / "file.txt").write_text("x")
    (tmp_path / "subdir").mkdir()
    result = await _list_directory({"path": str(tmp_path)})
    assert result["count"] == 2
    names = [e["name"] for e in result["result"]]
    assert "file.txt" in names
    assert "subdir" in names


@pytest.mark.asyncio
async def test_list_directory_not_found(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.filesystem import _list_directory

    result = await _list_directory({"path": str(tmp_path / "nope")})
    assert "error" in result


@pytest.mark.asyncio
async def test_list_directory_not_a_dir(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.filesystem import _list_directory

    file_path = str(tmp_path / "file.txt")
    Path(file_path).write_text("x")
    result = await _list_directory({"path": file_path})
    assert "error" in result


@pytest.mark.asyncio
async def test_create_directory(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.filesystem import _create_directory

    dir_path = str(tmp_path / "new_dir" / "nested")
    result = await _create_directory({"path": dir_path})
    assert result["result"] == "created"
    assert Path(dir_path).is_dir()


@pytest.mark.asyncio
async def test_file_exists(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.filesystem import _file_exists

    file_path = str(tmp_path / "exists.txt")
    Path(file_path).write_text("x")
    result = await _file_exists({"path": file_path})
    assert result["result"] is True

    result2 = await _file_exists({"path": str(tmp_path / "nope.txt")})
    assert result2["result"] is False


@pytest.mark.asyncio
async def test_file_exists_invalid_path() -> None:
    from personal_ai_secretary.tools.filesystem import _file_exists

    result = await _file_exists({"path": ""})
    assert "error" in result


@pytest.mark.asyncio
async def test_create_file_empty_path() -> None:
    from personal_ai_secretary.tools.filesystem import _create_file

    result = await _create_file({"path": "", "content": "x"})
    assert "error" in result


@pytest.mark.asyncio
async def test_read_file_empty_path() -> None:
    from personal_ai_secretary.tools.filesystem import _read_file

    result = await _read_file({"path": ""})
    assert "error" in result


@pytest.mark.asyncio
async def test_write_file_empty_path() -> None:
    from personal_ai_secretary.tools.filesystem import _write_file

    result = await _write_file({"path": "", "content": "x"})
    assert "error" in result


@pytest.mark.asyncio
async def test_list_directory_empty_path() -> None:
    from personal_ai_secretary.tools.filesystem import _list_directory

    result = await _list_directory({"path": ""})
    assert "error" in result


@pytest.mark.asyncio
async def test_create_directory_empty_path() -> None:
    from personal_ai_secretary.tools.filesystem import _create_directory

    result = await _create_directory({"path": ""})
    assert "error" in result


@pytest.mark.asyncio
async def test_path_traversal_blocked(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.filesystem import (
        DEFAULT_ALLOWED_ROOTS,
        _create_file,
    )
    from personal_ai_secretary.tools.registry import ToolError

    # Restrict allowed roots to only tmp_path so .. escapes are caught
    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path)]
    try:
        with pytest.raises(ToolError, match="outside allowed"):
            await _create_file({
                "path": str(tmp_path / ".." / "evil.txt"),
                "content": "bad",
            })
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


@pytest.mark.asyncio
async def test_sibling_directory_bypass_blocked(tmp_path: Path) -> None:
    """Sibling directory names that share a prefix with the allowed root must be blocked.

    Without the os.sep suffix guard, 'allowed/sibling_evil/file' would pass the
    startswith('allowed/sibling') check and grant unauthorised access.
    """
    from personal_ai_secretary.tools.filesystem import _validate_path
    from personal_ai_secretary.tools.registry import ToolError

    allowed = tmp_path / "safe"
    sibling = tmp_path / "safe_evil"
    allowed.mkdir()
    sibling.mkdir()

    sibling_file = sibling / "secret.txt"
    sibling_file.write_text("evil content")

    # The sibling path should be rejected even though its string starts with allowed
    with pytest.raises(ToolError, match="outside allowed"):
        _validate_path(str(sibling_file), allowed_roots=[str(allowed)])


# ─── System Prompt ───


def test_build_system_prompt_basic() -> None:
    from personal_ai_secretary.tools.prompt import build_system_prompt

    prompt = build_system_prompt()
    assert "Chiky" in prompt
    assert "AI secretary" in prompt


def test_build_system_prompt_with_tools() -> None:
    from personal_ai_secretary.tools.prompt import build_system_prompt

    prompt = build_system_prompt(tool_names=["calculator", "create_file"])
    assert "calculator" in prompt
    assert "create_file" in prompt
    assert "Tool Call Format" in prompt


def test_build_system_prompt_with_user_name() -> None:
    from personal_ai_secretary.tools.prompt import build_system_prompt

    prompt = build_system_prompt(user_name="Esteban")
    assert "Esteban" in prompt


def test_build_system_prompt_with_extra_context() -> None:
    from personal_ai_secretary.tools.prompt import build_system_prompt

    prompt = build_system_prompt(extra_context="User prefers dark mode")
    assert "User prefers dark mode" in prompt


def test_build_tool_result_prompt() -> None:
    from personal_ai_secretary.tools.prompt import build_tool_result_prompt

    result = build_tool_result_prompt("calculator", {"result": 42})
    assert "calculator" in result
    assert "42" in result


# ─── Tool Call Extraction ───


def test_extract_tool_call_from_code_block() -> None:
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    agent = ExecutionAgent()
    text = (
        "I'll create that file for you.\n\n"
        '```tool\n{"tool": "create_file", '
        '"args": {"path": "/tmp/test.txt", "content": "hello"}}\n```\n\n'
        "Done!"
    )
    result = agent._extract_tool_call(text)
    assert result is not None
    assert result[0] == "create_file"
    assert result[1]["path"] == "/tmp/test.txt"
    assert result[1]["content"] == "hello"


def test_extract_tool_call_returns_none_for_plain_text() -> None:
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    agent = ExecutionAgent()
    result = agent._extract_tool_call("Hello, how can I help you?")
    assert result is None


def test_extract_tool_call_invalid_json() -> None:
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    agent = ExecutionAgent()
    result = agent._extract_tool_call("```tool\nnot json\n```")
    assert result is None


def test_extract_tool_call_inline_json() -> None:
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    agent = ExecutionAgent()
    text = 'Here is the result: {"tool": "calculator", "args": {"expression": "1+1"}}'
    result = agent._extract_tool_call(text)
    assert result is not None
    assert result[0] == "calculator"


def test_extract_tool_call_inline_invalid_json() -> None:
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    agent = ExecutionAgent()
    text = 'Bad: {"tool": "calc", "args": {invalid}}'
    result = agent._extract_tool_call(text)
    assert result is None


# ─── Tool Registry Extended ───


def test_default_registry_includes_all_expected_tools() -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry

    registry = default_tool_registry()
    names = registry.names()

    # Original tools
    assert "calculator" in names
    assert "list_tools" in names

    # DateTime tools
    assert "datetime_now" in names
    assert "format_date" in names

    # Path tools
    assert "get_directory" in names

    # Filesystem tools
    assert "create_file" in names
    assert "read_file" in names
    assert "write_file" in names
    assert "list_directory" in names
    assert "create_directory" in names
    assert "file_exists" in names


@pytest.mark.asyncio
async def test_datetime_now_tool_via_registry() -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry

    registry = default_tool_registry()
    result = await registry.execute("datetime_now", {})
    assert "date" in result
    assert "time" in result


@pytest.mark.asyncio
async def test_get_directory_tool_via_registry() -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry

    registry = default_tool_registry()
    result = await registry.execute("get_directory", {"name": "home"})
    assert "result" in result


@pytest.mark.asyncio
async def test_filesystem_tools_via_registry(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS

    # Temporarily allow tmp_path
    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path)]
    try:
        registry = default_tool_registry()

        # Create (requires approval)
        create_result = await registry.execute(
            "create_file",
            {"path": str(tmp_path / "test.txt"), "content": "hello"},
            approved=True,
        )
        assert create_result["result"] == "created"

        # Read (no approval needed)
        read_result = await registry.execute(
            "read_file", {"path": str(tmp_path / "test.txt")}
        )
        assert read_result["result"] == "hello"

        # List (no approval needed)
        list_result = await registry.execute(
            "list_directory", {"path": str(tmp_path)}
        )
        assert list_result["count"] == 1

        # Exists (no approval needed)
        exists_result = await registry.execute(
            "file_exists", {"path": str(tmp_path / "test.txt")}
        )
        assert exists_result["result"] is True

        # Write (update, requires approval)
        write_result = await registry.execute(
            "write_file",
            {"path": str(tmp_path / "test.txt"), "content": "updated"},
            approved=True,
        )
        assert write_result["result"] == "updated"
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


@pytest.mark.asyncio
async def test_create_file_requires_approval(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS

    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path)]
    try:
        registry = default_tool_registry()
        with pytest.raises(PermissionError, match="approval"):
            await registry.execute(
                "create_file",
                {"path": str(tmp_path / "test.txt"), "content": "hello"},
            )
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


# ─── Agentic Loop Tests ───


@pytest.mark.asyncio
async def test_execute_tool_from_llm_unknown_tool() -> None:
    from uuid import uuid4

    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import RiskLevel
    from personal_ai_secretary.tools.builtin import default_tool_registry

    agent = ExecutionAgent(registry=default_tool_registry())
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="test",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
    )
    result = await agent._execute_tool_from_llm("nonexistent_tool", {}, data)
    assert "error" in result
    assert "Unknown tool" in result["error"]


@pytest.mark.asyncio
async def test_execute_tool_from_llm_no_registry() -> None:
    from uuid import uuid4

    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import RiskLevel

    agent = ExecutionAgent(provider=None, registry=None)
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="test",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
    )
    result = await agent._execute_tool_from_llm("calculator", {"expression": "1+1"}, data)
    assert "error" in result
    assert "not available" in result["error"].lower()


@pytest.mark.asyncio
async def test_execute_tool_from_llm_approval_required() -> None:
    from uuid import uuid4

    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import RiskLevel
    from personal_ai_secretary.tools.builtin import default_tool_registry

    agent = ExecutionAgent(registry=default_tool_registry())
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="test",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
    )
    # create_file requires approval
    result = await agent._execute_tool_from_llm(
        "create_file",
        {"path": "/tmp/test.txt", "content": "hello"},
        data,
    )
    assert "error" in result
    assert "requires approval" in result["error"].lower()


@pytest.mark.asyncio
async def test_execute_tool_from_llm_calculator() -> None:
    from uuid import uuid4

    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import RiskLevel
    from personal_ai_secretary.tools.builtin import default_tool_registry

    agent = ExecutionAgent(registry=default_tool_registry())
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="test",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
    )
    result = await agent._execute_tool_from_llm(
        "calculator", {"expression": "2 + 3"}, data
    )
    assert result == {"result": 5}


@pytest.mark.asyncio
async def test_execute_tool_from_llm_bad_args() -> None:
    from uuid import uuid4

    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import RiskLevel
    from personal_ai_secretary.tools.builtin import default_tool_registry

    agent = ExecutionAgent(registry=default_tool_registry())
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="test",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
    )
    result = await agent._execute_tool_from_llm(
        "calculator", {"bad_arg": "x"}, data
    )
    assert "error" in result


# ─── Filesystem Error Paths ───


@pytest.mark.asyncio
async def test_read_file_encoding_fallback(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.filesystem import _read_file

    # Write a file with latin-1 encoding
    file_path = tmp_path / "latin.txt"
    file_path.write_bytes(b"\xe9\xe8\xea")  # accented chars in latin-1
    result = await _read_file({"path": str(file_path)})
    # Should succeed via encoding fallback
    assert "result" in result


@pytest.mark.asyncio
async def test_create_file_invalid_path(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.filesystem import (
        DEFAULT_ALLOWED_ROOTS,
        _create_file,
    )
    from personal_ai_secretary.tools.registry import ToolError

    # Restrict to tmp_path only, then try to escape
    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path / "allowed")]
    try:
        with pytest.raises(ToolError):
            await _create_file({
                "path": str(tmp_path / "escape" / "file.txt"),
                "content": "x",
            })
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


# ─── K.1 tests ───


@pytest.mark.asyncio
async def test_create_file_returns_verified_exists(tmp_path: Path) -> None:
    """create_file result must include verified_exists=True after successful creation."""
    from personal_ai_secretary.tools.filesystem import _create_file

    result = await _create_file({
        "path": str(tmp_path / "cachorro.txt"),
        "content": "Hola",
    })
    assert result.get("result") == "created"
    assert result.get("verified_exists") is True
    assert (tmp_path / "cachorro.txt").exists()


@pytest.mark.asyncio
async def test_write_file_returns_verified_exists(tmp_path: Path) -> None:
    """write_file result must include verified_exists=True after successful write."""
    from personal_ai_secretary.tools.filesystem import _write_file

    result = await _write_file({
        "path": str(tmp_path / "out.txt"),
        "content": "content",
    })
    assert result.get("verified_exists") is True
    assert (tmp_path / "out.txt").read_text() == "content"


@pytest.mark.asyncio
async def test_create_directory_returns_verified_exists(tmp_path: Path) -> None:
    """create_directory result must include verified_exists=True."""
    from personal_ai_secretary.tools.filesystem import _create_directory

    result = await _create_directory({"path": str(tmp_path / "new_dir")})
    assert result.get("result") == "created"
    assert result.get("verified_exists") is True
    assert (tmp_path / "new_dir").is_dir()


def test_sanitize_response_strips_deepseek_tokens() -> None:
    """_sanitize_response must remove DeepSeek and other model protocol tokens."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    raw = (
        "<|tool_calls_begin|>\n"
        "<|tool_call_begin|>\n"
        'function\n<|tool_sep|>\ncreate_file\n{"path":"/tmp/x","content":""}\n'
        "<|tool_call_end|>\n"
        "<|tool_calls_end|>\n"
        "I will create the file for you."
    )
    cleaned = ExecutionAgent._sanitize_response(raw)
    assert "<|tool_calls_begin|>" not in cleaned
    assert "<|tool_call_begin|>" not in cleaned
    assert "<|tool_call_end|>" not in cleaned
    assert "<|tool_calls_end|>" not in cleaned
    assert "I will create the file for you." in cleaned


def test_sanitize_response_strips_im_tokens() -> None:
    """<|im_start|> / <|im_end|> tokens must be stripped."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    raw = "<|im_start|>assistant\nHello there!<|im_end|>"
    cleaned = ExecutionAgent._sanitize_response(raw)
    assert "<|im_start|>" not in cleaned
    assert "<|im_end|>" not in cleaned
    assert "Hello there!" in cleaned


def test_sanitize_response_preserves_user_code() -> None:
    """_sanitize_response must not destroy code the LLM generated for the user."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    code = (
        "Here is your Python script:\n\n"
        "```python\n"
        "print('Hello World')\n"
        "```\n"
    )
    cleaned = ExecutionAgent._sanitize_response(code)
    assert "print('Hello World')" in cleaned


def test_sanitize_response_collapses_blank_lines() -> None:
    """After stripping tokens, 3+ blank lines should be reduced to 2."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    raw = "<|tool_calls_begin|>\n\n\n\nActual response"
    cleaned = ExecutionAgent._sanitize_response(raw)
    assert "\n\n\n" not in cleaned
    assert "Actual response" in cleaned


def test_sanitize_response_plain_text_unchanged() -> None:
    """Plain text responses without special tokens must pass through unchanged."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    text = "Hello! I'm Chiky. How can I help you today?"
    assert ExecutionAgent._sanitize_response(text) == text


def test_system_prompt_includes_verified_exists_guidance() -> None:
    """System prompt must instruct LLM to check tool result errors."""
    from personal_ai_secretary.tools.prompt import build_system_prompt

    prompt = build_system_prompt(tool_names=["create_file"])
    assert "If requires_approval, ask user first" in prompt

