from datetime import UTC, datetime
from uuid import uuid4

import pytest

from personal_ai_secretary.observability.audit import (
    REDACTED,
    AuditEvent,
    InMemoryAuditStore,
    audit_event,
    sanitize_value,
)
from personal_ai_secretary.observability.metrics import Metrics, Timer
from personal_ai_secretary.observability.observer import Observability
from personal_ai_secretary.observability.tracing import (
    init_tracing,
    shutdown_tracing,
    start_span,
)

REQUEST_ID = uuid4()


def _event(
    *,
    request_id: str = str(REQUEST_ID),
    user_id: str = "user-1",
    outcome: str = "ok",
    details: dict | None = None,
) -> AuditEvent:
    return audit_event(
        "test", request_id, user_id, outcome, **(details or {})
    )


def test_sanitize_value_redacts_sensitive_keys_recursively() -> None:
    value = sanitize_value(
        {
            "ok": "fine",
            "Authorization": "Bearer abc",
            "api_key": "secret-123",
            "nested": {"password": "pw", "safe": "yes"},
            "list": [{"token": "tok"}, "plain"],
            "tuple": ({"client_secret": "cs"},),
        }
    )
    assert value["ok"] == "fine"
    assert value["Authorization"] == REDACTED
    assert value["api_key"] == REDACTED
    assert value["nested"]["password"] == REDACTED
    assert value["nested"]["safe"] == "yes"
    assert value["list"][0]["token"] == REDACTED
    assert value["list"][1] == "plain"
    assert value["tuple"][0]["client_secret"] == REDACTED


def test_sanitize_value_redacts_underscored_and_hyphenated_keys() -> None:
    value = sanitize_value(
        {"access_token": "a", "refresh-token": "b", "clientSecret": "c", "JWT": "d"}
    )
    assert value == {
        "access_token": REDACTED,
        "refresh-token": REDACTED,
        "clientSecret": REDACTED,
        "JWT": REDACTED,
    }


def test_sanitize_value_leaves_scalars_untouched() -> None:
    assert sanitize_value("bearer token") == "bearer token"
    assert sanitize_value(42) == 42
    assert sanitize_value(None) is None
    assert sanitize_value([1, "two"]) == [1, "two"]


JWT_SAMPLE = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.abcdefghijklmnopqrstuvwx"


def test_sanitize_value_redacts_embedded_url_credentials() -> None:
    value = sanitize_value(
        {
            "endpoint": "postgresql://admin:supersecret@db.example.com:5432/app",
            "plain": "https://example.com/resource",
        }
    )
    assert value["endpoint"] == "postgresql://[REDACTED]@db.example.com:5432/app"
    assert value["plain"] == "https://example.com/resource"


def test_sanitize_value_redacts_standalone_jwt_and_bearer() -> None:
    value = sanitize_value(
        {
            "payload": JWT_SAMPLE,
            "header": f"Bearer {JWT_SAMPLE}",
            "nested": {"deep": [JWT_SAMPLE]},
            "note": "the token value is private",
        }
    )
    assert value["payload"] == REDACTED
    assert value["header"] == REDACTED
    assert value["nested"]["deep"] == [REDACTED]
    assert value["note"] == "the token value is private"


def test_sanitize_value_does_not_overredact_ordinary_text() -> None:
    value = sanitize_value(
        {
            "summary": "Authentication uses bearer tokens over HTTPS",
            "url": "https://example.com/health",
            "fragment": "eyJ is not a complete token",
        }
    )
    assert value["summary"] == "Authentication uses bearer tokens over HTTPS"
    assert value["url"] == "https://example.com/health"
    assert value["fragment"] == "eyJ is not a complete token"


async def test_record_stores_sanitized_event() -> None:
    store = InMemoryAuditStore()
    await store.record(
        _event(details={"Authorization": "Bearer secret", "expression": "2+2"})
    )

    stored = await store.events()
    assert len(stored) == 1
    assert stored[0].details["Authorization"] == REDACTED
    assert stored[0].details["expression"] == "2+2"
    assert stored[0].timestamp.tzinfo is not None


