from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from personal_ai_secretary.agents.builtin import ReviewerAgent
from personal_ai_secretary.agents.contracts import (
    AgentArtifact,
    AgentInput,
    AgentRole,
    ExecutionPlan,
    PlanStep,
)
from personal_ai_secretary.domain.contracts import RiskLevel
from personal_ai_secretary.evaluation.runtime import (
    EvaluationContext,
    EvaluationCriterion,
    EvaluationOutcome,
    ReleaseGateEvaluator,
)
from personal_ai_secretary.tools.registry import ToolDefinition, ToolRegistry, ToolRisk

REQUEST_ID: UUID = uuid4()


def _artifact(
    role: AgentRole,
    content: str = "output",
    *,
    blocked: bool = False,
    evidence: tuple[str, ...] = (),
    metadata: dict[str, object] | None = None,
) -> AgentArtifact:
    return AgentArtifact(
        role=role,
        request_id=REQUEST_ID,
        content=content,
        risk_level=RiskLevel.LOW,
        evidence_ids=list(evidence),
        blocked=blocked,
        metadata=metadata or {},
    )


def _healthy_context(
    *,
    plan: ExecutionPlan | None = None,
    registry: ToolRegistry | None = None,
) -> EvaluationContext:
    artifacts = [
        _artifact(AgentRole.PLANNER),
        _artifact(AgentRole.RESEARCH),
        _artifact(AgentRole.EXECUTION),
        _artifact(AgentRole.REVIEWER),
    ]
    return EvaluationContext(
        request_id=REQUEST_ID,
        user_id="user-1",
        correlation_id="corr-eval",
        artifacts=artifacts,
        plan=plan,
        registry=registry,
    )


def _plan(*, requires_evidence: bool = False) -> ExecutionPlan:
    return ExecutionPlan(
        request_id=REQUEST_ID,
        steps=[
            PlanStep(
                id="respond",
                action="respond",
                requires_evidence=requires_evidence,
                risk_level=RiskLevel.LOW,
            )
        ],
        rationale="test plan",
    )


def test_evaluation_passes_for_healthy_artifacts() -> None:
    outcome = ReleaseGateEvaluator().evaluate(_healthy_context())
    assert outcome.passed
    assert outcome.rejection_reason() == "evaluation passed"


def test_evaluation_rejects_missing_artifact() -> None:
    context = _healthy_context()
    context = replace(
        context,
        artifacts=[a for a in context.artifacts if a.role != AgentRole.RESEARCH],
    )
    outcome = ReleaseGateEvaluator().evaluate(context)
    assert not outcome.passed
    assert "artifacts_complete" in [c.name for c in outcome.failed_criteria()]


def test_evaluation_rejects_empty_response() -> None:
    context = _healthy_context()
    context = replace(
        context,
        artifacts=[a for a in context.artifacts if a.role != AgentRole.EXECUTION]
        + [_artifact(AgentRole.EXECUTION, content="   ")],
    )
    outcome = ReleaseGateEvaluator().evaluate(context)
    assert not outcome.passed
    response = next(c for c in outcome.failed_criteria() if c.name == "response_present")
    assert "empty response" in response.detail


def test_evaluation_rejects_blocked_reviewer() -> None:
    context = _healthy_context()
    context = replace(
        context,
        artifacts=[a for a in context.artifacts if a.role != AgentRole.REVIEWER]
        + [
            _artifact(
                AgentRole.REVIEWER,
                content="Review blocked: invalid producer.",
                blocked=True,
            )
        ],
    )
    outcome = ReleaseGateEvaluator().evaluate(context)
    assert not outcome.passed
    reviewer = next(c for c in outcome.failed_criteria() if c.name == "reviewer_passed")
    assert "Review blocked" in reviewer.detail


