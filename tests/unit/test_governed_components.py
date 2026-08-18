from datetime import UTC, datetime
from uuid import uuid4

import pytest

from personal_ai_secretary.agents.builtin import (
    DEFAULT_AGENTS,
    ComplianceAgent,
    ExecutionAgent,
    PlannerAgent,
    ResearchAgent,
    ReviewerAgent,
)
from personal_ai_secretary.agents.contracts import AgentArtifact, AgentInput, AgentRole
from personal_ai_secretary.domain.contracts import ProviderInfo, ProviderResponse, RequestEnvelope
from personal_ai_secretary.evaluation.gates import release_ready
from personal_ai_secretary.memory.service import (
    InMemoryMemoryStore,
    MemoryClass,
    MemoryItem,
    MemoryPolicy,
)
from personal_ai_secretary.rag.service import GovernedRetriever, Source
from personal_ai_secretary.tools.registry import ToolDefinition, ToolRegistry, ToolRisk


@pytest.mark.asyncio
async def test_planner_returns_validated_plan() -> None:
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="u", text="hello", correlation_id="corr-1"
    )
    artifact = await PlannerAgent().run(data)
    assert artifact.role.value == "planner"
    assert '"steps"' in artifact.content


@pytest.mark.asyncio
async def test_reviewer_separation_of_duties() -> None:
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="u",
        text="hello",
        correlation_id="corr-1",
        context={"producer_role": "reviewer"},
    )
    assert (await ReviewerAgent().run(data)).blocked


@pytest.mark.asyncio
async def test_rag_is_governed_and_ranked() -> None:
    retriever = GovernedRetriever(
        [Source("s1", "https://example.com/hello", "hello", 1.0)]
    )
    evidence = await retriever.retrieve("hello")
    assert evidence and retriever.validate(evidence)


def test_memory_policy_enforces_user_scope() -> None:
    item = MemoryItem(
        "m", "owner", "x", MemoryClass.SESSION,
        MemoryPolicy().expiration(MemoryClass.SESSION),
    )
    assert MemoryPolicy().can_access(item, "owner")
    assert not MemoryPolicy().can_access(item, "other")


@pytest.mark.asyncio
async def test_tool_registry_blocks_unapproved_high_risk() -> None:
    async def handler(_: dict[str, object]) -> dict[str, object]:
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(ToolDefinition("approved", ToolRisk.HIGH, True, handler))
    with pytest.raises(PermissionError):
        await registry.execute("approved", {})
    assert await registry.execute("approved", {}, approved=True) == {"ok": True}


def test_release_gate() -> None:
    scores = {"workflow_success": 0.99, "security_pass": 1, "regression_pass": 1}
    assert release_ready(scores)
    scores["workflow_success"] = 0.90
    assert not release_ready(scores)

@pytest.mark.asyncio
async def test_reviewer_requires_non_reviewer_producer_and_output() -> None:
    data = AgentInput(
        request_id=uuid4(), session_id=uuid4(), user_id="u", text="hello",
        correlation_id="corr-1",
        context={"producer_role": "execution", "output": "result"},
    )
    artifact = await ReviewerAgent().run(data)
    assert not artifact.blocked
    assert artifact.metadata["producer_role"] == "execution"

    missing_output = data.model_copy(
        update={"context": {"producer_role": "execution", "output": "  "}}
    )
    assert (await ReviewerAgent().run(missing_output)).blocked

@pytest.mark.asyncio
async def test_governed_workflow_requires_approval_for_high_risk() -> None:
    from personal_ai_secretary.domain.contracts import RiskLevel
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    workflow = GovernedWorkflow()
    result = await workflow.run(uuid4(), uuid4(), "u", "danger", "corr-1", RiskLevel.HIGH)
    assert result.status == "blocked"
    assert result.blocked_reason is not None
    assert "approval required" in result.blocked_reason


@pytest.mark.asyncio
async def test_governed_workflow_completes_with_approval() -> None:
    from personal_ai_secretary.domain.contracts import RiskLevel
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    workflow = GovernedWorkflow()
    result = await workflow.run(
        uuid4(), uuid4(), "u", "hello", "corr-1", RiskLevel.HIGH, {"approval_granted": True}
    )
    assert result.status == "completed"
    assert result.response == "hello"
    assert [artifact.role.value for artifact in result.artifacts] == [
        "planner", "research", "execution", "reviewer", "compliance"
    ]

@pytest.mark.asyncio
async def test_governed_workflow_blocks_compliance_violation() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    workflow = GovernedWorkflow()
    result = await workflow.run(
        uuid4(), uuid4(), "u", "hello", "corr-1", context={"policy_violation": True}
    )
    assert result.status == "blocked"
    assert result.artifacts[-1].role.value == "compliance"
    assert result.artifacts[-1].blocked


