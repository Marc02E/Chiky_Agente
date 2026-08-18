import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from personal_ai_secretary.application.service import RequestService
from personal_ai_secretary.domain.contracts import (
    AuditEventResponse,
    ConversationHistory,
    ErrorEnvelope,
    EvidenceSourceCreate,
    EvidenceSourceResponse,
    MetricsSnapshot,
    RequestAccepted,
    RequestCreate,
    RequestListItem,
    RequestListResponse,
    RequestStatus,
    SendMessageResponse,
    SessionListItem,
    SessionListResponse,
    SessionStatus,
)
from personal_ai_secretary.infrastructure.database import (
    close_database,
    get_db,
    get_session_factory,
    init_database,
)
from personal_ai_secretary.infrastructure.memory import get_memory_store, init_memory_store
from personal_ai_secretary.infrastructure.observability import (
    get_observability,
    init_observability,
)
from personal_ai_secretary.infrastructure.rag import get_retriever, init_retriever
from personal_ai_secretary.infrastructure.stores import PostgresEvidenceStore
from personal_ai_secretary.observability.metrics import render_prometheus_snapshot
from personal_ai_secretary.observability.observer import Observability
from personal_ai_secretary.observability.tracing import (
    ATTRIBUTE_HTTP_ROUTE,
    ATTRIBUTE_HTTP_STATUS_CODE,
    extract_traceparent,
    http_request_attributes,
    init_tracing,
    mark_span_error,
    set_span_correlation,
    shutdown_tracing,
    start_span,
)
from personal_ai_secretary.providers.factory import AVAILABLE_PROVIDER_MODES, get_provider
from personal_ai_secretary.rag.service import EvidenceSource
from personal_ai_secretary.shared.auth import require_bearer_token
from personal_ai_secretary.shared.config import get_settings
from personal_ai_secretary.shared.telemetry import configure_logging, correlation_id_var
from personal_ai_secretary.tools.builtin import default_tool_registry

_logger = logging.getLogger("personal_ai_secretary.metrics")


