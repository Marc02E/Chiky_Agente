import contextlib
import json
import logging
import re
import time
from typing import Any, Protocol, cast

from personal_ai_secretary.agents.classifier import classify_task
from personal_ai_secretary.agents.contracts import (
    Agent,
    AgentArtifact,
    AgentInput,
    AgentRole,
    ExecutionPlan,
    PlanStep,
)
from personal_ai_secretary.agents.evidence import EvidenceTracker, validate_response
from personal_ai_secretary.agents.profiles import get_task_profile
from personal_ai_secretary.agents.task_state import (
    DEFAULT_TOTAL_DURATION_BUDGET_SECONDS,
    MAX_CONSECUTIVE_TOOL_FAILURES,
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
)
from personal_ai_secretary.agents.verifier import (
    verify_session_files,
    verify_tool_result,
)
from personal_ai_secretary.compliance.policy import (
    ComplianceRule,
    ComplianceRuleResult,
    evaluate_policy,
    select_rules,
)
from personal_ai_secretary.context.assembler import ContextAssembler
from personal_ai_secretary.context.budget import (
    ContextBudget,
    estimate_tokens,
    resolve_model_profile,
)
from personal_ai_secretary.context.files import AttachedFilesContext, format_attached_files
from personal_ai_secretary.context.project import ProjectContextTracker
from personal_ai_secretary.context.tools import prune_tool_results
from personal_ai_secretary.domain.contracts import RequestEnvelope, RiskLevel
from personal_ai_secretary.memory.service import MemoryStore
from personal_ai_secretary.observability.metrics import Timer
from personal_ai_secretary.observability.observer import Observability
from personal_ai_secretary.observability.request_metrics import RequestMetrics
from personal_ai_secretary.observability.tracing import (
    ATTRIBUTE_OUTCOME,
    ATTRIBUTE_PROVIDER,
    ATTRIBUTE_STAGE,
    ATTRIBUTE_TOOL_NAME,
    mark_span_error,
    set_span_correlation,
    start_span,
)
from personal_ai_secretary.providers.base import AIProvider
from personal_ai_secretary.providers.model_intelligence import (
    classify_failure,
    get_model_capabilities,
    should_suggest_fallback,
)
from personal_ai_secretary.rag.service import Evidence, Retriever
from personal_ai_secretary.shared.config import get_settings
from personal_ai_secretary.tools.filesystem import routing_workspace
from personal_ai_secretary.tools.registry import (
    ToolError,
    ToolRegistry,
    parse_tool_call,
)


class ModelSwitchable(Protocol):
    """A provider that exposes a settable ``model`` identifier."""

    model: str

logger = logging.getLogger("personal_ai_secretary.agents")

# Prefix used in the response text to signal that the agent needs explicit
# user approval before executing a tool. The API layer parses and strips this
# prefix before returning the assistant message to the client.
APPROVAL_REQUIRED_PREFIX = "__APPROVAL_REQUIRED__:"


def _status_for_exception(exc: BaseException) -> str:
    """Map an exception to the FASE AB.6 provenance status vocabulary."""
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ConnectionError):
        return "connection_error"
    if isinstance(exc, RuntimeError):
        msg = str(exc).lower()
        if "invalid or expired" in msg or "401" in msg or "unauthorized" in msg:
            return "unauthorized"
        return "failed"
    return "unknown"


_STATUS_CAUSE_PHRASES: dict[str, str] = {
    "timeout": "the connection timed out",
    "connection_error": "the connection failed",
    "unauthorized": "the credentials were rejected",
    "failed": "the provider reported an error",
    "unknown": "an unexpected error occurred",
}


def _redact_internal_paths(text: str) -> str:
    """Scrub absolute filesystem paths from a user-facing message.

    Exception text can embed internal machine paths (e.g. a failing
    ``/home/user/.ollama`` model path or a Windows ``C:\\Users\\...`` path).
    Those must never reach the user. Replaces any absolute path with a neutral
    placeholder. Scoped to error messages only; legit conversation responses
    keep real user file paths.
    """
    # Regex Windows drive paths: C:\Users\... and C:/Users/... forms.
    windows = re.compile(r"([A-Za-z]:[\\/][^\s,;()]+)")
    text = windows.sub("[internal path]", text)
    # POSIX absolute paths: /home/user/.ollama, /usr/share/..., etc.
    posix = re.compile(r"(?<![\w:])/(?:[\w.\-]+/)+[\w.\-]+")
    text = posix.sub("[internal path]", text)
    return text


class PlannerAgent:
    role = AgentRole.PLANNER

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry

    async def run(self, data: AgentInput) -> AgentArtifact:
        tool_name: str | None = None
        tool_approval = False
        if self.registry is not None:
            try:
                call = parse_tool_call(data.text)
            except ToolError:
                call = None
            if call is not None:
                definition = self.registry.get(call.name)
                if definition is not None:
                    tool_name = call.name
                    tool_approval = definition.requires_explicit_approval
        requires_approval = (
            data.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL} or tool_approval
        )
        action = f"execute tool {tool_name}" if tool_name else "prepare a direct response"
        plan = ExecutionPlan(
            request_id=data.request_id,
            steps=[
                PlanStep(
                    id="respond",
                    action=action,
                    requires_evidence=False,
                    requires_approval=requires_approval,
                    risk_level=data.risk_level,
                )
            ],
            rationale=(
                "Deterministic baseline plan; complex actions require governed tool selection."
            ),
        )
        return AgentArtifact(
            role=self.role,
            request_id=data.request_id,
            content=plan.model_dump_json(),
            risk_level=data.risk_level,
            metadata={
                "requires_approval": requires_approval,
                "tool": tool_name,
            },
        )


class ResearchAgent:
    role = AgentRole.RESEARCH

    def __init__(
        self,
        retriever: Retriever | None = None,
        memory: MemoryStore | None = None,
    ) -> None:
        self.retriever = retriever
        self.memory = memory

    async def run(self, data: AgentInput) -> AgentArtifact:
        evidence = await self._retrieve(data.text, data.user_id)
        memory_notes = await self._recall(data.user_id)
        if evidence or memory_notes:
            content = (
                f"Research retrieved {len(evidence)} evidence item(s) and "
                f"{len(memory_notes)} memory note(s)."
            )
        else:
            content = "No additional context available."
        return AgentArtifact(
            role=self.role,
            request_id=data.request_id,
            content=content,
            risk_level=data.risk_level,
            evidence_ids=[item.evidence_id for item in evidence],
            metadata={
                "evidence": [
                    {
                        "evidence_id": item.evidence_id,
                        "source_id": item.source_id,
                        "text": item.text,
                        "score": item.score,
                    }
                    for item in evidence
                ],
                "memory": memory_notes,
            },
        )

    async def _retrieve(self, query: str, user_id: str) -> list[Evidence]:
        if self.retriever is None:
            return []
        try:
            return await self.retriever.retrieve(query, limit=3, user_id=user_id)
        except Exception:
            logger.warning("RAG retrieval failed for research; continuing without evidence")
            return []

    async def _recall(self, user_id: str) -> list[str]:
        if self.memory is None:
            return []
        try:
            items = await self.memory.retrieve(user_id, limit=5)
        except Exception:
            logger.warning("Memory retrieval failed for research; continuing without context")
            return []
        return [item.content for item in items]


