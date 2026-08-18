from datetime import UTC, datetime, timedelta

from personal_ai_secretary.memory.service import (
    InMemoryMemoryStore,
    MemoryClass,
    MemoryItem,
    MemoryPolicy,
)


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


async def test_store_retrieves_only_own_user_items() -> None:
    store = InMemoryMemoryStore()
    await store.add(_item("m1", "alice", "alice note"))
    await store.add(_item("m2", "bob", "bob note"))

    assert [item.content for item in await store.retrieve("alice")] == ["alice note"]
    assert (await store.retrieve("bob"))[0].content == "bob note"
    assert await store.retrieve("charlie") == []


async def test_store_filters_by_memory_class() -> None:
    store = InMemoryMemoryStore()
    await store.add(_item("m1", "alice", "session note", MemoryClass.SESSION))
    await store.add(_item("m2", "alice", "long term note", MemoryClass.LONG_TERM))

    session_items = await store.retrieve("alice", memory_class=MemoryClass.SESSION)
    assert [item.memory_id for item in session_items] == ["m1"]
    long_term_items = await store.retrieve("alice", memory_class=MemoryClass.LONG_TERM)
    assert [item.memory_id for item in long_term_items] == ["m2"]


async def test_store_returns_newest_first_and_honors_limit() -> None:
    store = InMemoryMemoryStore()
    await store.add(_item("old", "alice", "old note", age_minutes=30))
    await store.add(_item("new", "alice", "new note", age_minutes=1))

    items = await store.retrieve("alice", limit=1)
    assert [item.memory_id for item in items] == ["new"]


async def test_store_excludes_expired_items() -> None:
    store = InMemoryMemoryStore()
    expired = _item("expired", "alice")
    expired.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await store.add(expired)
    await store.add(_item("valid", "alice"))

    assert [item.memory_id for item in await store.retrieve("alice")] == ["valid"]


async def test_store_clear_removes_all_items() -> None:
    store = InMemoryMemoryStore()
    await store.add(_item("m1", "alice"))
    await store.clear()
    assert await store.retrieve("alice") == []