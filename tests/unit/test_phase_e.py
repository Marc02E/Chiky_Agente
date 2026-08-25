"""Tests for Phase E: Code Generation & Project Creation.

Covers:
- create_project tool
- write_file append mode
- Multiple tool calls per response
- System prompt project guidance
- MAX_TOOL_ROUNDS increase
- Integration flows
- Security (path traversal, approval)
"""

import pytest

# ─── create_project Tool ───


@pytest.mark.asyncio
async def test_create_project_basic(tmp_path) -> None:
    from personal_ai_secretary.tools.project import _create_project

    result = await _create_project({
        "project_name": "my_app",
        "base_path": str(tmp_path),
        "files": [
            {"path": "main.py", "content": "print('hello')"},
            {"path": "README.md", "content": "# My App"},
        ],
    })
    assert result["project_name"] == "my_app"
    assert result["files_count"] == 2
    assert "main.py" in result["created_files"]
    assert "README.md" in result["created_files"]
    assert result["errors_count"] == 0

    # Verify files actually exist
    project_dir = tmp_path / "my_app"
    assert project_dir.exists()
    assert (project_dir / "main.py").read_text() == "print('hello')"
    assert (project_dir / "README.md").read_text() == "# My App"


@pytest.mark.asyncio
async def test_create_project_with_directories(tmp_path) -> None:
    from personal_ai_secretary.tools.project import _create_project

    result = await _create_project({
        "project_name": "snake_game",
        "base_path": str(tmp_path),
        "directories": ["src", "tests"],
        "files": [
            {"path": "main.py", "content": "import pygame"},
            {"path": "src/game.py", "content": "class Game: pass"},
            {"path": "tests/test_game.py", "content": "def test_game(): pass"},
            {"path": "README.md", "content": "# Snake Game"},
        ],
    })
    assert result["files_count"] == 4
    assert result["directories_count"] == 2

    project_dir = tmp_path / "snake_game"
    assert (project_dir / "src" / "game.py").exists()
    assert (project_dir / "tests" / "test_game.py").exists()


@pytest.mark.asyncio
async def test_create_project_missing_name(tmp_path) -> None:
    from personal_ai_secretary.tools.project import _create_project

    result = await _create_project({
        "base_path": str(tmp_path),
        "files": [],
    })
    assert "error" in result
    assert "project_name" in result["error"]


@pytest.mark.asyncio
async def test_create_project_missing_base_path() -> None:
    from personal_ai_secretary.tools.project import _create_project

    result = await _create_project({
        "project_name": "test",
        "files": [],
    })
    assert "error" in result
    assert "base_path" in result["error"]


@pytest.mark.asyncio
async def test_create_project_empty_files(tmp_path) -> None:
    from personal_ai_secretary.tools.project import _create_project

    result = await _create_project({
        "project_name": "empty_project",
        "base_path": str(tmp_path),
        "files": [],
    })
    assert result["files_count"] == 0
    assert (tmp_path / "empty_project").exists()


@pytest.mark.asyncio
async def test_create_project_invalid_files(tmp_path) -> None:
    from personal_ai_secretary.tools.project import _create_project

    result = await _create_project({
        "project_name": "bad_project",
        "base_path": str(tmp_path),
        "files": ["not_a_dict", {"no_path": True}],
    })
    assert result["errors_count"] == 2
    assert "Invalid file spec" in result["errors"][0]
    assert "missing 'path'" in result["errors"][1]


@pytest.mark.asyncio
async def test_create_project_path_traversal_blocked(tmp_path) -> None:
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS
    from personal_ai_secretary.tools.project import _create_project

    # Restrict allowed roots to only the test directory
    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path)]
    try:
        result = await _create_project({
            "project_name": "evil_project",
            "base_path": str(tmp_path),
            "files": [
                {"path": "../../etc/passwd", "content": "bad"},
            ],
        })
        # The file creation should fail due to path validation
        assert result["errors_count"] > 0
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


@pytest.mark.asyncio
async def test_create_project_via_registry(tmp_path) -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS

    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path)]
    try:
        registry = default_tool_registry()
        result = await registry.execute(
            "create_project",
            {
                "project_name": "test_project",
                "base_path": str(tmp_path),
                "files": [{"path": "main.py", "content": "print('hi')"}],
                "directories": [],
            },
            approved=True,
        )
        assert result["files_count"] == 1
        assert (tmp_path / "test_project" / "main.py").exists()
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


@pytest.mark.asyncio
async def test_create_project_requires_approval(tmp_path) -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS

    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path)]
    try:
        registry = default_tool_registry()
        with pytest.raises(PermissionError, match="approval"):
            await registry.execute(
                "create_project",
                {
                    "project_name": "test_project",
                    "base_path": str(tmp_path),
                    "files": [{"path": "main.py", "content": "print('hi')"}],
                    "directories": [],
                },
            )
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


# ─── write_file Append Mode ───


