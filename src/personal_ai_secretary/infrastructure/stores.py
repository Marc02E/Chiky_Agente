"""Persistent, user-scoped evidence store implementing the ``Retriever`` protocol.

Ranking preserves the deterministic keyword-overlap strategy of
``GovernedRetriever``, scoped to the requesting user so evidence never
leaks across users. Sources can expire through an optional TTL
``evidence_ttl_seconds``; expired sources are never retrieved.
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import cast

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from personal_ai_secretary.domain.models import (
    AuditEventRecord,
    EvidenceSourceRecord,
    MemoryItemRecord,
    MetricRecord,
)
from personal_ai_secretary.memory.service import MemoryClass, MemoryItem
from personal_ai_secretary.observability.audit import AuditEvent, sanitize_value
from personal_ai_secretary.observability.metrics import Metrics
from personal_ai_secretary.rag.service import Evidence, EvidenceSource


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _memory_item(row: MemoryItemRecord) -> MemoryItem:
    return MemoryItem(
        memory_id=row.memory_id,
        user_id=row.user_id,
        content=row.content,
        memory_class=MemoryClass(row.memory_class),
        created_at=_as_utc(row.created_at),
        expires_at=None if row.expires_at is None else _as_utc(row.expires_at),
    )


def _evidence_source(row: EvidenceSourceRecord) -> EvidenceSource:
    return EvidenceSource(
        source_id=row.source_id,
        user_id=row.user_id,
        uri=row.uri,
        title=row.title,
        content=row.content,
        authority=row.authority,
        created_at=_as_utc(row.created_at),
        expires_at=None if row.expires_at is None else _as_utc(row.expires_at),
    )


async def _prune_expired_evidence(session: AsyncSession) -> int:
    """Delete evidence_sources whose expires_at has already expired."""
    now = datetime.now(UTC)
    result = await session.execute(
        delete(EvidenceSourceRecord).where(EvidenceSourceRecord.expires_at < now)
    )
    return cast(int, result.rowcount)  # type: ignore[attr-defined]


async def _prune_old_audit(session: AsyncSession, retention_seconds: int) -> int:
    """Delete audit events older than retention_seconds."""
    from datetime import timedelta

    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=retention_seconds)
    result = await session.execute(
        delete(AuditEventRecord).where(AuditEventRecord.timestamp < cutoff)
    )
    return cast(int, result.rowcount)  # type: ignore[attr-defined]


async def _prune_expired_memory(session: AsyncSession) -> int:
    """Delete memory items whose expires_at has already expired."""
    from personal_ai_secretary.domain.models import MemoryItemRecord

    now = datetime.now(UTC)
    result = await session.execute(
        delete(MemoryItemRecord).where(MemoryItemRecord.expires_at < now)
    )
    return cast(int, result.rowcount)  # type: ignore[attr-defined]


class PostgresEvidenceStore:
    """Persistent, user-scoped evidence store implementing the ``Retriever`` protocol."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._ttl_seconds = ttl_seconds

    async def add(self, source: EvidenceSource) -> EvidenceSource:
        now = datetime.now(UTC)
        expires_at = None
        if self._ttl_seconds is not None:
            expires_at = now + timedelta(seconds=self._ttl_seconds)
        record = EvidenceSourceRecord(
            user_id=source.user_id,
            source_id=source.source_id,
            uri=source.uri,
            title=source.title,
            content=source.content,
            authority=source.authority,
            created_at=now,
            expires_at=expires_at,
        )
        async with self._session_factory() as session:
            existing = await session.get(
                EvidenceSourceRecord, (source.user_id, source.source_id)
            )
            if existing is None:
                session.add(record)
            else:
                existing.uri = source.uri
                existing.title = source.title
                existing.content = source.content
                existing.authority = source.authority
                existing.created_at = now
                existing.expires_at = expires_at
            await session.commit()
        return EvidenceSource(
            source_id=source.source_id,
            user_id=source.user_id,
            uri=source.uri,
            title=source.title,
            content=source.content,
            authority=source.authority,
            created_at=now,
            expires_at=expires_at,
        )

    async def list_sources(self, user_id: str, limit: int = 100) -> list[EvidenceSource]:
        async with self._session_factory() as session:
            stmt = (
                select(EvidenceSourceRecord)
                .where(EvidenceSourceRecord.user_id == user_id)
                .order_by(EvidenceSourceRecord.created_at.desc())
            )
            rows = list((await session.scalars(stmt)).all())
        return [_evidence_source(row) for row in rows[:limit]]

    async def retrieve(
        self, query: str, limit: int = 5, user_id: str | None = None
    ) -> list[Evidence]:
        if not query.strip() or limit <= 0 or not user_id:
            return []
        terms = set(query.lower().split())
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(EvidenceSourceRecord).where(
                            EvidenceSourceRecord.user_id == user_id
                        )
                    )
                ).all()
            )
        ranked: list[Evidence] = []
        for row in rows:
            if row.expires_at is not None and _as_utc(row.expires_at) <= now:
                continue
            haystack = f"{row.title} {row.uri}".lower()
            overlap = len(terms.intersection(haystack.split()))
            score = min(1.0, overlap / max(1, len(terms))) * row.authority
            if score <= 0:
                continue
            evidence_id = sha256(f"{row.source_id}:{query}".encode()).hexdigest()[:24]
            text = f"{haystack} {row.content}".strip()
            ranked.append(Evidence(evidence_id, row.source_id, text, score, now))
        return sorted(ranked, key=lambda item: item.score, reverse=True)[:limit]


