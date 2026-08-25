"""FASE Q — unit tests for agents/task_state.py.

Covers: TaskState machine, progress detection, task contracts,
failure recovery, latency classification, and duration budget.
"""

from __future__ import annotations

import pytest

from personal_ai_secretary.agents.classifier import TaskType
from personal_ai_secretary.agents.task_state import (
    DEFAULT_TOTAL_DURATION_BUDGET_SECONDS,
    MAX_CONSECUTIVE_TOOL_FAILURES,
    MAX_IDENTICAL_CALLS,
    MAX_READ_ROUNDS_WITHOUT_MODIFY,
    TERMINAL_STATES,
    ProgressDetector,
    TaskContract,
    TaskState,
    TaskStateMachine,
    build_task_contract,
    classify_latency_source,
    duration_budget_exceeded,
    failure_recovery_message,
    state_for_tool,
    task_requires_modification,
)

# ---------------------------------------------------------------------------
# Q.1 — Task state machine
# ---------------------------------------------------------------------------


class TestTaskStateMachine:
    def test_all_states_present(self) -> None:
        expected = {
            "idle", "understanding", "planning", "reading", "modifying",
            "executing", "verifying", "correcting", "completed", "failed",
            "blocked",
        }
        assert {s.value for s in TaskState} == expected

    def test_terminal_states_include_blocked(self) -> None:
        assert TERMINAL_STATES == frozenset({
            TaskState.COMPLETED, TaskState.FAILED, TaskState.BLOCKED,
        })

    def test_happy_path_transitions(self) -> None:
        m = TaskStateMachine()
        assert m.state is TaskState.IDLE
        for nxt in (
            TaskState.UNDERSTANDING, TaskState.PLANNING, TaskState.READING,
            TaskState.MODIFYING, TaskState.VERIFYING, TaskState.COMPLETED,
        ):
            m.transition(nxt)
        assert m.state is TaskState.COMPLETED
        assert m.terminal
        history = m.history_values()
        assert history[0] == "idle>understanding"
        assert history[-1] == "verifying>completed"

    def test_terminal_states_are_sticky(self) -> None:
        m = TaskStateMachine()
        m.transition(TaskState.FAILED)
        result = m.transition(TaskState.READING)
        assert result is TaskState.FAILED
        assert m.state is TaskState.FAILED

    def test_unusual_transition_is_logged_but_allowed(self) -> None:
        # IDLE -> VERIFYING is not in the allowed table; must not raise.
        m = TaskStateMachine()
        state = m.transition(TaskState.VERIFYING)
        assert state is TaskState.VERIFYING

    def test_self_transition_not_recorded(self) -> None:
        m = TaskStateMachine()
        m.transition(TaskState.READING)
        m.transition(TaskState.READING)
        assert len(m.transitions) == 1


# ---------------------------------------------------------------------------
# Tool / task-type classification helpers
# ---------------------------------------------------------------------------


class TestClassificationHelpers:
    @pytest.mark.parametrize(
        ("tool", "expected"),
        [
            ("read_file", TaskState.READING),
            ("list_directory", TaskState.READING),
            ("analyze_project", TaskState.READING),
            ("create_file", TaskState.MODIFYING),
            ("modify_file", TaskState.MODIFYING),
            ("create_project", TaskState.MODIFYING),
            ("execute_command", TaskState.EXECUTING),
            ("unknown_tool", TaskState.READING),
        ],
    )
    def test_state_for_tool(self, tool: str, expected: TaskState) -> None:
        assert state_for_tool(tool) is expected

    @pytest.mark.parametrize(
        "task_type",
        [
            TaskType.FILE_CREATE, TaskType.FILE_MODIFY,
            TaskType.PROJECT_CREATE, TaskType.CODE_GENERATION,
            TaskType.CODE_DEBUG, TaskType.GENERAL_DEVELOPMENT,
            TaskType.TEST_EXECUTION, TaskType.COMMAND_EXECUTION,
        ],
    )
    def test_mutating_types_require_modification(
        self, task_type: TaskType,
    ) -> None:
        assert task_requires_modification(task_type)

    @pytest.mark.parametrize(
        "task_type",
        [
            TaskType.CHAT, TaskType.FILE_READ, TaskType.PROJECT_ANALYSIS,
            TaskType.DOCUMENT_ANALYSIS,
        ],
    )
    def test_non_mutating_types(self, task_type: TaskType) -> None:
        assert not task_requires_modification(task_type)


