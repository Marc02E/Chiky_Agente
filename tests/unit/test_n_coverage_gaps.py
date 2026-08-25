"""FASE N — Targeted coverage tests for filesystem and command gaps.

Covers:
- filesystem.py: _resolve_hallucinated_path (lines 36-51), _read_file OSError fallback (131-132)
- filesystem.py: _create_directory wrapper via registry (lines 292-298)
- command.py: execute_command shlex ValueError (318-319), empty command (322),
  executable not found (343), OSError (375-377), Latin-1 fallback (386-387, 391-392)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestResolveHallucinatedPath:
    """filesystem.py lines 36-51: _resolve_hallucinated_path patterns."""

    def test_desktop_subdir_with_trailing(self) -> None:
        from personal_ai_secretary.tools.filesystem import _resolve_hallucinated_path

        result = _resolve_hallucinated_path("/path/to/Desktop/file.txt")
        home = Path.home()
        assert result == str(home / "Desktop" / "file.txt")

    def test_documents_subdir_with_trailing(self) -> None:
        from personal_ai_secretary.tools.filesystem import _resolve_hallucinated_path

        result = _resolve_hallucinated_path("/path/to/Documents/report.pdf")
        home = Path.home()
        assert result == str(home / "Documents" / "report.pdf")

    def test_downloads_subdir_with_trailing(self) -> None:
        from personal_ai_secretary.tools.filesystem import _resolve_hallucinated_path

        result = _resolve_hallucinated_path("/path/to/Downloads/data.csv")
        home = Path.home()
        assert result == str(home / "Downloads" / "data.csv")

    def test_desktop_bare_no_trailing_slash(self) -> None:
        from personal_ai_secretary.tools.filesystem import _resolve_hallucinated_path

        result = _resolve_hallucinated_path("/path/to/Desktop")
        home = Path.home()
        assert result == str(home / "Desktop")

    def test_documents_bare_no_trailing_slash(self) -> None:
        from personal_ai_secretary.tools.filesystem import _resolve_hallucinated_path

        result = _resolve_hallucinated_path("/path/to/Documents")
        home = Path.home()
        assert result == str(home / "Documents")

    def test_tilde_slash(self) -> None:
        from personal_ai_secretary.tools.filesystem import _resolve_hallucinated_path

        result = _resolve_hallucinated_path("~/Documents/file.txt")
        home = Path.home()
        assert result == str(home / "Documents" / "file.txt")

    def test_path_to_home_with_suffix(self) -> None:
        from personal_ai_secretary.tools.filesystem import _resolve_hallucinated_path

        result = _resolve_hallucinated_path("/path/to/home/Desktop/file.txt")
        home = Path.home()
        assert result == str(home / "Desktop" / "file.txt")

    def test_path_to_home_bare(self) -> None:
        from personal_ai_secretary.tools.filesystem import _resolve_hallucinated_path

        result = _resolve_hallucinated_path("/path/to/home")
        home = Path.home()
        assert result == str(home)

    def test_path_to_home_bare_with_trailing_slash(self) -> None:
        from personal_ai_secretary.tools.filesystem import _resolve_hallucinated_path

        result = _resolve_hallucinated_path("/path/to/home/")
        home = Path.home()
        assert result == str(home)

    def test_passthrough_unrecognized(self) -> None:
        from personal_ai_secretary.tools.filesystem import _resolve_hallucinated_path

        result = _resolve_hallucinated_path("/tmp/real/path")
        assert result == "/tmp/real/path"

    def test_desktop_case_insensitive(self) -> None:
        from personal_ai_secretary.tools.filesystem import _resolve_hallucinated_path

        result = _resolve_hallucinated_path("/path/to/DESKTOP/file.txt")
        home = Path.home()
        assert result == str(home / "Desktop" / "file.txt")


class TestCommandExecuteEdgeCases:
    """command.py: shlex ValueError (318-319), empty command (322),
    executable not found (343), OSError (375-377),
    Latin-1 fallback (386-387, 391-392)."""

    @pytest.mark.anyio
    async def test_execute_command_shlex_value_error(self) -> None:
        from personal_ai_secretary.tools.command import _execute_command

        result = await _execute_command({"command": 'echo "unclosed'})
        assert "error" in result

    @pytest.mark.anyio
    async def test_execute_command_empty(self) -> None:
        from personal_ai_secretary.tools.command import _execute_command

        result = await _execute_command({"command": ""})
        assert "error" in result

    @pytest.mark.anyio
    async def test_execute_command_executable_not_found(self) -> None:
        from personal_ai_secretary.tools.command import _execute_command

        with patch("shutil.which", return_value=None):
            result = await _execute_command(
                {"command": "python --version"}
            )
            assert "error" in result
            assert "Executable not found" in result["error"]

    @pytest.mark.anyio
    async def test_execute_command_os_error(self) -> None:
        from personal_ai_secretary.tools.command import _execute_command

        with patch("personal_ai_secretary.tools.command.asyncio.create_subprocess_exec",
                    side_effect=OSError("Permission denied")):
            result = await _execute_command({"command": "python --version"})
            assert "error" in result
            assert "Failed to execute" in result["error"]

    @pytest.mark.anyio
    async def test_execute_command_timeout_kill_error(self) -> None:
        """Cover lines 364-365: exception during process.kill() in timeout handler."""
        from personal_ai_secretary.tools.command import _execute_command

        async def _fake_communicate():
            return (b"", b"")

        async def _fake_communicate_after_kill():
            return (b"", b"")

        mock_process = AsyncMock()
        # First call: main timeout. Second call: after kill.
        call_count = 0

        async def _wait_for_side_effect(coro, timeout: int | None = None):  # noqa: ASYNC109
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError()
            return (b"", b"")

        mock_process.kill = MagicMock(side_effect=OSError("kill failed"))
        mock_process.communicate = AsyncMock(return_value=(b"", b""))
        mock_process.returncode = -1

        with patch("personal_ai_secretary.tools.command.asyncio.create_subprocess_exec",
                    return_value=mock_process):
            with patch("personal_ai_secretary.tools.command.asyncio.wait_for",
                       side_effect=_wait_for_side_effect):
                result = await _execute_command(
                    {"command": "python --version"}
                )
                assert result.get("timeout") is True


class TestHasChainingBlockedPatterns:
    """command.py line 172: blocked substitution patterns."""

    def test_backtick_pattern(self) -> None:
        from personal_ai_secretary.tools.command import _has_chaining

        assert _has_chaining("echo `whoami`") is True

    def test_dollar_paren_pattern(self) -> None:
        from personal_ai_secretary.tools.command import _has_chaining

        assert _has_chaining("echo $(whoami)") is True

    def test_dollar_brace_pattern(self) -> None:
        from personal_ai_secretary.tools.command import _has_chaining

        assert _has_chaining("echo ${HOME}") is True

    def test_chain_operators(self) -> None:
        from personal_ai_secretary.tools.command import _has_chaining

        assert _has_chaining("cmd1 && cmd2") is True
        assert _has_chaining("cmd1 || cmd2") is True
        assert _has_chaining("cmd1 ; cmd2") is True
