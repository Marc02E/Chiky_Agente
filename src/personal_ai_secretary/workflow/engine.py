from dataclasses import dataclass, field
from time import perf_counter
from typing import Any
from uuid import UUID

from personal_ai_secretary.agents.builtin import (
    DEFAULT_AGENTS,
    ExecutionAgent,
    PlannerAgent,
    ResearchAgent,
)
from personal_ai_secretary.agents.contracts import (
    Agent,
    AgentArtifact,
    AgentInput,
    AgentRole,
    ExecutionPlan,
)
from personal_ai_secretary.domain.contracts import RiskLevel
from personal_ai_secretary.evaluation.runtime import (
    EvaluationContext,
    EvaluationOutcome,
    ReleaseGateEvaluator,
)
from personal_ai_secretary.memory.service import MemoryStore
from personal_ai_secretary.observability.observer import Observability
from personal_ai_secretary.observability.tracing import (
    ATTRIBUTE_COMPLIANCE_RULE,
    ATTRIBUTE_EVIDENCE_COUNT,
    ATTRIBUTE_OUTCOME,
    ATTRIBUTE_REQUEST_ID,
    ATTRIBUTE_RISK_LEVEL,
    ATTRIBUTE_SESSION_ID,
    ATTRIBUTE_USER_ID,
    mark_span_error,
    set_span_correlation,
    start_span,
)
from personal_ai_secretary.providers.base import AIProvider
from personal_ai_secretary.rag.service import Retriever
from personal_ai_secretary.tools.registry import ToolRegistry


@dataclass(slots=True)
class WorkflowResult:
    request_id: UUID
    status: str
    response: str | None
    artifacts: list[AgentArtifact] = field(default_factory=list)
    blocked_reason: str | None = None
    rejected_reason: str | None = None
    evaluation: EvaluationOutcome | None = None
    # FASE AB.4: honest execution chain surfaced from the execution agent.
    fallback: dict[str, Any] | None = None


