from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from personal_ai_secretary.agents.builtin import ResearchAgent
from personal_ai_secretary.agents.contracts import AgentInput
from personal_ai_secretary.domain.models import Base
from personal_ai_secretary.infrastructure.stores import PostgresEvidenceStore
from personal_ai_secretary.rag.service import EvidenceSource


def _source(source_id: str, user_id: str, title: str = "hello policy") -> EvidenceSource:
    return EvidenceSource(
        source_id=source_id,
        user_id=user_id,
        uri="https://example.com/policies",
        title=title,
        content="follow the company policy when replying",
    )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker]]:
    db_path = tmp_path / "evidence.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, factory
    await engine.dispose()


async def test_evidence_store_add_and_retrieve_ranked(db) -> None:
    _, factory = db
    store = PostgresEvidenceStore(factory)
    await store.add(_source("s1", "alice", "send email policy"))

    evidence = await store.retrieve("send email", user_id="alice")
    assert len(evidence) == 1
    assert evidence[0].source_id == "s1"
    assert evidence[0].score > 0
    assert "policy" in evidence[0].text


async def test_evidence_store_is_user_scoped(db) -> None:
    _, factory = db
    store = PostgresEvidenceStore(factory)
    await store.add(_source("s1", "alice", "send email policy"))
    await store.add(_source("s2", "bob", "send email policy"))

    assert len(await store.retrieve("send email", user_id="alice")) == 1
    assert len(await store.retrieve("send email", user_id="bob")) == 1
    assert await store.retrieve("send email", user_id="charlie") == []


async def test_evidence_store_filters_expired_sources(db) -> None:
    _, factory = db
    store = PostgresEvidenceStore(factory, ttl_seconds=0)
    await store.add(_source("expired", "alice", "send email policy"))

    assert await store.retrieve("send email", user_id="alice") == []


async def test_evidence_store_upserts_per_user_and_source(db) -> None:
    _, factory = db
    store = PostgresEvidenceStore(factory)
    await store.add(_source("s1", "alice", "send email policy"))
    await store.add(_source("s1", "alice", "updated policy"))
    await store.add(_source("s1", "bob", "bob policy"))

    sources = await store.list_sources("alice")
    assert len(sources) == 1
    assert sources[0].title == "updated policy"
    assert [s.source_id for s in await store.list_sources("bob")] == ["s1"]


async def test_evidence_store_returns_nothing_without_user(db) -> None:
    _, factory = db
    store = PostgresEvidenceStore(factory)
    await store.add(_source("s1", "alice", "send email policy"))

    assert await store.retrieve("send email", user_id=None) == []
    assert await store.retrieve("send email", user_id="") == []


async def test_evidence_store_survives_reconnect(db, tmp_path: Path) -> None:
    _, factory = db
    store = PostgresEvidenceStore(factory)
    await store.add(_source("s1", "alice", "send email policy"))

    engine2 = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'evidence.db'}")
    factory2 = async_sessionmaker(engine2, expire_on_commit=False)
    store2 = PostgresEvidenceStore(factory2)
    evidence = await store2.retrieve("send email", user_id="alice")
    assert len(evidence) == 1
    assert evidence[0].source_id == "s1"
    await engine2.dispose()


async def test_research_agent_uses_persistent_evidence(db) -> None:
    _, factory = db
    store = PostgresEvidenceStore(factory)
    await store.add(_source("s1", "alice", "send email policy"))

    artifact = await ResearchAgent(store, None).run(
        AgentInput(
            request_id=uuid4(),
            session_id=uuid4(),
            user_id="alice",
            text="send email",
            correlation_id="corr-evidence",
        )
    )

    assert artifact.evidence_ids
    assert artifact.metadata["evidence"][0]["source_id"] == "s1"


async def test_workflow_retrieves_persistent_evidence_for_owner(db) -> None:
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    _, factory = db
    store = PostgresEvidenceStore(factory)
    await store.add(_source("s1", "alice", "send email policy"))
    await store.add(_source("s2", "bob", "send email policy"))

    workflow = GovernedWorkflow(retriever=store)
    alice = await workflow.run(
        uuid4(),
        uuid4(),
        "alice",
        "send email",
        "corr-wf-evidence-alice",
    )
    assert alice.status == "completed"
    research = alice.artifacts[1]
    assert research.role.value == "research"
    assert len(research.evidence_ids) == 1
    assert research.metadata["evidence"][0]["source_id"] == "s1"


async def test_workflow_evidence_observability_coherence(db) -> None:
    from personal_ai_secretary.observability.audit import InMemoryAuditStore
    from personal_ai_secretary.observability.metrics import Metrics
    from personal_ai_secretary.observability.observer import Observability
    from personal_ai_secretary.observability.tracing import (
        ATTRIBUTE_EVIDENCE_COUNT,
        clear_recorded_spans,
        get_recorded_spans,
    )
    from personal_ai_secretary.workflow.engine import GovernedWorkflow

    _, factory = db
    store = PostgresEvidenceStore(factory)
    await store.add(_source("s1", "alice", "send email policy"))

    store_audit = InMemoryAuditStore()
    metrics = Metrics.create()
    workflow = GovernedWorkflow(
        retriever=store, observability=Observability(store_audit, metrics)
    )
    clear_recorded_spans()
    result = await workflow.run(
        uuid4(), uuid4(), "alice", "send email", "corr-wf-evidence-coherence"
    )
    assert result.status == "completed"

    assert metrics.snapshot().get("evidence_retrieved") == 1

    research_span = next(
        span for span in get_recorded_spans() if span.name == "research"
    )
    assert research_span.attributes.get(ATTRIBUTE_EVIDENCE_COUNT) == 1

    research_event = next(
        e for e in await store_audit.events() if e.event_type == "research"
    )
    assert research_event.details["evidence_count"] == 1