# ---------------------------------------------------------------------------
# Q.2 — Progress detection
# ---------------------------------------------------------------------------


def _record_read(detector: ProgressDetector, path: str = "/tmp/a.py") -> None:
    detector.record("read_file", {"path": path}, {"result": "ok"})


class TestProgressDetector:
    def test_mutation_happened_tracks_modify_and_execute(self) -> None:
        d = ProgressDetector()
        assert not d.mutation_happened()
        _record_read(d)
        assert not d.mutation_happened()
        d.record("modify_file", {"path": "/tmp/a.py"}, {"result": "modified"})
        assert d.mutation_happened()

    def test_execute_counts_as_progress(self) -> None:
        d = ProgressDetector()
        d.record("execute_command", {"command": "pytest"}, {"exit_code": 0})
        assert d.mutation_happened()

    def test_identical_repetition_detected(self) -> None:
        d = ProgressDetector()
        for _ in range(MAX_IDENTICAL_CALLS + 1):
            _record_read(d, "/tmp/same.py")
        signals = d.evaluate(requires_modification=True, rounds_executed=3, max_rounds=15)
        assert signals.repeated_identical_call
        assert signals.unproductive

    def test_different_args_are_progress(self) -> None:
        d = ProgressDetector()
        for i in range(5):
            _record_read(d, f"/tmp/file{i}.py")
        signals = d.evaluate(requires_modification=False, rounds_executed=5, max_rounds=15)
        assert not signals.unproductive

    def test_read_without_modify_on_mutating_task(self) -> None:
        d = ProgressDetector()
        for i in range(MAX_READ_ROUNDS_WITHOUT_MODIFY):
            _record_read(d, f"/tmp/f{i}.py")
        signals = d.evaluate(requires_modification=True, rounds_executed=4, max_rounds=15)
        assert signals.read_without_modify
        assert signals.unproductive
        assert "read rounds without any modify" in signals.reason

    def test_read_without_modify_ignored_on_chat(self) -> None:
        d = ProgressDetector()
        for i in range(MAX_READ_ROUNDS_WITHOUT_MODIFY + 2):
            _record_read(d, f"/tmp/f{i}.py")
        signals = d.evaluate(requires_modification=False, rounds_executed=6, max_rounds=15)
        assert not signals.read_without_modify
        assert not signals.unproductive

    def test_mutation_resets_read_streak(self) -> None:
        d = ProgressDetector()
        for i in range(MAX_READ_ROUNDS_WITHOUT_MODIFY - 1):
            _record_read(d, f"/tmp/f{i}.py")
        d.record("modify_file", {"path": "/tmp/f.py"}, {"result": "modified"})
        _record_read(d)
        signals = d.evaluate(requires_modification=True, rounds_executed=5, max_rounds=15)
        assert not signals.read_without_modify

    def test_cycle_detection_abab(self) -> None:
        d = ProgressDetector()
        pattern = ["read_file", "list_directory"]
        for i in range(6):
            tool = pattern[i % 2]
            d.record(tool, {"path": f"/tmp/{tool}.txt"}, {"result": "ok"})
        signals = d.evaluate(requires_modification=False, rounds_executed=6, max_rounds=15)
        assert signals.cycle_detected
        assert signals.unproductive

    def test_no_cycle_in_varied_sequence(self) -> None:
        d = ProgressDetector()
        tools = ["read_file", "list_directory", "search_files",
                 "read_file", "list_directory"]
        for i, t in enumerate(tools):
            d.record(t, {"path": f"/tmp/p{i}"}, {"result": "ok"})
        signals = d.evaluate(requires_modification=False, rounds_executed=5, max_rounds=15)
        assert not signals.cycle_detected

    def test_directive_issued_once_then_counted(self) -> None:
        contract = build_task_contract("fix bug in app.py", TaskType.CODE_DEBUG)
        d = ProgressDetector()
        first = d.directive_for(contract)
        d.directive_for(contract)
        assert "BACKEND DIRECTIVE" in first
        assert "modify_file" in first
        assert d.directives_issued == 2