async def test_events_filters_by_user() -> None:
    store = InMemoryAuditStore()
    await store.record(_event(user_id="user-a"))
    await store.record(_event(user_id="user-b"))

    assert [e.user_id for e in await store.events(user_id="user-a")] == ["user-a"]
    assert [e.user_id for e in await store.events()] == ["user-a", "user-b"]


async def test_events_returns_latest_first_window() -> None:
    store = InMemoryAuditStore()
    for index in range(10):
        await store.record(_event(details={"index": index}))

    scoped = await store.events(limit=3)
    assert [e.details["index"] for e in scoped] == [7, 8, 9]


def test_audit_event_builds_utc_timestamp_and_details() -> None:
    event = audit_event("stage", "req-1", "user-1", "ok", correlation_id="corr-1")
    assert event.event_type == "stage"
    assert event.request_id == "req-1"
    assert event.user_id == "user-1"
    assert event.outcome == "ok"
    assert event.details == {"correlation_id": "corr-1"}
    assert abs((datetime.now(UTC) - event.timestamp).total_seconds()) < 5


def test_metrics_accumulates_counters() -> None:
    metrics = Metrics.create()
    metrics.inc("a")
    metrics.inc("a")
    metrics.inc("b", amount=3)

    snapshot = metrics.snapshot()
    assert snapshot == {"a": 2, "b": 3}
    assert metrics.snapshot() == snapshot


def test_metrics_records_durations() -> None:
    metrics = Metrics.create()
    metrics.record_duration("workflow", 0.42)

    assert metrics.duration_snapshot() == {"workflow": 0.42}
    assert metrics.duration_snapshot() == {"workflow": 0.42}


def test_timer_measures_elapsed() -> None:
    with Timer() as timer:
        pass
    assert timer.elapsed_seconds >= 0


async def test_observability_emit_records_metadata() -> None:
    store = InMemoryAuditStore()
    observability = Observability(store, Metrics.create())
    session_id = uuid4()

    await observability.emit(
        stage="provider",
        request_id=REQUEST_ID,
        user_id="user-1",
        correlation_id="corr-x",
        session_id=session_id,
        outcome="ok",
        details={"provider": "fake"},
    )

    event = (await store.events())[0]
    assert event.event_type == "provider"
    assert event.user_id == "user-1"
    assert event.outcome == "ok"
    assert event.details["correlation_id"] == "corr-x"
    assert event.details["session_id"] == str(session_id)
    assert event.details["provider"] == "fake"


async def test_observability_emit_links_trace_and_span_ids_when_span_active() -> None:
    store = InMemoryAuditStore()
    observability = Observability(store, Metrics.create())
    init_tracing(enabled=True, exporter_endpoint=None)
    try:
        with start_span("workflow.run") as span:
            await observability.emit(
                stage="planner",
                request_id=REQUEST_ID,
                user_id="user-1",
                correlation_id="corr-trace",
                outcome="ok",
            )
        context = span.get_span_context()
    finally:
        shutdown_tracing()

    event = (await store.events())[0]
    assert event.details["trace_id"] == format(context.trace_id, "032x")
    assert event.details["span_id"] == format(context.span_id, "016x")


async def test_observability_record_duration_links_trace_id() -> None:
    metrics = Metrics.create()
    observability = Observability(InMemoryAuditStore(), metrics)
    init_tracing(enabled=True, exporter_endpoint=None)
    try:
        with start_span("workflow.run") as span:
            observability.record_duration("workflow", 0.42)
        context = span.get_span_context()
    finally:
        shutdown_tracing()

    assert metrics.duration_snapshot()["workflow"] == 0.42
    assert metrics.duration_trace_snapshot()["workflow"] == format(
        context.trace_id, "032x"
    )
    assert observability.duration_trace_ids()["workflow"] == format(
        context.trace_id, "032x"
    )