class PostgresMemoryStore:
    """Memory store implementation using SQLAlchemy."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def add(self, item: MemoryItem) -> None:
        now = datetime.now(UTC)
        memory = MemoryItemRecord(
            memory_id=item.memory_id,
            user_id=item.user_id,
            content=item.content,
            memory_class=item.memory_class.value,
            created_at=now,
            expires_at=item.expires_at,
        )
        async with self._session_factory() as session:
            session.add(memory)
            await session.commit()

    async def list_items(self, user_id: str) -> list[MemoryItem]:
        async with self._session_factory() as session:
            stmt = select(MemoryItemRecord).where(MemoryItemRecord.user_id == user_id)
            rows = (await session.scalars(stmt)).all()
            return [
                MemoryItem(
                    memory_id=row.memory_id,
                    user_id=row.user_id,
                    content=row.content,
                    memory_class=MemoryClass(row.memory_class),
                    created_at=_as_utc(row.created_at),
                    expires_at=None if row.expires_at is None else _as_utc(row.expires_at),
                )
                for row in rows
            ]

    async def prune_expired(self) -> int:
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            result = await session.execute(
                delete(MemoryItemRecord).where(MemoryItemRecord.expires_at < now)
            )
            await session.commit()
            return cast(int, result.rowcount)  # type: ignore[attr-defined]

    async def retrieve(
        self,
        user_id: str,
        memory_class: MemoryClass | None = None,
        limit: int = 10,
    ) -> list[MemoryItem]:
        async with self._session_factory() as session:
            stmt = select(MemoryItemRecord).where(MemoryItemRecord.user_id == user_id)
            stmt = stmt.order_by(MemoryItemRecord.memory_class, MemoryItemRecord.memory_id)
            rows = (await session.scalars(stmt)).all()
            now = datetime.now(UTC)
            result: list[MemoryItem] = []
            for row in rows:
                item = MemoryItem(
                    memory_id=row.memory_id,
                    user_id=row.user_id,
                    content=row.content,
                    memory_class=MemoryClass(row.memory_class),
                    created_at=_as_utc(row.created_at),
                    expires_at=None if row.expires_at is None else _as_utc(row.expires_at),
                )
                if memory_class is not None and item.memory_class != memory_class:
                    continue
                if item.expires_at is None or item.expires_at > now:
                    result.append(item)
            return result[:limit]


class PostgresAuditStore:
    """Audit store implementation using SQLAlchemy."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, event: AuditEvent) -> AuditEvent:
        now = datetime.now(UTC)
        details = sanitize_value(event.details or {})
        corr_id = details.get("correlation_id", "") if isinstance(details, dict) else ""
        record = AuditEventRecord(
            event_type=event.event_type,
            request_id=event.request_id,
            user_id=event.user_id,
            outcome=event.outcome,
            timestamp=now,
            correlation_id=str(corr_id),
            details=details,
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
        return event

    async def record(self, event: AuditEvent) -> None:
        now = datetime.now(UTC)
        details = sanitize_value(event.details or {})
        # AuditEventRecord.correlation_id is NOT NULL, so provide a default
        corr_id = details.get("correlation_id") if isinstance(details, dict) else ""
        record = AuditEventRecord(
            event_type=event.event_type,
            request_id=event.request_id,
            user_id=event.user_id,
            outcome=event.outcome,
            timestamp=now,
            details=details,
            correlation_id=corr_id,
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()

    async def events(
        self,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        async with self._session_factory() as session:
            stmt = select(AuditEventRecord)
            if user_id:
                stmt = stmt.where(AuditEventRecord.user_id == user_id)
            stmt = stmt.limit(limit)
            stmt = stmt.order_by(AuditEventRecord.id.desc())
            rows = (await session.scalars(stmt)).all()
            return [
                AuditEvent(
                    event_type=row.event_type,
                    request_id=row.request_id,
                    user_id=row.user_id,
                    outcome=row.outcome,
                    timestamp=_as_utc(row.timestamp),
                    details=row.details,
                )
                for row in rows
            ]


class PostgresMetricSink:
    """Metric sink implementation using SQLAlchemy."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        worker_id: str | None = None,
        retention_seconds: int | None = None,
        now: datetime | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._worker_id: str = worker_id or "default"
        self._retention_seconds: int | None = retention_seconds
        if now is None:
            self._now: datetime = datetime.now(UTC)
            self._clock = None
        elif isinstance(now, datetime):
            self._now = now
            self._clock = None
        else:
            self._now = now.now
            self._clock = now

    def _current_time(self) -> datetime:
        if self._clock is not None:
            return self._clock.now
        return self._now

    async def record(
        self,
        metric_name: str,
        value: float,
        worker_id: str | None = None,
        metric_type: str = "gauge",
    ) -> None:
        wid = worker_id or self._worker_id
        now = self._current_time()
        record = MetricRecord(
            worker_id=wid,
            metric_name=metric_name,
            metric_type=metric_type,
            value=value,
            updated_at=now,
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()

    async def flush(self, metrics: Metrics) -> None:
        for name, amount in metrics.counters.items():
            await self.record(name, float(amount), metric_type="counter")
        for name, seconds in metrics.durations.items():
            await self.record(name, seconds, metric_type="duration")

    async def snapshot(self) -> tuple[dict[str, int], dict[str, float]]:
        async with self._session_factory() as session:
            from sqlalchemy import func

            now = self._current_time()
            base_filter = []
            if self._retention_seconds is not None:
                cutoff = now - timedelta(seconds=self._retention_seconds)
                base_filter.append(MetricRecord.updated_at >= cutoff)

            # Aggregate counters (sum) and durations (max)
            # Get counters
            stmt = (
                select(MetricRecord.metric_name, func.sum(MetricRecord.value))
                .where(MetricRecord.metric_type == "counter")
            )
            for f in base_filter:
                stmt = stmt.where(f)
            stmt = stmt.group_by(MetricRecord.metric_name)
            result = await session.execute(stmt)
            counters: dict[str, int] = {}
            for name, value in result.all():
                counters[name] = int(value) if value is not None else 0

            # Get durations
            stmt = (
                select(MetricRecord.metric_name, func.max(MetricRecord.value))
                .where(MetricRecord.metric_type == "duration")
            )
            for f in base_filter:
                stmt = stmt.where(f)
            stmt = stmt.group_by(MetricRecord.metric_name)
            result = await session.execute(stmt)
            durations: dict[str, float] = {}
            for name, value in result.all():
                if value is not None:
                    durations[name] = float(value)

            return counters, durations