@pytest.mark.asyncio
async def test_write_file_append_mode(tmp_path) -> None:
    from personal_ai_secretary.tools.filesystem import _write_file

    file_path = str(tmp_path / "append.txt")
    # Create initial file
    result1 = await _write_file({"path": file_path, "content": "Hello"})
    assert result1["result"] == "created"

    # Append to file
    result2 = await _write_file({"path": file_path, "content": " World", "mode": "append"})
    assert result2["result"] == "updated"
    assert result2["mode"] == "append"

    # Verify content
    content = (tmp_path / "append.txt").read_text()
    assert content == "Hello World"


@pytest.mark.asyncio
async def test_write_file_overwrite_mode(tmp_path) -> None:
    from personal_ai_secretary.tools.filesystem import _write_file

    file_path = str(tmp_path / "overwrite.txt")
    await _write_file({"path": file_path, "content": "old content"})
    result = await _write_file({"path": file_path, "content": "new content"})
    assert result["result"] == "updated"
    assert (tmp_path / "overwrite.txt").read_text() == "new content"


@pytest.mark.asyncio
async def test_write_file_append_creates_new(tmp_path) -> None:
    from personal_ai_secretary.tools.filesystem import _write_file

    file_path = str(tmp_path / "new_append.txt")
    result = await _write_file({"path": file_path, "content": "first", "mode": "append"})
    assert result["result"] == "created"
    assert (tmp_path / "new_append.txt").read_text() == "first"


@pytest.mark.asyncio
async def test_write_file_append_multiple_times(tmp_path) -> None:
    from personal_ai_secretary.tools.filesystem import _write_file

    file_path = str(tmp_path / "multi_append.txt")
    await _write_file({"path": file_path, "content": "line1\n"})
    await _write_file({"path": file_path, "content": "line2\n", "mode": "append"})
    await _write_file({"path": file_path, "content": "line3\n", "mode": "append"})

    content = (tmp_path / "multi_append.txt").read_text()
    assert content == "line1\nline2\nline3\n"


# ─── Multiple Tool Calls per Response ───


def test_extract_all_tool_calls_single() -> None:
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    agent = ExecutionAgent()
    text = (
        '```tool\n{"tool": "create_file", '
        '"args": {"path": "/tmp/test.txt", "content": "hello"}}\n```'
    )
    calls = agent._extract_all_tool_calls(text)
    assert len(calls) == 1
    assert calls[0][0] == "create_file"
    assert calls[0][1]["path"] == "/tmp/test.txt"


def test_extract_all_tool_calls_multiple() -> None:
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    agent = ExecutionAgent()
    text = (
        "I'll create the project structure.\n\n"
        '```tool\n{"tool": "create_directory", "args": {"path": "/tmp/project"}}\n```\n\n'
        '```tool\n{"tool": "create_file", "args": {"path": "/tmp/project/main.py", "content": "print(1)"}}\n```\n\n'
        '```tool\n{"tool": "create_file", "args": {"path": "/tmp/project/README.md", "content": "# Project"}}\n```'
    )
    calls = agent._extract_all_tool_calls(text)
    assert len(calls) == 3
    assert calls[0][0] == "create_directory"
    assert calls[1][0] == "create_file"
    assert calls[2][0] == "create_file"


def test_extract_all_tool_calls_none() -> None:
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    agent = ExecutionAgent()
    calls = agent._extract_all_tool_calls("Hello, how can I help?")
    assert calls == []


def test_extract_all_tool_calls_invalid_json() -> None:
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    agent = ExecutionAgent()
    calls = agent._extract_all_tool_calls("```tool\nnot json\n```")
    assert calls == []


def test_extract_tool_call_uses_all_calls() -> None:
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    agent = ExecutionAgent()
    text = (
        '```tool\n{"tool": "calculator", "args": {"expression": "1+1"}}\n```\n'
        '```tool\n{"tool": "datetime_now", "args": {}}\n```'
    )
    # _extract_tool_call should return the first one
    result = agent._extract_tool_call(text)
    assert result is not None
    assert result[0] == "calculator"


# ─── MAX_TOOL_ROUNDS ───


def test_max_tool_rounds_increased() -> None:
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    assert ExecutionAgent.MAX_TOOL_ROUNDS == 15


# ─── System Prompt Project Guidance ───


def test_system_prompt_includes_project_guidance() -> None:
    from personal_ai_secretary.tools.prompt import build_system_prompt

    prompt = build_system_prompt(tool_names=["create_file", "create_project"])
    assert "analyze" in prompt.lower() or "read" in prompt.lower()
    assert "README" in prompt


def test_system_prompt_allows_multiple_tool_calls() -> None:
    from personal_ai_secretary.tools.prompt import build_system_prompt

    prompt = build_system_prompt(tool_names=["create_file"])
    assert "Multiple" in prompt or "multiple" in prompt


def test_system_prompt_includes_modify_guidance() -> None:
    from personal_ai_secretary.tools.prompt import build_system_prompt

    prompt = build_system_prompt(tool_names=["create_file"])
    assert "modify" in prompt.lower()
    assert "read" in prompt.lower()


