from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from personal_ai_secretary.application.service import RequestService
from personal_ai_secretary.domain.contracts import (
    ProviderInfo,
    ProviderResponse,
    RequestCreate,
    RequestEnvelope,
)
from personal_ai_secretary.domain.models import Base, RequestRecord, SessionRecord
from personal_ai_secretary.evaluation.runtime import (
    EvaluationContext,
    EvaluationCriterion,
    EvaluationOutcome,
    ReleaseGateEvaluator,
)
from personal_ai_secretary.memory.service import (
    InMemoryMemoryStore,
    MemoryClass,
    MemoryItem,
)


class RejectingEvaluator(ReleaseGateEvaluator):
    def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
        return EvaluationOutcome(
            passed=False,
            criteria=(EvaluationCriterion("always_reject", False, "test-only rejection"),),
        )


class FakeProvider:
    name = "fake"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def health(self) -> ProviderInfo:
        return ProviderInfo(
            name=self.name,
            mode="test",
            available=True,
            is_ai=False,
            detail="test provider",
        )

    async def generate(self, request: RequestEnvelope) -> ProviderResponse:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider failure")
        return ProviderResponse(text=f"processed:{request.input}", provider=self.name)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_creates_session_and_request(session: AsyncSession) -> None:
    service = RequestService(session, FakeProvider())
    accepted = await service.create(RequestCreate(input="hello"), "user-1", "corr-1", "idem-1")

    result = await service.get(accepted.request_id)
    assert result is not None
    assert result.status == "accepted"
    assert result.result is None
    assert result.correlation_id == "corr-1"

    request_record = await session.get(RequestRecord, accepted.request_id)
    assert request_record is not None
    session_record = await session.get(SessionRecord, request_record.session_id)
    assert session_record is not None
    assert session_record.request_count == 1
    assert session_record.user_id == "user-1"


@pytest.mark.asyncio
async def test_create_reuses_existing_session(session: AsyncSession) -> None:
    session_id = uuid4()
    session.add(SessionRecord(session_id=session_id, user_id="user-1"))
    await session.commit()

    service = RequestService(session, FakeProvider())
    await service.create(
        RequestCreate(input="one", session_id=session_id), "user-1", "corr-1", None
    )
    await service.create(
        RequestCreate(input="two", session_id=session_id), "user-1", "corr-2", None
    )

    record = await session.get(SessionRecord, session_id)
    assert record is not None
    assert record.request_count == 2


@pytest.mark.asyncio
async def test_create_is_idempotent(session: AsyncSession) -> None:
    service = RequestService(session, FakeProvider())
    first = await service.create(RequestCreate(input="hello"), "user-1", "corr-1", "same-key")
    second = await service.create(RequestCreate(input="different"), "user-1", "corr-2", "same-key")

    assert second.request_id == first.request_id
    assert second.correlation_id == "corr-1"


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown_request(session: AsyncSession) -> None:
    service = RequestService(session, FakeProvider())
    assert await service.get(uuid4()) is None


@pytest.mark.asyncio
async def test_execute_completes_request(session: AsyncSession) -> None:
    provider = FakeProvider()
    service = RequestService(session, provider)
    accepted = await service.create(RequestCreate(input="hello"), "user-1", "corr-1", None)

    result = await service.execute(accepted.request_id)

    assert result is not None
    assert result.status == "completed"
    assert result.result == "processed:hello"
    assert provider.calls == 1

    completed = await service.execute(accepted.request_id)
    assert completed is not None
    assert completed.status == "completed"
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_execute_marks_failed_when_provider_fails(session: AsyncSession) -> None:
    service = RequestService(session, FakeProvider(fail=True))
    accepted = await service.create(RequestCreate(input="hello"), "user-1", "corr-1", None)

    result = await service.execute(accepted.request_id)

    assert result is not None
    assert result.status == "failed"
    assert result.result is None


