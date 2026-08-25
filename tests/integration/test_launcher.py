"""Tests for the application launcher module."""

import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.launch import (
    DEFAULT_PORT,
    READINESS_TIMEOUT,
    _is_port_open,
    _wait_for_readiness,
    main,
)


def test_is_port_open_when_free():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # Port was released after bind test
    assert _is_port_open(port) is False


def test_is_port_open_when_used():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.listen(1)
        assert _is_port_open(port) is True


def test_wait_for_readiness_timeout():
    # Port that nothing is listening on
    assert _wait_for_readiness(59999, timeout=0.5) is False


def test_launch_no_browser_flag():
    with patch("scripts.launch._wait_for_readiness", return_value=True):
        with patch("scripts.launch._is_port_open", return_value=False):
            with patch("subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_proc.poll.return_value = None
                mock_proc.wait.return_value = None
                mock_popen.return_value = mock_proc
                with patch("sys.argv", ["launch.py", "--no-browser"]):
                    result = main()
                assert result == 0


def test_launch_port_in_use():
    with patch("scripts.launch._is_port_open", return_value=True):
        with patch("sys.argv", ["launch.py"]):
            result = main()
        assert result == 1


def test_launch_readiness_timeout():
    with patch("scripts.launch._is_port_open", return_value=False):
        with patch("scripts.launch._wait_for_readiness", return_value=False):
            with patch("subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_proc.poll.return_value = None
                mock_proc.wait.return_value = None
                mock_proc.terminate.return_value = None
                mock_popen.return_value = mock_proc
                with patch("sys.argv", ["launch.py", "--no-browser"]):
                    result = main()
                assert result == 1


def test_launch_script_exists():
    assert Path("scripts/launch.py").is_file()


def test_launch_bat_exists():
    assert Path("scripts/launch.bat").is_file()


def test_default_port():
    assert DEFAULT_PORT == 8000


def test_readiness_timeout_value():
    assert READINESS_TIMEOUT == 30
