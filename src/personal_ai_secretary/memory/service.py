from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol


class MemoryClass(StrEnum):
    SESSION = "session"
    WORKING = "working"
    LONG_TERM = "long_term"
    ORGANIZATIONAL = "organizational"


@dataclass(slots=True)
class MemoryItem:
    memory_id: str
    user_id: str
    content: str
    memory_class: MemoryClass
    created_at: datetime
    expires_at: datetime | None = None


class MemoryStore(Protocol):
    async def add(self, item: MemoryItem) -> None: ...

    async def retrieve(
        self,
        user_id: str,
        memory_class: MemoryClass | None = None,
        limit: int = 10,
    ) -> list[MemoryItem]: ...


class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._items: dict[str, list[MemoryItem]] = {}

    async def add(self, item: MemoryItem) -> None:
        self._items.setdefault(item.user_id, []).append(item)

    async def retrieve(
        self,
        user_id: str,
        memory_class: MemoryClass | None = None,
        limit: int = 10,
    ) -> list[MemoryItem]:
        policy = MemoryPolicy()
        items = [
            item
            for item in self._items.get(user_id, [])
            if policy.can_access(item, user_id)
        ]
        if memory_class is not None:
            items = [item for item in items if item.memory_class == memory_class]
        items.sort(key=lambda item: item.created_at, reverse=True)
        return items[:limit]

    async def clear(self) -> None:
        self._items.clear()


class MemoryPolicy:
    TTL: dict[MemoryClass, timedelta | None] = {
        MemoryClass.SESSION: timedelta(hours=24),
        MemoryClass.WORKING: timedelta(days=7),
        MemoryClass.LONG_TERM: None,
        MemoryClass.ORGANIZATIONAL: None,
    }

    def expiration(self, memory_class: MemoryClass, now: datetime | None = None) -> datetime | None:
        ttl = self.TTL[memory_class]
        return None if ttl is None else (now or datetime.now(UTC)) + ttl

    def can_access(self, item: MemoryItem, user_id: str) -> bool:
        if item.user_id != user_id:
            return False
        return item.expires_at is None or item.expires_at > datetime.now(UTC)