@pytest.mark.asyncio
async def test_execute_blocks_high_risk_without_approval(session: AsyncSession) -> None:
    provider = FakeProvider()
    service = RequestService(session, provider)
    accepted = await service.create(
        RequestCreate(input="send email to the board"), "user-1", "corr-1", None
    )

    result = await service.execute(accepted.request_id)

    assert result is not None
    assert result.status == "blocked"
    assert result.result is not None
    assert "approval required" in result.result
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_execute_completes_high_risk_with_approval(session: AsyncSession) -> None:
    provider = FakeProvider()
    service = RequestService(session, provider)
    accepted = await service.create(
        RequestCreate(input="send email to the board"), "user-1", "corr-1", None
    )

    result = await service.execute(accepted.request_id, approval_granted=True)

    assert result is not None
    assert result.status == "completed"
    assert result.result == "processed:send email to the board"
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_execute_re_runs_blocked_request_with_approval(session: AsyncSession) -> None:
    provider = FakeProvider()
    service = RequestService(session, provider)
    accepted = await service.create(
        RequestCreate(input="send email to the board"), "user-1", "corr-1", None
    )

    blocked = await service.execute(accepted.request_id)
    assert blocked is not None
    assert blocked.status == "blocked"

    completed = await service.execute(accepted.request_id, approval_granted=True)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.result == "processed:send email to the board"
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_execute_returns_none_for_unknown_request(session: AsyncSession) -> None:
    service = RequestService(session, FakeProvider())
    assert await service.execute(uuid4()) is None


@pytest.mark.asyncio
async def test_completed_request_writes_user_scoped_memory(session: AsyncSession) -> None:
    store = InMemoryMemoryStore()
    service = RequestService(session, FakeProvider(), memory=store)
    accepted = await service.create(RequestCreate(input="hello"), "user-1", "corr-1", None)

    result = await service.execute(accepted.request_id)

    assert result is not None
    assert result.status == "completed"
    items = await store.retrieve("user-1")
    assert len(items) == 1
    assert items[0].user_id == "user-1"
    assert items[0].memory_class == MemoryClass.SESSION
    assert "hello" in items[0].content
    assert "processed:hello" in items[0].content
    assert await store.retrieve("other-user") == []


@pytest.mark.asyncio
async def test_completed_execute_does_not_duplicate_memory(session: AsyncSession) -> None:
    store = InMemoryMemoryStore()
    service = RequestService(session, FakeProvider(), memory=store)
    accepted = await service.create(RequestCreate(input="hello"), "user-1", "corr-1", None)

    await service.execute(accepted.request_id)
    await service.execute(accepted.request_id)

    assert len(await store.retrieve("user-1")) == 1


@pytest.mark.asyncio
async def test_blocked_request_does_not_write_memory(session: AsyncSession) -> None:
    store = InMemoryMemoryStore()
    service = RequestService(session, FakeProvider(), memory=store)
    accepted = await service.create(
        RequestCreate(input="send email to the board"), "user-1", "corr-1", None
    )

    await service.execute(accepted.request_id)

    assert await store.retrieve("user-1") == []


@pytest.mark.asyncio
async def test_get_session_returns_status_and_none_for_unknown(session: AsyncSession) -> None:
    service = RequestService(session, FakeProvider())
    session_id = uuid4()
    session.add(SessionRecord(session_id=session_id, user_id="user-1", request_count=3))
    await session.commit()

    result = await service.get_session(session_id, correlation_id="corr-session")
    assert result is not None
    assert result.session_id == session_id
    assert result.request_count == 3
    assert result.correlation_id == "corr-session"
    assert await service.get_session(uuid4()) is None


@pytest.mark.asyncio
async def test_create_rejects_session_owned_by_another_user(session: AsyncSession) -> None:
    session_id = uuid4()
    session.add(SessionRecord(session_id=session_id, user_id="owner", request_count=0))
    await session.commit()

    service = RequestService(session, FakeProvider())
    with pytest.raises(PermissionError):
        await service.create(
            RequestCreate(input="secret", session_id=session_id),
            "other",
            "corr",
            None,
        )