def test_evaluation_requires_evidence_when_planned() -> None:
    context = _healthy_context(plan=_plan(requires_evidence=True))
    outcome = ReleaseGateEvaluator().evaluate(context)
    assert not outcome.passed
    assert "evidence_when_required" in [c.name for c in outcome.failed_criteria()]

    context = replace(
        context,
        artifacts=[a for a in context.artifacts if a.role != AgentRole.RESEARCH]
        + [_artifact(AgentRole.RESEARCH, evidence=("e1",))],
    )
    assert ReleaseGateEvaluator().evaluate(context).passed


def test_evaluation_skips_evidence_check_when_not_required() -> None:
    context = _healthy_context(plan=_plan(requires_evidence=False))
    assert ReleaseGateEvaluator().evaluate(context).passed


def test_evaluation_rejects_tool_without_required_approval() -> None:
    async def send(_: dict[str, object]) -> dict[str, object]:
        return {"sent": True}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition("send_email", ToolRisk.HIGH, True, send, argument_schema={"to": "string"})
    )
    tool_metadata = {
        "tools": [
            {
                "name": "send_email",
                "user_id": "user-1",
                "correlation_id": "corr-eval",
                "approved": False,
            }
        ]
    }
    context = _healthy_context(registry=registry)
    context = replace(
        context,
        artifacts=[a for a in context.artifacts if a.role != AgentRole.EXECUTION]
        + [_artifact(AgentRole.EXECUTION, metadata=tool_metadata)],
    )
    outcome = ReleaseGateEvaluator().evaluate(context)
    assert not outcome.passed
    approval = next(c for c in outcome.failed_criteria() if c.name == "tool_approval_respected")
    assert "send_email" in approval.detail


def test_evaluation_accepts_approved_tool() -> None:
    async def send(_: dict[str, object]) -> dict[str, object]:
        return {"sent": True}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition("send_email", ToolRisk.HIGH, True, send, argument_schema={"to": "string"})
    )
    tool_metadata = {
        "tools": [
            {
                "name": "send_email",
                "user_id": "user-1",
                "correlation_id": "corr-eval",
                "approved": True,
            }
        ]
    }
    context = _healthy_context(registry=registry)
    context = replace(
        context,
        artifacts=[a for a in context.artifacts if a.role != AgentRole.EXECUTION]
        + [_artifact(AgentRole.EXECUTION, metadata=tool_metadata)],
    )
    assert ReleaseGateEvaluator().evaluate(context).passed


def test_evaluation_rejects_tool_with_mismatched_attribution() -> None:
    tool_metadata = {
        "tools": [
            {
                "name": "calculator",
                "user_id": "user-1",
                "correlation_id": "other-correlation",
                "approved": False,
            }
        ]
    }
    context = _healthy_context()
    context = replace(
        context,
        artifacts=[a for a in context.artifacts if a.role != AgentRole.EXECUTION]
        + [_artifact(AgentRole.EXECUTION, metadata=tool_metadata)],
    )
    outcome = ReleaseGateEvaluator().evaluate(context)
    assert not outcome.passed
    correlation = next(c for c in outcome.failed_criteria() if c.name == "tool_correlation_valid")
    assert "calculator" in correlation.detail


def test_evaluation_reports_all_failed_gates_with_reasons() -> None:
    context = replace(
        _healthy_context(),
        artifacts=[
            _artifact(AgentRole.PLANNER),
            _artifact(AgentRole.EXECUTION, content="  "),
            _artifact(
                AgentRole.REVIEWER,
                content="Review blocked: invalid producer.",
                blocked=True,
            ),
        ],
    )
    outcome = ReleaseGateEvaluator().evaluate(context)
    assert not outcome.passed
    failed = {c.name for c in outcome.failed_criteria()}
    assert {"artifacts_complete", "response_present", "reviewer_passed"} <= failed
    reason = outcome.rejection_reason()
    assert reason.startswith("execution rejected by evaluation gate(s):")
    assert "response_present" in reason
    assert "reviewer_passed" in reason


