"""FASE Q — Deterministic agent execution & task control.

Backend-owned control layer for the agentic loop:

- Q.1 TaskStateMachine: explicit observable task state (IDLE -> ... -> COMPLETED).
- Q.2 ProgressDetector: distinguishes valid repetition from unproductive loops.
- Q.3 Read->Modify enforcement: for modification tasks the backend injects a
  directive when the model keeps reading with sufficient information, and
  stops the loop if it still refuses to advance.
- Q.4 TaskContract: internal contract (objective, workspace, artifacts,
  verification) built deterministically by the backend — the LLM never has
  to remember the plan.

Design rule (FASE Q): the LLM proposes, the backend controls. Tools execute,
evidence demonstrates, verification decides, state transitions record.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from personal_ai_secretary.agents.classifier import TaskType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Q.1 — Task state machine
# ---------------------------------------------------------------------------


class TaskState(StrEnum):
    """Explicit states of a complex task."""

    IDLE = "idle"
    UNDERSTANDING = "understanding"
    PLANNING = "planning"
    READING = "reading"
    MODIFYING = "modifying"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    CORRECTING = "correcting"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


TERMINAL_STATES: frozenset[TaskState] = frozenset({
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.BLOCKED,
})

# Allowed forward transitions. The machine is lenient: unexpected transitions
# are logged but accepted so a quirky model path can never crash execution.
_ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.IDLE: frozenset({
        TaskState.UNDERSTANDING, TaskState.PLANNING, TaskState.READING,
        TaskState.MODIFYING, TaskState.EXECUTING, TaskState.COMPLETED,
        TaskState.FAILED, TaskState.BLOCKED,
    }),
    TaskState.UNDERSTANDING: frozenset({
        TaskState.PLANNING, TaskState.READING, TaskState.MODIFYING,
        TaskState.EXECUTING, TaskState.VERIFYING, TaskState.COMPLETED,
        TaskState.FAILED, TaskState.BLOCKED,
    }),
    TaskState.PLANNING: frozenset({
        TaskState.READING, TaskState.MODIFYING, TaskState.EXECUTING,
        TaskState.VERIFYING, TaskState.COMPLETED, TaskState.FAILED,
        TaskState.BLOCKED,
    }),
    TaskState.READING: frozenset({
        TaskState.READING, TaskState.MODIFYING, TaskState.EXECUTING,
        TaskState.PLANNING, TaskState.VERIFYING, TaskState.COMPLETED,
        TaskState.FAILED, TaskState.BLOCKED,
    }),
    TaskState.MODIFYING: frozenset({
        TaskState.MODIFYING, TaskState.EXECUTING, TaskState.VERIFYING,
        TaskState.CORRECTING, TaskState.READING, TaskState.COMPLETED,
        TaskState.FAILED, TaskState.BLOCKED,
    }),
    TaskState.EXECUTING: frozenset({
        TaskState.EXECUTING, TaskState.VERIFYING, TaskState.CORRECTING,
        TaskState.MODIFYING, TaskState.READING, TaskState.COMPLETED,
        TaskState.FAILED, TaskState.BLOCKED,
    }),
    TaskState.VERIFYING: frozenset({
        TaskState.VERIFYING, TaskState.CORRECTING, TaskState.COMPLETED,
        TaskState.FAILED, TaskState.BLOCKED, TaskState.READING,
        TaskState.MODIFYING,
    }),
    TaskState.CORRECTING: frozenset({
        TaskState.CORRECTING, TaskState.MODIFYING, TaskState.EXECUTING,
        TaskState.VERIFYING, TaskState.READING, TaskState.FAILED,
        TaskState.BLOCKED, TaskState.COMPLETED,
    }),
    # Terminal states accept no further transitions.
    TaskState.COMPLETED: frozenset({TaskState.COMPLETED}),
    TaskState.FAILED: frozenset({TaskState.FAILED}),
    TaskState.BLOCKED: frozenset({TaskState.BLOCKED}),
}


# Tool classification used to derive state transitions from tool executions.

READ_TOOLS: frozenset[str] = frozenset({
    "read_file", "read_files", "list_directory", "search_files",
    "analyze_project", "file_exists",
})

MODIFY_TOOLS: frozenset[str] = frozenset({
    "modify_file", "write_file", "create_file", "create_project",
    "create_directory",
})

EXECUTE_TOOLS: frozenset[str] = frozenset({"execute_command"})

VERIFY_TOOLS: frozenset[str] = frozenset({
    "verify_files", "file_exists", "read_file",
})


def state_for_tool(tool_name: str) -> TaskState:
    """Map an executed tool to the task state it advances."""
    if tool_name in READ_TOOLS:
        return TaskState.READING
    if tool_name in MODIFY_TOOLS:
        return TaskState.MODIFYING
    if tool_name in EXECUTE_TOOLS:
        return TaskState.EXECUTING
    return TaskState.READING


# Task types that inherently require a mutation before completion.
MUTATING_TASK_TYPES: frozenset[TaskType] = frozenset({
    TaskType.FILE_CREATE,
    TaskType.FILE_MODIFY,
    TaskType.PROJECT_CREATE,
    TaskType.CODE_GENERATION,
    TaskType.CODE_DEBUG,
    TaskType.GENERAL_DEVELOPMENT,
    TaskType.TEST_EXECUTION,
    TaskType.COMMAND_EXECUTION,
})


def task_requires_modification(task_type: TaskType) -> bool:
    """Whether successful completion of this task type requires mutation."""
    return task_type in MUTATING_TASK_TYPES


@dataclass
class TaskStateMachine:
    """Observable state machine for one request."""

    state: TaskState = TaskState.IDLE
    transitions: list[tuple[TaskState, TaskState]] = field(default_factory=list)

    def transition(self, new_state: TaskState) -> TaskState:
        """Move to ``new_state`` recording the transition.

        Terminal states are sticky; unexpected transitions are logged but
        allowed so backend control never depends on model discipline.
        """
        if self.state in TERMINAL_STATES:
            logger.debug("Ignoring transition from terminal state %s", self.state)
            return self.state
        if new_state not in _ALLOWED_TRANSITIONS[self.state]:
            logger.warning(
                "Unusual task transition %s -> %s (allowed but flagged)",
                self.state, new_state,
            )
        if new_state != self.state:
            self.transitions.append((self.state, new_state))
            self.state = new_state
        return self.state

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def history_values(self) -> list[str]:
        """Compact transition log for metrics: ["idle>understanding", ...]."""
        return [f"{src.value}>{dst.value}" for src, dst in self.transitions]


# ---------------------------------------------------------------------------
# Q.2 — Progress detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProgressSignals:
    """Signals produced by :class:`ProgressDetector` after each round."""

    repeated_identical_call: bool = False
    excessive_same_tool: bool = False
    read_without_modify: bool = False
    cycle_detected: bool = False
    unproductive: bool = False
    reason: str = ""

    @property
    def blocked_signal(self) -> bool:
        """True only for genuinely unproductive behaviour."""
        return self.unproductive


# Thresholds — tuned to stop loops early without punishing valid repetition.
MAX_IDENTICAL_CALLS = 2          # same tool + same args + same result hash
MAX_SAME_TOOL_BEFORE_SUSPECT = 3  # same tool name repeatedly (any args)
MAX_READ_ROUNDS_WITHOUT_MODIFY = 4  # read-only rounds on modification tasks
MAX_UNPRODUCTIVE_DIRECTIVES = 1     # directives injected before forced stop


def _result_hash(result: dict[str, Any]) -> str:
    """Stable short hash of a tool result (ignores volatile fields)."""
    normalized = {k: v for k, v in result.items() if k not in ("duration",)}
    try:
        payload = json.dumps(normalized, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = str(normalized)
    return hashlib.sha1(payload.encode("utf-8", "replace")).hexdigest()[:16]


@dataclass
class ProgressDetector:
    """Detects unproductive loops from executed tool calls (Q.2).

    Valid repetition vs loop:
    - Repeating a call with DIFFERENT args or a DIFFERENT result is progress.
    - Repeating the exact same call (tool+args) with the same result hash is
      not; reading files over and over without ever mutating on a task that
      requires modification is not.
    """

    identical_calls: dict[str, int] = field(default_factory=dict)
    tool_name_counts: dict[str, int] = field(default_factory=dict)
    result_hashes: dict[str, list[str]] = field(default_factory=dict)
    read_only_rounds: int = 0
    mutating_calls: int = 0
    executing_calls: int = 0
    last_tools: list[str] = field(default_factory=list)
    directives_issued: int = 0

    def record(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        """Record one executed tool call."""
        normalized_args = {
            k: v.strip() if isinstance(v, str) else v
            for k, v in args.items()
        }
        call_key = f"{tool_name}:{json.dumps(normalized_args, sort_keys=True, default=str)}"
        r_hash = _result_hash(result)

        self.identical_calls[call_key] = self.identical_calls.get(call_key, 0) + 1
        self.tool_name_counts[tool_name] = self.tool_name_counts.get(tool_name, 0) + 1
        hashes = self.result_hashes.setdefault(call_key, [])
        hashes.append(r_hash)
        self.last_tools.append(tool_name)

        if tool_name in READ_TOOLS:
            self.read_only_rounds += 1
        elif tool_name in MODIFY_TOOLS:
            self.mutating_calls += 1
            self.read_only_rounds = 0
        elif tool_name in EXECUTE_TOOLS:
            self.executing_calls += 1
            self.read_only_rounds = 0

    def mutation_happened(self) -> bool:
        """Whether any state-changing operation (file or command) executed."""
        return self.mutating_calls > 0 or self.executing_calls > 0

    def evaluate(
        self,
        *,
        requires_modification: bool,
        rounds_executed: int,
        max_rounds: int,
    ) -> ProgressSignals:
        """Evaluate progress after the latest recorded round."""
        reason_parts: list[str] = []

        repeated_identical = any(
            count > MAX_IDENTICAL_CALLS
            for count in self.identical_calls.values()
        )
        if repeated_identical:
            reason_parts.append("identical call repeated beyond limit")

        excessive_same_tool = any(
            count >= MAX_SAME_TOOL_BEFORE_SUSPECT + 1
            for count in self.tool_name_counts.values()
        )
        if excessive_same_tool:
            offender = max(self.tool_name_counts, key=lambda k: self.tool_name_counts[k])
            reason_parts.append(f"'{offender}' called too many times")

        read_without_modify = (
            requires_modification
            and self.mutating_calls == 0
            and self.read_only_rounds >= MAX_READ_ROUNDS_WITHOUT_MODIFY
        )
        if read_without_modify:
            reason_parts.append(
                f"{self.read_only_rounds} read rounds without any modify attempt"
            )

        cycle_detected = self._detect_cycle()
        if cycle_detected:
            reason_parts.append("cyclic tool pattern detected")

        unproductive = repeated_identical or read_without_modify or cycle_detected
        return ProgressSignals(
            repeated_identical_call=repeated_identical,
            excessive_same_tool=excessive_same_tool,
            read_without_modify=read_without_modify,
            cycle_detected=cycle_detected,
            unproductive=unproductive,
            reason="; ".join(reason_parts),
        )

    def _detect_cycle(self) -> bool:
        """Detect an immediate A,B,A,B (or longer period) repetition."""
        seq = self.last_tools[-8:]
        if len(seq) < 4:
            return False
        for period in (1, 2, 3):
            tail = seq[-period:]
            # A constant single-tool stream is not a cycle; it is handled by
            # the same-tool-name limits. Cycles need alternating patterns.
            if len(set(tail)) < 2:
                continue
            candidates = seq[-(2 * period):]
            if len(candidates) == 2 * period and candidates[:period] == tail:
                prefix = seq[: -(2 * period)]
                if prefix[-period:] == tail:
                    return True
        return False

    def directive_for(self, contract: TaskContract) -> str:
        """Build the backend directive telling the model what a valid next action is."""
        self.directives_issued += 1
        target = contract.expected_next_action or "apply the required modification now"
        return (
            "BACKEND DIRECTIVE (task control): You have enough information to act. "
            f"Stop reading. Next valid action: {target}. "
            "Do NOT repeat read/search/list calls."
        )


# ---------------------------------------------------------------------------
# Q.4 — Task contract
# ---------------------------------------------------------------------------


_CONTRACT_PHASE_BY_TYPE: dict[TaskType, str] = {
    TaskType.CHAT: "respond directly",
    TaskType.FILE_CREATE: "create then verify",
    TaskType.FILE_READ: "read and report",
    TaskType.FILE_MODIFY: "read -> modify -> verify",
    TaskType.PROJECT_ANALYSIS: "analyze and summarize",
    TaskType.PROJECT_CREATE: "plan -> create -> test -> verify",
    TaskType.CODE_GENERATION: "generate -> verify syntax",
    TaskType.CODE_DEBUG: "reproduce -> diagnose -> modify -> test -> verify",
    TaskType.TEST_EXECUTION: "execute tests -> report exit code",
    TaskType.DOCUMENT_ANALYSIS: "extract and summarize",
    TaskType.IMAGE_ANALYSIS: "describe if vision available",
    TaskType.COMMAND_EXECUTION: "execute -> report exit code",
    TaskType.GENERAL_DEVELOPMENT: "discover -> modify -> test -> verify",
}

_NEXT_ACTION_BY_TYPE: dict[TaskType, str] = {
    TaskType.FILE_CREATE: "create_file with the requested content",
    TaskType.FILE_MODIFY: "modify_file with the minimal required change",
    TaskType.CODE_DEBUG: "modify_file applying the diagnosed fix, then run tests",
    TaskType.CODE_GENERATION: "create_file with the generated code",
    TaskType.PROJECT_CREATE: "create_project (batch) with the planned structure",
    TaskType.GENERAL_DEVELOPMENT: "apply the modification with modify_file/create_file",
}

_FILE_HINT_RE = re.compile(
    r"\b[\w./\\-]+\.(?:py|js|ts|html|css|json|yaml|yml|toml"
    r"|txt|md|csv|xml|sql|sh|bat|rs|go|java)\b",
    re.IGNORECASE,
)


@dataclass
class TaskContract:
    """Q.4 — Internal contract of a task. Built by the backend, not the LLM."""

    objective: str
    workspace: str
    requested_action: str
    affected_files: list[str] = field(default_factory=list)
    current_phase: str = "understand -> plan -> act -> verify"
    expected_artifacts: list[str] = field(default_factory=list)
    verification_required: list[str] = field(default_factory=list)
    approval_required: bool = False
    completion_evidence: list[str] = field(default_factory=list)
    expected_next_action: str = ""
    requires_modification: bool = False

    def render_directive(self) -> str:
        """Compact prompt fragment describing the contract (~6 lines)."""
        lines = [
            "## Task Contract (backend-controlled)",
            f"Objective: {self.objective}",
            f"Required flow: {self.current_phase}.",
        ]
        if self.expected_artifacts:
            lines.append("Expected artifacts: " + ", ".join(self.expected_artifacts))
        if self.verification_required:
            lines.append(
                "Completion requires evidence: " + "; ".join(self.verification_required)
            )
        if self.requires_modification:
            lines.append(
                "Reading is finished once you can act. Do not keep reading "
                "files you already understand."
            )
        return "\n".join(lines)


def build_task_contract(
    user_text: str,
    task_type: TaskType,
    workspace: str = "",
    approval_required: bool = False,
) -> TaskContract:
    """Deterministically build a TaskContract from classified user input."""
    phase = _CONTRACT_PHASE_BY_TYPE.get(task_type, "understand -> plan -> act -> verify")
    next_action = _NEXT_ACTION_BY_TYPE.get(task_type, "")
    affected = list(dict.fromkeys(_FILE_HINT_RE.findall(user_text)))[:5]

    expected_artifacts: list[str] = []
    verification: list[str] = []
    evidence: list[str] = []

    if task_type is TaskType.CHAT or task_type is TaskType.DOCUMENT_ANALYSIS \
            or task_type is TaskType.IMAGE_ANALYSIS or task_type is TaskType.FILE_READ \
            or task_type is TaskType.PROJECT_ANALYSIS:
        evidence.append("response grounded in tool output")
    if task_type in (TaskType.FILE_CREATE, TaskType.CODE_GENERATION):
        expected_artifacts.extend(affected or ["requested file"])
        verification.extend(["created file exists on disk"])
        evidence.append("create_file tool success")
    if task_type is TaskType.FILE_MODIFY or task_type is TaskType.CODE_DEBUG:
        expected_artifacts.extend(affected or ["modified file"])
        verification.extend(["file content changed", "tests pass (when applicable)"])
        evidence.extend(["modify_file tool success", "verification passed"])
    if task_type is TaskType.PROJECT_CREATE:
        expected_artifacts.extend(["project skeleton", "README.md"])
        verification.extend(["all project files exist", "tests/entry point runs"])
        evidence.append("create_project verified file list")
    if task_type in (TaskType.TEST_EXECUTION, TaskType.COMMAND_EXECUTION):
        verification.append("command exits 0")
        evidence.append("execute_command exit_code=0")
    if task_type is TaskType.GENERAL_DEVELOPMENT:
        verification.extend(["changed files exist", "tests pass (when applicable)"])

    requires_mod = task_requires_modification(task_type)
    return TaskContract(
        objective=user_text.strip()[:300],
        workspace=workspace,
        requested_action=phase,
        affected_files=affected,
        current_phase=phase,
        expected_artifacts=expected_artifacts,
        verification_required=verification,
        approval_required=approval_required,
        completion_evidence=evidence,
        expected_next_action=next_action,
        requires_modification=requires_mod,
    )


# ---------------------------------------------------------------------------
# Q.6 — Failure recovery helpers
# ---------------------------------------------------------------------------

MAX_CONSECUTIVE_TOOL_FAILURES = 3


def failure_recovery_message(consecutive_failures: int, last_error: str) -> str:
    """Message emitted when consecutive tool failures exhaust the budget."""
    return (
        f"Stopping after {consecutive_failures} consecutive failed operations "
        f"(I stopped retrying blindly). Last error: {last_error[:200]}. "
        "Here is what was accomplished and what failed."
    )


# ---------------------------------------------------------------------------
# Q.7/Q.8 — Model-aware and latency helpers
# ---------------------------------------------------------------------------


def classify_latency_source(
    llm_seconds: float,
    tool_seconds: float,
    command_seconds: float,
) -> str:
    """Classify where total latency came from (slow model vs tools vs commands).

    Returns one of: 'llm_bound', 'tool_bound', 'command_bound', 'balanced'.
    """
    contributions = {
        "llm_bound": llm_seconds,
        "tool_bound": tool_seconds - command_seconds,
        "command_bound": command_seconds,
    }
    total = sum(max(0.0, v) for v in contributions.values())
    if total <= 0:
        return "balanced"
    dominant = max(contributions, key=lambda k: contributions[k])
    share = contributions[dominant] / total
    if share >= 0.6:
        return dominant
    return "balanced"


def duration_budget_exceeded(elapsed_seconds: float, budget_seconds: float) -> bool:
    """Q.8 — explicit wall-clock budget check for the whole agentic loop."""
    return elapsed_seconds >= budget_seconds


DEFAULT_TOTAL_DURATION_BUDGET_SECONDS = 600.0