@pytest.mark.asyncio
async def test_execute_runs_tool_when_registry_provided(session: AsyncSession) -> None:
    from personal_ai_secretary.tools.builtin import default_tool_registry

    provider = FakeProvider()
    service = RequestService(session, provider, tools=default_tool_registry())
    accepted = await service.create(
        RequestCreate(input='@tool:calculator {"expression": "2+2"}'),
        "user-1",
        "corr-1",
        None,
    )

    result = await service.execute(accepted.request_id)

    assert result is not None
    assert result.status == "completed"
    assert result.result == "Tool 'calculator' returned: 4"
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_execute_blocks_sensitive_tool_without_approval(session: AsyncSession) -> None:
    from personal_ai_secretary.tools.registry import ToolDefinition, ToolRegistry, ToolRisk

    async def send(_: dict[str, object]) -> dict[str, object]:
        return {"sent": True}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "send_email", ToolRisk.HIGH, True, send, argument_schema={"to": "string"}
        )
    )

    provider = FakeProvider()
    service = RequestService(session, provider, tools=registry)
    accepted = await service.create(
        RequestCreate(input='@tool:send_email {"to": "boss@example.com"}'),
        "user-1",
        "corr-1",
        None,
    )

    blocked = await service.execute(accepted.request_id)
    assert blocked is not None
    assert blocked.status == "blocked"
    assert blocked.result is not None
    assert "approval required" in blocked.result
    assert provider.calls == 0

    completed = await service.execute(accepted.request_id, approval_granted=True)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.result == 'Tool \'send_email\' returned: {"sent": true}'