class _RecordingProvider:
    name = "recording"

    def __init__(self) -> None:
        self.envelopes: list[RequestEnvelope] = []

    async def health(self) -> ProviderInfo:
        return ProviderInfo(
            name=self.name,
            mode="test",
            available=True,
            is_ai=False,
            detail="recording provider",
        )

    async def generate(self, request: RequestEnvelope) -> ProviderResponse:
        self.envelopes.append(request)
        return ProviderResponse(text=f"response:{request.input}", provider=self.name)


@pytest.mark.asyncio
async def test_execution_agent_delegates_to_provider() -> None:
    provider = _RecordingProvider()
    agent = ExecutionAgent(provider)
    request_id = uuid4()
    artifact = await agent.run(
        AgentInput(
            request_id=request_id,
            session_id=uuid4(),
            user_id="u",
            text="hello",
            correlation_id="corr-exec",
        )
    )
    assert artifact.content == "response:hello"
    assert artifact.blocked is False
    envelope = provider.envelopes[-1]
    assert envelope.request_id == request_id
    assert envelope.correlation_id == "corr-exec"


@pytest.mark.asyncio
async def test_governed_workflow_executes_via_provider() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    provider = _RecordingProvider()
    workflow = GovernedWorkflow(provider=provider)
    result = await workflow.run(uuid4(), uuid4(), "u", "hello", "corr-wf")
    assert result.status == "completed"
    assert result.response == "response:hello"
    assert provider.envelopes[0].correlation_id == "corr-wf"
    assert provider.envelopes[0].risk_level.value == "LOW"


def _memory_note(user_id: str, content: str, memory_id: str = "mem") -> MemoryItem:
    now = datetime.now(UTC)
    return MemoryItem(
        memory_id=memory_id,
        user_id=user_id,
        content=content,
        memory_class=MemoryClass.SESSION,
        created_at=now,
        expires_at=MemoryPolicy().expiration(MemoryClass.SESSION, now),
    )


@pytest.mark.asyncio
async def test_research_agent_queries_rag_and_memory() -> None:
    retriever = GovernedRetriever(
        [Source("s1", "https://example.com/policies", "send email policy", 1.0)]
    )
    store = InMemoryMemoryStore()
    await store.add(_memory_note("u", "user said hello earlier"))

    artifact = await ResearchAgent(retriever, store).run(
        AgentInput(
            request_id=uuid4(),
            session_id=uuid4(),
            user_id="u",
            text="send email",
            correlation_id="corr-r",
        )
    )

    assert artifact.evidence_ids
    assert artifact.metadata["evidence"][0]["text"].startswith("send email policy")
    assert artifact.metadata["memory"] == ["user said hello earlier"]
    assert "1 evidence item(s) and 1 memory note(s)" in artifact.content


@pytest.mark.asyncio
async def test_research_returns_multiple_ranked_evidence() -> None:
    retriever = GovernedRetriever(
        [
            Source("s1", "https://example.com/approval", "send email approval", 0.8),
            Source("s2", "https://example.com/outreach", "send email outreach guide", 0.5),
        ]
    )
    artifact = await ResearchAgent(retriever, None).run(
        AgentInput(
            request_id=uuid4(),
            session_id=uuid4(),
            user_id="u",
            text="send email",
            correlation_id="corr-multi",
        )
    )

    assert len(artifact.evidence_ids) == 2
    scores = [item["score"] for item in artifact.metadata["evidence"]]
    assert scores == sorted(scores, reverse=True)
    assert all(item["evidence_id"] for item in artifact.metadata["evidence"])


@pytest.mark.asyncio
async def test_research_memory_is_user_scoped() -> None:
    store = InMemoryMemoryStore()
    await store.add(_memory_note("alice", "alice context"))
    await store.add(_memory_note("bob", "bob context"))

    artifact = await ResearchAgent(None, store).run(
        AgentInput(
            request_id=uuid4(),
            session_id=uuid4(),
            user_id="alice",
            text="hello",
            correlation_id="corr-scope",
        )
    )

    assert artifact.metadata["memory"] == ["alice context"]


@pytest.mark.asyncio
async def test_research_without_sources_returns_no_evidence() -> None:
    artifact = await ResearchAgent(None, None).run(
        AgentInput(
            request_id=uuid4(),
            session_id=uuid4(),
            user_id="u",
            text="hello",
            correlation_id="corr-empty",
        )
    )

    assert artifact.evidence_ids == []
    assert artifact.metadata["evidence"] == []
    assert artifact.metadata["memory"] == []
    assert not artifact.blocked


@pytest.mark.asyncio
async def test_research_handles_rag_error_gracefully() -> None:
    class BrokenRetriever:
        async def retrieve(
            self, query: str, limit: int = 5, user_id: str | None = None
        ) -> list[object]:
            raise RuntimeError("corpus unavailable")

    artifact = await ResearchAgent(BrokenRetriever(), None).run(
        AgentInput(
            request_id=uuid4(),
            session_id=uuid4(),
            user_id="u",
            text="hello",
            correlation_id="corr-err",
        )
    )

    assert artifact.evidence_ids == []
    assert not artifact.blocked


