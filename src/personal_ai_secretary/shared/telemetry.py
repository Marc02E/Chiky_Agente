import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from uuid import uuid4

from fastapi import Request

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")
logger = logging.getLogger("personal_ai_secretary")

_LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s "
    "correlation_id=%(correlation_id)s %(message)s"
)


class CorrelationIdFilter(logging.Filter):
    """Attach the current correlation id to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_var.get() or "-"
        return True


class CorrelationIdFormatter(logging.Formatter):
    """Guarantee the correlation_id field on every record we format."""

    def format(self, record: logging.LogRecord) -> str:
        record.correlation_id = correlation_id_var.get() or "-"
        return super().format(record)


def configure_logging() -> None:
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(CorrelationIdFormatter(_LOG_FORMAT))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    if not any(isinstance(f, CorrelationIdFilter) for f in root.filters):
        root.addFilter(CorrelationIdFilter())


def get_correlation_id() -> str:
    value = correlation_id_var.get()
    if value:
        return value
    value = str(uuid4())
    correlation_id_var.set(value)
    return value


@asynccontextmanager
async def correlation_middleware(request: Request) -> AsyncIterator[str]:
    incoming = request.headers.get("X-Correlation-ID") or str(uuid4())
    token = correlation_id_var.set(incoming)
    try:
        yield incoming
    finally:
        correlation_id_var.reset(token)
