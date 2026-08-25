"""FASE K.6 — Secure command execution tool for Chiky.

Provides controlled shell/terminal capability with:
- centralized command allowlist
- chaining/metacharacter detection and blocking
- working directory validation within allowed roots
- subprocess execution with shell=False
- configurable timeout
- smart output truncation (first + last)
- approval-gated HIGH-RISK tool

Security priority: SEGURIDAD > CORRECCIÓN > COMPATIBILIDAD > FUNCIONALIDAD.
"""

import asyncio
import os
import re
import shlex
import time
from pathlib import Path
from typing import Any

from personal_ai_secretary.tools.filesystem import DEFAULT_ALLOWED_ROOTS
from personal_ai_secretary.tools.registry import ToolDefinition, ToolError, ToolRegistry, ToolRisk

# ---------------------------------------------------------------------------
# Command allowlist
# ---------------------------------------------------------------------------

ALLOWED_EXECUTABLES: frozenset[str] = frozenset({
    # Python
    "python", "python3", "py",
    "pytest",
    # Node
    "node", "npm", "npx",
    # Git
    "git",
    # Windows standalone executables
    "where",
})

# Explicitly blocked — never executed regardless of allowlist.
BLOCKED_EXECUTABLES: frozenset[str] = frozenset({
    "powershell", "powershell_ise", "pwsh",
    "cmd",
    "shutdown", "restart", "logoff",
    "format",
    "del", "erase",
    "rmdir", "rd",
    "reg",
    "diskpart",
    "taskkill", "tasklist",
    "net", "netsh",
    "schtasks",
    "sc",
    "icacls",
    "takeown",
    "cipher",
})

# ---------------------------------------------------------------------------
# Chaining / metacharacter detection
# ---------------------------------------------------------------------------

_CHAINING_OPERATORS: frozenset[str] = frozenset({
    "&&", "||", "|", "&",
})

_METACHARACTERS: frozenset[str] = frozenset({
    ">", ">>", "<",
})

# Regex to detect shell metacharacters and chaining operators.  Checks the
# raw command string so that semicolons, pipes, etc. that are part of
# arguments or hidden by shlex quoting are still caught.
_METACHAR_RE = re.compile(
    r"(?:&&|\|\||[|;&<>`])"  # chaining operators, semicolons, redirects, backticks
    r"|\$\("                  # $() command substitution
    r"|\$\{",                # ${} variable expansion
)


def _strip_quoted_portions(command: str) -> str:
    """Remove content inside single and double quotes for safe metachar detection.

    Only characters outside of quotes are preserved. This prevents false positives
    on semicolons and other metacharacters that appear inside quoted arguments
    such as ``python -c "import sys; sys.exit(1)"``.
    """
    result: list[str] = []
    in_single = False
    in_double = False
    for ch in command:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            result.append(ch)
    return "".join(result)

_BLOCKED_SUBSTITUTION_PATTERNS: tuple[str, ...] = (
    "`",      # backtick command substitution
    "$(",     # POSIX command substitution
    "${",     # variable expansion
)


# ---------------------------------------------------------------------------
# Output limits
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT: int = 30
_MAX_STDOUT_CHARS: int = 32_000
_MAX_STDERR_CHARS: int = 16_000


# ---------------------------------------------------------------------------
# Command parsing and validation
# ---------------------------------------------------------------------------


def _extract_executable_name(command: str) -> str:
    """Extract the executable name from a command string.

    Handles both bare names (``python``) and full paths
    (``C:\\Python313\\python.exe``).
    """
    parts = command.split(None, 1)
    if not parts:
        return ""
    token = parts[0]
    basename = Path(token).name
    # Strip .exe / .cmd / .bat extensions for comparison.
    for ext in (".exe", ".cmd", ".bat", ".com"):
        if basename.lower().endswith(ext):
            basename = basename[: -len(ext)]
            break
    return basename.lower()


def _has_chaining(command: str) -> bool:
    """Detect command chaining operators and shell metacharacters.

    Uses regex on the raw command string to catch all dangerous patterns,
    including semicolons and pipes that shlex might parse differently on
    Windows.
    """
    # Malformed quoting — treat as suspicious.
    try:
        shlex.split(command, posix=False)
    except ValueError:
        return True

    # Check for metacharacters outside of quoted portions.
    stripped = _strip_quoted_portions(command)
    if _METACHAR_RE.search(stripped):
        return True

    # Also check shlex tokens for standalone operators.
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        return True

    for token in tokens:
        if token in _CHAINING_OPERATORS or token in _METACHARACTERS:
            return True

    for pattern in _BLOCKED_SUBSTITUTION_PATTERNS:
        if pattern in command:
            return True

    return False


def _has_path_traversal(command: str) -> bool:
    """Detect ``..`` in command arguments that could escape the working dir."""
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        return True
    for token in tokens[1:]:
        if ".." in token:
            return True
    return False


def validate_command(command: str) -> str:
    """Validate a command string against the security policy.

    Returns the original command when valid.  Raises :class:`ToolError`
    when the command is blocked or contains forbidden patterns.
    """
    command = command.strip()
    if not command:
        raise ToolError("Empty command")

    if _has_chaining(command):
        raise ToolError(
            "Command contains chaining or shell metacharacters "
            "(&&, ||, |, ;, >, >>, <, &, backticks, $() ). "
            "Only single commands are allowed."
        )

    if _has_path_traversal(command):
        raise ToolError(
            "Command contains path traversal (..). "
            "Use absolute paths within allowed directories."
        )

    executable = _extract_executable_name(command)

    if executable in BLOCKED_EXECUTABLES:
        raise ToolError(
            f"Executable '{executable}' is blocked. "
            "This command is not allowed for security reasons."
        )

    if executable and executable not in ALLOWED_EXECUTABLES:
        raise ToolError(
            f"Executable '{executable}' is not in the allowlist. "
            f"Allowed: {', '.join(sorted(ALLOWED_EXECUTABLES))}"
        )

    return command