class ExecutionAgent:
    role = AgentRole.EXECUTION

    MAX_TOOL_ROUNDS = 15
    # FASE M.2: Maximum fix-test cycles before forced stop
    MAX_FIX_CYCLES = 5
    # FASE Q.8: explicit wall-clock budget for the whole agentic loop.
    # Slow models must not block a request indefinitely; this does NOT
    # shorten provider timeouts, it stops the loop with an explanation.
    MAX_TOTAL_DURATION_SECONDS = DEFAULT_TOTAL_DURATION_BUDGET_SECONDS

    # Models known to not reliably produce tool calls; fallback strategies apply.
    NON_TOOL_CALLING_MODELS: frozenset[str] = frozenset({
        "llama3", "llama3:8b", "llama3:70b",
    })

    def __init__(
        self,
        provider: AIProvider | None = None,
        registry: ToolRegistry | None = None,
        observability: Observability | None = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.observability = observability
        # K.5: metrics of the most recent provider-backed run (diagnostics).
        self.last_metrics: RequestMetrics | None = None
        # FASE O: evidence chain for response validation
        self._evidence = EvidenceTracker()

    async def run(self, data: AgentInput) -> AgentArtifact:
        if data.context.get("authorized", True) is not True:
            return AgentArtifact(
                role=self.role,
                request_id=data.request_id,
                content="Execution blocked: authorization required.",
                risk_level=data.risk_level,
                blocked=True,
            )
        # Try explicit @tool: invocation first (user-initiated tool call)
        tool_artifact = await self._try_tool(data)
        if tool_artifact is not None:
            return tool_artifact

        if self.provider is not None:
            content = await self._run_with_provider(data)
        else:
            content = data.context.get("planned_output", data.text)

        return AgentArtifact(
            role=self.role,
            request_id=data.request_id,
            content=content,
            risk_level=data.risk_level,
            metadata=self._execution_metadata(),
        )

    def _execution_metadata(self) -> dict[str, Any]:
        """FASE AB.4/AB.6: expose the resolved execution chain so the API can
        surface requested/selected/attempted/fallback/executed honestly."""
        meta: dict[str, Any] = {}
        if self.last_metrics is None:
            return meta
        m = self.last_metrics
        meta["executed_provider"] = m.provider
        meta["executed_model"] = m.model
        meta["requested_provider"] = m.requested_provider
        meta["requested_model"] = m.requested_model
        # FASE AB.6: the routing-mode selection recorded before execution.
        meta["selected_provider"] = m.selected_provider or m.requested_provider
        meta["selected_model"] = m.selected_model or m.requested_model
        # The provider actually attempted (and failed) is the fallback-from side.
        meta["attempted_provider"] = m.fallback_from_provider or m.provider
        meta["attempted_model"] = m.fallback_from_model or m.model
        meta["fallback_active"] = bool(m.fallback_executed)
        meta["fallback_chain"] = list(m.fallback_chain)
        meta["status"] = m.status
        meta["latency_ms"] = m.latency_ms
        meta["routing_reason"] = m.routing_reason
        if m.fallback_executed:
            meta["fallback_from_provider"] = m.fallback_from_provider
            meta["fallback_from_model"] = m.fallback_from_model
            meta["fallback_model"] = m.fallback_model
        return meta

    async def _run_with_provider(self, data: AgentInput) -> str:
        """Run the LLM with system prompt and agentic tool-calling loop."""
        assert self.provider is not None  # guarded by caller
        from personal_ai_secretary.domain.contracts import ConversationTurn
        from personal_ai_secretary.tools.prompt import (
            build_system_prompt,
            build_tool_result_prompt,
        )

        observation = self.observability
        provider = self.provider
        metrics = RequestMetrics()

        # Detect provider/model
        metrics.provider = provider.name
        provider_model = getattr(provider, "model", None)
        if isinstance(provider_model, str) and provider_model:
            metrics.model = provider_model
        metrics.requested_provider = provider.name
        metrics.requested_model = (
            str(provider_model) if isinstance(provider_model, str) else ""
        )
        # FASE AB.6: the routing-mode selection (what was chosen BEFORE any
        # fallback) must be recorded independently of what finally executed.
        metrics.selected_provider = provider.name
        metrics.selected_model = metrics.requested_model

        # Build compact system prompt with tool descriptions
        tool_names = self.registry.names() if self.registry else []
        compact_descs = self._tool_description_lines()
        memory_notes = data.context.get("memory_notes")
        extra_context: str | None = None
        if isinstance(memory_notes, list) and memory_notes:
            extra_context = "; ".join(str(n) for n in memory_notes[:5])

        # FASE S.1: Classify task early for task-aware prompt building
        task_type_for_prompt = classify_task(data.text)
        _SIMPLE_TASKS = ("chat", "file_read", "file_exists", "list_directory", "datetime")
        is_simple_task = task_type_for_prompt.value in _SIMPLE_TASKS

        system_prompt = build_system_prompt(
            tool_names=tool_names,
            compact_descriptions=compact_descs,
            extra_context=extra_context,
            model_name=metrics.model or None,
            task_type=task_type_for_prompt.value,
        )

        # FASE K.5 — model-aware context budget and assembly
        budget = ContextBudget.from_profile(resolve_model_profile(metrics.model))
        assembler = ContextAssembler(budget)

        # Attached files (optional context key) formatted within budget.
        attached_ctx: AttachedFilesContext | None = None
        raw_attached = data.context.get("attached_files")
        if isinstance(raw_attached, list) and raw_attached:
            attached_ctx = format_attached_files(raw_attached, budget.attached_files_chars)

        project_context = data.context.get("project_context")
        project_context_str = (
            str(project_context) if isinstance(project_context, str) and project_context else None
        )

        history = data.context.get("conversation_history", [])
        assembled = assembler.assemble(
            system_prompt=system_prompt,
            history=history if isinstance(history, list) else [],
            attached=attached_ctx,
            project_context=project_context_str,
        )
        metrics.context_build_time = assembled.stats.build_time
        metrics.truncation_applied = assembled.stats.truncation_applied
        metrics.summary_used = assembled.stats.summary_used
        metrics.history_truncated_turns = assembled.stats.history_dropped_turns
        metrics.attached_file_chars = assembled.stats.attached_file_chars
        metrics.project_context_chars = assembled.stats.project_context_chars

        # Combine system prompt with context lines (compact)
        context_lines: list[str] = []
        evidence_meta = data.context.get("research_evidence")
        if isinstance(evidence_meta, list) and evidence_meta:
            evidence_texts = [
                e.get("text", "")[:200]
                for e in evidence_meta[:3]
                if isinstance(e, dict)
            ]
            if evidence_texts:
                context_lines.append("Evidence: " + "; ".join(evidence_texts))
        if context_lines:
            assembled.system_prompt += "\n" + "\n".join(context_lines)

        system_prompt = assembled.system_prompt
        messages = assembled.messages

        metrics.system_prompt_chars = len(system_prompt)
        metrics.history_chars = sum(len(m.content) for m in messages)

        # FASE K.5 — incremental project context tracker (metadata only).
        project_tracker = ProjectContextTracker()

        # FASE N: Classify task and load profile for verification/failure strategy
        task_type = classify_task(data.text)
        task_profile = get_task_profile(task_type)
        metrics.final_outcome = task_type.value
        logger.info("Task classified: %s (goal: %s)", task_type.value, task_profile.goal)

        # FASE N: Model intelligence — check capabilities and log
        model_name = data.context.get("model", "") if data.context else ""
        model_caps = get_model_capabilities(model_name)
        if model_name:
            logger.info(
                "Model capabilities: tool_calling=%s, vision=%s, coding=%d",
                model_caps.tool_calling, model_caps.supports_vision,
                model_caps.coding_strength,
            )
            # Log fallback suggestion if applicable
            failure_info = classify_failure("start")
            if should_suggest_fallback(failure_info, model_name):
                logger.warning(
                    "Model '%s' may be insufficient for this task; consider fallback",
                    model_name,
                )

        # Agentic loop: LLM -> detect tool call -> execute -> feed back -> repeat
        current_messages = list(messages)
        final_text = ""
        # Track repeated tool calls for stall detection
        seen_tool_calls: dict[str, int] = {}
        tool_name_counts: dict[str, int] = {}
        # Track executed calls for dedup
        executed_calls: set[str] = set()
        # FASE M.2: Track files created/modified in this session to avoid re-reading
        session_files_created: set[str] = set()
        session_files_modified: set[str] = set()
        MAX_SAME_TOOL_CALLS = 2  # Exact duplicate (same tool + same args)
        MAX_SAME_TOOL_NAME = 4   # Same tool name (any args) — reduced from 6 to limit waste
        # FASE M.2: Track fix cycles (modify_file followed by execute_command)
        fix_cycle_count = 0
        last_was_modify = False
        # Tool result size cap (chars fed back to LLM), bounded by the
        # model-aware tool budget (K.5).
        MAX_TOOL_RESULT_CHARS = min(2000, max(budget.tool_results_chars, 200))
        loop_start_time = time.monotonic()

        # ── FASE Q — deterministic task control ──────────────────────────
        # Q.4 contract built by the backend; the LLM never has to remember it.
        workspace_dir = data.context.get("working_directory", "")
        task_contract: TaskContract = build_task_contract(
            data.text,
            task_type,
            workspace=workspace_dir if isinstance(workspace_dir, str) else "",
            approval_required=task_profile.approval_required,
        )
        # Q.1 explicit observable state machine.
        task_machine = TaskStateMachine()
        task_machine.transition(TaskState.UNDERSTANDING)
        task_machine.transition(TaskState.PLANNING)
        # Q.2 progress detector (valid repetition vs unproductive loop).
        progress = ProgressDetector()
        stall_rounds = 0          # consecutive rounds with zero new executions
        consecutive_failures = 0  # Q.6 consecutive failed tool operations
        mutation_done = False     # has the task reached MODIFYING/EXECUTING?
        project_desc = None       # FASE S.1.10: initialized for simple task fast path
        blocked_providers: set[str] = set()  # FASE AB.4: cumulative per-request failures
        # AB.5: last successful tool result, so a loop that must give up can
        # still surface REAL evidence to the user instead of raw protocol text.
        last_tool_summary: str = ""
        last_tool_name: str = ""
        task_machine.transition(TaskState.READING)

        for _round in range(self.MAX_TOOL_ROUNDS):
            # ── FASE Q.8 — explicit total-duration budget ─────────────────
            round_elapsed = time.monotonic() - loop_start_time
            if duration_budget_exceeded(
                round_elapsed, self.MAX_TOTAL_DURATION_SECONDS
            ):
                logger.warning(
                    "Duration budget exceeded (%.1fs >= %.1fs); stopping loop.",
                    round_elapsed,
                    self.MAX_TOTAL_DURATION_SECONDS,
                )
                final_text = (
                    "I stopped because this request exceeded its time budget "
                    f"({self.MAX_TOTAL_DURATION_SECONDS:.0f}s). Here is what was "
                    "accomplished so far and what remains."
                )
                metrics.duration_budget_exceeded = True
                task_machine.transition(TaskState.FAILED)
                break

            # K.5: prune older tool results when the running context exceeds
            # the total budget; recent results needed to continue stay intact.
            running_context_chars = len(system_prompt) + sum(
                len(m.content) for m in current_messages
            )
            if running_context_chars > budget.total_chars:
                current_messages, prune_stats = prune_tool_results(
                    current_messages, budget.tool_results_chars
                )
                if prune_stats.pruned_count:
                    metrics.truncation_applied = True

            # K.5: incremental project context — refresh per round so the
            # model sees the current working set without file contents.
            # FASE Q.4: the backend-owned task contract is injected every
            # round so the model never has to remember the objective.
            # FASE S.1.10: Skip project context and task contract for simple tasks
            round_system_prompt = system_prompt
            if not is_simple_task:
                project_desc = project_tracker.describe()
                if project_desc:
                    round_system_prompt = f"{system_prompt}\n{project_desc}"
                round_system_prompt = (
                    f"{round_system_prompt}\n{task_contract.render_directive()}"
                )

            # FASE AB.6: forward real image attachments on EVERY round so each
            # provider generation (including post-tool rounds) still shows
            # the image to the vision model.
            round_images: list[str] = []
            _ctx_images = (data.context or {}).get("images") or []
            for _img in _ctx_images:
                if isinstance(_img, str) and _img:
                    round_images.append(_img)
            if _round == 0 and round_images:
                round_system_prompt = (
                    f"{round_system_prompt}\n"
                    "An image was attached by the user and is being sent to you. "
                    "Observe it carefully and describe what it contains."
                )

            envelope = RequestEnvelope(
                request_id=data.request_id,
                session_id=data.session_id,
                user_id=data.user_id,
                input=data.text,
                risk_level=data.risk_level,
                correlation_id=data.correlation_id,
                messages=current_messages,
                context_summary=round_system_prompt,
                tool_schemas=self._build_tool_schemas(provider),
                images=round_images,
            )

            if observation is not None:
                observation.inc("provider_executions")
                await observation.emit(
                    stage="provider",
                    request_id=data.request_id,
                    user_id=data.user_id,
                    correlation_id=data.correlation_id,
                    session_id=data.session_id,
                    outcome="started",
                    details={"provider": provider.name, "round": _round + 1},
                )

            try:
                with Timer() as timer, start_span("provider.run") as provider_span:
                    set_span_correlation(data.correlation_id)
                    provider_span.set_attribute(ATTRIBUTE_PROVIDER, provider.name)
                    provider_span.set_attribute(ATTRIBUTE_STAGE, self.role.value)
                    try:
                        response = await provider.generate(envelope)
                    except BaseException as exc:
                        mark_span_error(exc)
                        provider_span.set_attribute(ATTRIBUTE_OUTCOME, "error")
                        raise
                    provider_span.set_attribute(ATTRIBUTE_OUTCOME, "ok")
            except (ConnectionError, TimeoutError, ValueError, RuntimeError) as exc:
                logger.warning("Provider error in round %d: %s", _round + 1, exc)
                if observation is not None:
                    observation.inc("provider_failures")
                    await observation.emit(
                        stage="provider",
                        request_id=data.request_id,
                        user_id=data.user_id,
                        correlation_id=data.correlation_id,
                        session_id=data.session_id,
                        outcome="error",
                        error=exc,
                        details={"provider": provider.name, "round": _round + 1},
                    )
                # ── FASE U: Intelligent fallback ──────────────────────────
                # When a provider fails, try to switch to a fallback provider
                # instead of just failing. This makes the system resilient.
                # FASE Y: Skip fallback in MANUAL mode - user chose exactly
                # what they want, and we must not silently switch.
                from personal_ai_secretary.providers.factory import get_model_manager
                from personal_ai_secretary.providers.model_manager import ModelManager

                manager = get_model_manager()
                _is_manual = (
                    isinstance(manager, ModelManager)
                    and manager.routing_mode == "manual"
                )
                if _is_manual:
                    manual_provider_name = getattr(provider, "name", "the selected provider")
                    logger.warning(
                        "MANUAL mode: provider '%s' failed, no fallback allowed. "
                        "Reporting error.",
                        manual_provider_name,
                    )
                    metrics.model_failures += 1
                    metrics.latency_classification = classify_latency_source(
                        sum(metrics.llm_times),
                        sum(metrics.tool_times),
                        float(sum(metrics.command_durations)),
                    )
                    metrics.finish()
                    metrics.latency_ms = int(metrics.total_time * 1000)
                    metrics.status = _status_for_exception(exc)
                    self.last_metrics = metrics
                    # FASE AB.6: keep the strict no-fallback contract AND the
                    # specific cause so the user knows what actually failed.
                    _cause_phrase = _STATUS_CAUSE_PHRASES.get(
                        metrics.status, "the provider reported an error"
                    )
                    _detail = _redact_internal_paths(str(exc))[:160].strip()
                    message = (
                        f"Provider '{manual_provider_name}' is unavailable or "
                        f"authentication failed ({_cause_phrase}). "
                        + (f"Detail: {_detail}. " if _detail else "")
                        + "In Manual mode, no automatic fallback is allowed. "
                        "Please configure a valid provider and model and try again."
                    )
                    message = _redact_internal_paths(message)
                    return self._sanitize_response(message)
                elif isinstance(manager, ModelManager) and manager._initialized:
                    failed_provider = getattr(provider, "name", "")
                    failed_model = getattr(provider, "model", "")
                    # FASE AB.4: only connectivity/credential failures blacklist
                    # the provider for the rest of the request — a dead server's
                    # other models can't help, but a bad model from a reachable
                    # provider must not block that provider's other models.
                    if isinstance(exc, (ConnectionError, TimeoutError)):
                        blocked_providers.add(failed_provider)
                    fallback = manager.get_fallback_provider(
                        failed_provider, failed_model, blocked_providers
                    )
                    if fallback:
                        fb_provider_name, fb_model_id = fallback
                        fb_instance = manager.get_provider_instance(fb_provider_name)
                        if fb_instance is not None:
                            logger.info(
                                "Fallback: switching from %s/%s to %s/%s",
                                failed_provider,
                                failed_model,
                                fb_provider_name,
                                fb_model_id,
                            )
                            provider = fb_instance
                            # FASE AB.4: apply the resolved fallback model so the
                            # selected fallback model_id actually executes.
                            try:
                                cast(ModelSwitchable, provider).model = fb_model_id
                            except (AttributeError, TypeError):
                                logger.warning(
                                    "Fallback: provider '%s' does not allow "
                                    "switching model, keeping configured model",
                                    fb_provider_name,
                                )
                            metrics.fallback_suggested = True
                            metrics.fallback_executed = True
                            metrics.fallback_model = fb_model_id
                            metrics.fallback_from_provider = failed_provider
                            metrics.fallback_from_model = failed_model
                            # FASE AB.6: preserve the full attempted chain.
                            metrics.fallback_chain.append(
                                f"{failed_provider}:{failed_model}"
                            )
                            metrics.provider = fb_provider_name
                            metrics.model = fb_model_id
                            # Record the failure of the original provider
                            manager.record_failure(failed_provider, failed_model)
                            # Continue the loop with the new provider
                            continue
                # ── FASE Q.7 — model-aware failure handling ──────────────
                # Classify the failure, never retry blindly, and surface a
                # fallback suggestion so a failing model cannot block the app.
                failure_info = classify_failure(exc)
                metrics.model_failures += 1
                task_machine.transition(TaskState.FAILED)
                metrics.latency_classification = classify_latency_source(
                    sum(metrics.llm_times),
                    sum(metrics.tool_times),
                    float(sum(metrics.command_durations)),
                )
                metrics.state_transitions = task_machine.history_values()
                metrics.final_task_state = task_machine.state.value
                metrics.finish()
                metrics.latency_ms = int(metrics.total_time * 1000)
                metrics.status = _status_for_exception(exc)
                message = (
                    f"I couldn't complete the request ({failure_info.reason}): "
                    f"{exc}. {failure_info.suggestion}"
                )
                # FASE AB.5: never leak internal absolute paths to the user.
                # Exception text can embed filesystem paths (e.g. a failing
                # .ollama model path on a Unix box); scrub them from the
                # user-facing message. Scoped to the error path only — normal
                # responses may legitimately mention real user file paths.
                message = _redact_internal_paths(message)
                fallback_model = should_suggest_fallback(
                    failure_info, metrics.model or ""
                )
                if fallback_model:
                    metrics.fallback_suggested = True
                    metrics.fallback_model = fallback_model
                    message += (
                        f" You can retry with model '{fallback_model}', "
                        "which may handle this task better."
                    )
                self.last_metrics = metrics
                return self._sanitize_response(message)
            except Exception as exc:
                logger.exception("Unexpected provider error in round %d", _round + 1)
                if observation is not None:
                    observation.inc("provider_failures")
                    await observation.emit(
                        stage="provider",
                        request_id=data.request_id,
                        user_id=data.user_id,
                        correlation_id=data.correlation_id,
                        session_id=data.session_id,
                        outcome="error",
                        error=exc,
                        details={"provider": provider.name, "round": _round + 1},
                    )
                metrics.finish()
                metrics.latency_ms = int(metrics.total_time * 1000)
                metrics.status = _status_for_exception(exc)
                return (
                    "I encountered an unexpected error while processing your request. "
                    "Please try again."
                )

            llm_elapsed = timer.elapsed_seconds
            llm_output = response.text
            response_chars = len(llm_output) if llm_output else 0
            metrics.record_llm_call(llm_elapsed, response_chars)

            if observation is not None:
                observation.record_duration("provider", llm_elapsed)
                await observation.emit(
                    stage="provider",
                    request_id=data.request_id,
                    user_id=data.user_id,
                    correlation_id=data.correlation_id,
                    session_id=data.session_id,
                    outcome="ok",
                    details={"provider": provider.name, "round": _round + 1},
                )

            if not llm_output:
                logger.warning("Empty LLM response in round %d", _round + 1)
                metrics.finish()
                metrics.latency_ms = int(metrics.total_time * 1000)
                metrics.status = "failed"
                return (
                    "The model returned an empty response. "
                    "Please try rephrasing your question."
                )

            final_text = llm_output

            # Record round context size
            approx_context = len(round_system_prompt) + sum(
                len(m.content) for m in current_messages
            )
            metrics.record_round(
                approx_context,
                len(round_system_prompt),
                sum(len(m.content) for m in messages),
                estimated_tokens=estimate_tokens(approx_context),
                attached_file_chars=metrics.attached_file_chars,
                project_context_chars=len(project_desc) if project_desc else 0,
                truncation_applied=metrics.truncation_applied,
                summary_used=metrics.summary_used,
            )

            # Try to detect tool calls in the LLM output (may be multiple)
            tool_calls = self._extract_all_tool_calls(llm_output)
            if not tool_calls or self.registry is None:
                # FASE Y (WS11): a mutating task whose model reply contains no
                # tool call has NOT performed its required change. Coach the
                # model toward the required mutation before declaring
                # completion. Small local models often answer with prose once
                # before acting, so allow up to 3 bounded nudges.
                if (
                    task_contract.requires_modification
                    and not mutation_done
                    and metrics.backend_directives < 3
                ):
                    directive = (
                        "You have not performed the required change. "
                        "Call the appropriate tool now (e.g. create_file/write_file) "
                        "with the exact path and content requested. Do not reply "
                        "with a plan — act."
                    )
                    current_messages.append(
                        ConversationTurn(role="user", content=directive)
                    )
                    metrics.backend_directives += 1
                    logger.info(
                        "FASE Y mutation directive injected: no tool call in mutating task"
                    )
                    continue
                break

            # ── FASE Q — backend-controlled call admission ───────────────
            # Normalize proposals and partition fresh vs already-executed.
            proposed: list[tuple[str, dict[str, Any], str]] = []
            for tool_name, tool_args in tool_calls:
                normalized_args = {
                    k: v.strip() if isinstance(v, str) else v
                    for k, v in tool_args.items()
                }
                call_key = f"{tool_name}:{json.dumps(normalized_args, sort_keys=True)}"
                proposed.append((tool_name, normalized_args, call_key))
                seen_tool_calls[call_key] = seen_tool_calls.get(call_key, 0) + 1
                tool_name_counts[tool_name] = tool_name_counts.get(tool_name, 0) + 1
                if call_key in executed_calls:
                    metrics.deduplicated_calls += 1

            fresh = [p for p in proposed if p[2] not in executed_calls]

            def _unproductive(stop_message: str) -> None:
                metrics.unproductive_loop_detections += 1
                task_machine.transition(TaskState.FAILED)
                logger.warning("FASE Q unproductive loop detected: %s", stop_message)

            if not fresh:
                # Every proposed call was already executed -> stalled round.
                stall_rounds += 1
                if (
                    task_contract.requires_modification
                    and not mutation_done
                ):
                    if progress.directives_issued == 0:
                        # Q.3: coach once toward MODIFYING before stopping.
                        directive = progress.directive_for(task_contract)
                        current_messages.append(
                            ConversationTurn(role="user", content=directive)
                        )
                        metrics.backend_directives += 1
                        logger.info("FASE Q directive injected: %s", directive)
                        # Give the model the next round to comply.
                        continue
                    stall_stop_msg = (
                        "I kept re-reading information I already had instead of "
                        "acting on it. I stopped to avoid an unproductive loop. "
                        "Here is what was accomplished and what is still missing."
                    )
                    _unproductive(stall_stop_msg)
                    final_text = stall_stop_msg
                    break
                # Non-modification task (analysis/chat): info already gathered.
                # FASE Y (WS10/WS11): when the model stalls by re-proposing an
                # already-executed call, coach it once to ANSWER with the real
                # tool result instead of leaving the tool-call protocol text as
                # the final response.
                if metrics.backend_directives == 0:
                    directive = (
                        "The requested tool has already been executed and its "
                        "result is shown above. Do not call any tool again. "
                        "Answer the user directly using that result."
                    )
                    current_messages.append(
                        ConversationTurn(role="user", content=directive)
                    )
                    metrics.backend_directives += 1
                    logger.info("FASE Y answer directive injected after tool exec")
                    continue
                # AB.5: the model still refuses to answer. Never expose raw
                # tool-call protocol text as the final message: surface the
                # REAL tool result gathered so far.
                if last_tool_name and last_tool_summary:
                    final_text = (
                        f"I executed '{last_tool_name}' and its real result is:\n"
                        f"{last_tool_summary}\n\n"
                        "(The model did not produce a plain-text summary; "
                        "showing the actual tool result above.)"
                    )
                else:
                    final_text = (
                        "I gathered the tool results but could not produce a "
                        "final answer. Please refresh the page and try again."
                    )
                break

            # Limit checks on repeated behaviour (valid repetition vs loop):
            # repeating with NEW args/results is fine; identical repeats are not.
            round_action = ""  # "", "break", "coached", or name-limit "break"
            for tool_name, _tool_args, call_key in proposed:
                if seen_tool_calls[call_key] > MAX_SAME_TOOL_CALLS:
                    logger.warning(
                        "Repeated identical tool call detected: %s (x%d).",
                        call_key,
                        seen_tool_calls[call_key],
                    )
                    if (
                        task_contract.requires_modification
                        and not mutation_done
                        and progress.directives_issued == 0
                    ):
                        directive = progress.directive_for(task_contract)
                        current_messages.append(
                            ConversationTurn(role="user", content=directive)
                        )
                        metrics.backend_directives += 1
                        logger.info("FASE Q directive injected: %s", directive)
                        stall_rounds = 0
                        round_action = "continue"
                    else:
                        final_text = (
                            f"I detected that I was repeating the same action ({tool_name}) "
                            "without making progress. I stopped to avoid an infinite loop. "
                            "Here is a summary of what was accomplished."
                        )
                        _unproductive(final_text)
                        round_action = "break"
                    break
                if tool_name_counts[tool_name] >= MAX_SAME_TOOL_NAME:
                    logger.warning(
                        "Tool name '%s' called %d times. Stopping excessive usage.",
                        tool_name,
                        tool_name_counts[tool_name],
                    )
                    final_text = (
                        f"I've used the '{tool_name}' tool {tool_name_counts[tool_name]} times. "
                        "Stopping to prevent excessive tool usage. "
                        "I'll summarize what was done."
                    )
                    _unproductive(final_text)
                    round_action = "break"
                    break
            if round_action == "break":
                break
            if round_action == "continue":
                continue

            stall_rounds = 0
            # Execute each fresh (non-duplicate) tool call
            for tool_name, tool_args, call_key in fresh:
                # Skip calls duplicated within the same batch (already
                # executed by an earlier item in this round).
                if call_key in executed_calls:
                    metrics.deduplicated_calls += 1
                    continue

                tool_start = time.monotonic()
                tool_result = await self._execute_tool_from_llm(
                    tool_name, tool_args, data
                )
                tool_elapsed = time.monotonic() - tool_start
                metrics.record_tool_call(tool_name, tool_elapsed)

                # FASE O: Record evidence for response validation
                self._evidence.record_execution(
                    tool_name=tool_name,
                    args=tool_args,
                    result=tool_result,
                    verified=not tool_result.get("verification_failed", False),
                    timestamp=tool_start,
                )

                # AB.5: remember the last successful tool outcome so a give-up
                # exit can still answer with real data (never hallucinated).
                if not tool_result.get("error"):
                    last_tool_name = tool_name
                    last_tool_summary = json.dumps(tool_result, default=str)[:1500]

                # ── FASE Q — state machine + progress tracking ───────
                # Q.2: every executed call feeds progress detection so
                # valid repetition and unproductive loops are separated
                # by backend data, not by model self-assessment.
                progress.record(tool_name, dict(tool_args), tool_result)
                mutation_done = progress.mutation_happened()
                if tool_name == "verify_files" or (
                    tool_name == "file_exists" and mutation_done
                ):
                    task_machine.transition(TaskState.VERIFYING)
                else:
                    task_machine.transition(state_for_tool(tool_name))

                # FASE N: Programmatic post-execution verification
                v_result = verify_tool_result(tool_name, tool_result)
                metrics.verification_status = "passed" if v_result.passed else "failed"
                if not v_result.passed:
                    failed_names = [c.name for c in v_result.checks if not c.passed]
                    logger.warning(
                        "Verification FAILED for tool '%s': %s",
                        tool_name, failed_names,
                    )
                    # Append verification failure hint so the LLM knows
                    # its action didn't actually produce the expected result
                    v_hint_parts = [
                        f"VERIFICATION FAILED — {c.detail}"
                        for c in v_result.checks if not c.passed
                    ]
                    tool_result = {
                        **tool_result,
                        "verification_failed": True,
                        "verification_hints": v_hint_parts,
                        "error": (
                            tool_result.get("error", "")
                            or "Verification failed: operation did not produce expected result"
                        ),
                    }
                    # Q.1: a failed verification moves the task to CORRECTING.
                    task_machine.transition(TaskState.CORRECTING)

                # FASE O: Workflow enforcement — pre-flight safety checks
                if tool_name in ("file_delete", "execute_command"):
                    # Safety gate: require explicit confirmation for destructive ops
                    if not tool_args.get("_safety_confirmed"):
                        tool_result = {
                            **tool_result,
                            "safety_gate": True,
                            "message": (
                                f"Destructive operation '{tool_name}' requires confirmation. "
                                "Set _safety_confirmed=true in arguments to proceed."
                            ),
                        }
                        metrics.security_blocks += 1

                # FASE O: Pre-flight checks for file operations
                if tool_name == "create_file":
                    import pathlib as _pathlib
                    parent_path = _pathlib.Path(tool_args.get("path", "")).parent
                    parent_dir = str(parent_path)
                    # Only warn if parent is non-trivial and likely doesn't exist
                    if (
                        parent_dir
                        and parent_dir not in (".", "", "/")
                        and not parent_path.exists()
                    ):
                        tool_result = {
                            **tool_result,
                            "preflight_warning": (
                                f"Parent directory does not exist: {parent_dir}. "
                                "Consider using create_directory first."
                            ),
                        }

                # FASE O: Track test execution metrics
                if tool_name == "execute_command" and "error" not in tool_result:
                    cmd = tool_args.get("command", "").lower()
                    test_keywords = ("pytest", "unittest", "test", "cargo test", "go test")
                    if any(kw in cmd for kw in test_keywords):
                        metrics.tests_executed += 1
                        exit_code = tool_result.get("exit_code", -1)
                        if exit_code == 0:
                            metrics.tests_passed += 1
                        else:
                            metrics.tests_failed += 1

                # Record command execution metrics for execute_command tool.
                if tool_name == "execute_command" and "error" not in tool_result:
                    metrics.record_command(
                        duration=float(tool_result.get("duration", 0.0)),
                        exit_code=int(tool_result.get("exit_code", -1)),
                        output_chars=(
                            len(tool_result.get("stdout", ""))
                            + len(tool_result.get("stderr", ""))
                        ),
                        output_truncated=bool(
                            tool_result.get("output_truncated", False)
                        ),
                        timed_out=bool(tool_result.get("timeout", False)),
                    )

                # FASE L.1 — Record workflow stage for UI progress.
                _WORKFLOW_STAGE_MAP: dict[str, str] = {
                    "analyze_project": "analyzing",
                    "read_files": "reading",
                    "search_files": "searching",
                    "create_project": "creating",
                    "create_file": "creating",
                    "write_file": "creating",
                    "modify_file": "modifying",
                    "execute_command": "executing",
                    "verify_files": "verifying",
                    "file_exists": "verifying",
                    "list_directory": "listing",
                }
                stage = _WORKFLOW_STAGE_MAP.get(tool_name, "")
                if stage:
                    metrics.record_stage(stage)
                    if self.observability is not None:
                        await self.observability.emit(
                            stage="workflow",
                            request_id=data.request_id,
                            user_id=data.user_id,
                            correlation_id=data.correlation_id,
                            session_id=data.session_id,
                            outcome="progress",
                            details={
                                "workflow_stage": stage,
                                "tool": tool_name,
                            },
                        )

                # If approval is required, stop the loop
                if tool_result.get("requires_approval") is True:
                    approval_info = json.dumps({
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                    }, default=str)
                    logger.info(
                        "Approval required for tool '%s'; surfacing approval request.",
                        tool_name,
                    )
                    friendly = (
                        f"I need your permission to run **{tool_name}**. "
                        "Please review the operation details and click **Approve** to proceed, "
                        "or **Cancel** to abort."
                    )
                    final_text = (
                        f"{APPROVAL_REQUIRED_PREFIX}{approval_info}\n\n{friendly}"
                    )
                    metrics.finish()
                    logger.info("Request metrics: %s", metrics.summary())
                    break

                # Track successful execution for dedup
                executed_calls.add(call_key)

                # ── FASE Q.6 — bounded failure recovery ──────────────
                # FAIL -> DIAGNOSE -> CORRECT -> RETRY -> VERIFY, with a
                # hard limit: never FAILED -> infinite retry.
                if tool_result.get("error"):
                    consecutive_failures += 1
                    metrics.consecutive_tool_failures = consecutive_failures
                    if consecutive_failures >= MAX_CONSECUTIVE_TOOL_FAILURES:
                        logger.warning(
                            "Consecutive tool failures reached %d; stopping.",
                            consecutive_failures,
                        )
                        final_text = failure_recovery_message(
                            consecutive_failures, str(tool_result.get("error"))
                        )
                        task_machine.transition(TaskState.FAILED)
                        break
                else:
                    consecutive_failures = 0

                # FASE M.2: Track fix cycles (modify_file + execute_command pair)
                if tool_name in ("modify_file", "write_file"):
                    last_was_modify = True
                    metrics.diagnosis_attempts += 1  # FASE O: Track fix attempts
                elif tool_name == "execute_command" and last_was_modify:
                    fix_cycle_count += 1
                    last_was_modify = False
                    if fix_cycle_count >= self.MAX_FIX_CYCLES:
                        logger.warning(
                            "Fix cycle limit reached (%d). Breaking loop.",
                            fix_cycle_count,
                        )
                        final_text = (
                            f"I've attempted {fix_cycle_count} fix cycles "
                            "without full success. Here is a summary of what was done "
                            "and what still needs attention."
                        )
                        break
                else:
                    last_was_modify = False

                # FASE M.2: Track files created/modified in this session
                if tool_name in ("create_file", "write_file", "create_project"):
                    fp = tool_args.get("file_path") or tool_args.get("path") or ""
                    if fp:
                        session_files_created.add(fp)
                elif tool_name == "modify_file":
                    fp = tool_args.get("file_path") or tool_args.get("path") or ""
                    if fp:
                        session_files_modified.add(fp)

                # K.5: note file/project metadata (never contents) so the
                # project context stays incremental.
                project_tracker.note_tool_result(tool_name, tool_result)

                # Cap tool result size fed back to LLM
                result_str = json.dumps(tool_result, default=str)
                if len(result_str) > MAX_TOOL_RESULT_CHARS:
                    tool_result = {
                        "result": result_str[:MAX_TOOL_RESULT_CHARS] + "...[truncated]",
                    }
                    metrics.tool_results_chars += MAX_TOOL_RESULT_CHARS
                else:
                    metrics.tool_results_chars += len(result_str)

                # Add recovery guidance if error
                enhanced_result = dict(tool_result)
                if tool_result.get("error"):
                    enhanced_result["recovery_hint"] = (
                        "Tool returned an error. Try a different approach."
                    )
                result_prompt = build_tool_result_prompt(tool_name, enhanced_result)
                current_messages.append(
                    ConversationTurn(role="user", content=result_prompt)
                )
            else:
                # All fresh calls executed normally -> continue the loop.
                current_messages.append(
                    ConversationTurn(role="assistant", content=llm_output)
                )
                continue
            break  # execution loop broke early -> stop the agentic loop

        elapsed = time.monotonic() - loop_start_time
        metrics.record_stage("completed")

        # ── FASE Q.5 — completion requires evidence ──────────────────────
        # A mutating task is never COMPLETED just because the model answered.
        approval_pending = final_text.startswith(APPROVAL_REQUIRED_PREFIX)
        if approval_pending:
            task_machine.transition(TaskState.BLOCKED)
        elif task_machine.state not in TERMINAL_STATES:
            if task_contract.requires_modification and not mutation_done:
                task_machine.transition(TaskState.FAILED)
                final_text = (
                    f"{final_text}\n\n"
                    "⚠ **Task status: NOT COMPLETED** — this task requires "
                    "changes but no modifying or executing action was performed "
                    f"(expected artifacts: "
                    f"{', '.join(task_contract.expected_artifacts) or 'unspecified'})."
                )
            else:
                task_machine.transition(TaskState.COMPLETED)

        # ── FASE Q.8 — latency source classification & budget check ─────
        metrics.latency_classification = classify_latency_source(
            sum(metrics.llm_times),
            sum(metrics.tool_times),
            float(sum(metrics.command_durations)),
        )
        metrics.duration_budget_exceeded = duration_budget_exceeded(
            elapsed, self.MAX_TOTAL_DURATION_SECONDS
        )
        metrics.state_transitions = task_machine.history_values()
        metrics.final_task_state = task_machine.state.value

        # FASE N: Final outcome determination
        if final_text.startswith("I've attempted"):
            metrics.final_outcome = f"{task_type.value}:max_fix_cycles"
        elif metrics.verification_status == "failed":
            metrics.final_outcome = f"{task_type.value}:verification_failed"
        else:
            metrics.final_outcome = task_type.value
        metrics.finish()
        metrics.latency_ms = int(metrics.total_time * 1000)
        metrics.status = "ok"
        self.last_metrics = metrics
        logger.info(
            "Agentic loop completed: rounds=%d tool_calls=%d dedup=%d elapsed=%.2fs metrics=%s",
            metrics.rounds,
            metrics.tool_calls,
            metrics.deduplicated_calls,
            elapsed,
            metrics.summary(),
        )

        # FASE N: Session-level verification — confirm all claimed file ops actually happened
        if session_files_created or session_files_modified:
            session_v = verify_session_files(session_files_created, session_files_modified)
            if not session_v.passed:
                missing = [
                    c.detail for c in session_v.checks if not c.passed
                ]
                logger.warning(
                    "Session-level verification FAILED: %d missing files", len(missing),
                )
                metrics.verification_status = "failed"
                # Append a truth guarantee note to the final response
                final_text = (
                    f"{final_text}\n\n"
                    "⚠ **Note**: I stated I created/modified files that were not found on disk: "
                    + "; ".join(missing)
                )

        # FASE O: Validate response against evidence
        validation = validate_response(final_text, self._evidence)
        if validation.disclaimers:
            final_text = (
                f"{final_text}\n\n"
                + "\n".join(validation.disclaimers)
            )
        # FASE Y (WS10): Ground datetime claims in the real tool result.
        # Some models fabricate "today's date/time" instead of using the
        # real datetime_now result, so we correct the final text with the
        # actual evidence value when it contradicts the model's prose.
        final_text = self._ground_response_with_evidence(final_text)
        # Store evidence summary in metrics for observability
        evidence_summary = self._evidence.get_evidence_summary()
        metrics.correction_attempts = evidence_summary.get("failed", 0)

        # FASE U: Record provider feedback in ModelManager
        from personal_ai_secretary.providers.factory import get_model_manager
        from personal_ai_secretary.providers.model_manager import ModelManager

        manager = get_model_manager()
        if isinstance(manager, ModelManager) and manager._initialized:
            provider_name = getattr(provider, "name", None)
            provider_model = getattr(provider, "model", None)
            if provider_name and provider_model:
                if metrics.verification_status == "failed":
                    manager.record_failure(provider_name, provider_model)
                else:
                    manager.record_success(provider_name, provider_model)

        return self._sanitize_response(final_text)

    def _ground_response_with_evidence(self, text: str) -> str:
        """Replace confirmed counterfactual claims with the real evidence.

        FASE Y (WS10): a model may answer "La fecha y hora actuales son
        25 de abril de 2023, 14:30" even though the real datetime_now
        tool (executed and recorded in evidence) returned a different
        value. We patch the final response so it cannot contradict the
        recorded real result.
        """
        import re

        grounded = text
        for record in self._evidence.records:
            if record.tool_name != "datetime_now":
                continue
            if "error" in record.result or record.result.get("verification_failed"):
                continue
            real_date = record.result.get("date")
            real_time = record.result.get("time")
            if not real_date or not real_time:
                continue
            break
        else:
            return grounded

        # Compare against the real result: if the model's prose already
        # carries the real date, leave it untouched.
        _date_in = (
            real_date in grounded
            or any(
                real_date in piece
                for piece in re.findall(r"\d{4}-\d{2}-\d{2}", grounded)
            )
        )
        _time_in = any(
            real_time in piece
            for piece in re.findall(r"\d{2}:\d{2}(?::\d{2})?", grounded)
        )
        if _date_in and _time_in:
            return grounded

        # Detect a conflicting claimed datetime in the response.
        conflicting_dates = set(re.findall(r"\d{4}-\d{2}-\d{2}", grounded))
        conflicting_times = set(re.findall(r"\d{1,2}:\d{2}(?::\d{2})?", grounded))
        has_conflict = (
            (conflicting_dates and any(d != real_date for d in conflicting_dates))
            or (conflicting_times and any(t != real_time for t in conflicting_times))
        )
        if has_conflict or (not _date_in and not conflicting_times):
            # Remove the fabricated date/time fragments to avoid a
            # self-contradicting message, then state the real value.
            grounded = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", real_date, grounded)
            grounded = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", real_time, grounded)
            appendix = (
                f"\n\n(La hora real obtenida con la herramienta es: "
                f"{real_date} a las {real_time} UTC.)"
            )
            if appendix not in grounded:
                grounded = grounded + appendix
        return grounded

    @staticmethod
    def _sanitize_response(text: str) -> str:
        """Remove internal LLM protocol tokens from the final response.

        Some models (e.g. DeepSeek) emit special tokens like <|tool_calls_begin|>
        that must never be shown to the user. DeepSeek models use Unicode
        fullwidth delimiters (\\uff5c) and lower-block separators (\\u2581)
        instead of ASCII pipes in the raw output.

        The tool token block is NESTED:
          <delim>tool_calls_begin<delim>
            <delim>tool_call_begin<delim> ... <delim>tool_call_end<delim>
          <delim>tool_calls_end<delim>
          <delim>tool_outputs_begin<delim>
            <delim>tool_output_begin<delim> content <delim>tool_output_end<delim>
          <delim>tool_outputs_end</delim>

        Non-greedy regex stops at the first closing delimiter (inner token),
        leaving intermediate content (e.g. tool output JSON).  Instead, we
        find the FIRST opening ``<delim`` and the LAST ``delim>`` and remove
        the entire range in one shot.
        """
        cleaned = text
        # ── DeepSeek non-ASCII token block removal ────────────────────
        # DeepSeek models use Unicode fullwidth characters as delimiters:
        #   <\uff5c tool \u2581 calls \u2581 begin \uff5c>
        # Found via hex analysis of actual Ollama responses.
        _DEEPSEEK_OPEN_DELIM = "\uff5c"
        _DEEPSEEK_CLOSE_DELIM = "\uff5c"
        start = cleaned.find(f"<{_DEEPSEEK_OPEN_DELIM}")
        if start != -1:
            last_close = cleaned.rfind(f"{_DEEPSEEK_CLOSE_DELIM}>", start)
            if last_close != -1:
                cleaned = cleaned[:start] + cleaned[last_close + len(f"{_DEEPSEEK_CLOSE_DELIM}>"):]
        # ── ASCII special tokens ──────────────────────────────────────
        special_tokens = [
            r"<\|tool_calls_begin\|>",
            r"<\|tool_calls_end\|>",
            r"<\|tool_call_begin\|>",
            r"<\|tool_call_end\|>",
            r"<\|tool_outputs_begin\|>",
            r"<\|tool_outputs_end\|>",
            r"<\|tool_output_begin\|>",
            r"<\|tool_output_end\|>",
            r"<\|tool_sep\|>",
            r"<\|plugin_call\|>",
            r"<\|endoftext\|>",
            r"<\|im_start\|>",
            r"<\|im_end\|>",
            r"<\|assistant\|>",
            r"<\|user\|>",
            r"<\|system\|>",
            r"\[TOOL_CALLS\]",
            r"\[TOOL_RESULTS\]",
        ]
        pattern = "|".join(special_tokens)
        cleaned = re.sub(pattern, "", cleaned)
        # ── DeepSeek mojibake fix ──────────────────────────────────────
        # DeepSeek tokenizer splits accented chars into Â + base char,
        # producing double-encoded UTF-8 (e.g. Â¡ instead of ¡).
        # Fix common Spanish/French/Portuguese patterns.
        _MOJIBAKE_MAP = {
            "\u00c2\u00a1": "\u00a1",  # Â¡ -> ¡
            "\u00c2\u00bf": "\u00bf",  # Â¿ -> ¿
            "\u00c3\u00a9": "\u00e9",  # Ã© -> é
            "\u00c3\u00a1": "\u00e1",  # Ã¡ -> á
            "\u00c3\u00ad": "\u00ed",  # Ã­ -> í
            "\u00c3\u00b3": "\u00f3",  # Ã³ -> ó
            "\u00c3\u00ba": "\u00fa",  # Ãº -> ú
            "\u00c3\u00b1": "\u00f1",  # Ã± -> ñ
            "\u00c3\u00bc": "\u00fc",  # Ã¼ -> ü
            "\u00c3\u00a0": "\u00e0",  # Ã  -> à
            "\u00c3\u00aa": "\u00ea",  # Ãª -> ê
            "\u00c3\u00b4": "\u00f4",  # Ã´ -> ô
            "\u00c3\u00a3": "\u00e3",  # Ã£ -> ã
            "\u00c2\u00ba": "\u00ba",  # Âº -> º
            "\u00c2\u00aa": "\u00aa",  # Âª -> ª
            "\u00c3\u0081": "\u00c1",  # Ã\u0081 -> Á
            "\u00c3\u0089": "\u00c9",  # Ã\u0089 -> É
            "\u00c3\u0093": "\u00d3",  # Ã\u0093 -> Ó
            "\u00c3\u009a": "\u00da",  # Ã\u009a -> Ú
            "\u00c3\u0091": "\u00d1",  # Ã\u0091 -> Ñ
        }
        for bad, good in _MOJIBAKE_MAP.items():
            cleaned = cleaned.replace(bad, good)
        # ── Fenced tool-call blocks ────────────────────────────────────
        # The agent drives tool execution from ```tool ... ``` reports; if
        # a stall leaves one of these blocks as (part of) the final text it
        # must never be shown verbatim to the user.
        cleaned = re.sub(r"```tool\s*\n.*?\n```", "", cleaned, flags=re.DOTALL)
        # Collapse runs of blank lines produced by token removal
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        result = cleaned.strip()
        if not result and text.strip():
            logger.warning(
                "Sanitization stripped entire response! "
                "Original len=%d preview=%.200s",
                len(text), text[:200],
            )
            # FASE Y (WS11): a response that was only a raw tool-call block
            # (e.g. stalled loop left ```tool...``` as the final text) must
            # never arrive at the user empty. Fall back to a neutral summary.
            return (
                "The requested action was completed. "
                "Some details were omitted from the response."
            )
        return result

    def _extract_tool_call(self, text: str) -> tuple[str, dict[str, Any]] | None:
        """Extract a single tool call from LLM output.

        Looks for ```tool ... ``` blocks or inline tool invocations.
        Returns (tool_name, arguments) or None.
        """
        calls = self._extract_all_tool_calls(text)
        return calls[0] if calls else None

    def _tool_description_lines(self) -> list[str]:
        """Build 'name: description (args: ...)' lines for the text prompt.

        Models using the text-based ```tool``` protocol (e.g.
        deepseek-coder-v2, which has no native tool calling) frequently emit
        tool calls with empty or missing required arguments when the prompt
        only lists tool names. Appending the required and optional argument
        names to each tool line materially reduces such malformed calls.
        """
        if self.registry is None:
            return []
        lines: list[str] = []
        for name in sorted(self.registry.names()):
            definition = self.registry.get(name)
            if definition is None:
                continue
            desc = definition.compact_description or name
            arg_hints: list[str] = []
            for arg in (definition.argument_schema or {}):
                if arg not in arg_hints:
                    arg_hints.append(arg)
            for arg in sorted(definition.optional_arguments or frozenset()):
                if arg not in arg_hints:
                    arg_hints.append(arg)
            if arg_hints:
                lines.append(f"{name}: {desc} (args: {', '.join(arg_hints)})")
            else:
                lines.append(f"{name}: {desc}")
        return lines

    def _build_tool_schemas(self, provider: Any) -> list[dict[str, Any]]:
        """Build native function-call schemas for tool-capable providers.

        Returns empty list when the provider does not support native
        function calling, so callers can skip the native `tools` payload.
        """
        if self.registry is None:
            return []
        provider_name = getattr(provider, "name", "")
        if provider_name != "ollama":
            return []
        try:
            from personal_ai_secretary.providers.model_intelligence import (
                get_model_capabilities,
            )
            model_id = getattr(provider, "model", None) or ""
            caps = get_model_capabilities(str(model_id))
            if not caps.tool_calling:
                return []
        except Exception:  # noqa: BLE001
            return []
        schemas: list[dict[str, Any]] = []
        for name in sorted(self.registry.names()):
            definition = self.registry.get(name)
            if definition is None:
                continue
            if getattr(definition, "requires_explicit_approval", False):
                continue
            properties: dict[str, Any] = {}
            required: list[str] = []
            for arg_name, arg_type in (definition.argument_schema or {}).items():
                properties[arg_name] = {"type": arg_type}
                required.append(arg_name)
            for opt_name in (definition.optional_arguments or frozenset()):
                if opt_name not in properties:
                    properties[opt_name] = {"type": "string"}
            schemas.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": definition.compact_description or name,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            })
        return schemas

    def _extract_all_tool_calls(
        self, text: str
    ) -> list[tuple[str, dict[str, Any]]]:
        """Extract ALL tool calls from LLM output.

        Handles multiple model formats:
        - ```tool ... ``` blocks (primary)
        - <tool_call>...</tool_call> XML tags
        - {"tool": "...", "args": {...}} inline JSON
        - {"tool_name": "...", "tool_args": {...}} alt format
        Returns list of (tool_name, arguments) tuples.
        """
        import json
        import re

        calls: list[tuple[str, dict[str, Any]]] = []

        def _parse_tool_json(parsed: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
            """Normalize tool call JSON.

            Supports multiple model formats:
            - {"tool": "...", "args": {...}}
            - {"tool_name": "...", "tool_args": {...}}
            - {"name": "...", "parameters": {...}}   (OpenAI style)
            - {"name": "...", "arguments": {...}}    (OpenAI style)
            - {"function": {"name": "...", "arguments": ...}}
            """
            if not isinstance(parsed, dict):
                return None
            # OpenAI wrapper: {"function": {"name": ..., "arguments": ...}}
            function = parsed.get("function")
            if isinstance(function, dict) and function.get("name"):
                tool_name = function["name"]
                tool_args = function.get("arguments")
                # arguments may be a JSON-encoded string
                if isinstance(tool_args, str):
                    try:
                        import json as _json

                        decoded = _json.loads(tool_args)
                        tool_args = decoded if isinstance(decoded, dict) else {}
                    except (json.JSONDecodeError, ValueError):
                        tool_args = {}
                if not isinstance(tool_args, dict):
                    tool_args = {}
                return (str(tool_name), tool_args)
            tool_name = parsed.get("tool") or parsed.get("tool_name")
            tool_args = parsed.get("args") or parsed.get("tool_args")
            if not tool_name:
                # OpenAI style: {"name": ..., "parameters": ...}
                if "name" in parsed and (
                    "parameters" in parsed or "arguments" in parsed
                ):
                    tool_name = parsed.get("name")
                    tool_args = (
                        parsed.get("parameters") or parsed.get("arguments")
                    )
            if not tool_name:
                return None
            if not isinstance(tool_args, dict):
                tool_args = {}
            return (str(tool_name), tool_args)

        # Find all ```tool ... ``` code blocks (primary format)
        for match in re.finditer(r"```tool\s*\n(.*?)\n```", text, re.DOTALL):
            try:
                parsed = json.loads(match.group(1).strip())
                # Models (esp. deepseek-coder-v2) may emit a whole ARRAY of
                # tool calls inside a single ```tool block for multi-file tasks.
                if isinstance(parsed, list):
                    for item in parsed:
                        result = _parse_tool_json(item)
                        if result is not None:
                            calls.append(result)
                else:
                    result = _parse_tool_json(parsed)
                    if result is not None:
                        calls.append(result)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.debug("Failed to parse tool block: %s", exc)

        # Try XML tool_call format (some models like Hermes)
        if not calls:
            for match in re.finditer(
                r"<tool_call>\s*(.*?)\s*</tool_call>", text, re.DOTALL
            ):
                try:
                    parsed = json.loads(match.group(1).strip())
                    result = _parse_tool_json(parsed)
                    if result is not None:
                        calls.append(result)
                except (json.JSONDecodeError, ValueError):
                    pass

        # FASE Y: DeepSeek delimiter format.
        #   <\uff5ctool\u2581calls\u2581begin\uff5c>
        #     <\uff5ctool\u2581call\u2581begin\uff5c>function<\uff5ctool\u2581sep\uff5c>
        #     tool_name\n```json\n{args}\n```
        #     <\uff5ctool\u2581call\u2581end\uff5c>
        #   <\uff5ctool\u2581calls\u2581end\uff5c>
        if not calls:
            _D = "\uff5c"
            _S = "\u2581"
            _call_block = re.compile(
                rf"{_D}tool{_S}call{_S}begin{_D}"
                rf"(.*?)"
                rf"{_D}tool{_S}call{_S}end{_D}",
                re.DOTALL,
            )
            for cm in _call_block.finditer(text):
                inner = cm.group(1)
                sep_marker = f"function<{_D}tool{_S}sep{_D}>"
                sep_idx = inner.find(sep_marker)
                if sep_idx == -1:
                    continue
                rest = inner[sep_idx + len(sep_marker):].strip()
                name_line, _, args_part = rest.partition("\n")
                tool_name = name_line.strip()
                if not tool_name:
                    continue
                args_json = args_part.strip()
                # xargs may be wrapped in ```json ... ``` or plain XML-adjacent text.
                args_json = args_json.replace("```json", "").replace("```", "").strip()
                args: dict[str, Any] = {}
                if args_json:
                    try:
                        parsed_args = json.loads(args_json)
                        if isinstance(parsed_args, dict):
                            args = parsed_args
                    except (json.JSONDecodeError, ValueError):
                        logger.debug(
                            "DeepSeek tool args unparseable for %s: %.80s",
                            tool_name, args_json,
                        )
                calls.append((tool_name, args))

        # If no tool blocks found, try inline pattern
        if not calls:
            # Try to find {"tool": "...", "args": {...}} OR {"tool_name": "...", ...}
            calls = self._extract_inline_tool_calls(text)

        return calls

    def _extract_inline_tool_calls(
        self, text: str
    ) -> list[tuple[str, dict[str, Any]]]:
        """Extract inline tool calls from text.

        Handles nested JSON objects in args by finding balanced braces.
        Supports both {"tool": "...", "args": {...}} and {"tool_name": "...", "tool_args": {...}}.
        """
        import json
        import re

        calls: list[tuple[str, dict[str, Any]]] = []

        # OpenAI wrapper: {"function": {"name": ..., "arguments": ...}}
        pattern = re.compile(r'\{"function"\s*:\s*\{')
        for match in pattern.finditer(text):
            start = match.start()
            json_obj = self._extract_balanced_json(text, start)
            if json_obj is not None:
                try:
                    parsed = json.loads(json_obj)
                    function = parsed.get("function")
                    if isinstance(function, dict) and function.get("name"):
                        tool_args = function.get("arguments")
                        if isinstance(tool_args, str):
                            try:
                                decoded = json.loads(tool_args)
                                tool_args = (
                                    decoded
                                    if isinstance(decoded, dict)
                                    else {}
                                )
                            except (json.JSONDecodeError, ValueError):
                                tool_args = {}
                        if isinstance(tool_args, dict):
                            calls.append(
                                (str(function["name"]), tool_args)
                            )
                            break
                except (json.JSONDecodeError, ValueError):
                    pass

        # Find the start of a potential tool call (both formats)
        pattern = re.compile(r'\{"tool(?:_name)?"\s*:\s*"')
        for match in pattern.finditer(text):
            start = match.start()
            # Try to find balanced JSON starting from this position
            json_obj = self._extract_balanced_json(text, start)
            if json_obj is not None:
                try:
                    parsed = json.loads(json_obj)
                    if isinstance(parsed, dict) and ("tool" in parsed or "tool_name" in parsed):
                        tool_name = str(parsed.get("tool") or parsed.get("tool_name", ""))
                        tool_args = parsed.get("args") or parsed.get("tool_args") or {}
                        if not isinstance(tool_args, dict):
                            tool_args = {}
                        calls.append((tool_name, tool_args))
                        break  # Only first inline match
                except (json.JSONDecodeError, ValueError):
                    pass

        if not calls:
            # OpenAI style: {"name": "...", "parameters": {...}} or
            # {"name": "...", "arguments": {...}}
            pattern = re.compile(r'\{"name"\s*:\s*"')
            for match in pattern.finditer(text):
                start = match.start()
                json_obj = self._extract_balanced_json(text, start)
                if json_obj is not None:
                    try:
                        parsed = json.loads(json_obj)
                        if (
                            isinstance(parsed, dict)
                            and parsed.get("name")
                            and (
                                "parameters" in parsed or "arguments" in parsed
                            )
                        ):
                            tool_args = (
                                parsed.get("parameters")
                                or parsed.get("arguments")
                                or {}
                            )
                            if isinstance(tool_args, dict):
                                calls.append((str(parsed["name"]), tool_args))
                                break
                    except (json.JSONDecodeError, ValueError):
                        pass

        return calls

    @staticmethod
    def _extract_balanced_json(text: str, start: int) -> str | None:
        """Extract a balanced JSON object from text starting at position."""
        if start >= len(text) or text[start] != "{":
            return None

        depth = 0
        in_string = False
        escape = False

        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]

        return None

    async def _execute_tool_from_llm(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        data: AgentInput,
    ) -> dict[str, Any]:
        """Execute a tool called by the LLM."""
        observation = self.observability
        if self.registry is None:
            return {"error": "Tool registry not available"}

        definition = self.registry.get(tool_name)
        if definition is None:
            return {"error": f"Unknown tool: {tool_name}"}

        # Check approval
        approved = data.context.get("approval_granted") is True
        if definition.requires_explicit_approval and not approved:
            if observation is not None:
                await observation.emit(
                    stage="tool",
                    request_id=data.request_id,
                    user_id=data.user_id,
                    correlation_id=data.correlation_id,
                    session_id=data.session_id,
                    outcome="blocked",
                    details={"name": tool_name, "reason": "approval required"},
                )
            return {
                "error": (
                    f"Tool '{tool_name}' requires approval. "
                    "Please ask the user for confirmation."
                ),
                "requires_approval": True,
            }

        try:
            with Timer() as timer, start_span("tool.run") as tool_span:
                set_span_correlation(data.correlation_id)
                tool_span.set_attribute(ATTRIBUTE_TOOL_NAME, tool_name)
                tool_span.set_attribute(ATTRIBUTE_STAGE, self.role.value)
                try:
                    with self._workspace_bound(data):
                        result = await self.registry.execute(
                            tool_name, tool_args, approved=approved
                        )
                except BaseException as exc:
                    mark_span_error(exc)
                    tool_span.set_attribute(ATTRIBUTE_OUTCOME, "error")
                    raise
                tool_span.set_attribute(ATTRIBUTE_OUTCOME, "ok")
        except ToolError as exc:
            if observation is not None:
                observation.inc("tool_failures")
                await observation.emit(
                    stage="tool",
                    request_id=data.request_id,
                    user_id=data.user_id,
                    correlation_id=data.correlation_id,
                    session_id=data.session_id,
                    outcome="error",
                    error=exc,
                    details={"name": tool_name},
                )
            return {"error": f"Tool error: {exc}"}
        except Exception as exc:
            if observation is not None:
                observation.inc("tool_failures")
                await observation.emit(
                    stage="tool",
                    request_id=data.request_id,
                    user_id=data.user_id,
                    correlation_id=data.correlation_id,
                    session_id=data.session_id,
                    outcome="error",
                    error=exc,
                    details={"name": tool_name},
                )
            return {"error": f"Tool execution failed: {exc}"}

        if observation is not None:
            observation.inc("tool_executions")
            observation.record_duration("tool", timer.elapsed_seconds)
            await observation.emit(
                stage="tool",
                request_id=data.request_id,
                user_id=data.user_id,
                correlation_id=data.correlation_id,
                session_id=data.session_id,
                outcome="ok",
                details={"name": tool_name},
            )
        return result

    @contextlib.contextmanager
    def _workspace_bound(self, data: AgentInput) -> Any:
        """Bind file-tool path resolution to the request's working directory.

        FASE AB.6: an agent must never write outside its workspace. Relative
        tool paths are anchored to ``working_directory`` (when provided) and
        access outside it is rejected; otherwise they fall back to
        WORKSPACE_ROOT instead of the server's process CWD.
        """
        wd = data.context.get("working_directory") if data.context else None
        if not isinstance(wd, str) or not wd:
            wd = None
        with routing_workspace(wd):
            yield

    async def _try_tool(self, data: AgentInput) -> AgentArtifact | None:
        observation = self.observability
        try:
            call = parse_tool_call(data.text)
        except ToolError as exc:
            if observation is not None:
                observation.inc("tool_failures")
                await observation.emit(
                    stage="tool",
                    request_id=data.request_id,
                    user_id=data.user_id,
                    correlation_id=data.correlation_id,
                    session_id=data.session_id,
                    outcome="error",
                    error=exc,
                )
            return self._tool_artifact(
                data, f"Invalid tool call: {exc}", {"error": str(exc)}
            )
        if call is None:
            return None
        if self.registry is None:
            if observation is not None:
                observation.inc("tool_failures")
                await observation.emit(
                    stage="tool",
                    request_id=data.request_id,
                    user_id=data.user_id,
                    correlation_id=data.correlation_id,
                    session_id=data.session_id,
                    outcome="error",
                    details={"name": call.name, "error": "tool registry not configured"},
                )
            return self._tool_artifact(
                data, f"Tool '{call.name}' is not available.", {"name": call.name}
            )
        definition = self.registry.get(call.name)
        if definition is None:
            if observation is not None:
                observation.inc("tool_failures")
                await observation.emit(
                    stage="tool",
                    request_id=data.request_id,
                    user_id=data.user_id,
                    correlation_id=data.correlation_id,
                    session_id=data.session_id,
                    outcome="error",
                    details={"name": call.name, "error": "unknown tool"},
                )
            return self._tool_artifact(
                data, f"Tool '{call.name}' is not available.", {"name": call.name}
            )
        approved = data.context.get("approval_granted") is True
        if definition.requires_explicit_approval and not approved:
            if observation is not None:
                await observation.emit(
                    stage="tool",
                    request_id=data.request_id,
                    user_id=data.user_id,
                    correlation_id=data.correlation_id,
                    session_id=data.session_id,
                    outcome="blocked",
                    details={"name": call.name, "reason": "approval required"},
                )
            return AgentArtifact(
                role=self.role,
                request_id=data.request_id,
                content=f"Execution blocked: approval required for tool '{call.name}'.",
                risk_level=data.risk_level,
                blocked=True,
                metadata={
                    "tools": [
                        {
                            "name": call.name,
                            "user_id": data.user_id,
                            "correlation_id": data.correlation_id,
                            "approved": False,
                        }
                    ]
                },
            )
        try:
            with Timer() as timer, start_span("tool.run") as tool_span:
                set_span_correlation(data.correlation_id)
                tool_span.set_attribute(ATTRIBUTE_TOOL_NAME, call.name)
                tool_span.set_attribute(ATTRIBUTE_STAGE, self.role.value)
                try:
                    with self._workspace_bound(data):
                        result = await self.registry.execute(
                            call.name, call.arguments, approved=approved
                        )
                except BaseException as exc:
                    mark_span_error(exc)
                    tool_span.set_attribute(ATTRIBUTE_OUTCOME, "error")
                    raise
                tool_span.set_attribute(ATTRIBUTE_OUTCOME, "ok")
        except ToolError as exc:
            if observation is not None:
                observation.inc("tool_failures")
                await observation.emit(
                    stage="tool",
                    request_id=data.request_id,
                    user_id=data.user_id,
                    correlation_id=data.correlation_id,
                    session_id=data.session_id,
                    outcome="error",
                    error=exc,
                    details={"name": call.name},
                )
            return self._tool_artifact(
                data,
                f"Tool '{call.name}' rejected arguments: {exc}",
                {"name": call.name, "error": str(exc)},
            )
        except Exception as exc:
            if observation is not None:
                observation.inc("tool_failures")
                await observation.emit(
                    stage="tool",
                    request_id=data.request_id,
                    user_id=data.user_id,
                    correlation_id=data.correlation_id,
                    session_id=data.session_id,
                    outcome="error",
                    error=exc,
                    details={"name": call.name},
                )
            raise
        if observation is not None:
            observation.inc("tool_executions")
            observation.record_duration("tool", timer.elapsed_seconds)
            await observation.emit(
                stage="tool",
                request_id=data.request_id,
                user_id=data.user_id,
                correlation_id=data.correlation_id,
                session_id=data.session_id,
                outcome="ok",
                details={"name": call.name},
            )
        return self._tool_artifact(
            data,
            self._format_tool_result(call.name, result),
            {"name": call.name, "arguments": call.arguments, "result": result},
        )

    def _tool_artifact(
        self, data: AgentInput, content: str, tool: dict[str, object]
    ) -> AgentArtifact:
        return AgentArtifact(
            role=self.role,
            request_id=data.request_id,
            content=content,
            risk_level=data.risk_level,
            metadata={
                "tools": [
                    {
                        **tool,
                        "user_id": data.user_id,
                        "correlation_id": data.correlation_id,
                        "approved": data.context.get("approval_granted") is True,
                    }
                ]
            },
        )

    @staticmethod
    def _format_tool_result(name: str, result: dict[str, object]) -> str:
        if result.get("error"):
            return f"Tool '{name}' reported an error: {result['error']}"
        if "result" in result:
            return f"Tool '{name}' returned: {result['result']}"
        return f"Tool '{name}' returned: {json.dumps(result)}"


class ReviewerAgent:
    role = AgentRole.REVIEWER

    async def run(self, data: AgentInput) -> AgentArtifact:
        producer = data.context.get("producer_role")
        output = data.context.get("output")
        valid_producer = isinstance(producer, str) and producer in {
            role.value for role in AgentRole if role is not AgentRole.REVIEWER
        }
        has_output = isinstance(output, str) and bool(output.strip())
        blocked = not valid_producer or not has_output
        if blocked:
            if producer == self.role.value:
                reason = "separation of duties violation"
            elif not valid_producer:
                reason = "a valid non-reviewer producer is required"
            else:
                reason = "an artifact with non-empty output is required"
            content = f"Review blocked: {reason}."
        else:
            content = f"Review passed for {producer} artifact."
        return AgentArtifact(
            role=self.role,
            request_id=data.request_id,
            content=content,
            risk_level=data.risk_level,
            blocked=blocked,
            metadata={"producer_role": producer},
        )


class ComplianceAgent:
    role = AgentRole.COMPLIANCE

    def __init__(
        self,
        rules: tuple[ComplianceRule, ...] | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._rules = rules
        self._enabled = enabled

    def _active_rules(self) -> tuple[ComplianceRule, ...]:
        # FASE 13D: the explicit toggle always wins so COMPLIANCE_ENABLED=false
        # (or an injected ``enabled=False``) is unambiguous and can never be
        # re-enabled by an injected rules tuple.
        if self._enabled is False:
            return ()
        if self._rules is not None:
            return self._rules
        settings = get_settings()
        if not settings.compliance_enabled:
            return ()
        return select_rules(settings.compliance_rules)

    async def run(self, data: AgentInput) -> AgentArtifact:
        policy_violation = data.context.get("policy_violation", False)
        output = data.context.get("output")
        output_ok = isinstance(output, str) and bool(output.strip())
        user_input = data.context.get("user_input")
        if not isinstance(user_input, str):
            user_input = data.text
        failing: ComplianceRuleResult | None = None
        if isinstance(output, str) and output_ok and not policy_violation:
            failing = evaluate_policy(user_input, output, self._active_rules())
        blocked = policy_violation is True or not output_ok or failing is not None
        if blocked:
            if failing is not None:
                content = (
                    f"Compliance blocked the request (rule: {failing.rule_id})."
                )
            else:
                content = "Compliance blocked the request."
        else:
            content = "Compliance passed."
        metadata: dict[str, object] = {"policy_violation": policy_violation}
        if failing is not None:
            metadata["rule_id"] = failing.rule_id
            metadata["rule_reason"] = failing.reason
        return AgentArtifact(
            role=self.role,
            request_id=data.request_id,
            content=content,
            risk_level=data.risk_level,
            blocked=blocked,
            metadata=metadata,
        )


DEFAULT_AGENTS: dict[AgentRole, Agent] = {
    AgentRole.PLANNER: PlannerAgent(),
    AgentRole.RESEARCH: ResearchAgent(),
    AgentRole.EXECUTION: ExecutionAgent(),
    AgentRole.REVIEWER: ReviewerAgent(),
    AgentRole.COMPLIANCE: ComplianceAgent(),
}