# ─── Registry Includes New Tools ───


def test_registry_includes_create_project() -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry

    registry = default_tool_registry()
    assert "create_project" in registry.names()
    assert registry.get("create_project") is not None


def test_create_project_requires_approval_in_registry() -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry

    registry = default_tool_registry()
    defn = registry.get("create_project")
    assert defn is not None
    assert defn.requires_explicit_approval is True


# ─── Agentic Loop Integration ───


@pytest.mark.asyncio
async def test_execute_all_tool_calls() -> None:
    """Test that _extract_all_tool_calls returns multiple calls."""
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    agent = ExecutionAgent()
    text = (
        "Creating project structure:\n\n"
        '```tool\n{"tool": "create_directory", "args": {"path": "/tmp/proj"}}\n```\n\n'
        '```tool\n{"tool": "create_file", "args": {"path": "/tmp/proj/main.py", "content": "x"}}\n```\n\n'
        '```tool\n{"tool": "create_file", "args": {"path": "/tmp/proj/README.md", "content": "# Proj"}}\n```'
    )
    calls = agent._extract_all_tool_calls(text)
    assert len(calls) == 3
    # Verify all are valid tool names
    registry_names = {
        "create_directory", "create_file", "read_file", "write_file",
        "list_directory", "file_exists", "calculator", "list_tools",
        "datetime_now", "format_date", "get_directory", "create_project",
    }
    for name, _ in calls:
        assert name in registry_names


@pytest.mark.asyncio
async def test_create_project_full_workflow(tmp_path) -> None:
    """Simulate a full project creation workflow."""
    from personal_ai_secretary.tools.filesystem import (
        _create_directory,
        _create_file,
        _list_directory,
        _read_file,
    )

    project_dir = str(tmp_path / "snake_game")

    # Step 1: Create project directory
    dir_result = await _create_directory({"path": project_dir})
    assert dir_result["result"] == "created"

    # Step 2: Create source files
    main_result = await _create_file({
        "path": f"{project_dir}/main.py",
        "content": "import pygame\n\ndef main():\n    pygame.init()",
    })
    assert main_result["result"] == "created"

    game_result = await _create_file({
        "path": f"{project_dir}/game.py",
        "content": "class Snake:\n    def __init__(self):\n        self.body = []",
    })
    assert game_result["result"] == "created"

    # Step 3: Create README
    readme_result = await _create_file({
        "path": f"{project_dir}/README.md",
        "content": "# Snake Game\nA simple Snake game using pygame.",
    })
    assert readme_result["result"] == "created"

    # Step 4: Verify structure
    list_result = await _list_directory({"path": project_dir})
    assert list_result["count"] == 3
    names = [e["name"] for e in list_result["result"]]
    assert "main.py" in names
    assert "game.py" in names
    assert "README.md" in names

    # Step 5: Read back and verify
    read_result = await _read_file({"path": f"{project_dir}/main.py"})
    assert "pygame" in read_result["result"]


@pytest.mark.asyncio
async def test_edit_existing_project_read_then_write(tmp_path) -> None:
    """Test editing existing project: read first, then modify."""
    from personal_ai_secretary.tools.filesystem import _create_file, _read_file, _write_file

    file_path = str(tmp_path / "project.py")

    # Create file
    await _create_file({
        "path": file_path,
        "content": "def hello():\n    return 'hello'",
    })

    # Read before modifying
    read_result = await _read_file({"path": file_path})
    assert "hello" in read_result["result"]

    # Modify file
    write_result = await _write_file({
        "path": file_path,
        "content": "def hello():\n    return 'hello'\n\ndef goodbye():\n    return 'bye'",
    })
    assert write_result["result"] == "updated"

    # Verify change
    verify = await _read_file({"path": file_path})
    assert "goodbye" in verify["result"]
    assert "hello" in verify["result"]


# ─── Error Handling ───


@pytest.mark.asyncio
async def test_create_project_with_errors(tmp_path) -> None:
    """Test that errors in file creation are collected and reported."""
    from personal_ai_secretary.tools.project import _create_project

    result = await _create_project({
        "project_name": "error_project",
        "base_path": str(tmp_path),
        "files": [
            {"path": "valid.py", "content": "x"},
            {"path": "", "content": "y"},  # Empty path
            "not_a_dict",  # Invalid spec
        ],
    })
    assert result["files_count"] == 1
    assert result["errors_count"] == 2


@pytest.mark.asyncio
async def test_write_file_append_error(tmp_path) -> None:
    """Test append mode with non-existent file."""
    from personal_ai_secretary.tools.filesystem import _write_file

    # Append to non-existent file should create it
    result = await _write_file({
        "path": str(tmp_path / "new_file.txt"),
        "content": "content",
        "mode": "append",
    })
    assert result["result"] == "created"
    assert result["mode"] == "append"
