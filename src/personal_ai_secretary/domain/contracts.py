from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AttachedFile(BaseModel):
    """A file attached to a message by the user."""

    name: str = Field(min_length=1, max_length=255)
    content: str = Field(max_length=5_000)
    size: int = Field(ge=0)
    mime_type: str = Field(default="text/plain", max_length=100)


class RequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: str = Field(min_length=1, max_length=20_000)
    session_id: UUID | None = None
    attached_files: list["AttachedFile"] = Field(default_factory=list)


class RequestAccepted(BaseModel):
    request_id: UUID
    status: str = "accepted"
    correlation_id: str


class RequestStatus(BaseModel):
    request_id: UUID
    status: str
    result: str | None = None
    correlation_id: str


class SessionStatus(BaseModel):
    session_id: UUID
    request_count: int
    correlation_id: str


class ProviderInfo(BaseModel):
    name: str
    mode: str
    available: bool
    is_ai: bool
    detail: str


class ProviderResponse(BaseModel):
    text: str
    provider: str
    model: str | None = None


class ErrorEnvelope(BaseModel):
    code: str
    message: str
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool


class ConversationMessage(BaseModel):
    message_id: UUID
    request_id: UUID
    role: str
    content: str
    status: str
    created_at: datetime


class SendMessageResponse(BaseModel):
    session_id: UUID
    request_id: UUID
    status: str
    user_message: ConversationMessage
    assistant_message: ConversationMessage | None = None
    correlation_id: str
    # Populated when the agent requires explicit user approval before executing a tool.
    # The UI should show a confirmation dialog and resend the original message with
    # the X-Approval-Granted: true header if the user accepts.
    approval_request: dict[str, Any] | None = None
    # FASE V: Provider fallback information surfaced to the frontend.
    fallback_info: dict[str, Any] | None = None


class ConversationHistory(BaseModel):
    session_id: UUID
    messages: list[ConversationMessage]
    correlation_id: str


class AuditEventResponse(BaseModel):
    event_type: str
    request_id: str
    user_id: str
    outcome: str
    timestamp: datetime
    details: dict[str, Any] = Field(default_factory=dict)


class MetricsSnapshot(BaseModel):
    counters: dict[str, int]
    durations: dict[str, float] = Field(default_factory=dict)
    duration_trace_ids: dict[str, str] = Field(default_factory=dict)


class ConversationTurn(BaseModel):
    """A single turn in conversation history sent to the provider."""

    role: str = Field(min_length=1, max_length=16)
    content: str = Field(min_length=1, max_length=20_000)


class RequestEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID = Field(default_factory=uuid4)
    session_id: UUID = Field(default_factory=uuid4)
    user_id: str = Field(min_length=1, max_length=200)
    input: str = Field(min_length=1, max_length=20_000)
    risk_level: RiskLevel = RiskLevel.LOW
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: str = Field(min_length=1, max_length=128)
    messages: list[ConversationTurn] = Field(default_factory=list)
    context_summary: str | None = None


class EvidenceSourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=64)
    uri: str = Field(min_length=1, max_length=512)
    title: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1, max_length=100_000)
    authority: float = Field(default=0.5, ge=0.0, le=1.0)


class EvidenceSourceResponse(BaseModel):
    source_id: str
    uri: str
    title: str
    authority: float
    created_at: datetime
    expires_at: datetime | None = None


class SessionListItem(BaseModel):
    session_id: UUID
    user_id: str
    created_at: datetime
    updated_at: datetime | None = None
    request_count: int
    title: str | None = None


class SessionListResponse(BaseModel):
    sessions: list[SessionListItem]


class SessionRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)


class RequestListItem(BaseModel):
    request_id: UUID
    session_id: UUID
    status: str
    correlation_id: str
    created_at: datetime
    updated_at: datetime


class RequestListResponse(BaseModel):
    requests: list[RequestListItem]
