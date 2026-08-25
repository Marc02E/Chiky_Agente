"""FASE O — Tests for new tools: file_delete, file_copy, generate_tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from personal_ai_secretary.tools.filesystem import (
    register_filesystem_tools,
    DEFAULT_ALLOWED_ROOTS,
)
from personal_ai_secretary.tools.development import (
    register_development_tools,
)
from personal_ai_secretary.tools.registry import ToolRegistry

_ORIGINAL_ALLOWED_ROOTS: list[str] = DEFAULT_ALLOWED_ROOTS[:]


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace with some files."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "hello.txt").write_text("hello world")
    (ws / "data.csv").write_text("a,b,c\n1,2,3")
    sub = ws / "subdir"
    sub.mkdir()
    (sub / "nested.txt").write_text("nested content")
    return ws


def _make_registry(workspace: Path) -> ToolRegistry:
    registry = ToolRegistry()
    register_filesystem_tools(registry, allowed_roots=[str(workspace)])
    register_development_tools(registry)
    return registry


@pytest.fixture(autouse=True)
def _restore_allowed_roots():
    """Always restore DEFAULT_ALLOWED_ROOTS after each test."""
    yield
    DEFAULT_ALLOWED_ROOTS[:] = _ORIGINAL_ALLOWED_ROOTS


@pytest.fixture
def _allow_workspace(tmp_workspace: Path):
    """Temporarily add workspace to allowed roots for development tools."""
    DEFAULT_ALLOWED_ROOTS[:] = [str(tmp_workspace)]
    yield tmp_workspace


# ---------------------------------------------------------------------------
# file_delete tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_delete_success(tmp_workspace: Path) -> None:
    registry = _make_registry(tmp_workspace)
    result = await registry.execute(
        "file_delete", {"path": str(tmp_workspace / "hello.txt")}, approved=True,
    )
    assert result.get("result") == "deleted"
    assert result.get("verified_absent") is True
    assert not (tmp_workspace / "hello.txt").exists()


@pytest.mark.asyncio
async def test_file_delete_not_found(tmp_workspace: Path) -> None:
    registry = _make_registry(tmp_workspace)
    result = await registry.execute(
        "file_delete", {"path": str(tmp_workspace / "nonexistent.txt")}, approved=True,
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_file_delete_no_path(tmp_workspace: Path) -> None:
    registry = _make_registry(tmp_workspace)
    # path is required, so passing empty string triggers the handler's error check
    result = await registry.execute("file_delete", {"path": ""}, approved=True)
    assert "error" in result


@pytest.mark.asyncio
async def test_file_delete_protected_file(tmp_workspace: Path) -> None:
    protected = tmp_workspace / "pyproject.toml"
    protected.write_text("[project]\nname='test'")
    registry = _make_registry(tmp_workspace)
    result = await registry.execute(
        "file_delete", {"path": str(protected)}, approved=True,
    )
    assert "error" in result
    assert "Protected" in result["error"]


@pytest.mark.asyncio
async def test_file_delete_directory_fails(tmp_workspace: Path) -> None:
    registry = _make_registry(tmp_workspace)
    result = await registry.execute(
        "file_delete", {"path": str(tmp_workspace / "subdir")}, approved=True,
    )
    assert "error" in result
    assert "Not a file" in result["error"]


# ---------------------------------------------------------------------------
# file_copy tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_copy_success(tmp_workspace: Path) -> None:
    registry = _make_registry(tmp_workspace)
    dst = tmp_workspace / "hello_copy.txt"
    result = await registry.execute(
        "file_copy",
        {"source": str(tmp_workspace / "hello.txt"), "destination": str(dst)},
        approved=True,
    )
    assert result.get("result") == "copied"
    assert result.get("verified_exists") is True
    assert result.get("same_size") is True
    assert dst.read_text() == "hello world"


@pytest.mark.asyncio
async def test_file_copy_source_not_found(tmp_workspace: Path) -> None:
    registry = _make_registry(tmp_workspace)
    result = await registry.execute(
        "file_copy",
        {
            "source": str(tmp_workspace / "nonexistent.txt"),
            "destination": str(tmp_workspace / "copy.txt"),
        },
        approved=True,
    )
    assert "error" in result
    assert "Source not found" in result["error"]


@pytest.mark.asyncio
async def test_file_copy_no_source(tmp_workspace: Path) -> None:
    registry = _make_registry(tmp_workspace)
    result = await registry.execute(
        "file_copy", {"source": "", "destination": "/tmp/copy.txt"}, approved=True,
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_file_copy_no_destination(tmp_workspace: Path) -> None:
    registry = _make_registry(tmp_workspace)
    result = await registry.execute(
        "file_copy",
        {"source": str(tmp_workspace / "hello.txt"), "destination": ""},
        approved=True,
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_file_copy_directory_fails(tmp_workspace: Path) -> None:
    registry = _make_registry(tmp_workspace)
    result = await registry.execute(
        "file_copy",
        {
            "source": str(tmp_workspace / "subdir"),
            "destination": str(tmp_workspace / "copy"),
        },
        approved=True,
    )
    assert "error" in result
    assert "not a file" in result["error"].lower()


# ---------------------------------------------------------------------------
# generate_tests tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_tests_creates_file(tmp_workspace: Path, _allow_workspace: Path) -> None:
    src = tmp_workspace / "mymodule.py"
    src.write_text("def hello():\n    return 'world'\n")
    registry = _make_registry(tmp_workspace)
    result = await registry.execute(
        "generate_tests",
        {"module_path": str(src), "framework": "pytest"},
        approved=True,
    )
    assert result.get("result") == "created"
    assert result.get("verified_exists") is True
    assert result.get("framework") == "pytest"
    test_file = Path(result["test_file"])
    assert test_file.exists()


@pytest.mark.asyncio
async def test_generate_tests_unittest_framework(tmp_workspace: Path, _allow_workspace: Path) -> None:
    src = tmp_workspace / "util.py"
    src.write_text("def add(a, b):\n    return a + b\n")
    registry = _make_registry(tmp_workspace)
    result = await registry.execute(
        "generate_tests",
        {"module_path": str(src), "framework": "unittest"},
        approved=True,
    )
    assert result.get("framework") == "unittest"
    assert result.get("verified_exists") is True
    content = Path(result["test_file"]).read_text()
    assert "unittest" in content


@pytest.mark.asyncio
async def test_generate_tests_no_module_path(tmp_workspace: Path, _allow_workspace: Path) -> None:
    registry = _make_registry(tmp_workspace)
    result = await registry.execute(
        "generate_tests",
        {"module_path": "", "framework": "pytest"},
        approved=True,
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_generate_tests_module_not_found(tmp_workspace: Path, _allow_workspace: Path) -> None:
    registry = _make_registry(tmp_workspace)
    result = await registry.execute(
        "generate_tests",
        {"module_path": str(tmp_workspace / "nonexistent.py"), "framework": "pytest"},
        approved=True,
    )
    assert "error" in result
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_generate_tests_custom_test_name(tmp_workspace: Path, _allow_workspace: Path) -> None:
    src = tmp_workspace / "helper.py"
    src.write_text("def util(): pass\n")
    registry = _make_registry(tmp_workspace)
    result = await registry.execute(
        "generate_tests",
        {"module_path": str(src), "framework": "pytest", "test_name": "my_custom_test.py"},
        approved=True,
    )
    assert result.get("verified_exists") is True
    assert "my_custom_test.py" in result["test_file"]
