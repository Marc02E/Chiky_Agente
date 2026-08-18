import json
import logging

from personal_ai_secretary.agents.contracts import (
    Agent,
    AgentArtifact,
    AgentInput,
    AgentRole,
    ExecutionPlan,
    PlanStep,
)
from personal_ai_secretary.compliance.policy import (
    ComplianceRule,
    ComplianceRuleResult,
    evaluate_policy,
    select_rules,
)
from personal_ai_secretary.domain.contracts import RequestEnvelope, RiskLevel
from personal_ai_secretary.memory.service import MemoryStore
from personal_ai_secretary.observability.metrics import Timer
from personal_ai_secretary.observability.observer import Observability
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
from personal_ai_secretary.rag.service import Evidence, Retriever
from personal_ai_secretary.shared.config import get_settings
from personal_ai_secretary.tools.registry import (
    ToolError,
    ToolRegistry,
    parse_tool_call,
)

logger = logging.getLogger("personal_ai_secretary.agents")


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

    def __init__(
        self,
        provider: AIProvider | None = None,
        registry: ToolRegistry | None = None,
        observability: Observability | None = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.observability = observability

    async def run(self, data: AgentInput) -> AgentArtifact:
        if data.context.get("authorized", True) is not True:
            return AgentArtifact(
                role=self.role,
                request_id=data.request_id,
                content="Execution blocked: authorization required.",
                risk_level=data.risk_level,
                blocked=True,
            )
        tool_artifact = await self._try_tool(data)
        if tool_artifact is not None:
            return tool_artifact
        if self.provider is not None:
            from personal_ai_secretary.domain.contracts import ConversationTurn

            history = data.context.get("conversation_history", [])
            context_lines: list[str] = []
            memory_notes = data.context.get("memory_notes")
            if isinstance(memory_notes, list) and memory_notes:
                context_lines.append("Previous context: " + "; ".join(str(n) for n in memory_notes))
            evidence_meta = data.context.get("research_evidence")
            if isinstance(evidence_meta, list) and evidence_meta:
                evidence_texts = [
                    e.get("text", "") for e in evidence_meta if isinstance(e, dict)
                ]
                if evidence_texts:
                    context_lines.append("Research evidence: " + "; ".join(evidence_texts))
            context_summary = "\n".join(context_lines) if context_lines else None
            messages = [
                ConversationTurn(role=turn["role"], content=turn["content"])
                for turn in history
                if isinstance(turn, dict) and "role" in turn and "content" in turn
            ]
            envelope = RequestEnvelope(
                request_id=data.request_id,
                session_id=data.session_id,
                user_id=data.user_id,
                input=data.text,
                risk_level=data.risk_level,
                correlation_id=data.correlation_id,
                messages=messages,
                context_summary=context_summary,
            )
            observation = self.observability
            if observation is not None:
                observation.inc("provider_executions")
                await observation.emit(
                    stage="provider",
                    request_id=data.request_id,
                    user_id=data.user_id,
                    correlation_id=data.correlation_id,
                    session_id=data.session_id,
                    outcome="started",
                    details={"provider": self.provider.name},
                )
            try:
                with Timer() as timer, start_span("provider.run") as provider_span:
                    set_span_correlation(data.correlation_id)
                    provider_span.set_attribute(ATTRIBUTE_PROVIDER, self.provider.name)
                    provider_span.set_attribute(ATTRIBUTE_STAGE, self.role.value)
                    try:
                        response = await self.provider.generate(envelope)
                    except BaseException as exc:
                        mark_span_error(exc)
                        provider_span.set_attribute(ATTRIBUTE_OUTCOME, "error")
                        raise
                    provider_span.set_attribute(ATTRIBUTE_OUTCOME, "ok")
            except Exception as exc:
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
                        details={"provider": self.provider.name},
                    )
                raise
            if observation is not None:
                observation.record_duration("provider", timer.elapsed_seconds)
                await observation.emit(
                    stage="provider",
                    request_id=data.request_id,
                    user_id=data.user_id,
                    correlation_id=data.correlation_id,
                    session_id=data.session_id,
                    outcome="ok",
                    details={"provider": self.provider.name},
                )
            content = response.text
        else:
            content = data.context.get("planned_output", data.text)
        return AgentArtifact(
            role=self.role,
            request_id=data.request_id,
            content=content,
            risk_level=data.risk_level,
        )

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