# ---------------------------------------------------------------------------
# Q.4 — Task contracts
# ---------------------------------------------------------------------------


class TestTaskContract:
    def test_file_modify_contract(self) -> None:
        c = build_task_contract(
            "update settings in config.py", TaskType.FILE_MODIFY,
        )
        assert c.requires_modification
        assert "config.py" in c.affected_files
        assert c.verification_required
        assert "modify_file" in c.expected_next_action
        directive = c.render_directive()
        assert "Task Contract" in directive
        assert "config.py" in directive

    def test_chat_contract_has_no_artifacts(self) -> None:
        c = build_task_contract("hello there", TaskType.CHAT)
        assert not c.requires_modification
        assert c.expected_artifacts == []
        assert not c.verification_required

    def test_command_execution_contract(self) -> None:
        c = build_task_contract("run the test suite", TaskType.TEST_EXECUTION)
        assert c.requires_modification
        assert any("exit" in v for v in c.verification_required)

    def test_objective_truncated(self) -> None:
        c = build_task_contract("x" * 1000, TaskType.CHAT)
        assert len(c.objective) <= 300

    def test_approval_flag_propagates(self) -> None:
        c = build_task_contract("rm file.txt", TaskType.COMMAND_EXECUTION,
                                approval_required=True)
        assert c.approval_required

    def test_workspace_recorded(self) -> None:
        c = build_task_contract("create app.py", TaskType.FILE_CREATE,
                                workspace="C:/work")
        assert c.workspace == "C:/work"


# ---------------------------------------------------------------------------
# Q.6/Q.7/Q.8 — Failure recovery, latency, budget
# ---------------------------------------------------------------------------


class TestFailureAndLatencyHelpers:
    def test_recovery_message_contains_stopping(self) -> None:
        msg = failure_recovery_message(3, "boom")
        assert "stopping" in msg.lower()
        assert "3" in msg
        assert "boom" in msg

    def test_recovery_message_truncates_error(self) -> None:
        msg = failure_recovery_message(MAX_CONSECUTIVE_TOOL_FAILURES, "e" * 5000)
        assert len(msg) < 400

    def test_latency_llm_bound(self) -> None:
        assert classify_latency_source(90.0, 5.0, 0.0) == "llm_bound"

    def test_latency_tool_bound(self) -> None:
        assert classify_latency_source(1.0, 9.0, 0.0) == "tool_bound"

    def test_latency_command_bound(self) -> None:
        assert classify_latency_source(1.0, 9.0, 8.0) == "command_bound"

    def test_latency_balanced(self) -> None:
        assert classify_latency_source(10.0, 10.0, 0.0) == "balanced"

    def test_latency_empty(self) -> None:
        assert classify_latency_source(0.0, 0.0, 0.0) == "balanced"

    def test_duration_budget_exceeded(self) -> None:
        assert duration_budget_exceeded(601.0, DEFAULT_TOTAL_DURATION_BUDGET_SECONDS)
        assert duration_budget_exceeded(600.0, DEFAULT_TOTAL_DURATION_BUDGET_SECONDS)
        assert not duration_budget_exceeded(599.0, DEFAULT_TOTAL_DURATION_BUDGET_SECONDS)


# ---------------------------------------------------------------------------
# Contract dataclass defaults
# ---------------------------------------------------------------------------


class TestTaskContractDefaults:
    def test_render_directive_minimal(self) -> None:
        c = TaskContract(objective="obj", workspace="", requested_action="act")
        text = c.render_directive()
        assert "Objective: obj" in text
        assert "Required flow:" in text