def validate_working_directory(
    working_directory: str | None,
    allowed_roots: list[str] | None = None,
) -> str:
    """Validate that ``working_directory`` exists and is within allowed roots.

    Returns the resolved absolute path on success.  Raises :class:`ToolError`
    on security violations.
    """
    if not working_directory:
        return str(Path.cwd())

    try:
        target = Path(working_directory).resolve()
    except (OSError, ValueError) as exc:
        raise ToolError(f"Invalid path: {exc}") from exc

    if not target.exists():
        raise ToolError(f"Working directory does not exist: {working_directory}")

    if not target.is_dir():
        raise ToolError(f"Working directory is not a directory: {working_directory}")

    # Now validate against allowed roots (after confirming it exists as a dir).
    roots = allowed_roots or DEFAULT_ALLOWED_ROOTS
    target_str = str(target)
    for root in roots:
        root_resolved = str(Path(root).resolve())
        if target_str == root_resolved or target_str.startswith(root_resolved + os.sep):
            return str(target)

    raise ToolError(
        f"Access denied: working directory '{working_directory}' is outside "
        f"allowed directories. Allowed roots: {', '.join(roots)}"
    )


# ---------------------------------------------------------------------------
# Smart output truncation
# ---------------------------------------------------------------------------


def _truncate_output(text: str, max_chars: int) -> tuple[str, bool]:
    """Truncate output, keeping the first and last portions.

    Returns ``(text, was_truncated)``.
    """
    if len(text) <= max_chars:
        return text, False

    marker = "\n\n... [output truncated — showing beginning and end] ...\n\n"
    available = max_chars - len(marker)
    head = available // 2
    tail = available - head
    return text[:head] + marker + text[-tail:], True


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------


async def _execute_command(arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute a single command in a controlled subprocess.

    Returns a result dict with success, exit_code, stdout, stderr,
    duration, and working_directory.  Never raises for expected errors.
    """
    command = str(arguments.get("command", "")).strip()
    working_directory = str(arguments.get("working_directory", "")).strip() or None
    timeout = int(arguments.get("timeout", _DEFAULT_TIMEOUT))

    timeout = max(1, min(timeout, 300))

    # Validate command against security policy.
    try:
        command = validate_command(command)
    except ToolError as exc:
        return {"error": str(exc)}

    # Validate working directory.
    try:
        resolved_wd = validate_working_directory(working_directory)
    except ToolError as exc:
        return {"error": str(exc)}

    # Parse into [executable, *args].
    try:
        parts = shlex.split(command, posix=False)
    except ValueError as exc:
        return {"error": f"Cannot parse command: {exc}"}

    if not parts:
        return {"error": "Empty command after parsing"}

    # Strip surrounding quotes from tokens — shlex.split(posix=False) on
    # Windows retains quote characters which would otherwise be passed
    # literally to the child process (e.g. python -c "print(1)" would
    # receive the quotes, evaluating "print(1)" as a string expression).
    parts = [
        tok.strip("\"'")
        if len(tok) >= 2 and tok[0] in ("'", '"') and tok[-1] == tok[0]
        else tok
        for tok in parts
    ]

    executable = parts[0]
    cmd_args = parts[1:]

    # Resolve executable via PATH (avoids passing raw user strings to Popen).
    import shutil

    resolved_exe = shutil.which(executable)
    if resolved_exe is None:
        return {"error": f"Executable not found in PATH: {executable}"}

    # Execute subprocess with shell=False (async-safe).
    start_time = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            resolved_exe, *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=resolved_wd,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout,
        )
        duration = time.monotonic() - start_time
        exit_code = process.returncode
    except TimeoutError:
        duration = time.monotonic() - start_time
        try:
            process.kill()
            await process.communicate()
        except Exception:  # noqa: BLE001
            pass
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "duration": round(duration, 3),
            "working_directory": resolved_wd,
            "timeout": True,
        }
    except OSError as exc:
        duration = time.monotonic() - start_time
        return {
            "error": f"Failed to execute '{executable}': {exc}",
            "duration": round(duration, 3),
            "working_directory": resolved_wd,
        }

    # Decode output (UTF-8 primary, latin-1 fallback).
    try:
        stdout = stdout_bytes.decode("utf-8")
    except UnicodeDecodeError:
        stdout = stdout_bytes.decode("latin-1")

    try:
        stderr = stderr_bytes.decode("utf-8")
    except UnicodeDecodeError:
        stderr = stderr_bytes.decode("latin-1")

    stdout, stdout_truncated = _truncate_output(stdout, _MAX_STDOUT_CHARS)
    stderr, stderr_truncated = _truncate_output(stderr, _MAX_STDERR_CHARS)

    return {
        "success": exit_code == 0,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "duration": round(duration, 3),
        "working_directory": resolved_wd,
        "output_truncated": stdout_truncated or stderr_truncated,
    }


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_command_tools(registry: ToolRegistry) -> None:
    """Register the ``execute_command`` tool."""
    registry.register(
        ToolDefinition(
            "execute_command",
            ToolRisk.HIGH,
            True,  # requires explicit approval
            _execute_command,
            argument_schema={
                "command": "string",
            },
            optional_arguments=frozenset({"working_directory", "timeout"}),
            compact_description="Execute a safe shell command (approval required)",
            argument_aliases={
                "cmd": "command", "shell": "command",
                "run": "command", "dir": "working_directory",
            },
        )
    )
