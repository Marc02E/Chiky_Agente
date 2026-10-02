"""FASE AB.6 — Filesystem Security: workspace containment at the tool layer.

AB.5 found a REAL path escape: an agent writing a relative path like
``../../ola.txt`` resolved it against the process CWD (the repo) and the
containment root was the user's home, so the write landed OUTSIDE the
workspace. These tests prove the backend/tool layer now anchors relative
paths to the active workspace / WORKSPACE_ROOT and rejects any escape:

    * traversal (../, ../../, sub/../../)
    * absolute paths outside the workspace
    * Windows absolute paths
    * symlink/junction escapes (when the OS allows creating them)

No frontend assumptions — every assertion goes through the real tool layer.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from personal_ai_secretary.tools.builtin import default_tool_registry
from personal_ai_secretary.tools.filesystem import (
    DEFAULT_ALLOWED_ROOTS,
    routing_workspace,
)
from personal_ai_secretary.tools.registry import ToolError


@pytest.fixture(autouse=True)
def _clean_roots() -> list[str]:
    """Save/restore the module rollover so the default (empty) applies."""
    saved = DEFAULT_ALLOWED_ROOTS[:]
    DEFAULT_ALLOWED_ROOTS[:] = []
    yield saved
    DEFAULT_ALLOWED_ROOTS[:] = saved


@pytest.fixture()
def registry():
    return default_tool_registry()


async def _create(registry, workspace: Path, path: str) -> dict:
    with routing_workspace(str(workspace)):
        return await registry.execute(
            "create_file", {"path": path, "content": "x"}, approved=True
        )


def _assert_access_denied(exc: ToolError) -> None:
    assert "Access denied" in str(exc) or "outside" in str(exc)


class TestRelativeTraversal:
    @pytest.mark.asyncio
    async def test_simple_parent_escape_rejected(self, registry, tmp_path: Path) -> None:
        """'../escape.txt' must be rejected and never written outside."""
        with pytest.raises(ToolError) as err:
            await _create(registry, tmp_path, "../escape.txt")
        _assert_access_denied(err.value)
        assert not (tmp_path.parent / "escape.txt").exists()

    @pytest.mark.asyncio
    async def test_double_parent_escape_rejected(self, registry, tmp_path: Path) -> None:
        """AB.5 reproducer: '../../ola.txt' must be rejected."""
        with pytest.raises(ToolError) as err:
            await _create(registry, tmp_path, "../../ola.txt")
        _assert_access_denied(err.value)
        assert not (tmp_path.parent / "ola.txt").exists()
        assert not (tmp_path.parent.parent / "ola.txt").exists()

    @pytest.mark.asyncio
    async def test_nested_traversal_rejected(self, registry, tmp_path: Path) -> None:
        with pytest.raises(ToolError):
            await _create(registry, tmp_path, "sub/dir/../../../evil.txt")

    @pytest.mark.asyncio
    async def test_relative_write_inside_workspace_succeeds(
        self, registry, tmp_path: Path
    ) -> None:
        """A bare relative filename must land INSIDE the workspace."""
        result = await _create(registry, tmp_path, "hola.txt")
        assert "error" not in result
        assert result["verified_exists"] is True
        target = tmp_path / "hola.txt"
        assert target.exists()
        assert str(Path(result["path"])) == str(target.resolve())


class TestAbsolutePaths:
    @pytest.mark.asyncio
    async def test_absolute_outside_workspace_rejected(
        self, registry, tmp_path: Path
    ) -> None:
        outside = tmp_path.parent / "outside.txt"
        with pytest.raises(ToolError) as err:
            await _create(registry, tmp_path, str(outside))
        _assert_access_denied(err.value)
        assert not outside.exists()

    @pytest.mark.asyncio
    async def test_windows_absolute_system32_rejected(
        self, registry, tmp_path: Path
    ) -> None:
        if not sys.platform.startswith("win"):
            pytest.skip("Windows absolute-path semantics only apply on Windows")
        system_path = "C:\\Windows\\System32\\chiky_evil.txt"
        with pytest.raises(ToolError):
            await _create(registry, tmp_path, system_path)

    @pytest.mark.asyncio
    async def test_windows_style_path_rejected_nonwin(
        self, registry, tmp_path: Path
    ) -> None:
        """Even on non-Windows, a drive-qualified absolute path leads outside."""
        if sys.platform.startswith("win"):
            pytest.skip("covered by the Windows-specific test")
        with pytest.raises(ToolError):
            await _create(registry, tmp_path, "C:\\Windows\\System32\\evil.txt")


class TestOtherFileTools:
    @pytest.mark.asyncio
    async def test_read_file_traversal_rejected(self, registry, tmp_path: Path) -> None:
        (tmp_path / "ok.txt").write_text("hi")
        with routing_workspace(str(tmp_path)):
            with pytest.raises(ToolError):
                await registry.execute("read_file", {"path": "../ok.txt"})

    @pytest.mark.asyncio
    async def test_modify_file_traversal_rejected(self, registry, tmp_path: Path) -> None:
        outside = tmp_path.parent / "victim.txt"
        outside.write_text("old")
        with routing_workspace(str(tmp_path)):
            result = await registry.execute(
                "modify_file",
                {"path": "../victim.txt", "search": "old", "replacement": "new"},
                approved=True,
            )
        assert "Access denied" in str(result.get("error", ""))
        assert outside.read_text() == "old"


class TestDefaultRootsNoWorkspace:
    @pytest.mark.asyncio
    async def test_relative_escape_rejected_without_active_workspace(
        self, registry, tmp_path: Path
    ) -> None:
        """Even with no pinned roots and no active workspace, a relative
        escape must be rejected because relative paths anchor to WORKSPACE_ROOT
        (never to the process CWD)."""
        with pytest.raises(ToolError) as err:
            await registry.execute(
                "create_file", {"path": "../../globally_forbidden.txt", "content": "x"},
                approved=True,
            )
        _assert_access_denied(err.value)
        # Nothing created at the CWD-relative sibling either.
        cwd_sibling = Path.cwd().parent / "globally_forbidden.txt"
        assert not cwd_sibling.exists()


class TestSymlinkEscape:
    @pytest.mark.asyncio
    async def test_symlink_dir_escape_rejected(self, registry, tmp_path: Path) -> None:
        """A symlink inside the workspace pointing outside the workspace must
        not let the agent escape (Path.resolve follows the link)."""
        outside = tmp_path.parent / "real_outside"
        outside.mkdir(exist_ok=True)
        link = tmp_path / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, PermissionError, NotImplementedError):
            pytest.skip("symlink creation is not permitted in this environment")

        with routing_workspace(str(tmp_path)):
            with pytest.raises(ToolError):
                await registry.execute(
                    "create_file",
                    {"path": "linked/evil.txt", "content": "x"},
                    approved=True,
                )
        assert not (outside / "evil.txt").exists()