async def test_observability_emit_without_span_records_no_trace_ids() -> None:
    store = InMemoryAuditStore()
    observability = Observability(store, Metrics.create())
    shutdown_tracing()

    await observability.emit(
        stage="planner",
        request_id=REQUEST_ID,
        user_id="user-1",
        correlation_id="corr-plain",
        outcome="ok",
    )

    event = (await store.events())[0]
    assert "trace_id" not in event.details
    assert "span_id" not in event.details


async def test_observability_emit_includes_error_without_leaking_token() -> None:
    store = InMemoryAuditStore()
    observability = Observability(store, Metrics.create())

    await observability.emit(
        stage="request",
        request_id=REQUEST_ID,
        user_id="user-1",
        correlation_id="corr-x",
        outcome="failed",
        error=RuntimeError("boom"),
        details={"Authorization": "Bearer secret", "context": "nothing sensitive"},
    )

    event = (await store.events())[0]
    assert "boom" in event.details["error"]
    assert event.details["Authorization"] == REDACTED
    assert event.details["context"] == "nothing sensitive"


def test_observability_delegates_metrics() -> None:
    metrics = Metrics.create()
    observability = Observability(InMemoryAuditStore(), metrics)

    observability.inc("tool_executions")
    observability.record_duration("tool", 0.1)

    assert metrics.snapshot() == {"tool_executions": 1}
    assert metrics.duration_snapshot() == {"tool": 0.1}


@pytest.mark.asyncio
async def test_workflow_emits_stage_events_and_metrics_on_completion() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    store = InMemoryAuditStore()
    metrics = Metrics.create()
    workflow = GovernedWorkflow(observability=Observability(store, metrics))
    request_id = uuid4()

    result = await workflow.run(
        request_id, uuid4(), "user-1", "hello", "corr-obs-complete"
    )

    assert result.status == "completed"
    stages = [e.event_type for e in await store.events()]
    assert stages == [
        "planner",
        "approval",
        "research",
        "execution",
        "reviewer",
        "evaluation",
        "compliance",
        "completed",
    ]
    assert metrics.snapshot()["evaluation_passes"] == 1
    assert {"workflow", "research", "evaluation"} <= set(metrics.duration_snapshot())
    assert all(
        e.details["correlation_id"] == "corr-obs-complete" for e in await store.events()
    )


@pytest.mark.asyncio
async def test_workflow_emits_blocked_terminal_event() -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    store = InMemoryAuditStore()
    workflow = GovernedWorkflow(observability=Observability(store, Metrics.create()))

    result = await workflow.run(
        uuid4(),
        uuid4(),
        "user-1",
        "send email to the board",
        "corr-obs-block",
        risk_level="HIGH",
    )

    assert result.status == "blocked"
    stages = [e.event_type for e in await store.events()]
    assert stages == ["planner", "approval", "blocked"]
    assert (await store.events())[-1].details["reason"].startswith("Execution blocked")


@pytest.mark.asyncio
async def test_workflow_emits_rejected_terminal_event_and_metric() -> None:
    from personal_ai_secretary.evaluation.runtime import (
        EvaluationContext,
        EvaluationCriterion,
        EvaluationOutcome,
        ReleaseGateEvaluator,
    )
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    class AlwaysReject(ReleaseGateEvaluator):
        def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
            return EvaluationOutcome(
                passed=False,
                criteria=(EvaluationCriterion("always_reject", False, "nope"),),
            )

    store = InMemoryAuditStore()
    metrics = Metrics.create()
    workflow = GovernedWorkflow(
        evaluator=AlwaysReject(), observability=Observability(store, metrics)
    )

    result = await workflow.run(
        uuid4(), uuid4(), "user-1", "hello", "corr-obs-reject"
    )

    assert result.status == "rejected"
    assert (await store.events())[-1].event_type == "rejected"
    assert (await store.events())[-1].outcome == "rejected"
    assert metrics.snapshot()["evaluation_rejections"] == 1
    evaluation_event = next(
        e for e in await store.events() if e.event_type == "evaluation"
    )
    assert "always_reject" in evaluation_event.details["failed_gates"]


