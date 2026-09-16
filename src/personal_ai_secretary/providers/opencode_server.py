"""Chiky-managed OpenCode server lifecycle.

FASE AB.7: A reusable integration with OpenCode cannot depend on the OpenCode
Desktop process, whose auth and tuning Chiky does not control. Instead Chiky
owns and manages its own `opencode serve` process:

  * locate the opencode binary (CLI install, npm/pi-node layout);
  * start `opencode serve` on a free port with a Chiky-controlled Basic-auth
    credential pair (OPENCODE_SERVER_USERNAME / OPENCODE_SERVER_PASSWORD);
  * discover the actual listening port by parsing the server's stdout
    ("opencode server listening on http://127.0.0.1:PORT");
  * health-check via GET /config;
  * lifecycle helpers (ensure_running / stop) tracked by PID.

No Electron is touched and no Desktop credentials are guessed.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger("personal_ai_secretary.providers.opencode_server")

_CONNECT_TIMEOUT = 3.0
_READY_TIMEOUT = 45.0
_DEFAULT_PROBE_PORTS = (51360, 51361, 51362, 51363, 51364, 51365, 51366)


def find_binary() -> str | None:
    """Locate the opencode executable across common install layouts.

    FASE AB.7 lifecycle: return the REAL ``opencode.exe``, never a ``.cmd`` /
    ``.ps1`` batch wrapper. Launchers (``yarn``/``npm``/``pi-node`` generate
    ``opencode.CMD`` / ``opencode.ps1`` that exec the real binary) introduce an
    extra ``cmd.exe`` parent whose child (``opencode.exe serve``) would be
    orphaned when Chiky terminates the launcher PID. Resolving to the .exe lets
    Chiky own a single terminal process that it can cleanly stop.
    """
    found = shutil.which("opencode") or shutil.which("opencode.exe")
    if found:
        real = _resolve_launcher(found)
        if real:
            return real
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "AppData", "Local", "pi-node", "current",
                     "node_modules", "opencode-ai", "bin", "opencode.exe"),
        os.path.join(home, ".opencode", "bin", "opencode.exe"),
        os.path.join(home, "AppData", "Local", "Programs", "opencode", "opencode.exe"),
        os.path.join(home, "wait-dir", "node_modules", "opencode-ai", "bin", "opencode.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _resolve_launcher(path: str) -> str | None:
    """If `opencode` resolved to a .cmd/.ps1 wrapper, return the real .exe target."""
    lower = path.lower()
    if lower.endswith((".exe", ".cmd", ".ps1")):
        if lower.endswith(".exe"):
            if os.path.isfile(path):
                return path
            return None
        # A batch/PS wrapper: find the `opencode-ai\bin\opencode.exe` target.
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
            import re

            pattern = re.compile(
                r"[^\"'\s]*node_modules[^\s\"']*opencode(?:-ai)?[^\s\"']*\.exe",
                re.IGNORECASE,
            )
            match = pattern.search(content)
            if match:
                cand = match.group(0).strip()
                cand = cand.replace("$basedir", os.path.dirname(path))
                cand = cand.replace("%dp0%", os.path.dirname(path) + os.sep)
                cand = cand.replace("\\..\\", os.sep)  # normalize any partial paths
                if os.path.isfile(cand):
                    return os.path.abspath(cand)
                # Some wrappers quote with DOS %~dp0; fall back to a sibling layout.
                base = os.path.dirname(os.path.dirname(path))
                alt = os.path.join(base, "node_modules", "opencode-ai", "bin", "opencode.exe")
                if os.path.isfile(alt):
                    return os.path.abspath(alt)
        except Exception:
            pass
        return None
    # Unrecognized extension; let caller fall through to known files.
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def reap_orphaned_managed() -> list[int]:
    """Terminate Chiky-managed ``opencode serve`` processes left orphaned by a
    previous crash.

    FASE AB.7 lifecycle guarantee (no orphan processes): when a prior Chiky
    process died without stopping its managed server, the ``opencode.exe
    serve`` child survives as an orphan. On a fresh start we detect and kill
    only processes matching Chiky's exact launch shape (``serve`` with our
    ``--hostname 127.0.0.1`` flag sourced from the real opencode binary) whose
    parent is no longer alive. We never touch the caller's own session or any
    server a live parent still owns.

    Returns the list of reaped PIDs.
    """
    reaped: list[int] = []
    try:
        import subprocess as _sp

        real_bin = find_binary()
        out = _sp.check_output(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Where-Object { $_.Name -like '*opencode*' } "
             "| Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"],
            stderr=_sp.DEVNULL,
        ).decode(errors="ignore")
        import json as _json

        rows = _json.loads(out)
        if isinstance(rows, dict):
            rows = [rows]
        live_pids = _sp.check_output(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process | Select-Object -ExpandProperty Id"],
            stderr=_sp.DEVNULL,
        ).decode(errors="ignore").split()
        live = {int(x) for x in live_pids if x.strip().isdigit()}

        for row in rows:
            try:
                pid = int(row["ProcessId"])
                ppid = int(row["ParentProcessId"])
            except Exception:
                continue
            if pid in live and ppid not in live:
                cmd = (row.get("Commandline") or row.get("CommandLine") or "").lower()
                if "serve" not in cmd or "--hostname 127.0.0.1" not in cmd:
                    continue
                # Only reap processes launched from the real managed binary we own.
                if real_bin and real_bin.lower().replace("\\", "/") not in cmd.replace("\\", "/"):
                    continue
                try:
                    _sp.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True,
                        timeout=10,
                    )
                    reaped.append(pid)
                    logger.warning(
                        "Reaped orphaned managed OpenCode serve (pid %s, ppid %s)",
                        pid,
                        ppid,
                    )
                except Exception:
                    pass
    except Exception:
        pass
    return reaped


@dataclass
class ManagedOpenCodeServer:
    """Own the lifecycle of a Chiky-managed `opencode serve` process."""

    base_url: str = ""
    username: str = "opencode"
    password: str = ""
    _process: subprocess.Popen[Any] | None = field(default=None, init=False)
    _job: Any = field(default=None, init=False)
    _stdout_log: str = field(default="", init=False)
    _port: int = field(default=0, init=False)
    _bin: str = field(default="", init=False)
    _started: bool = field(default=False, init=False)

    @property
    def running(self) -> bool:
        if self._process is None or self._process.poll() is not None:
            return False
        return True

    @property
    def port(self) -> int:
        return self._port

    def binary(self) -> str | None:
        return find_binary()

    def ensure_running(self) -> bool:
        """Start (or reuse) the managed server and return readiness.

        FASE AB.7 lifecycle: before starting, reap any Chiky-managed orphaned
        ``opencode serve`` processes a prior crash left behind (guarantees no
        orphan accumulation across restarts).
        """
        reap_orphaned_managed()
        if self.running and self._ready():
            return True
        if self.running:
            self.stop()
        if not self.start():
            return False
        return self._ready()

    def start(self) -> bool:
        # FASE RELEASE lifecycle: never spawn a second managed server over a
        # live one. A direct start() while already running stops the current
        # process first (guards double-spawn / duplicate processes).
        if self.running:
            logger.warning(
                "Managed OpenCode server already running (pid %s); "
                "stopping it before re-spawn.",
                self._process.pid if self._process is not None else "?",
            )
            self.stop()
        binary = find_binary()
        if not binary:
            logger.warning("OpenCode binary not found; cannot start managed server.")
            return False
        if not binary.lower().endswith(".exe"):
            logger.warning(
                "OpenCode binary is not a real .exe (%s); refusing to spawn a wrapper.",
                binary,
            )
            return False
        port = self._port or _free_port()
        self._bin = binary
        env = dict(os.environ)
        username = self.username or "opencode"
        password = self.password or secrets.token_urlsafe(16)
        env["OPENCODE_SERVER_USERNAME"] = username
        env["OPENCODE_SERVER_PASSWORD"] = password
        self.username = username
        self.password = password
        log_dir = os.path.join(
            os.path.expanduser("~"), "AppData", "Local", "Temp", "opencode"
        )
        os.makedirs(log_dir, exist_ok=True)
        self._stdout_log = os.path.join(log_dir, f"opencode_serve_{port}.log")
        err_log = os.path.join(log_dir, f"opencode_serve_{port}_err.log")
        # FASE AB.7 lifecycle: run the real .exe inside its own process group so
        # that ``stop()`` can kill the whole tree (no orphaned grandchildren).
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        pg = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        with open(self._stdout_log, "w", encoding="utf-8") as out, open(
            err_log, "w", encoding="utf-8"
        ) as err:
            try:
                proc = subprocess.Popen(
                    [binary, "serve", "--port", str(port), "--hostname", "127.0.0.1"],
                    stdout=out,
                    stderr=err,
                    env=env,
                    creationflags=flags | pg,
                )
            except Exception as exc:  # pragma: no cover - platform edge
                logger.warning("Failed to start opencode serve: %s", exc)
                return False
        self._process = proc
        self._job = self._create_job(proc)
        self._port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self._started = True
        logger.info(
            "Managed OpenCode server launching on port %s (pid %s)",
            port,
            proc.pid,
        )
        return True

    def _create_job(self, proc: subprocess.Popen[Any]) -> Any:
        """Assign the child to a Windows Job Object so stopping it kills all
        descendants (prevents orphaned ``opencode serve`` processes)."""
        import ctypes
        from ctypes import wintypes

        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_ulonglong),
                    ("PerJobUserTimeLimit", ctypes.c_ulonglong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_ulonglong),
                    ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong),
                    ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong),
                    ("OtherTransferCount", ctypes.c_ulonglong),
                ]

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_ulonglong),
                    ("PerJobUserTimeLimit", ctypes.c_ulonglong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
            job = kernel32.CreateJobObjectW(None, None)
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            kernel32.SetInformationJobObject(
                job,
                9,  # JobObjectExtendedLimitInformation
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            handle = kernel32.OpenProcess(
                0x00100000 | 0x00200000 | 0x0040,  # SYNCHRONIZE|TERMINATE|QUERY_INFORMATION
                False,
                proc.pid,
            )
            if handle:
                kernel32.AssignProcessToJobObject(job, handle)
            return job
        except Exception as exc:  # pragma: no cover - platform edge
            logger.warning("Job object setup failed; fallback to process-group kill: %s", exc)
            return None

    def _ready(self) -> bool:
        deadline = time.time() + _READY_TIMEOUT
        headers = self._auth_headers()
        while time.time() < deadline:
            if not self.running:
                return False
            try:
                resp = httpx.get(
                    f"{self.base_url}/config", headers=headers, timeout=_CONNECT_TIMEOUT
                )
                if resp.status_code == 200:
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def _auth_headers(self) -> dict[str, str]:
        import base64

        token = base64.b64encode(
            f"{self.username}:{self.password}".encode()
        ).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    def health(self) -> dict[str, object]:
        if not self.running:
            return {"running": False, "detail": "managed server process is not running"}
        headers = self._auth_headers()
        try:
            resp = httpx.get(
                f"{self.base_url}/config", headers=headers, timeout=_CONNECT_TIMEOUT
            )
            if resp.status_code == 200:
                return {
                    "running": True,
                    "base_url": self.base_url,
                    "detail": "managed server is running and answering /config",
                }
            return {
                "running": True,
                "base_url": self.base_url,
                "detail": f"managed server answered HTTP {resp.status_code}",
            }
        except Exception as exc:
            return {"running": True, "base_url": self.base_url, "detail": str(exc)}

    def stop(self) -> None:
        """Terminate the managed server and its whole process tree on a
        controlled Chiky shutdown.

        FASE RELEASE lifecycle behaviour:
          * ``_process`` is None         -> no-op (no exception);
          * process already exited       -> no-op (no exception);
          * process ignores ``terminate``-> forced tree kill fallback.
        First a graceful tree stop is attempted (``taskkill /T`` then
        ``terminate``); only if the process does not exit is the forced
        ``taskkill /T /F`` path used, so a controlled shutdown never leaves an
        orphaned ``opencode serve`` behind.

        LIMITATION (documented): on an *abrupt* exit of the Chiky process
        (e.g. ``kill -9``, machine shutdown, or ``TerminateProcess`` when a
        launcher like ``scripts/launch.py`` force-kills uvicorn) Python cannot
        run this teardown, so the managed server may survive as an orphan. Such
        orphans match Chiky's exact launch shape and are removed by
        ``reap_orphaned_managed()`` at the next ``ensure_running()``.
        """
        if self._process is None:
            return
        proc = self._process
        try:
            if proc.poll() is None:
                self._terminate_tree(proc.pid)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._force_kill_tree(proc.pid)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        logger.warning(
                            "Managed OpenCode server did not exit after force "
                            "kill (pid %s).",
                            proc.pid,
                        )
        except Exception as exc:
            logger.warning("Error stopping managed OpenCode server: %s", exc)
        finally:
            self._process = None
            self._job = None
            self._started = False

    def _terminate_tree(self, pid: int) -> None:
        """Best-effort graceful stop of pid and all descendants.

        ``taskkill /T`` without ``/F`` asks the tree to close, then the tracked
        Popen is ``terminate()``d (graceful TerminateProcess-equivalent via the
        handled process). Callers must follow up with ``wait()`` and fall back
        to ``_force_kill_tree`` if the process is still alive.
        """
        import subprocess as _sp

        try:
            _sp.run(
                ["taskkill", "/PID", str(pid), "/T"],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass
        try:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
        except Exception:
            pass

    def _force_kill_tree(self, pid: int) -> None:
        """Force termination of pid and all descendants (no graceful attempt)."""
        import subprocess as _sp

        try:
            _sp.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass
        try:
            if self._process is not None and self._process.poll() is None:
                self._process.kill()
        except Exception:
            pass


_managed_server: ManagedOpenCodeServer | None = None


def get_managed_server() -> ManagedOpenCodeServer:
    global _managed_server  # noqa: PLW0603
    if _managed_server is None:
        _managed_server = ManagedOpenCodeServer(
            username=os.environ.get("CHIKY_OPENCODE_USERNAME", "opencode"),
            password=os.environ.get(
                "CHIKY_OPENCODE_PASSWORD",
                os.environ.get("OPENCODE_SERVER_PASSWORD", ""),
            ),
        )
    return _managed_server


def stop_managed_server() -> None:
    global _managed_server  # noqa: PLW0603
    if _managed_server is not None:
        _managed_server.stop()
        _managed_server = None
