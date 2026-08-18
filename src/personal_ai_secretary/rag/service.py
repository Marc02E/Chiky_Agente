from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Source:
    source_id: str
    uri: str
    title: str
    authority: float = 0.5
    freshness: datetime | None = None


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    """A user-scoped source of evidence for persistent retrieval (FASE 13B)."""

    source_id: str
    user_id: str
    uri: str
    title: str
    content: str
    authority: float = 0.5
    created_at: datetime | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    source_id: str
    text: str
    score: float
    retrieved_at: datetime


class Retriever(Protocol):
    async def retrieve(
        self, query: str, limit: int = 5, user_id: str | None = None
    ) -> list[Evidence]: ...


class GovernedRetriever:
    def __init__(self, sources: Iterable[Source] = ()) -> None:
        self.sources = tuple(sources)

    async def retrieve(
        self, query: str, limit: int = 5, user_id: str | None = None
    ) -> list[Evidence]:
        if not query.strip() or limit <= 0:
            return []
        terms = set(query.lower().split())
        ranked: list[Evidence] = []
        now = datetime.now(UTC)
        for source in self.sources:
            haystack = f"{source.title} {source.uri}".lower()
            overlap = len(terms.intersection(haystack.split()))
            score = min(1.0, overlap / max(1, len(terms))) * source.authority
            if score <= 0:
                continue
            evidence_id = sha256(f"{source.source_id}:{query}".encode()).hexdigest()[:24]
            ranked.append(Evidence(evidence_id, source.source_id, haystack, score, now))
        return sorted(ranked, key=lambda item: item.score, reverse=True)[:limit]

    @staticmethod
    def validate(evidence: Iterable[Evidence]) -> bool:
        return all(item.source_id and item.text and item.score >= 0 for item in evidence)