@pytest.mark.asyncio
async def test_execution_agent_records_provider_duration_and_event() -> None:
    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.domain.contracts import ProviderResponse, RequestEnvelope

    class FakeProvider:
        name = "fake"

        async def generate(self, request: RequestEnvelope) -> ProviderResponse:
            return ProviderResponse(text="processed", provider=self.name)

    store = InMemoryAuditStore()
    metrics = Metrics.create()
    agent = ExecutionAgent(
        FakeProvider(), observability=Observability(store, metrics)
    )
    result = await agent.run(
        type(
            "Input",
            (),
            {
                "request_id": REQUEST_ID,
                "session_id": uuid4(),
                "user_id": "user-1",
                "text": "hello",
                "risk_level": "LOW",
                "correlation_id": "corr-provider",
                "context": {},
            },
        )()
    )

    assert result.content == "processed"
    assert metrics.snapshot()["provider_executions"] == 1
    assert "provider" in metrics.duration_snapshot()
    provider_events = [e for e in await store.events() if e.event_type == "provider"]
    assert [e.outcome for e in provider_events] == ["started", "ok"]


@pytest.mark.asyncio
async def test_execution_agent_counts_provider_failure() -> None:
    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.domain.contracts import RequestEnvelope

    class FailingProvider:
        name = "fake"

        async def generate(self, request: RequestEnvelope) -> object:
            raise RuntimeError("provider down")

    store = InMemoryAuditStore()
    metrics = Metrics.create()
    agent = ExecutionAgent(
        FailingProvider(), observability=Observability(store, metrics)
    )
    with pytest.raises(RuntimeError):
        await agent.run(
            type(
                "Input",
                (),
                {
                    "request_id": REQUEST_ID,
                    "session_id": uuid4(),
                    "user_id": "user-1",
                    "text": "hello",
                    "risk_level": "LOW",
                    "correlation_id": "corr-provider-fail",
                    "context": {},
                },
            )()
        )

    assert metrics.snapshot()["provider_failures"] == 1
    assert "provider down" in (await store.events())[-1].details["error"]


@pytest.mark.asyncio
async def test_execution_agent_counts_tool_execution_and_failure() -> None:
    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.tools.registry import ToolDefinition, ToolRegistry, ToolRisk

    async def add(values: dict) -> dict[str, object]:
        return {"result": int(values["a"]) + int(values["b"])}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "add",
            ToolRisk.LOW,
            False,
            add,
            argument_schema={"a": "integer", "b": "integer"},
        )
    )
    store = InMemoryAuditStore()
    metrics = Metrics.create()
    agent = ExecutionAgent(None, registry, Observability(store, metrics))

    ok = await agent.run(
        type(
            "Input",
            (),
            {
                "request_id": REQUEST_ID,
                "session_id": uuid4(),
                "user_id": "user-1",
                "text": '@tool:add {"a": 1, "b": 2}',
                "risk_level": "LOW",
                "correlation_id": "corr-tool",
                "context": {},
            },
        )()
    )
    assert "returned: 3" in ok.content
    assert metrics.snapshot()["tool_executions"] == 1
    assert "tool" in metrics.duration_snapshot()
    assert (await store.events())[-1].event_type == "tool"
    assert (await store.events())[-1].details["name"] == "add"

    bad = await agent.run(
        type(
            "Input",
            (),
            {
                "request_id": REQUEST_ID,
                "session_id": uuid4(),
                "user_id": "user-1",
                "text": '@tool:add {"a": "x"}',
                "risk_level": "LOW",
                "correlation_id": "corr-tool-bad",
                "context": {},
            },
        )()
    )
    assert "rejected arguments" in bad.content
    assert metrics.snapshot()["tool_failures"] == 1