@pytest.mark.asyncio
async def test_execute_fails_when_tool_handler_raises(session: AsyncSession) -> None:
    from personal_ai_secretary.tools.registry import ToolDefinition, ToolRegistry, ToolRisk

    async def explode(_: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("handler crashed")

    registry = ToolRegistry()
    registry.register(ToolDefinition("boom", ToolRisk.LOW, False, explode))

    service = RequestService(session, FakeProvider(), tools=registry)
    accepted = await service.create(
        RequestCreate(input="@tool:boom {}"), "user-1", "corr-1", None
    )

    result = await service.execute(accepted.request_id)

    assert result is not None
    assert result.status == "failed"
    assert result.result is None


@pytest.mark.asyncio
async def test_execute_surfaces_rejected_status(session: AsyncSession) -> None:
    provider = FakeProvider()
    service = RequestService(session, provider, evaluator=RejectingEvaluator())
    accepted = await service.create(RequestCreate(input="hello"), "user-1", "corr-1", None)

    result = await service.execute(accepted.request_id)

    assert result is not None
    assert result.status == "rejected"
    assert result.result is not None
    assert "rejected by evaluation" in result.result
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_rejected_request_does_not_write_memory(session: AsyncSession) -> None:
    store = InMemoryMemoryStore()
    service = RequestService(
        session, FakeProvider(), memory=store, evaluator=RejectingEvaluator()
    )
    accepted = await service.create(RequestCreate(input="hello"), "user-1", "corr-1", None)

    await service.execute(accepted.request_id)

    assert await store.retrieve("user-1") == []


@pytest.mark.asyncio
async def test_execute_emits_observability_for_completed(session: AsyncSession) -> None:
    from personal_ai_secretary.observability.audit import InMemoryAuditStore
    from personal_ai_secretary.observability.metrics import Metrics
    from personal_ai_secretary.observability.observer import Observability

    audit = InMemoryAuditStore()
    metrics = Metrics.create()
    service = RequestService(
        session, FakeProvider(), observability=Observability(audit, metrics)
    )
    accepted = await service.create(RequestCreate(input="hello"), "user-1", "corr-obs", None)

    result = await service.execute(accepted.request_id)

    assert result is not None
    assert result.status == "completed"
    assert metrics.snapshot()["requests_total"] == 1
    assert metrics.snapshot()["requests_completed"] == 1
    stages = [e.event_type for e in await audit.events("user-1")]
    assert stages[0] == "request_received"
    assert stages[-1] == "request"
    assert "planner" in stages
    assert "provider" in stages
    assert "evaluation" in stages
    assert "completed" in stages
    assert (await audit.events("user-1"))[-1].outcome == "completed"
    assert all(e.details["correlation_id"] == "corr-obs" for e in await audit.events("user-1"))


@pytest.mark.asyncio
async def test_execute_emits_failed_event_and_metric(session: AsyncSession) -> None:
    from personal_ai_secretary.observability.audit import InMemoryAuditStore
    from personal_ai_secretary.observability.metrics import Metrics
    from personal_ai_secretary.observability.observer import Observability

    audit = InMemoryAuditStore()
    metrics = Metrics.create()
    service = RequestService(
        session,
        FakeProvider(fail=True),
        observability=Observability(audit, metrics),
    )
    accepted = await service.create(RequestCreate(input="hello"), "user-1", "corr-fail", None)

    result = await service.execute(accepted.request_id)

    assert result is not None
    assert result.status == "failed"
    assert metrics.snapshot()["requests_failed"] == 1
    terminal = (await audit.events("user-1"))[-1]
    assert terminal.event_type == "request"
    assert terminal.outcome == "failed"
    assert "provider failure" in terminal.details["error"]


@pytest.mark.asyncio
async def test_execute_emits_blocked_metric(session: AsyncSession) -> None:
    from personal_ai_secretary.observability.audit import InMemoryAuditStore
    from personal_ai_secretary.observability.metrics import Metrics
    from personal_ai_secretary.observability.observer import Observability

    audit = InMemoryAuditStore()
    metrics = Metrics.create()
    service = RequestService(
        session, FakeProvider(), observability=Observability(audit, metrics)
    )
    accepted = await service.create(
        RequestCreate(input="send email to the board"), "user-1", "corr-block", None
    )

    result = await service.execute(accepted.request_id)

    assert result is not None
    assert result.status == "blocked"
    assert metrics.snapshot()["requests_blocked"] == 1
    assert (await audit.events("user-1"))[-1].outcome == "blocked"


@pytest.mark.asyncio
async def test_execute_emits_rejected_metric(session: AsyncSession) -> None:
    from personal_ai_secretary.observability.audit import InMemoryAuditStore
    from personal_ai_secretary.observability.metrics import Metrics
    from personal_ai_secretary.observability.observer import Observability

    audit = InMemoryAuditStore()
    metrics = Metrics.create()
    service = RequestService(
        session,
        FakeProvider(),
        evaluator=RejectingEvaluator(),
        observability=Observability(audit, metrics),
    )
    accepted = await service.create(RequestCreate(input="hello"), "user-1", "corr-rej", None)

    result = await service.execute(accepted.request_id)

    assert result is not None
    assert result.status == "rejected"
    assert metrics.snapshot()["requests_rejected"] == 1
    assert (await audit.events("user-1"))[-1].outcome == "rejected"


class FailingMemoryStore(InMemoryMemoryStore):
    async def add(self, item: MemoryItem) -> None:
        raise RuntimeError("memory store unavailable")


@pytest.mark.asyncio
async def test_idempotency_key_is_scoped_per_user(session: AsyncSession) -> None:
    service = RequestService(session, FakeProvider())
    shared_key = "shared-key"

    user_a = await service.create(
        RequestCreate(input="a"), "user-a", "corr-a", shared_key
    )
    user_a_again = await service.create(
        RequestCreate(input="a2"), "user-a", "corr-a2", shared_key
    )
    user_b = await service.create(
        RequestCreate(input="b"), "user-b", "corr-b", shared_key
    )

    assert user_a.request_id == user_a_again.request_id
    assert user_b.request_id != user_a.request_id


@pytest.mark.asyncio
async def test_memory_failure_does_not_flip_completed_to_failed(
    session: AsyncSession,
) -> None:
    from personal_ai_secretary.observability.audit import InMemoryAuditStore
    from personal_ai_secretary.observability.metrics import Metrics
    from personal_ai_secretary.observability.observer import Observability

    audit = InMemoryAuditStore()
    metrics = Metrics.create()
    provider = FakeProvider()
    service = RequestService(
        session,
        provider,
        memory=FailingMemoryStore(),
        observability=Observability(audit, metrics),
    )
    accepted = await service.create(RequestCreate(input="hello"), "user-1", "corr-mem", None)

    result = await service.execute(accepted.request_id)

    assert result is not None
    assert result.status == "completed"
    assert result.result == "processed:hello"
    assert provider.calls == 1
    assert metrics.snapshot()["requests_completed"] == 1
    outcomes = [event.outcome for event in await audit.events("user-1")]
    assert "completed" in outcomes
    assert "error" in outcomes


@pytest.mark.asyncio
async def test_recover_stale_running_marks_failed_without_reexecution(
    session: AsyncSession,
) -> None:
    provider = FakeProvider()
    service = RequestService(session, provider)
    accepted = await service.create(RequestCreate(input="hello"), "user-1", "corr-stale", None)

    record = await session.get(RequestRecord, accepted.request_id)
    assert record is not None
    record.status = "running"
    record.updated_at = datetime.now(UTC) - timedelta(hours=1)
    await session.commit()

    recovered = await service.recover_stale_running(older_than_seconds=0)
    assert recovered == 1

    status = await service.get(accepted.request_id)
    assert status is not None
    assert status.status == "failed"
    assert provider.calls == 0

    again = await service.recover_stale_running(older_than_seconds=0)
    assert again == 0


@pytest.mark.asyncio
async def test_recover_skips_fresh_running(session: AsyncSession) -> None:
    provider = FakeProvider()
    service = RequestService(session, provider)
    accepted = await service.create(RequestCreate(input="hello"), "user-1", "corr-fresh", None)

    record = await session.get(RequestRecord, accepted.request_id)
    assert record is not None
    record.status = "running"
    record.updated_at = datetime.now(UTC)
    await session.commit()

    recovered = await service.recover_stale_running(older_than_seconds=3600)
    assert recovered == 0
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_execute_recovers_stale_running_request(session: AsyncSession) -> None:
    provider = FakeProvider()
    service = RequestService(session, provider)
    accepted = await service.create(RequestCreate(input="hello"), "user-1", "corr-exec", None)

    record = await session.get(RequestRecord, accepted.request_id)
    assert record is not None
    record.status = "running"
    record.updated_at = datetime.now(UTC) - timedelta(hours=1)
    await session.commit()

    result = await service.execute(accepted.request_id, "user-1")

    assert result is not None
    assert result.status == "failed"
    assert provider.calls == 0

    persisted = await session.get(RequestRecord, accepted.request_id)
    assert persisted is not None
    assert persisted.status == "failed"
    assert persisted.result is not None
    assert "recovered from a stale running state" in persisted.result


@pytest.mark.asyncio
async def test_send_message_happy_path(session: AsyncSession) -> None:
    provider = FakeProvider()
    service = RequestService(session, provider)
    payload = RequestCreate(input="hello world")
    response = await service.send_message(payload, "user-1", "corr-sm", None)

    assert response.status == "completed"
    assert response.user_message.role == "user"
    assert response.user_message.content == "hello world"
    assert response.assistant_message is not None
    assert response.assistant_message.role == "assistant"
    assert response.assistant_message.content == "processed:hello world"
    assert response.correlation_id == "corr-sm"


@pytest.mark.asyncio
async def test_send_message_with_session_id(session: AsyncSession) -> None:
    provider = FakeProvider()
    service = RequestService(session, provider)
    session_id = uuid4()
    payload = RequestCreate(input="msg with session", session_id=session_id)
    response = await service.send_message(payload, "user-1", "corr-sm2", None)

    assert response.session_id == session_id
    assert response.status == "completed"


@pytest.mark.asyncio
async def test_history_returns_conversation_messages(session: AsyncSession) -> None:
    provider = FakeProvider()
    service = RequestService(session, provider)

    session_id = uuid4()
    await service.create(
        RequestCreate(input="q1", session_id=session_id), "user-1", "c1", None
    )
    accepted2 = await service.create(
        RequestCreate(input="q2", session_id=session_id), "user-1", "c2", None
    )
    await service.execute(accepted2.request_id)

    history = await service.history(session_id, "user-1", "corr-h")
    assert history is not None
    assert len(history.messages) == 3
    assert history.messages[0].role == "user"
    assert history.messages[0].content == "q1"
    assert history.messages[1].role == "user"
    assert history.messages[1].content == "q2"
    assert history.messages[2].role == "assistant"
    assert history.messages[2].content == "processed:q2"
    assert history.session_id == session_id


@pytest.mark.asyncio
async def test_history_returns_none_for_wrong_user(session: AsyncSession) -> None:
    provider = FakeProvider()
    service = RequestService(session, provider)
    session_id = uuid4()
    await service.create(
        RequestCreate(input="q", session_id=session_id), "user-1", "c", None
    )

    result = await service.history(session_id, "user-2", "corr-h2")
    assert result is None


@pytest.mark.asyncio
async def test_create_rejects_idempotency_key_for_different_session(
    session: AsyncSession,
) -> None:
    service = RequestService(session, FakeProvider())
    first_session = uuid4()
    second_session = uuid4()

    await service.create(
        RequestCreate(input="first", session_id=first_session),
        "user-1",
        "corr-1",
        "shared-key",
    )

    with pytest.raises(ValueError, match="already associated with another session"):
        await service.create(
            RequestCreate(input="second", session_id=second_session),
            "user-1",
            "corr-2",
            "shared-key",
        )
