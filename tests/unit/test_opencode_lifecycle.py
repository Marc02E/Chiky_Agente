"""FASE RELEASE — Managed OpenCode lifecycle unit tests.

Prove the controlled-shutdown guarantees with NO inference and NO mocks:

  1. stop() cleanly terminates a running managed process (controlled shutdown);
  2. stop() is a no-op when the process already exited (no exception);
  3. stop() is a no-op when there is no process (cleanup over None, no exception);
  4. start() while already running stops the previous process (no duplicate
     spawn / no leaked server) — requires a real opencode binary and runs only
     when CHIKY_ALLOW_LIVE_INTEGRATION=1.

Uses real short-lived child processes (a sleeping python), not simulated ones.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from personal_ai_secretary.providers.opencode_server import (
    ManagedOpenCodeServer,
    find_binary,
)

_PG = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _spawn_sleeper(seconds: int = 600) -> subprocess.Popen:
    """A real long-lived child process that Chiky can stop."""
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW | _PG,
    )


def test_controlled_shutdown_stops_managed_process() -> None:
    server = ManagedOpenCodeServer()
    proc = _spawn_sleeper()
    server._process = proc
    assert proc.poll() is None, "sleeper should be running before stop()"
    server.stop()
    assert server._process is None, "tracked process handle must be cleared"
    assert proc.poll() is not None, "managed process must be terminated by stop()"


def test_stop_on_already_terminated_process_is_noop() -> None:
    proc = _spawn_sleeper()
    proc.kill()
    proc.wait(timeout=10)
    assert proc.poll() is not None
    server = ManagedOpenCodeServer()
    server._process = proc
    server.stop()  # must not raise
    assert server._process is None
    assert proc.poll() is not None


def test_stop_with_no_process_is_noop() -> None:
    server = ManagedOpenCodeServer()
    assert server._process is None
    server.stop()  # must not raise (cleanup over process=None)
    assert server._process is None


@pytest.mark.skipif(
    not os.environ.get("CHIKY_ALLOW_LIVE_INTEGRATION"),
    reason="requires a live managed OpenCode server",
)
def test_start_while_running_stops_previous_process() -> None:
    binary = find_binary()
    if not binary or not binary.lower().endswith(".exe"):
        pytest.skip("real opencode.exe not available")
    server = ManagedOpenCodeServer()
    assert server.start(), "managed server did not start"
    first = server._process
    assert first is not None and first.poll() is None
    first_pid = first.pid
    assert server.start(), "second start should succeed after stopping the first"
    second = server._process
    time.sleep(0.5)
    assert second is not None and second.poll() is None, "second server must be running"
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {first_pid}"],
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout
    assert str(first_pid) not in out, "previous managed process must not leak"
    server.stop()