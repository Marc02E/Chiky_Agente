"""FASE K.2 — Tests for Chiky development agent capabilities."""

from pathlib import Path

import pytest

# ═══════════════════════════════════════════════════════════════════════════════
# analyze_project
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_analyze_project_basic(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _analyze_project

    # Create a small project structure
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')")
    (tmp_path / "src" / "utils.py").write_text("# utils")
    (tmp_path / "README.md").write_text("# Project")

    result = await _analyze_project({"path": str(tmp_path)})
    assert result["project_name"] == tmp_path.name
    assert result["total_files"] >= 3
    assert result["total_directories"] >= 1
    assert len(result["structure"]) > 0


@pytest.mark.asyncio
async def test_analyze_project_with_content(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _analyze_project

    (tmp_path / "app.py").write_text("x = 1")
    result = await _analyze_project({
        "path": str(tmp_path),
        "include_content": True,
    })
    file_nodes = [n for n in result["structure"] if n.get("type") == "file"]
    assert any("content" in n for n in file_nodes)


@pytest.mark.asyncio
async def test_analyze_project_empty_path() -> None:
    from personal_ai_secretary.tools.development import _analyze_project

    result = await _analyze_project({"path": ""})
    assert "error" in result


@pytest.mark.asyncio
async def test_analyze_project_not_found(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _analyze_project

    result = await _analyze_project({"path": str(tmp_path / "nonexistent")})
    assert "error" in result


@pytest.mark.asyncio
async def test_analyze_project_not_a_dir(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _analyze_project

    f = tmp_path / "file.txt"
    f.write_text("x")
    result = await _analyze_project({"path": str(f)})
    assert "error" in result


@pytest.mark.asyncio
async def test_analyze_project_skips_git(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _analyze_project

    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("git config")
    (tmp_path / "app.py").write_text("x = 1")

    result = await _analyze_project({"path": str(tmp_path)})
    all_names = [n["name"] for n in result["structure"]]
    assert ".git" not in all_names
    assert "app.py" in all_names


@pytest.mark.asyncio
async def test_analyze_project_max_depth(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _analyze_project

    (tmp_path / "a" / "b" / "c").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c" / "deep.py").write_text("x")

    result = await _analyze_project({"path": str(tmp_path), "max_depth": 1})
    all_paths = [n["path"] for n in result["structure"]]
    assert not any("c/deep.py" in p for p in all_paths)


@pytest.mark.asyncio
async def test_analyze_project_path_traversal_blocked(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _analyze_project
    from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS

    original = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_path)]
    try:
        result = await _analyze_project({
            "path": str(tmp_path / ".." / "escape"),
        })
        assert "error" in result
    finally:
        DEFAULT_ALLOWED_ROOTS[:] = original


# ═══════════════════════════════════════════════════════════════════════════════
# read_files
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_read_files_multiple(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _read_files

    (tmp_path / "a.txt").write_text("alpha")
    (tmp_path / "b.txt").write_text("beta")

    result = await _read_files({
        "paths": [str(tmp_path / "a.txt"), str(tmp_path / "b.txt")],
    })
    assert result["read_count"] == 2
    assert result["error_count"] == 0
    assert "alpha" in result["files"][str(tmp_path / "a.txt")]["content"]
    assert "beta" in result["files"][str(tmp_path / "b.txt")]["content"]


@pytest.mark.asyncio
async def test_read_files_with_line_count(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _read_files

    f = tmp_path / "multi.txt"
    f.write_text("line1\nline2\nline3")

    result = await _read_files({"paths": [str(f)]})
    file_info = result["files"][str(f)]
    assert file_info["lines"] == 3


@pytest.mark.asyncio
async def test_read_files_empty_list() -> None:
    from personal_ai_secretary.tools.development import _read_files

    result = await _read_files({"paths": []})
    assert "error" in result


@pytest.mark.asyncio
async def test_read_files_not_found(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _read_files

    result = await _read_files({
        "paths": [str(tmp_path / "ghost.txt")],
    })
    assert result["read_count"] == 0
    assert result["error_count"] == 1


@pytest.mark.asyncio
async def test_read_files_too_many() -> None:
    from personal_ai_secretary.tools.development import _read_files

    paths = [f"/tmp/file_{i}.txt" for i in range(25)]
    result = await _read_files({"paths": paths})
    assert "error" in result
    assert "Too many" in result["error"]


@pytest.mark.asyncio
async def test_read_files_mixed_valid_invalid(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _read_files

    (tmp_path / "ok.txt").write_text("good")
    result = await _read_files({
        "paths": [
            str(tmp_path / "ok.txt"),
            str(tmp_path / "missing.txt"),
        ],
    })
    assert result["read_count"] == 1
    assert result["error_count"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# modify_file
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_modify_file_replace_all(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _modify_file

    f = tmp_path / "code.py"
    f.write_text("foo bar foo baz foo")

    result = await _modify_file({
        "path": str(f),
        "mode": "replace",
        "search": "foo",
        "replacement": "qux",
    })
    assert result["result"] == "modified"
    assert f.read_text() == "qux bar qux baz qux"


@pytest.mark.asyncio
async def test_modify_file_replace_first(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _modify_file

    f = tmp_path / "code.py"
    f.write_text("foo bar foo baz")

    result = await _modify_file({
        "path": str(f),
        "mode": "replace_first",
        "search": "foo",
        "replacement": "qux",
    })
    assert result["result"] == "modified"
    assert f.read_text() == "qux bar foo baz"


@pytest.mark.asyncio
async def test_modify_file_append(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _modify_file

    f = tmp_path / "log.txt"
    f.write_text("line1\n")

    result = await _modify_file({
        "path": str(f),
        "mode": "append",
        "content": "line2",
    })
    assert result["result"] == "modified"
    assert f.read_text() == "line1\nline2"


@pytest.mark.asyncio
async def test_modify_file_prepend(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _modify_file

    f = tmp_path / "file.txt"
    f.write_text("end")

    result = await _modify_file({
        "path": str(f),
        "mode": "prepend",
        "content": "start\n",
    })
    assert result["result"] == "modified"
    assert f.read_text() == "start\nend"


@pytest.mark.asyncio
async def test_modify_file_insert_after(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _modify_file

    f = tmp_path / "code.py"
    f.write_text("import os\nprint('hi')")

    result = await _modify_file({
        "path": str(f),
        "mode": "insert_after",
        "search": "import os",
        "content": "import sys",
    })
    assert result["result"] == "modified"
    assert "import os\nimport sys" in f.read_text()


@pytest.mark.asyncio
async def test_modify_file_insert_before(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _modify_file

    f = tmp_path / "code.py"
    f.write_text("print('first')\nprint('second')")

    result = await _modify_file({
        "path": str(f),
        "mode": "insert_before",
        "search": "print('second')",
        "content": "# added",
    })
    assert result["result"] == "modified"
    content = f.read_text()
    assert content.index("# added") < content.index("print('second')")


@pytest.mark.asyncio
async def test_modify_file_overwrite(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _modify_file

    f = tmp_path / "old.txt"
    f.write_text("old content")

    result = await _modify_file({
        "path": str(f),
        "mode": "overwrite",
        "content": "new content",
    })
    assert result["result"] == "modified"
    assert f.read_text() == "new content"


@pytest.mark.asyncio
async def test_modify_file_search_not_found(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _modify_file

    f = tmp_path / "code.py"
    f.write_text("hello world")

    result = await _modify_file({
        "path": str(f),
        "mode": "replace",
        "search": "nonexistent",
        "replacement": "x",
    })
    assert "error" in result


@pytest.mark.asyncio
async def test_modify_file_not_found(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _modify_file

    result = await _modify_file({
        "path": str(tmp_path / "missing.py"),
        "mode": "overwrite",
        "content": "x",
    })
    assert "error" in result


@pytest.mark.asyncio
async def test_modify_file_empty_path() -> None:
    from personal_ai_secretary.tools.development import _modify_file

    result = await _modify_file({"path": "", "mode": "overwrite", "content": "x"})
    assert "error" in result


@pytest.mark.asyncio
async def test_modify_file_unknown_mode(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _modify_file

    f = tmp_path / "x.txt"
    f.write_text("x")
    result = await _modify_file({
        "path": str(f),
        "mode": "bad_mode",
        "content": "y",
    })
    assert "error" in result


@pytest.mark.asyncio
async def test_modify_file_replace_no_change(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _modify_file

    f = tmp_path / "x.txt"
    f.write_text("hello")

    result = await _modify_file({
        "path": str(f),
        "mode": "replace",
        "search": "hello",
        "replacement": "hello",
    })
    assert result["result"] == "unchanged"


@pytest.mark.asyncio
async def test_modify_file_verified_exists(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _modify_file

    f = tmp_path / "v.txt"
    f.write_text("old")

    result = await _modify_file({
        "path": str(f),
        "mode": "overwrite",
        "content": "new",
    })
    assert result["verified_exists"] is True
    assert f.read_text() == "new"


# ═══════════════════════════════════════════════════════════════════════════════
# search_files
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_search_files_finds_pattern(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _search_files

    (tmp_path / "a.py").write_text("def hello():\n    pass")
    (tmp_path / "b.py").write_text("x = 42")

    result = await _search_files({
        "path": str(tmp_path),
        "pattern": "hello",
    })
    assert result["match_count"] >= 1
    assert result["matches"][0]["file"] == "a.py"


@pytest.mark.asyncio
async def test_search_files_with_include_filter(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _search_files

    (tmp_path / "a.py").write_text("target = 1")
    (tmp_path / "b.txt").write_text("target = 2")

    result = await _search_files({
        "path": str(tmp_path),
        "pattern": "target",
        "include": "*.py",
    })
    files = [m["file"] for m in result["matches"]]
    assert "a.py" in files
    assert "b.txt" not in files


@pytest.mark.asyncio
async def test_search_files_case_insensitive(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _search_files

    (tmp_path / "a.py").write_text("HELLO = 1")

    result = await _search_files({
        "path": str(tmp_path),
        "pattern": "hello",
    })
    assert result["match_count"] >= 1


@pytest.mark.asyncio
async def test_search_files_empty_pattern(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _search_files

    result = await _search_files({
        "path": str(tmp_path),
        "pattern": "",
    })
    assert "error" in result


@pytest.mark.asyncio
async def test_search_files_empty_path() -> None:
    from personal_ai_secretary.tools.development import _search_files

    result = await _search_files({"path": "", "pattern": "x"})
    assert "error" in result


@pytest.mark.asyncio
async def test_search_files_no_matches(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _search_files

    (tmp_path / "a.py").write_text("nothing here")

    result = await _search_files({
        "path": str(tmp_path),
        "pattern": "zzz_nonexistent_zzz",
    })
    assert result["match_count"] == 0
    assert len(result["matches"]) == 0


@pytest.mark.asyncio
async def test_search_files_truncated(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _search_files

    # Create enough matches to exceed max_results
    lines = "\n".join(["target" for _ in range(10)])
    (tmp_path / "big.py").write_text(lines)

    result = await _search_files({
        "path": str(tmp_path),
        "pattern": "target",
        "max_results": 3,
    })
    assert result["truncated"] is True
    assert len(result["matches"]) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# verify_files
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_verify_files_exists(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _verify_files

    (tmp_path / "ok.txt").write_text("content")

    result = await _verify_files({
        "verifications": [{"path": str(tmp_path / "ok.txt"), "should_exist": True}],
    })
    assert result["all_passed"] is True


@pytest.mark.asyncio
async def test_verify_files_not_exists(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _verify_files

    result = await _verify_files({
        "verifications": [
            {"path": str(tmp_path / "missing.txt"), "should_exist": True},
        ],
    })
    assert result["all_passed"] is False
    assert "does not exist" in result["results"][0]["failure_reason"]


@pytest.mark.asyncio
async def test_verify_files_should_not_exist(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _verify_files

    (tmp_path / "exists.txt").write_text("x")

    result = await _verify_files({
        "verifications": [
            {"path": str(tmp_path / "exists.txt"), "should_exist": False},
        ],
    })
    assert result["all_passed"] is False


@pytest.mark.asyncio
async def test_verify_files_content_contains(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _verify_files

    (tmp_path / "config.json").write_text('{"key": "value"}')

    result = await _verify_files({
        "verifications": [
            {
                "path": str(tmp_path / "config.json"),
                "content_contains": '"key"',
            },
        ],
    })
    assert result["all_passed"] is True


@pytest.mark.asyncio
async def test_verify_files_content_not_found(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _verify_files

    (tmp_path / "f.txt").write_text("hello")

    result = await _verify_files({
        "verifications": [
            {"path": str(tmp_path / "f.txt"), "content_contains": "MISSING"},
        ],
    })
    assert result["all_passed"] is False


@pytest.mark.asyncio
async def test_verify_files_min_size(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _verify_files

    (tmp_path / "big.txt").write_text("x" * 100)

    result = await _verify_files({
        "verifications": [
            {"path": str(tmp_path / "big.txt"), "min_size": 50},
        ],
    })
    assert result["all_passed"] is True


@pytest.mark.asyncio
async def test_verify_files_max_size(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _verify_files

    (tmp_path / "big.txt").write_text("x" * 200)

    result = await _verify_files({
        "verifications": [
            {"path": str(tmp_path / "big.txt"), "max_size": 50},
        ],
    })
    assert result["all_passed"] is False


@pytest.mark.asyncio
async def test_verify_files_empty_list() -> None:
    from personal_ai_secretary.tools.development import _verify_files

    result = await _verify_files({"verifications": []})
    assert "error" in result


@pytest.mark.asyncio
async def test_verify_files_multiple_mixed(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.development import _verify_files

    (tmp_path / "a.txt").write_text("hello")
    result = await _verify_files({
        "verifications": [
            {"path": str(tmp_path / "a.txt"), "should_exist": True},
            {"path": str(tmp_path / "b.txt"), "should_exist": True},
        ],
    })
    assert result["all_passed"] is False
    assert result["total"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Tool Registry Integration
# ═══════════════════════════════════════════════════════════════════════════════


def test_default_registry_includes_development_tools() -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry

    registry = default_tool_registry()
    names = registry.names()

    assert "analyze_project" in names
    assert "read_files" in names
    assert "modify_file" in names
    assert "search_files" in names
    assert "verify_files" in names


def test_development_tools_have_correct_risk_levels() -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry

    registry = default_tool_registry()

    # Low risk, no approval
    for name in ("analyze_project", "read_files", "search_files", "verify_files"):
        defn = registry.get(name)
        assert defn is not None
        assert defn.risk.value == "low"
        assert defn.requires_explicit_approval is False

    # High risk, requires approval
    defn = registry.get("modify_file")
    assert defn is not None
    assert defn.risk.value == "high"
    assert defn.requires_explicit_approval is True


@pytest.mark.asyncio
async def test_modify_file_requires_approval(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry

    f = tmp_path / "x.txt"
    f.write_text("old")

    registry = default_tool_registry()
    with pytest.raises(PermissionError, match="approval"):
        await registry.execute(
            "modify_file",
            {"path": str(f), "mode": "overwrite", "content": "new"},
            approved=False,
        )


@pytest.mark.asyncio
async def test_modify_file_with_approval(tmp_path: Path) -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry

    f = tmp_path / "x.txt"
    f.write_text("old")

    registry = default_tool_registry()
    result = await registry.execute(
        "modify_file",
        {
            "path": str(f),
            "mode": "overwrite",
            "search": "",
            "replacement": "",
            "content": "new",
        },
        approved=True,
    )
    assert result["result"] == "modified"
    assert f.read_text() == "new"


# ═══════════════════════════════════════════════════════════════════════════════
# Model Adaptation
# ═══════════════════════════════════════════════════════════════════════════════


def test_system_prompt_includes_model_guidance_for_llama() -> None:
    from personal_ai_secretary.tools.prompt import build_system_prompt

    prompt = build_system_prompt(model_name="llama3.2")
    assert "Llama" in prompt or "llama" in prompt.lower()


def test_system_prompt_includes_model_guidance_for_deepseek() -> None:
    from personal_ai_secretary.tools.prompt import build_system_prompt

    prompt = build_system_prompt(model_name="deepseek-coder-v2")
    assert "DeepSeek" in prompt or "deepseek" in prompt.lower()


def test_system_prompt_no_model_guidance_when_none() -> None:
    from personal_ai_secretary.tools.prompt import build_system_prompt

    prompt = build_system_prompt(model_name=None)
    assert "Model-Specific Notes" not in prompt


def test_system_prompt_includes_development_workflow() -> None:
    from personal_ai_secretary.tools.prompt import build_system_prompt

    prompt = build_system_prompt(tool_names=["analyze_project", "modify_file"])
    assert "Development Workflow" in prompt
    assert "modify_file" in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# XML Tool Call Extraction
# ═══════════════════════════════════════════════════════════════════════════════


def test_extract_xml_tool_call() -> None:
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    agent = ExecutionAgent()
    text = 'Here is the call:\n<tool_call>\n{"tool": "calculator", "args": {"expression": "1+1"}}\n</tool_call>'
    calls = agent._extract_all_tool_calls(text)
    assert len(calls) == 1
    assert calls[0][0] == "calculator"
    assert calls[0][1]["expression"] == "1+1"


def test_extract_multiple_tool_blocks() -> None:
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    agent = ExecutionAgent()
    text = (
        '```tool\n{"tool": "calculator", "args": {"expression": "1+1"}}\n```\n'
        '```tool\n{"tool": "list_tools", "args": {}}\n```'
    )
    calls = agent._extract_all_tool_calls(text)
    assert len(calls) == 2
    names = [c[0] for c in calls]
    assert "calculator" in names
    assert "list_tools" in names


# ═══════════════════════════════════════════════════════════════════════════════
# Error Recovery in Agentic Loop
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_agent_recovery_hint_on_tool_error(tmp_path: Path) -> None:
    """When a tool returns an error, the recovery hint must be included in the result prompt."""
    from personal_ai_secretary.tools.prompt import build_tool_result_prompt

    result = {"error": "File not found: /tmp/missing.txt"}
    enhanced = dict(result)
    enhanced["recovery_hint"] = (
        "The previous tool call returned an error. "
        "Try a different approach: check the path, "
        "use a different tool, or ask the user for clarification."
    )
    prompt = build_tool_result_prompt("read_file", enhanced)
    assert "recovery_hint" in prompt
    assert "error" in prompt


@pytest.mark.asyncio
async def test_agent_handles_invalid_tool_call_gracefully() -> None:
    """Invalid tool calls must be handled without crashing."""
    from uuid import uuid4

    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import RiskLevel

    agent = ExecutionAgent()
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="@tool:calculator not-json",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
    )
    result = await agent.run(data)
    assert "Invalid tool call" in result.content


@pytest.mark.asyncio
async def test_agent_handles_unknown_tool_gracefully() -> None:
    """Unknown tool calls must be reported clearly."""
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
        text="@tool:nonexistent_tool {}",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
    )
    result = await agent.run(data)
    assert "not available" in result.content


@pytest.mark.asyncio
async def test_agent_blocks_unauthorized_execution() -> None:
    """Agent must block when authorized=False is in context."""
    from uuid import uuid4

    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import RiskLevel

    agent = ExecutionAgent()
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text="do something",
        correlation_id="test",
        risk_level=RiskLevel.LOW,
        context={"authorized": False},
    )
    result = await agent.run(data)
    assert result.blocked is True
    assert "authorization required" in result.content.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# Attached File Context
# ═══════════════════════════════════════════════════════════════════════════════


def test_attached_file_model() -> None:
    """AttachedFile model must exist and support basic fields."""
    from personal_ai_secretary.domain.contracts import AttachedFile

    af = AttachedFile(
        name="test.py",
        content="print('hello')",
        size=14,
        mime_type="text/plain",
    )
    assert af.name == "test.py"
    assert af.content == "print('hello')"


def test_request_create_with_attached_files() -> None:
    """RequestCreate must accept attached_files."""
    from personal_ai_secretary.domain.contracts import AttachedFile, RequestCreate

    rc = RequestCreate(
        input="analyze this file",
        attached_files=[
            AttachedFile(name="f.py", content="x=1", size=3),
        ],
    )
    assert len(rc.attached_files) == 1
    assert rc.attached_files[0].name == "f.py"


# ═══════════════════════════════════════════════════════════════════════════════
# NON_TOOL_CALLING_MODELS constant
# ═══════════════════════════════════════════════════════════════════════════════


def test_non_tool_calling_models_constant_exists() -> None:
    from personal_ai_secretary.agents.builtin import ExecutionAgent

    assert isinstance(ExecutionAgent.NON_TOOL_CALLING_MODELS, frozenset)
    assert "llama3" in ExecutionAgent.NON_TOOL_CALLING_MODELS


# ═══════════════════════════════════════════════════════════════════════════════
# Integration: Full Development Flow
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_full_development_flow_analyze_read_modify_verify(tmp_path: Path) -> None:
    """End-to-end: create project → analyze → read → modify → verify."""
    from personal_ai_secretary.tools.development import (
        _analyze_project,
        _modify_file,
        _read_files,
        _search_files,
        _verify_files,
    )

    # 1. Create project
    project = tmp_path / "myapp"
    project.mkdir()
    (project / "main.py").write_text("def main():\n    pass")
    (project / "config.json").write_text('{"debug": false}')

    # 2. Analyze
    analysis = await _analyze_project({"path": str(project)})
    assert analysis["total_files"] == 2

    # 3. Read multiple files
    files = await _read_files({
        "paths": [str(project / "main.py"), str(project / "config.json")],
    })
    assert files["read_count"] == 2

    # 4. Search for pattern
    search = await _search_files({
        "path": str(project),
        "pattern": "def main",
    })
    assert search["match_count"] >= 1

    # 5. Modify
    modify = await _modify_file({
        "path": str(project / "main.py"),
        "mode": "replace",
        "search": "pass",
        "replacement": "print('hello')",
    })
    assert modify["result"] == "modified"

    # 6. Verify
    verify = await _verify_files({
        "verifications": [
            {
                "path": str(project / "main.py"),
                "content_contains": "print('hello')",
            },
            {
                "path": str(project / "config.json"),
                "content_contains": '"debug"',
            },
        ],
    })
    assert verify["all_passed"] is True
