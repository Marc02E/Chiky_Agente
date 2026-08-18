import re
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

REDACTED = "[REDACTED]"

_SENSITIVE_PARTS: tuple[str, ...] = (
    "token",
    "authorization",
    "auth",
    "secret",
    "password",
    "passwd",
    "credential",
    "apikey",
    "api_key",
    "api-key",
    "bearer",
    "jwt",
    "private_key",
    "access_token",
    "refresh_token",
    "client_secret",
    "session_token",
)

_URL_CREDENTIALS_RE = re.compile(r"(://)([^/@\s]+):([^/@\s]+)@")
_STANDALONE_JWT_RE = re.compile(
    r"^eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}$"
)
_BEARER_JWT_RE = re.compile(
    r"^Bearer\s+(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,})$"
)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("_", "").replace("-", "").replace(" ", "")
    return any(part in normalized for part in _SENSITIVE_PARTS)


def _redact_secret_value(value: str) -> str:
    """Redact values that embed credentials even under non-sensitive keys.

    Conservative value-level detection: URLs with embedded credentials
    (``scheme://user:password@host``) and standalone JWTs / Bearer tokens.
    Ordinary text, short values and non-secret strings are left untouched.
    """
    redacted = _URL_CREDENTIALS_RE.sub(r"\1[REDACTED]@", value)
    if redacted != value:
        return redacted
    stripped = value.strip()
    if _STANDALONE_JWT_RE.match(stripped) or _BEARER_JWT_RE.match(stripped):
        return REDACTED
    return value


def sanitize_value(value: Any) -> Any:
    """Recursively redact secrets regardless of where they appear.

    Guard rails so bearer tokens, JWTs, API keys, passwords and other
    credentials never reach the audit trail, whether under sensitive key
    names or embedded in arbitrary string values.
    """
    if isinstance(value, dict):
        return {
            str(key): REDACTED if _is_sensitive_key(str(key)) else sanitize_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_value(item) for item in value)
    if isinstance(value, str):
        return _redact_secret_value(value)
    return value


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_type: str
    request_id: str
    user_id: str
    outcome: str
    timestamp: datetime
    details: dict[str, Any]


def audit_event(
    event_type: str, request_id: str, user_id: str, outcome: str, **details: Any
) -> AuditEvent:
    return AuditEvent(event_type, request_id, user_id, outcome, datetime.now(UTC), details)


class AuditStore(Protocol):
    async def record(self, event: AuditEvent) -> None: ...

    async def events(
        self, user_id: str | None = None, limit: int = 100
    ) -> list[AuditEvent]: ...


class InMemoryAuditStore:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = threading.Lock()

    async def record(self, event: AuditEvent) -> None:
        sanitized = replace(event, details=sanitize_value(event.details))
        with self._lock:
            self._events.append(sanitized)

    async def events(
        self, user_id: str | None = None, limit: int = 100
    ) -> list[AuditEvent]:
        with self._lock:
            scoped = [
                event
                for event in self._events
                if user_id is None or event.user_id == user_id
            ]
            return scoped[-limit:]