class GovernedWorkflow:
    def __init__(
        self,
        provider: AIProvider | None = None,
        retriever: Retriever | None = None,
        memory: MemoryStore | None = None,
        tools: ToolRegistry | None = None,
        evaluator: ReleaseGateEvaluator | None = None,
        observability: Observability | None = None,
        agents: dict[AgentRole, Agent] | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.evaluator = evaluator or ReleaseGateEvaluator()
        self.observability = observability
        if agents is not None:
            self.agents = agents
        else:
            self.agents = dict(DEFAULT_AGENTS)
            if provider is not None or tools is not None:
                self.agents[AgentRole.EXECUTION] = ExecutionAgent(
                    provider, tools, observability
                )
            if tools is not None:
                self.agents[AgentRole.PLANNER] = PlannerAgent(tools)
            if retriever is not None or memory is not None:
                self.agents[AgentRole.RESEARCH] = ResearchAgent(retriever, memory)

    async def run(
        self,
        request_id: UUID,
        session_id: UUID,
        user_id: str,
        text: str,
        correlation_id: str,
        risk_level: RiskLevel = RiskLevel.LOW,
        context: dict[str, object] | None = None,
        session_history: list[dict[str, str]] | None = None,
    ) -> WorkflowResult:
        started = perf_counter()
        try:
            with start_span(
                "workflow.run",
                attributes={
                    ATTRIBUTE_REQUEST_ID: str(request_id),
                    ATTRIBUTE_SESSION_ID: str(session_id),
                    ATTRIBUTE_USER_ID: user_id,
                    ATTRIBUTE_RISK_LEVEL: risk_level.value
                    if isinstance(risk_level, RiskLevel)
                    else str(risk_level),
                },
            ) as root_span:
                set_span_correlation(correlation_id)
                try:
                    result = await self._run_core(
                        request_id,
                        session_id,
                        user_id,
                        text,
                        correlation_id,
                        risk_level,
                        context,
                        session_history,
                    )
                except BaseException as exc:
                    mark_span_error(exc)
                    raise
                root_span.set_attribute(ATTRIBUTE_OUTCOME, result.status)
                return result
        finally:
            if self.observability is not None:
                self.observability.record_duration(
                    "workflow", perf_counter() - started
                )

    async def _run_core(
        self,
        request_id: UUID,
        session_id: UUID,
        user_id: str,
        text: str,
        correlation_id: str,
        risk_level: RiskLevel,
        context: dict[str, object] | None,
        session_history: list[dict[str, str]] | None,
    ) -> WorkflowResult:
        observation = self.observability
        base = AgentInput(
            request_id=request_id,
            session_id=session_id,
            user_id=user_id,
            text=text,
            correlation_id=correlation_id,
            risk_level=risk_level,
            context=context or {},
        )
        artifacts: list[AgentArtifact] = []

        with start_span("planner") as plan_span:
            plan = await self.agents[AgentRole.PLANNER].run(base)
            plan_span.set_attribute(ATTRIBUTE_OUTCOME, "blocked" if plan.blocked else "ok")
        artifacts.append(plan)
        if observation is not None:
            await observation.emit(
                stage="planner",
                request_id=request_id,
                user_id=user_id,
                correlation_id=correlation_id,
                session_id=session_id,
                outcome="blocked" if plan.blocked else "ok",
                details={"requires_approval": plan.metadata.get("requires_approval", False)},
            )
        if plan.blocked:
            return await self._blocked(
                observation,
                request_id,
                session_id,
                user_id,
                correlation_id,
                artifacts,
                plan.content,
            )

        plan_model: ExecutionPlan | None = None
        try:
            plan_model = ExecutionPlan.model_validate_json(plan.content)
        except ValueError:
            plan_model = None

        if plan.metadata.get("requires_approval", False) and base.context.get(
            "approval_granted"
        ) is not True:
            reason = "Execution blocked: approval required for high-risk or critical actions."
            with start_span("approval") as approval_span:
                approval_span.set_attribute(ATTRIBUTE_OUTCOME, "blocked")
                if observation is not None:
                    await observation.emit(
                        stage="approval",
                        request_id=request_id,
                        user_id=user_id,
                        correlation_id=correlation_id,
                        session_id=session_id,
                        outcome="blocked",
                        details={"reason": reason},
                    )
            return await self._blocked(
                observation, request_id, session_id, user_id, correlation_id, artifacts, reason
            )
        with start_span("approval") as approval_span:
            approval_span.set_attribute(ATTRIBUTE_OUTCOME, "approved")
            if observation is not None:
                await observation.emit(
                    stage="approval",
                    request_id=request_id,
                    user_id=user_id,
                    correlation_id=correlation_id,
                    session_id=session_id,
                    outcome="approved",
                )

        research_context = {**base.context, "plan": plan.content}
        research_started = perf_counter()
        with start_span("research") as research_span:
            research = await self.agents[AgentRole.RESEARCH].run(
                base.model_copy(update={"context": research_context})
            )
            research_span.set_attribute(
                ATTRIBUTE_OUTCOME, "blocked" if research.blocked else "ok"
            )
            research_span.set_attribute(
                ATTRIBUTE_EVIDENCE_COUNT, len(research.evidence_ids)
            )
        if observation is not None:
            observation.record_duration(
                "research", perf_counter() - research_started
            )
            if research.evidence_ids:
                observation.inc("evidence_retrieved", len(research.evidence_ids))
            await observation.emit(
                stage="research",
                request_id=request_id,
                user_id=user_id,
                correlation_id=correlation_id,
                session_id=session_id,
                outcome="blocked" if research.blocked else "ok",
                details={"evidence_count": len(research.evidence_ids)},
            )
        artifacts.append(research)
        if research.blocked:
            return await self._blocked(
                observation,
                request_id,
                session_id,
                user_id,
                correlation_id,
                artifacts,
                research.content,
            )

        execution_context = {
            **base.context,
            "evidence": research.evidence_ids,
            "research_evidence": research.metadata.get("evidence", []),
            "memory_notes": research.metadata.get("memory", []),
            "conversation_history": session_history or [],
        }
        with start_span("execution") as execution_span:
            execution = await self.agents[AgentRole.EXECUTION].run(
                base.model_copy(update={"context": execution_context})
            )
            execution_span.set_attribute(
                ATTRIBUTE_OUTCOME, "blocked" if execution.blocked else "ok"
            )
        artifacts.append(execution)
        if observation is not None:
            await observation.emit(
                stage="execution",
                request_id=request_id,
                user_id=user_id,
                correlation_id=correlation_id,
                session_id=session_id,
                outcome="blocked" if execution.blocked else "ok",
            )
        execution_meta = dict(execution.metadata)
        fallback_info: dict[str, Any] | None = None
        # FASE AB.6: surface provenance for EVERY executed message so the UI
        # can show exactly which provider/model ran (not just fallback events).
        if execution_meta.get("executed_provider"):
            fallback_info = {
                "fallback_active": bool(execution_meta.get("fallback_active")),
                "requested_provider": execution_meta.get("requested_provider"),
                "requested_model": execution_meta.get("requested_model"),
                "selected_provider": execution_meta.get("selected_provider"),
                "selected_model": execution_meta.get("selected_model"),
                "attempted_provider": execution_meta.get("attempted_provider"),
                "attempted_model": execution_meta.get("attempted_model"),
                "fallback_from": execution_meta.get("fallback_from_provider"),
                "fallback_from_provider": execution_meta.get("fallback_from_provider"),
                "fallback_from_model": execution_meta.get("fallback_from_model"),
                "fallback_model": execution_meta.get("fallback_model"),
                "fallback_chain": execution_meta.get("fallback_chain", []),
                "routing_reason": execution_meta.get("routing_reason", ""),
                "status": execution_meta.get("status", "ok"),
                "latency_ms": execution_meta.get("latency_ms", 0),
                "executed_provider": execution_meta.get("executed_provider"),
                "executed_model": execution_meta.get("executed_model"),
            }
        if execution.blocked:
            return await self._blocked(
                observation,
                request_id,
                session_id,
                user_id,
                correlation_id,
                artifacts,
                execution.content,
            )

        review_context = {
            **base.context,
            "producer_role": execution.role.value,
            "output": execution.content,
        }
        with start_span("reviewer") as review_span:
            review = await self.agents[AgentRole.REVIEWER].run(
                base.model_copy(update={"context": review_context})
            )
            review_span.set_attribute(
                ATTRIBUTE_OUTCOME, "blocked" if review.blocked else "passed"
            )
        artifacts.append(review)
        if observation is not None:
            await observation.emit(
                stage="reviewer",
                request_id=request_id,
                user_id=user_id,
                correlation_id=correlation_id,
                session_id=session_id,
                outcome="blocked" if review.blocked else "passed",
            )

        evaluation_started = perf_counter()
        with start_span("evaluation") as evaluation_span:
            evaluation = self.evaluator.evaluate(
                EvaluationContext(
                    request_id=request_id,
                    user_id=user_id,
                    correlation_id=correlation_id,
                    artifacts=list(artifacts),
                    plan=plan_model,
                    registry=self.tools,
                )
            )
            evaluation_span.set_attribute(
                ATTRIBUTE_OUTCOME, "passed" if evaluation.passed else "rejected"
            )
        if observation is not None:
            observation.record_duration(
                "evaluation", perf_counter() - evaluation_started
            )
            observation.inc("evaluation_passes" if evaluation.passed else "evaluation_rejections")
            await observation.emit(
                stage="evaluation",
                request_id=request_id,
                user_id=user_id,
                correlation_id=correlation_id,
                session_id=session_id,
                outcome="passed" if evaluation.passed else "rejected",
                details={
                    "failed_gates": [c.name for c in evaluation.failed_criteria()],
                },
            )
        if not evaluation.passed:
            if observation is not None:
                await observation.emit(
                    stage="rejected",
                    request_id=request_id,
                    user_id=user_id,
                    correlation_id=correlation_id,
                    session_id=session_id,
                    outcome="rejected",
                    details={"reason": evaluation.rejection_reason()},
                )
            return WorkflowResult(
                request_id,
                "rejected",
                None,
                list(artifacts),
                rejected_reason=evaluation.rejection_reason(),
                evaluation=evaluation,
                fallback=fallback_info,
            )

        compliance_context = {
            **base.context,
            "user_input": text,
            "output": execution.content,
            "producer_role": execution.role.value,
            "review": review.content,
        }
        with start_span("compliance") as compliance_span:
            compliance = await self.agents[AgentRole.COMPLIANCE].run(
                base.model_copy(update={"context": compliance_context})
            )
            compliance_span.set_attribute(
                ATTRIBUTE_OUTCOME, "blocked" if compliance.blocked else "passed"
            )
            if compliance.blocked:
                rule_id = compliance.metadata.get("rule_id")
                if isinstance(rule_id, str):
                    compliance_span.set_attribute(ATTRIBUTE_COMPLIANCE_RULE, rule_id)
        artifacts.append(compliance)
        if observation is not None:
            compliance_details: dict[str, object] = {}
            if compliance.blocked:
                compliance_details["rule_id"] = compliance.metadata.get("rule_id")
            await observation.emit(
                stage="compliance",
                request_id=request_id,
                user_id=user_id,
                correlation_id=correlation_id,
                session_id=session_id,
                outcome="blocked" if compliance.blocked else "passed",
                details=compliance_details,
            )
            observation.inc(
                "compliance_blocks" if compliance.blocked else "compliance_passes"
            )
        if compliance.blocked:
            return WorkflowResult(
                request_id,
                "blocked",
                None,
                list(artifacts),
                blocked_reason=compliance.content,
                evaluation=evaluation,
                fallback=fallback_info,
            )

        with start_span("completion") as completion_span:
            completion_span.set_attribute(ATTRIBUTE_OUTCOME, "ok")
            if observation is not None:
                await observation.emit(
                    stage="completed",
                    request_id=request_id,
                    user_id=user_id,
                    correlation_id=correlation_id,
                    session_id=session_id,
                    outcome="ok",
                    details={"response": execution.content},
                )
        return WorkflowResult(
            request_id,
            "completed",
            execution.content,
            list(artifacts),
            evaluation=evaluation,
            fallback=fallback_info,
        )

    async def _blocked(
        self,
        observation: Observability | None,
        request_id: UUID,
        session_id: UUID,
        user_id: str,
        correlation_id: str,
        artifacts: list[AgentArtifact],
        reason: str,
    ) -> WorkflowResult:
        if observation is not None:
            await observation.emit(
                stage="blocked",
                request_id=request_id,
                user_id=user_id,
                correlation_id=correlation_id,
                session_id=session_id,
                outcome="blocked",
                details={"reason": reason},
            )
        return WorkflowResult(request_id, "blocked", None, artifacts, reason)