"""FASE RELEASE — Controlled shutdown must not leave an orphaned managed server.

The app lifespan finally block calls ``stop_managed_server()`` first. This test
spawns the REAL Chiky-managed ``opencode serve`` (via the provider, NO
inference: ``/config`` health only) and then drives exactly that shutdown hook,
proving the managed process is gone and the module global is cleared — i.e. a
controlled Chiky shutdown leaves no orphaned OpenCode.

Gated on CHIKY_ALLOW_LIVE_INTEGRATION (set in this module) so the unit suite
never spawns processes.
"""

from __future__ import annotations

import asyncio
import os
import subprocess

os.environ["CHIKY_ALLOW_LIVE_INTEGRATION"] = "1"

from personal_ai_secretary.providers.opencode_provider import OpenCodeProvider  # noqa: E402


def _pid_alive(pid: int) -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}"],
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout
    # Locale-independent: the numeric PID only appears when the process exists.
    return str(pid) in out


def test_release_controlled_shutdown_stops_managed_server() -> None:
    import personal_ai_secretary.providers.opencode_server as ocmod

    provider = OpenCodeProvider(model="big-pickle", manage_server=True)
    health = asyncio.run(provider.health())
    assert health.available, f"managed OpenCode server not available: {health.detail}"

    manager = ocmod.get_managed_server()
    assert manager is not None and manager.running
    pid = manager._process.pid
    assert pid and _pid_alive(pid), "managed process should be running"

    # Exactly what the app lifespan finally block does on controlled shutdown.
    ocmod.stop_managed_server()

    assert not _pid_alive(pid), f"managed OpenCode serve (pid {pid}) must not outlive shutdown"
    assert manager._process is None, "tracked process handle must be cleared by stop()"
    assert ocmod._managed_server is None, "module global must be cleared"