class _RecordingEvaluator(ReleaseGateEvaluator):
    def __init__(self, *, passed: bool = True) -> None:
        self.passed_outcome = passed
        self.calls: list[EvaluationContext] = []

    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        self.calls.append(context)
        if not self.passed_outcome:
            return EvaluationOutcome(
                passed=False,
                criteria=(
                    EvaluationCriterion("always_reject", False, "test-only rejection"),
                ),
            )
        return super().evaluate(context)


def _input(text: str = "hello", **context: object) -> AgentInput:
    return AgentInput(
        request_id=REQUEST_ID,
        session_id=uuid4(),
        user_id="user-1",
        text=text,
        correlation_id="corr-wf-eval",
        context=context,
    )


@pytest.mark.asyncio
async def test_workflow_runs_evaluation_after_reviewer_before_compliance() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    events: list[str] = []

    class OrderedReviewer(ReviewerAgent):
        async def run(self, data: AgentInput) -> AgentArtifact:
            events.append("reviewer")
            return await super().run(data)

    class OrderedEvaluator(ReleaseGateEvaluator):
        def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
            events.append("evaluation")
            return super().evaluate(context)

    class OrderedCompliance:
        role = AgentRole.COMPLIANCE

        async def run(self, data: AgentInput) -> AgentArtifact:
            events.append("compliance")
            return AgentArtifact(
                role=self.role,
                request_id=data.request_id,
                content="Compliance passed.",
                risk_level=data.risk_level,
            )

    from personal_ai_secretary.agents.builtin import DEFAULT_AGENTS

    agents = dict(DEFAULT_AGENTS)
    agents[AgentRole.REVIEWER] = OrderedReviewer()
    agents[AgentRole.COMPLIANCE] = OrderedCompliance()

    workflow = GovernedWorkflow(evaluator=OrderedEvaluator(), agents=agents)
    result = await workflow.run(REQUEST_ID, uuid4(), "user-1", "hello", "corr-wf-eval")

    assert result.status == "completed"
    assert events == ["reviewer", "evaluation", "compliance"]


@pytest.mark.asyncio
async def test_workflow_rejects_when_evaluation_fails() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    workflow = GovernedWorkflow(evaluator=_RecordingEvaluator(passed=False))
    result = await workflow.run(REQUEST_ID, uuid4(), "user-1", "hello", "corr-wf-reject")

    assert result.status == "rejected"
    assert result.response is None
    assert result.rejected_reason is not None
    assert "always_reject" in result.rejected_reason
    assert result.evaluation is not None
    assert not result.evaluation.passed
    assert [a.role.value for a in result.artifacts] == [
        "planner", "research", "execution", "reviewer"
    ]


@pytest.mark.asyncio
async def test_workflow_rejects_empty_execution_output() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    class EmptyExecution:
        role = AgentRole.EXECUTION

        async def run(self, data: AgentInput) -> AgentArtifact:
            return AgentArtifact(
                role=self.role,
                request_id=data.request_id,
                content="   ",
                risk_level=data.risk_level,
            )

    from personal_ai_secretary.agents.builtin import DEFAULT_AGENTS

    agents = dict(DEFAULT_AGENTS)
    agents[AgentRole.EXECUTION] = EmptyExecution()

    workflow = GovernedWorkflow(agents=agents)
    result = await workflow.run(REQUEST_ID, uuid4(), "user-1", "hello", "corr-wf-empty")

    assert result.status == "rejected"
    assert "response_present" in (result.rejected_reason or "")


@pytest.mark.asyncio
async def test_workflow_rejects_blocked_reviewer_via_evaluation() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    workflow = GovernedWorkflow()
    result = await workflow.run(
        REQUEST_ID, uuid4(), "user-1", "hello", "corr-wf-reviewer"
    )

    assert result.status == "completed"

    class FailingReviewer:
        role = AgentRole.REVIEWER

        async def run(self, data: AgentInput) -> AgentArtifact:
            return AgentArtifact(
                role=self.role,
                request_id=data.request_id,
                content="Review blocked: separation of duties violation.",
                risk_level=data.risk_level,
                blocked=True,
            )

    from personal_ai_secretary.agents.builtin import DEFAULT_AGENTS

    agents = dict(DEFAULT_AGENTS)
    agents[AgentRole.REVIEWER] = FailingReviewer()
    workflow = GovernedWorkflow(agents=agents)
    result = await workflow.run(REQUEST_ID, uuid4(), "user-1", "hello", "corr-wf-reviewer-2")

    assert result.status == "rejected"
    assert "reviewer_passed" in (result.rejected_reason or "")


