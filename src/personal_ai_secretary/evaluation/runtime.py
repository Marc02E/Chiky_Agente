from dataclasses import dataclass
from uuid import UUID

from personal_ai_secretary.agents.contracts import AgentArtifact, AgentRole, ExecutionPlan
from personal_ai_secretary.tools.registry import ToolRegistry

_EXPECTED_ROLES: tuple[AgentRole, ...] = (
    AgentRole.PLANNER,
    AgentRole.RESEARCH,
    AgentRole.EXECUTION,
    AgentRole.REVIEWER,
)


@dataclass(frozen=True, slots=True)
class EvaluationCriterion:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    passed: bool
    criteria: tuple[EvaluationCriterion, ...] = ()

    def failed_criteria(self) -> list[EvaluationCriterion]:
        return [criterion for criterion in self.criteria if not criterion.passed]

    def rejection_reason(self) -> str:
        if self.passed:
            return "evaluation passed"
        failed = [f"{c.name} ({c.detail})" for c in self.failed_criteria()]
        return "execution rejected by evaluation gate(s): " + "; ".join(failed)


@dataclass(frozen=True, slots=True)
class EvaluationContext:
    request_id: UUID
    user_id: str
    correlation_id: str
    artifacts: list[AgentArtifact]
    plan: ExecutionPlan | None = None
    registry: ToolRegistry | None = None


class ReleaseGateEvaluator:
    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        criteria = (
            self._artifacts_complete(context),
            self._response_present(context),
            self._reviewer_passed(context),
            self._evidence_when_required(context),
            self._tool_approval_respected(context),
            self._tool_correlation_valid(context),
        )
        return EvaluationOutcome(passed=all(c.passed for c in criteria), criteria=criteria)

    @staticmethod
    def _by_role(context: EvaluationContext, role: AgentRole) -> AgentArtifact | None:
        return next((artifact for artifact in context.artifacts if artifact.role == role), None)

    def _artifacts_complete(self, context: EvaluationContext) -> EvaluationCriterion:
        missing = [
            role.value for role in _EXPECTED_ROLES if self._by_role(context, role) is None
        ]
        if missing:
            return EvaluationCriterion(
                "artifacts_complete",
                False,
                f"missing artifact(s): {', '.join(missing)}",
            )
        return EvaluationCriterion("artifacts_complete", True, "all expected artifacts present")

    def _response_present(self, context: EvaluationContext) -> EvaluationCriterion:
        execution = self._by_role(context, AgentRole.EXECUTION)
        content = execution.content if execution is not None else ""
        if not content.strip():
            return EvaluationCriterion(
                "response_present", False, "execution produced an empty response"
            )
        return EvaluationCriterion(
            "response_present", True, "execution produced a non-empty response"
        )

    def _reviewer_passed(self, context: EvaluationContext) -> EvaluationCriterion:
        reviewer = self._by_role(context, AgentRole.REVIEWER)
        if reviewer is None:
            return EvaluationCriterion("reviewer_passed", False, "reviewer artifact missing")
        if reviewer.blocked:
            return EvaluationCriterion("reviewer_passed", False, reviewer.content)
        return EvaluationCriterion(
            "reviewer_passed", True, "reviewer approved the execution output"
        )

    def _evidence_when_required(self, context: EvaluationContext) -> EvaluationCriterion:
        requires_evidence = context.plan is not None and any(
            step.requires_evidence for step in context.plan.steps
        )
        if not requires_evidence:
            return EvaluationCriterion(
                "evidence_when_required", True, "no evidence required by the plan"
            )
        research = self._by_role(context, AgentRole.RESEARCH)
        if research is None or not research.evidence_ids:
            return EvaluationCriterion(
                "evidence_when_required",
                False,
                "plan requires evidence but research found none",
            )
        return EvaluationCriterion(
            "evidence_when_required",
            True,
            f"evidence provided: {len(research.evidence_ids)} item(s)",
        )

    def _tool_approval_respected(self, context: EvaluationContext) -> EvaluationCriterion:
        entries = self._tool_entries(context)
        violations: list[str] = []
        for entry in entries:
            name = entry.get("name")
            if not isinstance(name, str):
                continue
            definition = context.registry.get(name) if context.registry is not None else None
            if (
                definition is not None
                and definition.requires_explicit_approval
                and entry.get("approved") is not True
            ):
                violations.append(name)
        if violations:
            return EvaluationCriterion(
                "tool_approval_respected",
                False,
                f"tool(s) executed without required approval: {', '.join(sorted(violations))}",
            )
        if not entries:
            return EvaluationCriterion("tool_approval_respected", True, "no tools were executed")
        return EvaluationCriterion(
            "tool_approval_respected", True, "all executed tools respected approval rules"
        )

    def _tool_correlation_valid(self, context: EvaluationContext) -> EvaluationCriterion:
        entries = self._tool_entries(context)
        invalid: list[str] = []
        for entry in entries:
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                invalid.append("<unnamed>")
                continue
            if (
                entry.get("user_id") != context.user_id
                or entry.get("correlation_id") != context.correlation_id
            ):
                invalid.append(name)
        if invalid:
            return EvaluationCriterion(
                "tool_correlation_valid",
                False,
                "tool(s) with inconsistent user/correlation attribution: "
                + ", ".join(sorted(set(invalid))),
            )
        if not entries:
            return EvaluationCriterion("tool_correlation_valid", True, "no tools were executed")
        return EvaluationCriterion(
            "tool_correlation_valid", True, "tool attribution matches the request context"
        )

    @staticmethod
    def _tool_entries(context: EvaluationContext) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for artifact in context.artifacts:
            tools = artifact.metadata.get("tools")
            if isinstance(tools, list):
                entries.extend(tool for tool in tools if isinstance(tool, dict))
        return entries