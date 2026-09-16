"""Chiky agente — Application Launcher.

Starts the backend server, waits for readiness, opens the browser,
and handles clean shutdown.

Usage:
    python scripts/launch.py [--port PORT] [--no-browser]
"""

from __future__ import annotations

import argparse
import logging
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("chiky-launcher")

DEFAULT_PORT = 8000
READINESS_TIMEOUT = 30
READINESS_POLL_INTERVAL = 0.5


def _is_port_open(port: int) -> bool:
    """Check if a port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def _wait_for_readiness(port: int, timeout: float) -> bool:
    """Poll /health/live until the server responds or timeout."""
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{port}/api/v1/health/live"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError, ConnectionError):
            pass
        time.sleep(READINESS_POLL_INTERVAL)
    return False


def _open_browser(port: int) -> None:
    """Open the default browser to the application URL."""
    url = f"http://127.0.0.1:{port}"
    log.info("Opening browser at %s", url)
    webbrowser.open(url)


def _open_server_log(project_root: Path):
    """Open an append-only file for the server's stdout/stderr.

    Writes to a real file instead of an undrained pipe: a pipe whose buffer
    fills would block the uvicorn process writing its logs, leaving the API
    unresponsive (UI hangs, messages never answered).
    """
    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)
    return open(log_dir / "backend.log", "ab")


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch Chiky agente")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to run the server on (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically open the browser",
    )
    args = parser.parse_args()

    port = args.port

    # Check if port is already in use
    if _is_port_open(port):
        log.error(
            "Port %d is already in use. Another instance may be running.\n"
            "Close the other instance or use: python scripts/launch.py --port PORT",
            port,
        )
        return 1

    # Find project root (parent of scripts/)
    project_root = Path(__file__).resolve().parent.parent
    src_dir = project_root / "src"

    log.info("Starting Chiky agente on port %d...", port)

    # Start the server as a subprocess
    server_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "personal_ai_secretary.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "info",
        ],
        cwd=str(project_root),
        env={**{"PYTHONPATH": str(src_dir)}, **{k: v for k, v in __import__("os").environ.items()}},
        stdout=_open_server_log(project_root),
        stderr=subprocess.STDOUT,
    )

    try:
        log.info("Waiting for server to be ready...")
        if not _wait_for_readiness(port, READINESS_TIMEOUT):
            log.error(
                "Server did not become ready within %d seconds.\n"
                "Check the server output above for errors.",
                READINESS_TIMEOUT,
            )
            server_proc.terminate()
            server_proc.wait(timeout=5)
            return 1

        log.info("Server is ready!")

        if not args.no_browser:
            _open_browser(port)

        log.info("Application is running. Press Ctrl+C to stop.")
        server_proc.wait()

    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        if server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()
                server_proc.wait()
        log.info("Server stopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