@pytest.mark.asyncio
async def test_workflow_evaluation_receives_correlation_and_user() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    evaluator = _RecordingEvaluator()
    workflow = GovernedWorkflow(evaluator=evaluator)
    result = await workflow.run(REQUEST_ID, uuid4(), "user-1", "hello", "corr-wf-ctx")

    assert result.status == "completed"
    assert evaluator.calls
    context = evaluator.calls[-1]
    assert context.request_id == REQUEST_ID
    assert context.user_id == "user-1"
    assert context.correlation_id == "corr-wf-ctx"


@pytest.mark.asyncio
async def test_workflow_evaluation_passes_then_compliance_still_blocks() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    workflow = GovernedWorkflow()
    result = await workflow.run(
        REQUEST_ID,
        uuid4(),
        "user-1",
        "hello",
        "corr-wf-compliance",
        context={"policy_violation": True},
    )

    assert result.status == "blocked"
    assert result.evaluation is not None
    assert result.evaluation.passed
    assert result.artifacts[-1].role.value == "compliance"


@pytest.mark.asyncio
async def test_workflow_tools_still_respect_approval_with_evaluation() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    async def send(_: dict[str, object]) -> dict[str, object]:
        return {"sent": True}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition("send_email", ToolRisk.HIGH, True, send, argument_schema={"to": "string"})
    )

    blocked = await GovernedWorkflow(tools=registry).run(
        REQUEST_ID,
        uuid4(),
        "user-1",
        '@tool:send_email {"to": "boss@example.com"}',
        "corr-wf-tool-block",
    )
    assert blocked.status == "blocked"

    completed = await GovernedWorkflow(tools=registry).run(
        REQUEST_ID,
        uuid4(),
        "user-1",
        '@tool:send_email {"to": "boss@example.com"}',
        "corr-wf-tool-ok",
        context={"approval_granted": True},
    )
    assert completed.status == "completed"
    assert completed.evaluation is not None
    assert completed.evaluation.passed


@pytest.mark.asyncio
async def test_workflow_calculator_tool_passes_evaluation() -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    workflow = GovernedWorkflow(tools=default_tool_registry())
    result = await workflow.run(
        REQUEST_ID,
        uuid4(),
        "user-1",
        '@tool:calculator {"expression": "2+2"}',
        "corr-wf-calc",
    )
    assert result.status == "completed"
    assert result.evaluation is not None
    assert result.evaluation.passed


def test_evaluation_rejects_missing_reviewer_artifact() -> None:
    context = replace(
        _healthy_context(),
        artifacts=[
            a for a in _healthy_context().artifacts if a.role != AgentRole.REVIEWER
        ],
    )
    outcome = ReleaseGateEvaluator().evaluate(context)
    reviewer = next(c for c in outcome.failed_criteria() if c.name == "reviewer_passed")
    assert "reviewer artifact missing" in reviewer.detail


def test_evaluation_rejects_unnamed_tool_entry() -> None:
    context = replace(
        _healthy_context(),
        artifacts=[
            a for a in _healthy_context().artifacts if a.role != AgentRole.EXECUTION
        ]
        + [
            _artifact(
                AgentRole.EXECUTION,
                metadata={
                    "tools": [
                        {
                            "name": 123,
                            "user_id": "user-1",
                            "correlation_id": "corr-eval",
                        }
                    ]
                },
            )
        ],
    )
    outcome = ReleaseGateEvaluator().evaluate(context)
    correlation = next(
        c for c in outcome.failed_criteria() if c.name == "tool_correlation_valid"
    )
    assert "<unnamed>" in correlation.detail


