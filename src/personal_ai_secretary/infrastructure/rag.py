from personal_ai_secretary.infrastructure.database import get_session_factory
from personal_ai_secretary.infrastructure.stores import PostgresEvidenceStore
from personal_ai_secretary.rag.service import GovernedRetriever, Retriever
from personal_ai_secretary.shared.config import get_settings

_retriever: Retriever | None = None


def init_retriever() -> Retriever:
    global _retriever
    settings = get_settings()
    if settings.persistent_stores:
        _retriever = PostgresEvidenceStore(
            get_session_factory(), ttl_seconds=settings.evidence_ttl_seconds
        )
    else:
        _retriever = GovernedRetriever()
    return _retriever


def get_retriever() -> Retriever:
    if _retriever is None:
        return init_retriever()
    return _retriever