@pytest.mark.asyncio
async def test_workflow_evidence_reaches_execution_agent() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    class RecordingExecution:
        role = AgentRole.EXECUTION

        def __init__(self) -> None:
            self.seen: dict[str, object] | None = None

        async def run(self, data: AgentInput) -> AgentArtifact:
            self.seen = dict(data.context)
            return AgentArtifact(
                role=self.role,
                request_id=data.request_id,
                content="done",
                risk_level=data.risk_level,
            )

    spy = RecordingExecution()
    retriever = GovernedRetriever(
        [Source("s1", "https://example.com/policies", "hello policy", 1.0)]
    )
    agents = dict(DEFAULT_AGENTS)
    agents[AgentRole.RESEARCH] = ResearchAgent(retriever, None)
    agents[AgentRole.EXECUTION] = spy

    workflow = GovernedWorkflow(agents=agents)
    result = await workflow.run(uuid4(), uuid4(), "u", "hello", "corr-e")

    assert result.status == "completed"
    assert spy.seen is not None
    assert spy.seen["evidence"]
    assert spy.seen["research_evidence"]
    assert spy.seen["memory_notes"] == []


@pytest.mark.asyncio
async def test_workflow_continues_without_evidence() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    workflow = GovernedWorkflow()
    result = await workflow.run(uuid4(), uuid4(), "u", "hello", "corr-none")

    assert result.status == "completed"
    research = result.artifacts[1]
    assert research.role.value == "research"
    assert research.evidence_ids == []
    assert [artifact.role.value for artifact in result.artifacts] == [
        "planner", "research", "execution", "reviewer", "compliance"
    ]


@pytest.mark.asyncio
async def test_provider_not_called_before_research_completes() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    events: list[str] = []

    class OrderedResearch:
        role = AgentRole.RESEARCH

        async def run(self, data: AgentInput) -> AgentArtifact:
            events.append("research")
            return AgentArtifact(
                role=self.role,
                request_id=data.request_id,
                content="research done",
                risk_level=data.risk_level,
                evidence_ids=["e1"],
            )

    provider = _RecordingProvider()
    original_generate = provider.generate

    async def generate_and_record(request: RequestEnvelope) -> ProviderResponse:
        events.append("provider")
        return await original_generate(request)

    provider.generate = generate_and_record  # type: ignore[method-assign]

    agents = dict(DEFAULT_AGENTS)
    agents[AgentRole.RESEARCH] = OrderedResearch()
    agents[AgentRole.EXECUTION] = ExecutionAgent(provider)

    workflow = GovernedWorkflow(agents=agents)
    result = await workflow.run(uuid4(), uuid4(), "u", "hello", "corr-order")

    assert result.status == "completed"
    assert events == ["research", "provider"]


@pytest.mark.asyncio
async def test_workflow_propagates_correlation_id_to_research() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    captured: list[str] = []

    class CaptureResearch:
        role = AgentRole.RESEARCH

        async def run(self, data: AgentInput) -> AgentArtifact:
            captured.append(data.correlation_id)
            return AgentArtifact(
                role=self.role,
                request_id=data.request_id,
                content="ok",
                risk_level=data.risk_level,
            )

    agents = dict(DEFAULT_AGENTS)
    agents[AgentRole.RESEARCH] = CaptureResearch()

    workflow = GovernedWorkflow(agents=agents)
    await workflow.run(uuid4(), uuid4(), "u", "hello", "corr-propagated")

    assert captured == ["corr-propagated"]


@pytest.mark.asyncio
async def test_execution_agent_blocks_unauthorized() -> None:
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="u",
        text="do something",
        correlation_id="corr",
        context={"authorized": False},
    )
    artifact = await ExecutionAgent().run(data)
    assert artifact.blocked
    assert "authorization required" in artifact.content.lower()


@pytest.mark.asyncio
async def test_execution_agent_no_registry_with_tool_call() -> None:
    from personal_ai_secretary.providers.deterministic import DeterministicProvider

    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="u",
        text='@tool:calculator {"expression": "1+1"}',
        correlation_id="corr",
    )
    artifact = await ExecutionAgent(
        provider=DeterministicProvider(), registry=None
    ).run(data)
    assert "not available" in artifact.content.lower()


def test_format_tool_result_error_branch() -> None:
    result = ExecutionAgent._format_tool_result("mytool", {"error": "something broke"})
    assert "something broke" in result
    assert "mytool" in result