@pytest.mark.asyncio
async def test_workflow_blocks_when_plan_rejected() -> None:
    from personal_ai_secretary.agents.builtin import DEFAULT_AGENTS
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    class BlockedPlanner:
        role = AgentRole.PLANNER

        async def run(self, data: AgentInput) -> AgentArtifact:
            return AgentArtifact(
                role=self.role,
                request_id=data.request_id,
                content="Plan blocked.",
                risk_level=data.risk_level,
                blocked=True,
            )

    agents = dict(DEFAULT_AGENTS)
    agents[AgentRole.PLANNER] = BlockedPlanner()
    result = await GovernedWorkflow(agents=agents).run(
        REQUEST_ID, uuid4(), "user-1", "hello", "corr-wf-plan-block"
    )
    assert result.status == "blocked"
    assert "Plan blocked" in (result.blocked_reason or "")


@pytest.mark.asyncio
async def test_workflow_handles_unparseable_plan_content() -> None:
    from personal_ai_secretary.agents.builtin import DEFAULT_AGENTS
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    class WeirdPlanner:
        role = AgentRole.PLANNER

        async def run(self, data: AgentInput) -> AgentArtifact:
            return AgentArtifact(
                role=self.role,
                request_id=data.request_id,
                content="not-a-plan",
                risk_level=data.risk_level,
            )

    agents = dict(DEFAULT_AGENTS)
    agents[AgentRole.PLANNER] = WeirdPlanner()
    result = await GovernedWorkflow(agents=agents).run(
        REQUEST_ID, uuid4(), "user-1", "hello", "corr-wf-plan-weird"
    )
    assert result.status == "completed"
    assert result.evaluation is not None
    assert result.evaluation.passed


@pytest.mark.asyncio
async def test_workflow_blocks_when_research_rejected() -> None:
    from personal_ai_secretary.agents.builtin import DEFAULT_AGENTS
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    class BlockedResearch:
        role = AgentRole.RESEARCH

        async def run(self, data: AgentInput) -> AgentArtifact:
            return AgentArtifact(
                role=self.role,
                request_id=data.request_id,
                content="Research blocked.",
                risk_level=data.risk_level,
                blocked=True,
            )

    agents = dict(DEFAULT_AGENTS)
    agents[AgentRole.RESEARCH] = BlockedResearch()
    result = await GovernedWorkflow(agents=agents).run(
        REQUEST_ID, uuid4(), "user-1", "hello", "corr-wf-research-block"
    )
    assert result.status == "blocked"
    assert "Research blocked" in (result.blocked_reason or "")


@pytest.mark.asyncio
async def test_workflow_blocks_when_execution_rejected() -> None:
    from personal_ai_secretary.agents.builtin import DEFAULT_AGENTS
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    class BlockedExecution:
        role = AgentRole.EXECUTION

        async def run(self, data: AgentInput) -> AgentArtifact:
            return AgentArtifact(
                role=self.role,
                request_id=data.request_id,
                content="Execution blocked: authorization required.",
                risk_level=data.risk_level,
                blocked=True,
            )

    agents = dict(DEFAULT_AGENTS)
    agents[AgentRole.EXECUTION] = BlockedExecution()
    result = await GovernedWorkflow(agents=agents).run(
        REQUEST_ID, uuid4(), "user-1", "hello", "corr-wf-exec-block"
    )
    assert result.status == "blocked"
    assert "authorization" in (result.blocked_reason or "")


def test_release_gate_still_available() -> None:
    from personal_ai_secretary.evaluation.gates import release_ready

    assert release_ready({"workflow_success": 0.99, "security_pass": 1, "regression_pass": 1})
    assert not release_ready({"workflow_success": 0.90, "security_pass": 1, "regression_pass": 1})