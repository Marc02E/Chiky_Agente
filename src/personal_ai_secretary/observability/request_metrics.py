"""Per-request metrics for the agentic loop.

Tracks LLM calls, tool calls, rounds, timing, and approximate context size
to enable measurement before/after optimization.

FASE K.5 adds context-window observability: context build time, estimated
tokens, per-category character counts, truncation flags, and summary usage.

FASE K.6 adds command execution metrics: command count, duration, timeout
flag, exit codes, and output sizes.

FASE L.1 adds development workflow stage tracking for UI progress reporting.

FASE L.2 adds project intelligence metrics: discovery time, technology detection,
progressive reads, and task-aware inspection.

FASE L.3 adds autonomous loop metrics: correction attempts, final outcome,
and verification status.

FASE L.4-L.6 adds debugging and multi-model metrics: diagnosis attempts,
test executions, model failures, and fallback tracking.

FASE L.7-L.9 adds document and vision metrics: uploaded files, document types,
extraction time, vision requests, and security blocks.
"""

from dataclasses import dataclass, field
from time import perf_counter

from personal_ai_secretary.context.budget import estimate_tokens  # re-export

__all__ = ["RequestMetrics", "estimate_tokens"]


@dataclass
class RequestMetrics:
    """Per-request metrics collected during agentic loop execution."""

    provider: str = ""
    model: str = ""

    llm_calls: int = 0
    tool_calls: int = 0
    rounds: int = 0

    total_time: float = 0.0
    llm_times: list[float] = field(default_factory=list)
    tool_times: list[float] = field(default_factory=list)

    context_chars_approx: int = 0
    response_chars_approx: int = 0

    system_prompt_chars: int = 0
    history_chars: int = 0
    tool_results_chars: int = 0

    # FASE K.5 — context window management observability.
    context_build_time: float = 0.0
    estimated_context_tokens: int = 0
    attached_file_chars: int = 0
    project_context_chars: int = 0
    truncation_applied: bool = False
    history_truncated_turns: int = 0
    summary_used: bool = False

    tool_call_names: list[str] = field(default_factory=list)
    deduplicated_calls: int = 0

    # FASE K.6 — command execution observability.
    command_count: int = 0
    command_durations: list[float] = field(default_factory=list)
    command_timeout_count: int = 0
    command_exit_codes: list[int] = field(default_factory=list)
    command_output_chars: int = 0
    command_output_truncated_count: int = 0

    # FASE L.1 — development workflow stage tracking.
    workflow_stages: list[str] = field(default_factory=list)
    workflow_stage_times: list[float] = field(default_factory=list)
    _stage_start: float = field(default_factory=perf_counter, repr=False, compare=False)
    _current_stage: str = ""

    # FASE L.2 — project intelligence observability.
    project_discovery_time: float = 0.0
    project_technologies_detected: list[str] = field(default_factory=list)
    project_files_discovered: int = 0
    project_entry_points: int = 0
    project_test_locations: int = 0
    progressive_read_level: int = 0
    progressive_read_time: float = 0.0
    task_inspection_time: float = 0.0

    # FASE L.3 — autonomous loop observability.
    correction_attempts: int = 0
    correction_tools_used: list[str] = field(default_factory=list)
    final_outcome: str = "unknown"
    verification_status: str = "pending"
    max_corrections_reached: bool = False

    # FASE L.4-L.6 — debugging and multi-model observability.
    diagnosis_attempts: int = 0
    tests_executed: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    model_failures: int = 0
    # FASE AB.4: full execution chain for honest routing transparency.
    requested_provider: str = ""
    requested_model: str = ""
    fallback_suggested: bool = False
    fallback_executed: bool = False
    fallback_model: str = ""
    fallback_from_provider: str = ""
    fallback_from_model: str = ""
    llm_duration: float = 0.0
    tool_duration: float = 0.0
    # FASE AB.6: enriched provenance and routing transparency.
    selected_provider: str = ""   # what routing mode chose before execution
    selected_model: str = ""      # what routing mode chose before execution
    routing_reason: str = ""      # e.g. "best verified model for coding"
    fallback_chain: list[str] = field(default_factory=list)
    status: str = "ok"            # ok|failed|timeout|unauthorized|connection_error|unknown
    latency_ms: int = 0           # total wall-clock time of the request in ms
    command_duration: float = 0.0

    # FASE L.7-L.9 — document and vision observability.
    uploaded_files: int = 0
    document_types: list[str] = field(default_factory=list)
    extraction_time: float = 0.0
    vision_requests: int = 0
    vision_failures: int = 0
    vision_blocks: int = 0
    analysis_stage: str = ""
    security_blocks: int = 0
    model_capability_mismatches: int = 0

    # FASE Q — deterministic task control observability.
    state_transitions: list[str] = field(default_factory=list)
    final_task_state: str = "idle"
    unproductive_loop_detections: int = 0
    backend_directives: int = 0
    consecutive_tool_failures: int = 0
    duration_budget_exceeded: bool = False
    latency_classification: str = ""

    _start: float = field(default_factory=perf_counter, repr=False, compare=False)

    def record_llm_call(self, elapsed: float, response_chars: int) -> None:
        self.llm_calls += 1
        self.llm_times.append(elapsed)
        self.response_chars_approx += response_chars

    def record_tool_call(self, name: str, elapsed: float) -> None:
        self.tool_calls += 1
        self.tool_times.append(elapsed)
        self.tool_call_names.append(name)

    def record_command(
        self,
        duration: float,
        exit_code: int,
        output_chars: int = 0,
        output_truncated: bool = False,
        timed_out: bool = False,
    ) -> None:
        self.command_count += 1
        self.command_durations.append(duration)
        self.command_exit_codes.append(exit_code)
        self.command_output_chars += output_chars
        if output_truncated:
            self.command_output_truncated_count += 1
        if timed_out:
            self.command_timeout_count += 1

    def record_project_discovery(
        self,
        elapsed: float,
        technologies: list[str],
        file_count: int,
        entry_points: int,
        test_locations: int,
    ) -> None:
        """Record project discovery metrics for L.2 intelligence."""
        self.project_discovery_time = elapsed
        self.project_technologies_detected = technologies
        self.project_files_discovered = file_count
        self.project_entry_points = entry_points
        self.project_test_locations = test_locations

    def record_progressive_read(self, level: int, elapsed: float) -> None:
        """Record progressive read strategy metrics for L.2."""
        self.progressive_read_level = level
        self.progressive_read_time = elapsed

    def record_task_inspection(self, elapsed: float) -> None:
        """Record task-aware inspection metrics for L.2."""
        self.task_inspection_time = elapsed

    def record_correction(self, tool_name: str) -> None:
        """Record an auto-correction attempt for L.3."""
        self.correction_attempts += 1
        self.correction_tools_used.append(tool_name)

    def record_file_upload(self, document_type: str) -> None:
        """Record a file upload for L.7-L.9."""
        self.uploaded_files += 1
        if document_type not in self.document_types:
            self.document_types.append(document_type)

    def record_vision_request(self, success: bool = True) -> None:
        """Record a vision request for L.7-L.9."""
        self.vision_requests += 1
        if not success:
            self.vision_failures += 1

    def record_vision_block(self) -> None:
        """Record a vision block (model doesn't support vision)."""
        self.vision_blocks += 1

    def record_security_block(self) -> None:
        """Record a security block event."""
        self.security_blocks += 1

    def record_extraction_time(self, elapsed: float) -> None:
        """Record document extraction time."""
        self.extraction_time += elapsed

    def record_analysis_stage(self, stage: str) -> None:
        """Record the current analysis stage."""
        self.analysis_stage = stage

    def set_final_outcome(self, outcome: str) -> None:
        """Set the final outcome of the autonomous loop."""
        self.final_outcome = outcome

    def set_verification_status(self, status: str) -> None:
        """Set the verification status."""
        self.verification_status = status

    def record_stage(self, stage: str) -> None:
        """Record a development workflow stage transition for UI progress."""
        now = perf_counter()
        if self._current_stage:
            elapsed = now - self._stage_start
            self.workflow_stage_times.append(round(elapsed, 3))
        self.workflow_stages.append(stage)
        self._current_stage = stage
        self._stage_start = now

    def record_round(
        self,
        context_chars: int,
        system_prompt_chars: int = 0,
        history_chars: int = 0,
        estimated_tokens: int = 0,
        attached_file_chars: int = 0,
        project_context_chars: int = 0,
        truncation_applied: bool | None = None,
        summary_used: bool | None = None,
    ) -> None:
        self.rounds += 1
        self.context_chars_approx = max(self.context_chars_approx, context_chars)
        if system_prompt_chars:
            self.system_prompt_chars = system_prompt_chars
        if history_chars:
            self.history_chars = history_chars
        if estimated_tokens:
            self.estimated_context_tokens = max(self.estimated_context_tokens, estimated_tokens)
        if attached_file_chars:
            self.attached_file_chars = attached_file_chars
        if project_context_chars:
            self.project_context_chars = project_context_chars
        if truncation_applied:
            self.truncation_applied = True
        if summary_used:
            self.summary_used = True

    def finish(self) -> None:
        self.total_time = perf_counter() - self._start

    def summary(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "deduplicated_calls": self.deduplicated_calls,
            "rounds": self.rounds,
            "total_time_s": round(self.total_time, 3),
            "avg_llm_time_s": (
                round(sum(self.llm_times) / len(self.llm_times), 3)
                if self.llm_times
                else 0.0
            ),
            "context_build_time_s": round(self.context_build_time, 6),
            "context_chars_max": self.context_chars_approx,
            "estimated_context_tokens": self.estimated_context_tokens,
            "response_chars_total": self.response_chars_approx,
            "system_prompt_chars": self.system_prompt_chars,
            "history_chars": self.history_chars,
            "tool_results_chars": self.tool_results_chars,
            "attached_file_chars": self.attached_file_chars,
            "project_context_chars": self.project_context_chars,
            "truncation_applied": self.truncation_applied,
            "history_truncated_turns": self.history_truncated_turns,
            "summary_used": self.summary_used,
            "command_count": self.command_count,
            "command_avg_duration_s": (
                round(sum(self.command_durations) / len(self.command_durations), 3)
                if self.command_durations
                else 0.0
            ),
            "command_timeout_count": self.command_timeout_count,
            "command_exit_codes": list(self.command_exit_codes),
            "command_output_chars": self.command_output_chars,
            "command_output_truncated_count": self.command_output_truncated_count,
            "workflow_stages": list(self.workflow_stages),
            "workflow_stage_times": list(self.workflow_stage_times),
            # FASE L.2
            "project_discovery_time_s": round(self.project_discovery_time, 3),
            "project_technologies_detected": list(self.project_technologies_detected),
            "project_files_discovered": self.project_files_discovered,
            "project_entry_points": self.project_entry_points,
            "project_test_locations": self.project_test_locations,
            "progressive_read_level": self.progressive_read_level,
            "progressive_read_time_s": round(self.progressive_read_time, 3),
            "task_inspection_time_s": round(self.task_inspection_time, 3),
            # FASE L.3
            "correction_attempts": self.correction_attempts,
            "correction_tools_used": list(self.correction_tools_used),
            "final_outcome": self.final_outcome,
            "verification_status": self.verification_status,
            "max_corrections_reached": self.max_corrections_reached,
            # FASE Q — deterministic task control.
            "state_transitions": list(self.state_transitions),
            "final_task_state": self.final_task_state,
            "unproductive_loop_detections": self.unproductive_loop_detections,
            "backend_directives": self.backend_directives,
            "consecutive_tool_failures": self.consecutive_tool_failures,
            "duration_budget_exceeded": self.duration_budget_exceeded,
            "latency_classification": self.latency_classification,
        }
