from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from personal_ai_secretary.domain.models import Base
from personal_ai_secretary.infrastructure.stores import (
    PostgresAuditStore,
    PostgresMemoryStore,
    _prune_expired_evidence,
    _prune_expired_memory,
    _prune_old_audit,
)
from personal_ai_secretary.memory.service import MemoryClass, MemoryItem, MemoryPolicy
from personal_ai_secretary.observability.audit import REDACTED, audit_event


def _item(
    memory_id: str,
    user_id: str,
    content: str = "note",
    memory_class: MemoryClass = MemoryClass.SESSION,
    age_minutes: int = 0,
) -> MemoryItem:
    now = datetime.now(UTC)
    return MemoryItem(
        memory_id=memory_id,
        user_id=user_id,
        content=content,
        memory_class=memory_class,
        created_at=now - timedelta(minutes=age_minutes),
        expires_at=MemoryPolicy().expiration(memory_class, now),
    )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker]]:
    db_path = tmp_path / "stores.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, factory
    await engine.dispose()


async def test_persistent_memory_store_scopes_by_user(db) -> None:
    _, factory = db
    store = PostgresMemoryStore(factory)
    await store.add(_item("m1", "alice", "alice note"))
    await store.add(_item("m2", "bob", "bob note"))

    assert [item.content for item in await store.retrieve("alice")] == ["alice note"]
    assert (await store.retrieve("bob"))[0].content == "bob note"
    assert await store.retrieve("charlie") == []


async def test_persistent_memory_store_filters_class_and_expiry(db) -> None:
    _, factory = db
    store = PostgresMemoryStore(factory)
    await store.add(_item("s", "alice", "session note", MemoryClass.SESSION))
    await store.add(_item("lt", "alice", "long term", MemoryClass.LONG_TERM))
    expired = _item("expired", "alice")
    expired.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await store.add(expired)

    session_items = await store.retrieve("alice", memory_class=MemoryClass.SESSION)
    assert [item.memory_id for item in session_items] == ["s"]
    assert [item.memory_id for item in await store.retrieve("alice")] == ["lt", "s"]


async def test_persistent_memory_store_survives_reconnect(db, tmp_path: Path) -> None:
    _, factory = db
    store = PostgresMemoryStore(factory)
    await store.add(_item("m1", "alice", "persisted note"))

    engine2 = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stores.db'}")
    factory2 = async_sessionmaker(engine2, expire_on_commit=False)
    store2 = PostgresMemoryStore(factory2)
    assert [item.content for item in await store2.retrieve("alice")] == ["persisted note"]
    await engine2.dispose()


async def test_persistent_audit_store_roundtrip_and_redaction(db) -> None:
    _, factory = db
    store = PostgresAuditStore(factory)
    await store.record(
        audit_event(
            "request",
            "req-1",
            "user-a",
            "ok",
            correlation_id="corr-1",
            Authorization="Bearer secret",
        )
    )
    await store.record(
        audit_event("request", "req-2", "user-b", "ok", correlation_id="corr-2")
    )

    user_events = await store.events(user_id="user-a")
    assert len(user_events) == 1
    assert user_events[0].details["Authorization"] == REDACTED
    assert user_events[0].details["correlation_id"] == "corr-1"

    all_events = await store.events()
    assert [e.request_id for e in all_events] == ["req-2", "req-1"]
    assert all(e.timestamp.tzinfo is not None for e in all_events)


async def test_persistent_audit_store_honors_limit_and_survives_reconnect(
    db, tmp_path: Path
) -> None:
    _, factory = db
    store = PostgresAuditStore(factory)
    for index in range(5):
        await store.record(
            audit_event("request", f"req-{index}", "user-a", "ok", correlation_id="c")
        )
    scoped = await store.events(limit=2)
    assert [e.request_id for e in scoped] == ["req-4", "req-3"]

    engine2 = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stores.db'}")
    factory2 = async_sessionmaker(engine2, expire_on_commit=False)
    store2 = PostgresAuditStore(factory2)
    assert len(await store2.events()) == 5
    await engine2.dispose()


async def test_memory_store_list_items(db) -> None:
    _, factory = db
    store = PostgresMemoryStore(factory)
    await store.add(_item("m1", "alice", "note 1", MemoryClass.SESSION))
    await store.add(_item("m2", "alice", "note 2", MemoryClass.LONG_TERM))
    await store.add(_item("m3", "bob", "bob note"))

    items = await store.list_items("alice")
    assert len(items) == 2
    contents = {item.content for item in items}
    assert contents == {"note 1", "note 2"}


async def test_memory_store_prune_expired(db) -> None:
    _, factory = db
    store = PostgresMemoryStore(factory)
    expired = _item("expired", "alice")
    expired.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await store.add(expired)
    await store.add(_item("fresh", "alice"))

    pruned = await store.prune_expired()
    assert pruned == 1
    remaining = await store.retrieve("alice")
    assert len(remaining) == 1
    assert remaining[0].memory_id == "fresh"


async def test_audit_store_create_roundtrip(db) -> None:
    _, factory = db
    store = PostgresAuditStore(factory)
    event = audit_event("request", "req-create", "user-a", "ok", correlation_id="c")
    returned = await store.create(event)
    assert returned.request_id == "req-create"
    assert returned.user_id == "user-a"

    events = await store.events(user_id="user-a")
    assert len(events) == 1
    assert events[0].request_id == "req-create"


async def test_prune_expired_evidence(db) -> None:
    _, factory = db
    from personal_ai_secretary.domain.models import EvidenceSourceRecord

    async with factory() as session:
        record = EvidenceSourceRecord(
            user_id="alice",
            source_id="s-expired",
            uri="https://example.com",
            title="t",
            content="c",
            authority=1.0,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        session.add(record)
        await session.commit()
        pruned = await _prune_expired_evidence(session)
        await session.commit()
    assert pruned == 1


async def test_prune_old_audit(db) -> None:
    _, factory = db
    store = PostgresAuditStore(factory)
    await store.record(
        audit_event("request", "old-req", "user-a", "ok", correlation_id="c")
    )
    async with factory() as session:
        pruned = await _prune_old_audit(session, retention_seconds=0)
        await session.commit()
    assert pruned == 1


async def test_prune_expired_memory(db) -> None:
    _, factory = db
    expired = _item("expired-m", "alice")
    expired.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    store = PostgresMemoryStore(factory)
    await store.add(expired)
    async with factory() as session:
        pruned = await _prune_expired_memory(session)
        await session.commit()
    assert pruned == 1