@pytest.mark.asyncio
async def test_reviewer_blocks_invalid_producer() -> None:
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="u",
        text="hello",
        correlation_id="corr",
        context={"producer_role": "unknown_role", "output": "text"},
    )
    artifact = await ReviewerAgent().run(data)
    assert artifact.blocked
    assert "valid non-reviewer producer" in artifact.content.lower()


@pytest.mark.asyncio
async def test_compliance_agent_uses_injected_rules() -> None:
    from personal_ai_secretary.compliance.policy import ProhibitedCommandsRule

    rules = (ProhibitedCommandsRule(),)
    agent = ComplianceAgent(enabled=True, rules=rules)
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="u",
        text="hello",
        correlation_id="corr",
        context={"output": "all good", "user_input": "hello"},
    )
    artifact = await agent.run(data)
    assert not artifact.blocked


@pytest.mark.asyncio
async def test_compliance_agent_disabled_returns_passed() -> None:
    agent = ComplianceAgent(enabled=False)
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="u",
        text="hello",
        correlation_id="corr",
        context={"output": "all good", "user_input": "hello"},
    )
    artifact = await agent.run(data)
    assert not artifact.blocked
    assert "compliance passed" in artifact.content.lower()


@pytest.mark.asyncio
async def test_planner_handles_malformed_tool_call() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="do_thing",
            risk=ToolRisk.LOW,
            requires_explicit_approval=False,
            handler=lambda **kw: "ok",
        )
    )
    agent = PlannerAgent(registry=registry)
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="u",
        text="{bad json",
        correlation_id="corr",
    )
    artifact = await agent.run(data)
    assert artifact.role == AgentRole.PLANNER
    assert "steps" in artifact.content.lower() or "plan" in artifact.content.lower()


@pytest.mark.asyncio
async def test_research_handles_memory_error_gracefully() -> None:
    class BrokenMemory:
        async def retrieve(self, user_id, memory_class=None, limit=10):
            raise RuntimeError("memory store is broken")

    agent = ResearchAgent(memory=BrokenMemory())  # type: ignore[arg-type]
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="u",
        text="research this",
        correlation_id="corr",
    )
    artifact = await agent.run(data)
    assert not artifact.blocked


@pytest.mark.asyncio
async def test_execution_agent_tool_failure_with_observability() -> None:
    from personal_ai_secretary.observability.audit import InMemoryAuditStore
    from personal_ai_secretary.observability.metrics import Metrics
    from personal_ai_secretary.observability.observer import Observability

    async def exploding_handler(args: dict) -> dict:
        raise RuntimeError("boom")

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="explode",
            risk=ToolRisk.LOW,
            requires_explicit_approval=False,
            handler=exploding_handler,
        )
    )
    obs = Observability(audit=InMemoryAuditStore(), metrics=Metrics.create())
    agent = ExecutionAgent(provider=None, registry=registry, observability=obs)
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="u",
        text="@tool:explode {}",
        correlation_id="corr",
    )
    with pytest.raises(RuntimeError, match="boom"):
        await agent.run(data)


@pytest.mark.asyncio
async def test_execution_agent_no_registry_with_observability() -> None:
    from personal_ai_secretary.observability.audit import InMemoryAuditStore
    from personal_ai_secretary.observability.metrics import Metrics
    from personal_ai_secretary.observability.observer import Observability

    obs = Observability(audit=InMemoryAuditStore(), metrics=Metrics.create())
    agent = ExecutionAgent(provider=None, registry=None, observability=obs)
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="u",
        text="@tool:missing {}",
        correlation_id="corr",
    )
    artifact = await agent.run(data)
    assert "not available" in artifact.content.lower()


@pytest.mark.asyncio
async def test_execution_agent_tool_approval_blocked_with_observability() -> None:
    from personal_ai_secretary.observability.audit import InMemoryAuditStore
    from personal_ai_secretary.observability.metrics import Metrics
    from personal_ai_secretary.observability.observer import Observability

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="dangerous",
            risk=ToolRisk.HIGH,
            requires_explicit_approval=True,
            handler=lambda **kw: "ok",
        )
    )
    obs = Observability(audit=InMemoryAuditStore(), metrics=Metrics.create())
    agent = ExecutionAgent(provider=None, registry=registry, observability=obs)
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="u",
        text="@tool:dangerous {}",
        correlation_id="corr",
    )
    artifact = await agent.run(data)
    assert artifact.blocked


@pytest.mark.asyncio
async def test_compliance_agent_disabled_via_settings(monkeypatch) -> None:
    from personal_ai_secretary.shared.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "compliance_enabled", False)
    monkeypatch.setattr(settings, "compliance_rules", [])
    agent = ComplianceAgent()
    data = AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="u",
        text="hello",
        correlation_id="corr",
        context={"output": "all good", "user_input": "hello"},
    )
    artifact = await agent.run(data)
    assert not artifact.blocked
    assert "passed" in artifact.content.lower()
