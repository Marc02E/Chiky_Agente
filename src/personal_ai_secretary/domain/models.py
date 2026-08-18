from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RequestRecord(Base):
    __tablename__ = "requests"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "idempotency_key", name="uq_requests_user_id_idempotency_key"
        ),
    )

    request_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(index=True)
    user_id: Mapped[str] = mapped_column(String(200), index=True)
    input: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="accepted")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(128), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class SessionRecord(Base):
    __tablename__ = "sessions"

    session_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[str] = mapped_column(String(200), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    request_count: Mapped[int] = mapped_column(Integer, default=0)


class MemoryItemRecord(Base):
    __tablename__ = "memory_items"

    memory_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(200), index=True)
    content: Mapped[str] = mapped_column(Text)
    memory_class: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AuditEventRecord(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(200), index=True)
    outcome: Mapped[str] = mapped_column(String(32))
    correlation_id: Mapped[str] = mapped_column(String(128), index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, default=lambda: datetime.now(UTC)
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class MetricRecord(Base):
    __tablename__ = "metric_records"
    __table_args__ = (
        UniqueConstraint("worker_id", "metric_name", name="uq_metric_records_worker_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_id: Mapped[str] = mapped_column(String(36), index=True)
    metric_name: Mapped[str] = mapped_column(String(128))
    metric_type: Mapped[str] = mapped_column(String(16))
    value: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, default=lambda: datetime.now(UTC)
    )


class EvidenceSourceRecord(Base):
    """User-scoped source of evidence for persistent RAG retrieval (FASE 13B)."""

    __tablename__ = "evidence_sources"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "source_id", name="pk_evidence_sources"),
    )

    user_id: Mapped[str] = mapped_column(String(200))
    source_id: Mapped[str] = mapped_column(String(64))
    uri: Mapped[str] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(String(256))
    content: Mapped[str] = mapped_column(Text)
    authority: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )