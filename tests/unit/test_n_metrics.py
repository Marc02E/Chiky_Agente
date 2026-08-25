"""FASE N tests — Metrics Wiring.

Tests that RequestMetrics fields are properly used and reportable.
"""

from __future__ import annotations

from personal_ai_secretary.observability.request_metrics import RequestMetrics


class TestRequestMetricsFields:
    def test_default_verification_status(self) -> None:
        m = RequestMetrics()
        assert m.verification_status == "pending"

    def test_default_final_outcome(self) -> None:
        m = RequestMetrics()
        assert m.final_outcome == "unknown"

    def test_default_correction_attempts(self) -> None:
        m = RequestMetrics()
        assert m.correction_attempts == 0

    def test_default_max_corrections_reached(self) -> None:
        m = RequestMetrics()
        assert m.max_corrections_reached is False

    def test_verification_status_settable(self) -> None:
        m = RequestMetrics()
        m.verification_status = "passed"
        assert m.verification_status == "passed"

    def test_verification_status_failed(self) -> None:
        m = RequestMetrics()
        m.verification_status = "failed"
        assert m.verification_status == "failed"

    def test_final_outcome_settable(self) -> None:
        m = RequestMetrics()
        m.final_outcome = "file_create"
        assert m.final_outcome == "file_create"

    def test_correction_attempts_incrementable(self) -> None:
        m = RequestMetrics()
        m.correction_attempts += 1
        m.correction_attempts += 1
        assert m.correction_attempts == 2

    def test_max_corrections_reached_settable(self) -> None:
        m = RequestMetrics()
        m.max_corrections_reached = True
        assert m.max_corrections_reached is True


class TestRequestMetricsSummary:
    def test_summary_returns_dict(self) -> None:
        m = RequestMetrics()
        s = m.summary()
        assert isinstance(s, dict)

    def test_summary_contains_verification_status(self) -> None:
        m = RequestMetrics()
        m.verification_status = "passed"
        s = m.summary()
        assert s["verification_status"] == "passed"

    def test_summary_contains_final_outcome(self) -> None:
        m = RequestMetrics()
        m.final_outcome = "code_debug"
        s = m.summary()
        assert s["final_outcome"] == "code_debug"

    def test_summary_contains_correction_attempts(self) -> None:
        m = RequestMetrics()
        m.correction_attempts = 3
        s = m.summary()
        assert s["correction_attempts"] == 3

    def test_summary_contains_max_corrections(self) -> None:
        m = RequestMetrics()
        m.max_corrections_reached = True
        s = m.summary()
        assert s["max_corrections_reached"] is True


class TestRequestMetricsFinish:
    def test_finish_sets_total_time(self) -> None:
        m = RequestMetrics()
        m.finish()
        assert m.total_time >= 0

    def test_finish_preserves_fields(self) -> None:
        m = RequestMetrics()
        m.verification_status = "failed"
        m.final_outcome = "test_type"
        m.correction_attempts = 2
        m.finish()
        assert m.verification_status == "failed"
        assert m.final_outcome == "test_type"
        assert m.correction_attempts == 2


class TestRequestMetricsToolCalls:
    def test_record_tool_call(self) -> None:
        m = RequestMetrics()
        m.record_tool_call("create_file", 0.1)
        assert m.tool_calls == 1

    def test_record_multiple_tool_calls(self) -> None:
        m = RequestMetrics()
        m.record_tool_call("create_file", 0.1)
        m.record_tool_call("execute_command", 0.5)
        m.record_tool_call("read_file", 0.05)
        assert m.tool_calls == 3

    def test_record_stage(self) -> None:
        m = RequestMetrics()
        m.record_stage("creating")
        assert "creating" in m.workflow_stages

    def test_record_command_output_truncated(self) -> None:
        m = RequestMetrics()
        m.record_command(duration=1.0, exit_code=0, output_chars=1000, output_truncated=True)
        assert m.command_output_truncated_count == 1

    def test_record_command_timeout(self) -> None:
        m = RequestMetrics()
        m.record_command(duration=30.0, exit_code=-1, timed_out=True)
        assert m.command_timeout_count == 1

    def test_record_correction(self) -> None:
        m = RequestMetrics()
        m.record_correction("modify_file")
        assert m.correction_attempts == 1
        assert "modify_file" in m.correction_tools_used


class TestRequestMetricsSetters:
    def test_set_final_outcome(self) -> None:
        m = RequestMetrics()
        m.set_final_outcome("completed")
        assert m.final_outcome == "completed"

    def test_set_verification_status(self) -> None:
        m = RequestMetrics()
        m.set_verification_status("passed")
        assert m.verification_status == "passed"
