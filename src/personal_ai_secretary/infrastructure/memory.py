from personal_ai_secretary.infrastructure.database import get_session_factory
from personal_ai_secretary.infrastructure.stores import PostgresMemoryStore
from personal_ai_secretary.memory.service import InMemoryMemoryStore, MemoryStore
from personal_ai_secretary.shared.config import get_settings

_store: MemoryStore | None = None


def init_memory_store() -> MemoryStore:
    global _store
    settings = get_settings()
    if settings.persistent_stores:
        _store = PostgresMemoryStore(get_session_factory())
    else:
        _store = InMemoryMemoryStore()
    assert _store is not None
    return _store


def get_memory_store() -> MemoryStore:
    if _store is None:
        return init_memory_store()
    assert _store is not None
    return _store