async def _metrics_flush_loop(observability: Observability, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await observability.flush_metrics()
        except Exception:
            _logger.exception("metric flush failed")


async def _sweep_loop(
    retention_seconds: int,
    interval_seconds: int,
) -> None:
    """Background sweep that periodically prunes old/expired data.

    Runs during the app lifespan. Cancelable via asyncio.CancelledError.
    Does not block shutdown; each iteration has its own error handling so a
    single failure does not tumble the application.
    """
    from personal_ai_secretary.infrastructure.stores import (
        _prune_expired_evidence,
        _prune_expired_memory,
        _prune_old_audit,
    )

    if retention_seconds <= 0:
        # Sweep disabled: no retention pruning configured.
        return

    session_factory = get_session_factory()
    while True:
        try:
            async with session_factory() as session:
                await _prune_old_audit(session, retention_seconds)
                await _prune_expired_memory(session)
                await _prune_expired_evidence(session)
        except Exception:
            _logger.exception("retention sweep iteration failed")
        await asyncio.sleep(interval_seconds)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    await init_database()
    init_memory_store()
    init_observability()
    init_retriever()
    init_tracing(get_settings().otel_enabled, get_settings().otel_exporter_endpoint)
    observability = get_observability()
    async with get_session_factory()() as session:
        await RequestService(session, get_provider()).recover_stale_running()
    # Only start the retention sweep in production/development; tests use
    # in-memory SQLite and expect no background side-effects.
    sweep_task: asyncio.Task[None] | None = None
    if settings.app_env != 'test' and settings.audit_retention_seconds > 0:
        sweep_task = asyncio.create_task(
            _sweep_loop(
                retention_seconds=settings.audit_retention_seconds,
                interval_seconds=settings.cleanup_interval_seconds,
            )
        )
    flush_task = asyncio.create_task(
        _metrics_flush_loop(observability, get_settings().metrics_flush_seconds)
    )
    try:
        yield
    finally:
        if sweep_task is not None:
            sweep_task.cancel()
            with suppress(asyncio.CancelledError):
                await sweep_task
        flush_task.cancel()
        with suppress(asyncio.CancelledError):
            await flush_task
        await observability.flush_metrics()
        shutdown_tracing()
        await close_database()


settings = get_settings()
app = FastAPI(title="Personal AI Secretary API", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def correlation_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    incoming = request.headers.get("X-Correlation-ID") or correlation_id_var.get()
    if not incoming:
        incoming = str(uuid4())
    token = correlation_id_var.set(incoming)
    try:
        # FASE 12B: root the request in a request-layer span so the workflow
        # spans recorded downstream become its children. Only the route
        # *template* (e.g. /api/v1/requests/{request_id}) is recorded, never
        # the raw path, so user data embedded in URLs is not traced.
        # FASE 12F: an inbound W3C traceparent header (when valid) makes this
        # span a child of the caller's distributed trace; malformed or absent
        # headers are ignored and the span stays a root.
        parent_context = extract_traceparent(request.headers.get("traceparent"))
        with start_span(
            "http.request",
            attributes=http_request_attributes(request.method, None),
            parent_context=parent_context,
        ) as span:
            set_span_correlation(incoming)
            try:
                response = await call_next(request)
            except BaseException as exc:
                mark_span_error(exc)
                raise
            route = request.scope.get("route")
            if route is not None:
                span.set_attribute(ATTRIBUTE_HTTP_ROUTE, route.path)
            span.set_attribute(ATTRIBUTE_HTTP_STATUS_CODE, response.status_code)
            response.headers["X-Correlation-ID"] = incoming
            return response
    finally:
        correlation_id_var.reset(token)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    request_id = request.headers.get("X-Request-ID", "unknown")
    envelope = ErrorEnvelope(
        code=f"HTTP_{exc.status_code}",
        message=str(exc.detail),
        request_id=request_id,
        retryable=False,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=envelope.model_dump(mode="json"),
    )


def _user_id(claims: dict[str, Any]) -> str:
    value = claims.get("sub")
    return str(value) if value else "development-user"


def _service(db: AsyncSession) -> RequestService:
    return RequestService(
        db,
        get_provider(),
        memory=get_memory_store(),
        retriever=get_retriever(),
        tools=default_tool_registry(),
        observability=get_observability(),
    )


@app.get(f"{settings.api_v1_prefix}/health/live")
async def live() -> dict[str, str]:
    return {"status": "alive"}


@app.get(f"{settings.api_v1_prefix}/health/ready")
async def ready() -> Response:
    provider = get_provider()
    health = await provider.health()
    db_ok = True
    try:
        async with get_session_factory()() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    available = health.available and db_ok
    return JSONResponse(
        status_code=200 if available else 503,
        content={
            "status": "ready" if available else "degraded",
            "provider": health.model_dump(mode="json"),
            "database": "ok" if db_ok else "unavailable",
        },
    )


@app.get(f"{settings.api_v1_prefix}/providers")
async def providers() -> dict[str, object]:
    active = get_provider()
    return {
        "active": (await active.health()).model_dump(mode="json"),
        "available_modes": list(AVAILABLE_PROVIDER_MODES),
    }


def _evidence_store() -> PostgresEvidenceStore:
    retriever = get_retriever()
    if not isinstance(retriever, PostgresEvidenceStore):
        raise HTTPException(
            status_code=503,
            detail="Evidence persistence requires persistent stores to be enabled.",
        )
    return retriever


@app.post(
    f"{settings.api_v1_prefix}/evidence",
    response_model=EvidenceSourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_evidence(
    payload: EvidenceSourceCreate,
    claims: dict[str, Any] = Depends(require_bearer_token),
) -> EvidenceSourceResponse:
    store = _evidence_store()
    source = await store.add(
        EvidenceSource(
            source_id=payload.source_id,
            user_id=_user_id(claims),
            uri=payload.uri,
            title=payload.title,
            content=payload.content,
            authority=payload.authority,
        )
    )
    if source.created_at is None:
        raise RuntimeError("Evidence source was not persisted")
    return EvidenceSourceResponse(
        source_id=source.source_id,
        uri=source.uri,
        title=source.title,
        authority=source.authority,
        created_at=source.created_at,
        expires_at=source.expires_at,
    )


@app.get(
    f"{settings.api_v1_prefix}/evidence",
    response_model=list[EvidenceSourceResponse],
)
async def list_evidence(
    limit: int = Query(default=100, ge=1, le=1000),
    claims: dict[str, Any] = Depends(require_bearer_token),
) -> list[EvidenceSourceResponse]:
    store = _evidence_store()
    sources = await store.list_sources(_user_id(claims), limit=limit)
    return [
        EvidenceSourceResponse(
            source_id=source.source_id,
            uri=source.uri,
            title=source.title,
            authority=source.authority,
            created_at=source.created_at
            if source.created_at is not None
            else datetime.now(UTC),
            expires_at=source.expires_at,
        )
        for source in sources
    ]


@app.post(
    f"{settings.api_v1_prefix}/requests",
    response_model=RequestAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_request(
    payload: RequestCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    claims: dict[str, Any] = Depends(require_bearer_token),
    db: AsyncSession = Depends(get_db),
) -> RequestAccepted:
    service = _service(db)
    try:
        return await service.create(
            payload,
            _user_id(claims),
            correlation_id_var.get(),
            idempotency_key,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post(
    f"{settings.api_v1_prefix}/sessions/{{session_id}}/messages",
    response_model=SendMessageResponse,
)
async def send_session_message(
    session_id: UUID,
    payload: RequestCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    approval_granted: bool = Header(default=False, alias="X-Approval-Granted"),
    claims: dict[str, Any] = Depends(require_bearer_token),
    db: AsyncSession = Depends(get_db),
) -> SendMessageResponse:
    if payload.session_id is not None and payload.session_id != session_id:
        raise HTTPException(status_code=400, detail="Payload session_id does not match path")
    message_payload = payload.model_copy(update={"session_id": session_id})
    service = _service(db)
    try:
        return await service.send_message(
            message_payload,
            _user_id(claims),
            correlation_id_var.get(),
            idempotency_key,
            approval_granted,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get(
    f"{settings.api_v1_prefix}/sessions/{{session_id}}/messages",
    response_model=ConversationHistory,
)
async def get_session_messages(
    session_id: UUID,
    claims: dict[str, Any] = Depends(require_bearer_token),
    db: AsyncSession = Depends(get_db),
) -> ConversationHistory:
    result = await _service(db).history(
        session_id, _user_id(claims), correlation_id_var.get()
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return result


@app.post(
    f"{settings.api_v1_prefix}/requests/{{request_id}}/execute",
    response_model=RequestStatus,
)
async def execute_request(
    request_id: UUID,
    approval_granted: bool = Header(default=False, alias="X-Approval-Granted"),
    claims: dict[str, Any] = Depends(require_bearer_token),
    db: AsyncSession = Depends(get_db),
) -> RequestStatus:
    result = await _service(db).execute(
        request_id, _user_id(claims), approval_granted
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return result


@app.get(f"{settings.api_v1_prefix}/requests/{{request_id}}", response_model=RequestStatus)
async def get_request(
    request_id: UUID,
    claims: dict[str, Any] = Depends(require_bearer_token),
    db: AsyncSession = Depends(get_db),
) -> RequestStatus:
    result = await _service(db).get(request_id, _user_id(claims))
    if result is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return result


@app.get(f"{settings.api_v1_prefix}/sessions/{{session_id}}", response_model=SessionStatus)
async def get_session(
    session_id: UUID,
    claims: dict[str, Any] = Depends(require_bearer_token),
    db: AsyncSession = Depends(get_db),
) -> SessionStatus:
    result = await _service(db).get_session(
        session_id, _user_id(claims), correlation_id_var.get()
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return result


@app.get(f"{settings.api_v1_prefix}/sessions", response_model=SessionListResponse)
async def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    claims: dict[str, Any] = Depends(require_bearer_token),
    db: AsyncSession = Depends(get_db),
) -> SessionListResponse:
    records = await _service(db).list_sessions(_user_id(claims), limit=limit)
    return SessionListResponse(
        sessions=[
            SessionListItem(
                session_id=record.session_id,
                user_id=record.user_id,
                created_at=record.created_at,
                request_count=record.request_count or 0,
            )
            for record in records
        ]
    )


@app.get(f"{settings.api_v1_prefix}/requests", response_model=RequestListResponse)
async def list_requests(
    limit: int = Query(default=50, ge=1, le=200),
    claims: dict[str, Any] = Depends(require_bearer_token),
    db: AsyncSession = Depends(get_db),
) -> RequestListResponse:
    records = await _service(db).list_requests(_user_id(claims), limit=limit)
    return RequestListResponse(
        requests=[
            RequestListItem(
                request_id=record.request_id,
                session_id=record.session_id,
                status=record.status,
                correlation_id=record.correlation_id,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
            for record in records
        ]
    )


@app.get(
    f"{settings.api_v1_prefix}/observability/audit",
    response_model=list[AuditEventResponse],
)
async def get_audit(
    limit: int = Query(default=100, ge=1, le=1000),
    claims: dict[str, Any] = Depends(require_bearer_token),
) -> list[AuditEventResponse]:
    events = await get_observability().audit.events(_user_id(claims), limit=limit)
    return [
        AuditEventResponse(
            event_type=event.event_type,
            request_id=event.request_id,
            user_id=event.user_id,
            outcome=event.outcome,
            timestamp=event.timestamp,
            details=event.details,
        )
        for event in events
    ]


@app.get(
    f"{settings.api_v1_prefix}/observability/metrics",
    response_model=MetricsSnapshot,
)
async def get_metrics(
    claims: dict[str, Any] = Depends(require_bearer_token),
) -> MetricsSnapshot:
    counters, durations = await get_observability().metrics_snapshot()
    return MetricsSnapshot(
        counters=counters,
        durations=durations,
        duration_trace_ids=get_observability().duration_trace_ids(),
    )


@app.get(f"{settings.api_v1_prefix}/metrics", include_in_schema=True)
async def get_prometheus_metrics(
    claims: dict[str, Any] = Depends(require_bearer_token),
) -> Response:
    counters, durations = await get_observability().metrics_snapshot()
    return Response(
        content=render_prometheus_snapshot(counters, durations),
        media_type="text/plain; version=0.0.4",
    )


def run() -> None:
    import uvicorn

    uvicorn.run("personal_ai_secretary.api.app:app", host="127.0.0.1", port=8000, reload=False)
