import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
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
from personal_ai_secretary.providers.factory import (
    AVAILABLE_PROVIDER_MODES,
    get_current_model,
    get_model_manager,
    get_provider,
    initialize_model_manager,
    set_current_model,
)
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
    # K2: Auto-select a valid Ollama model if the configured default is unavailable
    if settings.ai_provider == "local":
        _ollama_provider = get_provider()
        if hasattr(_ollama_provider, "list_models"):
            _available_models = await _ollama_provider.list_models()
            _configured = settings.ollama_model
            if _available_models and _configured not in _available_models:
                _selected = _available_models[0]
                set_current_model(_selected)
                logging.getLogger("personal_ai_secretary.startup").warning(
                    "Configured Ollama model '%s' not available. Auto-selected '%s'. "
                    "Available models: %s",
                    _configured,
                    _selected,
                    ", ".join(_available_models),
                )
            elif _available_models and _configured in _available_models:
                set_current_model(_configured)
        # Pre-warm model to eliminate cold start latency (non-blocking)
        if hasattr(_ollama_provider, "warmup"):
            asyncio.create_task(_ollama_provider.warmup())
    init_tracing(get_settings().otel_enabled, get_settings().otel_exporter_endpoint)
    # FASE T: Initialize multi-provider model manager
    await initialize_model_manager()
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

# ─── UI: Mount static files and include UI routes ───
_UI_STATIC_DIR = Path(__file__).resolve().parent.parent / "ui" / "static"
if _UI_STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_UI_STATIC_DIR)), name="static")

from personal_ai_secretary.ui.routes import router as ui_router  # noqa: E402

app.include_router(ui_router)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(str(_UI_STATIC_DIR / "index.html"))


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
    from personal_ai_secretary.providers.model_manager import ModelManager

    manager = get_model_manager()
    # If ModelManager is initialized and has providers, use its selected provider
    provider = get_provider()
    if isinstance(manager, ModelManager) and manager._initialized:
        instance = manager.get_provider_instance()
        if instance is not None:
            provider = instance
    return RequestService(
        db,
        provider,
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
    health = await active.health()
    result: dict[str, object] = {
        "active": health.model_dump(mode="json"),
        "available_modes": list(AVAILABLE_PROVIDER_MODES),
    }
    if settings.ai_provider == "local":
        model = get_current_model()
        if model:
            result["current_model"] = model
    return result


@app.get(f"{settings.api_v1_prefix}/providers/local/models")
async def list_local_models() -> dict[str, object]:
    provider = get_provider()
    if not hasattr(provider, "list_models"):
        raise HTTPException(status_code=503, detail="Local provider does not support model listing")
    models = await provider.list_models()
    current = get_current_model()
    return {
        "models": models,
        "current": current,
    }


@app.post(f"{settings.api_v1_prefix}/providers/local/model")
async def set_local_model(
    payload: dict[str, str],
    claims: dict[str, Any] = Depends(require_bearer_token),
) -> dict[str, str]:
    model = payload.get("model", "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="Model name is required")
    provider = get_provider()
    if not hasattr(provider, "list_models"):
        raise HTTPException(
            status_code=503,
            detail="Local provider does not support model selection",
        )
    available = await provider.list_models()
    if model not in available:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model}' is not available. Available: {', '.join(available)}",
        )
    set_current_model(model)
    return {"model": model, "status": "selected"}


# ── FASE T: Multi-Provider Intelligence Endpoints ──────────────────────


@app.get(f"{settings.api_v1_prefix}/providers/models")
async def list_all_providers_models() -> dict[str, object]:
    """List all discovered providers and their models with status."""
    manager = get_model_manager()
    from personal_ai_secretary.providers.model_manager import ModelManager

    assert isinstance(manager, ModelManager)
    providers = manager.registry.all_models()
    connectivity = manager.connectivity.status
    return {
        "providers": [p.to_dict() for p in providers],
        "connectivity": {
            "online": connectivity.online,
            "latency_ms": round(connectivity.latency_ms, 1),
            "check_count": connectivity.check_count,
        },
        "selected_provider": manager.selected_provider,
        "selected_model": manager.selected_model,
    }


@app.post(f"{settings.api_v1_prefix}/providers/select")
async def select_provider_model(
    payload: dict[str, str],
    claims: dict[str, Any] = Depends(require_bearer_token),
) -> dict[str, object]:
    """Select a specific provider and model."""
    provider_name = payload.get("provider", "").strip()
    model_id = payload.get("model", "").strip() or None
    if not provider_name:
        raise HTTPException(status_code=400, detail="Provider name is required")
    manager = get_model_manager()
    from personal_ai_secretary.providers.model_manager import ModelManager

    assert isinstance(manager, ModelManager)
    success = manager.select_provider(provider_name, model_id)
    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{provider_name}' is not available",
        )
    return {
        "provider": provider_name,
        "model": model_id,
        "status": "selected",
    }


@app.post(f"{settings.api_v1_prefix}/providers/verify")
async def verify_model(
    payload: dict[str, str],
    claims: dict[str, Any] = Depends(require_bearer_token),
) -> dict[str, object]:
    """Run capability verification on a specific model."""
    provider_name = payload.get("provider", "").strip()
    model_id = payload.get("model", "").strip()
    if not provider_name or not model_id:
        raise HTTPException(
            status_code=400, detail="Both provider and model are required"
        )
    manager = get_model_manager()
    from personal_ai_secretary.providers.model_manager import ModelManager

    assert isinstance(manager, ModelManager)
    report = await manager.verify_model(provider_name, model_id)
    return {
        "model_id": report.model_id,
        "provider": report.provider,
        "passed": report.overall_passed,
        "passed_count": report.passed_count,
        "total_tests": len(report.tests),
        "duration_seconds": round(report.total_duration_seconds, 2),
        "tests": [
            {
                "name": t.test_name,
                "passed": t.passed,
                "duration": round(t.duration_seconds, 2),
                "detail": t.detail[:200],
            }
            for t in report.tests
        ],
    }


@app.post(f"{settings.api_v1_prefix}/providers/verify-all")
async def verify_all_models(
    claims: dict[str, Any] = Depends(require_bearer_token),
) -> dict[str, object]:
    """Run capability verification on all discovered models."""
    manager = get_model_manager()
    from personal_ai_secretary.providers.model_manager import ModelManager

    assert isinstance(manager, ModelManager)
    reports = await manager.verify_all()
    return {
        "results": {
            key: {
                "passed": r.overall_passed,
                "passed_count": r.passed_count,
                "total_tests": len(r.tests),
                "duration_seconds": round(r.total_duration_seconds, 2),
            }
            for key, r in reports.items()
        },
    }


@app.get(f"{settings.api_v1_prefix}/providers/recommended")
async def get_recommended_model() -> dict[str, object]:
    """Get the currently recommended model based on routing logic."""
    manager = get_model_manager()
    from personal_ai_secretary.providers.model_manager import ModelManager

    assert isinstance(manager, ModelManager)
    recommended = manager.get_recommended()
    if recommended is None:
        return {"recommended": None, "reason": "No verified models available"}
    return {
        "recommended": recommended,
        "reason": "Best available verified model",
    }


@app.get(f"{settings.api_v1_prefix}/providers/connectivity")
async def check_connectivity() -> dict[str, object]:
    """Check internet connectivity status."""
    manager = get_model_manager()
    from personal_ai_secretary.providers.model_manager import ModelManager

    assert isinstance(manager, ModelManager)
    status = manager.connectivity.status
    return {
        "online": status.online,
        "latency_ms": round(status.latency_ms, 1),
        "check_count": status.check_count,
        "fail_count": status.fail_count,
    }


# ── FASE V: Provider Transparency ──────────────────────────────────────


@app.get(f"{settings.api_v1_prefix}/providers/transparency")
async def get_provider_transparency() -> dict[str, object]:
    """Consolidated provider transparency endpoint for the frontend.

    Returns a single payload with provider, model, status, connectivity,
    capabilities, and health — so the frontend doesn't need 3+ calls.
    """
    manager = get_model_manager()
    from personal_ai_secretary.providers.model_manager import ModelManager

    result: dict[str, object] = {
        "provider": None,
        "model": None,
        "status": "unknown",
        "mode": get_settings().ai_provider,
        "connectivity": {"online": True, "latency_ms": 0.0},
        "auto_selected": False,
        "fallback_active": False,
        "fallback_from": None,
        "fallback_model": None,
        "verified_capabilities": {},
        "provider_health": {},
        "all_providers": [],
    }

    if isinstance(manager, ModelManager) and manager._initialized:
        result["provider"] = manager.selected_provider
        result["model"] = manager.selected_model
        result["connectivity"] = {
            "online": manager.connectivity.is_online,
            "latency_ms": round(manager.connectivity._status.latency_ms, 1),
        }

        if manager.selected_provider and manager.selected_model:
            entry = manager.registry.get_key(
                manager.selected_model, manager.selected_provider
            )
            if entry:
                result["status"] = entry.status.value
                result["verified_capabilities"] = {
                    "basic_response": entry.verified_capabilities.basic_response,
                    "tool_calling": entry.verified_capabilities.tool_calling,
                    "coding": entry.verified_capabilities.coding,
                    "file_creation": entry.verified_capabilities.file_creation,
                    "file_modification": entry.verified_capabilities.file_modification,
                    "multi_step": entry.verified_capabilities.multi_step,
                    "security": entry.verified_capabilities.security,
                    "context_handling": entry.verified_capabilities.context_handling,
                    "verification": entry.verified_capabilities.verification,
                    "system_prompt": entry.verified_capabilities.system_prompt,
                    "argument_compatibility": entry.verified_capabilities.argument_compatibility,
                    "score": entry.verified_capabilities.score,
                }
                result["latency_ms"] = round(entry.measured_latency_ms, 1)
                result["reliability"] = round(entry.reliability, 3)
                result["success_count"] = entry.success_count
                result["failure_count"] = entry.failure_count

        # Get provider health
        instance = manager.get_provider_instance()
        if instance is not None:
            try:
                health = await instance.health()
                result["provider_health"] = health.model_dump(mode="json")
            except Exception:
                result["provider_health"] = {"available": False}

        # All discovered providers summary
        all_providers = []
        for entry in manager.registry.all_models():
            all_providers.append({
                "provider": entry.provider_name,
                "model": entry.model_id,
                "display_name": entry.display_name,
                "status": entry.status.value,
                "latency_ms": round(entry.measured_latency_ms, 1),
                "reliability": round(entry.reliability, 3),
                "success_count": entry.success_count,
                "failure_count": entry.failure_count,
                "verified_capabilities_score": entry.verified_capabilities.score,
            })
        result["all_providers"] = all_providers
    else:
        # Fallback to factory-based provider
        provider = get_provider()
        health = await provider.health()
        result["provider"] = provider.name
        result["model"] = get_current_model()
        result["provider_health"] = health.model_dump(mode="json")
        result["status"] = "available" if health.available else "unavailable"

    return result


# ── FASE U: Intelligent Multi-Provider Orchestration ─────────────────────


@app.post(f"{settings.api_v1_prefix}/providers/auto-select")
async def auto_select_model(
    payload: dict[str, Any],
) -> dict[str, object]:
    """Auto-select the best model for a given task type.

    Body:
      task_description: str - description of the task
      needs_vision: bool - whether the task needs vision
    """
    manager = get_model_manager()
    from personal_ai_secretary.providers.model_manager import ModelManager

    assert isinstance(manager, ModelManager)
    task = payload.get("task_description", "general task")
    needs_vision = payload.get("needs_vision", False)
    decision = manager.select_for_task(task, needs_vision=needs_vision)
    if decision is None:
        return {
            "selected": False,
            "reason": "No suitable model found",
            "provider": None,
            "model": None,
        }
    # Apply the selection
    manager.select_provider(decision.provider_name, decision.model_id)
    return {
        "selected": True,
        "provider": decision.provider_name,
        "model": decision.model_id,
        "reason": decision.reason,
    }


@app.get(f"{settings.api_v1_prefix}/providers/current")
async def get_current_provider_info() -> dict[str, object]:
    """Get the currently active provider and model with status."""
    manager = get_model_manager()
    from personal_ai_secretary.providers.model_manager import ModelManager

    result: dict[str, object] = {}
    if isinstance(manager, ModelManager) and manager._initialized:
        result["provider"] = manager.selected_provider
        result["model"] = manager.selected_model
        result["connectivity"] = {
            "online": manager.connectivity.is_online,
        }
        if manager.selected_provider and manager.selected_model:
            entry = manager.registry.get_key(
                manager.selected_model, manager.selected_provider
            )
            if entry:
                result["status"] = entry.to_dict()
        # Get provider health if instance available
        instance = manager.get_provider_instance()
        if instance is not None:
            try:
                health = await instance.health()
                result["health"] = health.model_dump(mode="json")
            except Exception:
                result["health"] = {"available": False, "detail": "health check failed"}
    else:
        # Fallback to factory-based provider
        provider = get_provider()
        health = await provider.health()
        result["provider"] = provider.name
        result["health"] = health.model_dump(mode="json")
        result["model"] = get_current_model()
    return result


@app.post(f"{settings.api_v1_prefix}/providers/feedback")
async def record_provider_feedback(
    payload: dict[str, Any],
) -> dict[str, str]:
    """Record success/failure feedback for a provider/model pair."""
    manager = get_model_manager()
    from personal_ai_secretary.providers.model_manager import ModelManager

    assert isinstance(manager, ModelManager)
    provider_name = payload.get("provider", "")
    model_id = payload.get("model", "")
    outcome = payload.get("outcome", "")  # "success" or "failure"
    if not provider_name or not model_id or not outcome:
        raise HTTPException(
            status_code=400,
            detail="provider, model, and outcome are required",
        )
    if outcome == "success":
        manager.record_success(provider_name, model_id)
    elif outcome == "failure":
        manager.record_failure(provider_name, model_id)
    else:
        raise HTTPException(
            status_code=400,
            detail="outcome must be 'success' or 'failure'",
        )
    return {"status": "recorded"}


@app.get(f"{settings.api_v1_prefix}/providers/config")
async def get_provider_config() -> dict[str, object]:
    """Get provider configuration (keys masked)."""
    s = get_settings()
    return {
        "ollama_base_url": s.ollama_base_url,
        "ollama_model": s.ollama_model,
        "gemini_model": s.gemini_model,
        "gemini_base_url": s.gemini_base_url,
        "gemini_configured": bool(s.gemini_api_key),
        "nvidia_configured": bool(s.nvidia_api_key),
        "opencode_base_url": s.opencode_base_url,
        "opencode_model": s.opencode_model,
        "ai_provider": s.ai_provider,
    }


@app.post(f"{settings.api_v1_prefix}/providers/config")
async def update_provider_config(
    payload: dict[str, Any],
    claims: dict[str, Any] = Depends(require_bearer_token),
) -> dict[str, str]:
    """Update provider configuration (API keys, models).

    API keys are stored in the in-memory settings and environment.
    They are NEVER logged or returned in responses.
    """
    from personal_ai_secretary.shared.config import get_settings

    s = get_settings()
    updated: list[str] = []
    # Gemini API key
    gemini_key = payload.get("gemini_api_key")
    if gemini_key is not None:
        if not isinstance(gemini_key, str) or len(gemini_key) < 10:
            raise HTTPException(
                status_code=400,
                detail="Invalid Gemini API key",
            )
        s.gemini_api_key = gemini_key
        updated.append("gemini_api_key")
    # NVIDIA API key
    nvidia_key = payload.get("nvidia_api_key")
    if nvidia_key is not None:
        if not isinstance(nvidia_key, str) or len(nvidia_key) < 10:
            raise HTTPException(
                status_code=400,
                detail="Invalid NVIDIA API key",
            )
        s.nvidia_api_key = nvidia_key
        updated.append("nvidia_api_key")
    # Gemini model
    gemini_model = payload.get("gemini_model")
    if gemini_model is not None:
        s.gemini_model = str(gemini_model)
        updated.append("gemini_model")
    # Ollama model
    ollama_model = payload.get("ollama_model")
    if ollama_model is not None:
        s.ollama_model = str(ollama_model)
        updated.append("ollama_model")
    # AI provider mode
    ai_provider = payload.get("ai_provider")
    if ai_provider is not None:
        if ai_provider not in ("deterministic", "local", "remote"):
            raise HTTPException(
                status_code=400,
                detail="ai_provider must be deterministic, local, or remote",
            )
        s.ai_provider = ai_provider
        updated.append("ai_provider")
    if not updated:
        raise HTTPException(
            status_code=400,
            detail="No valid configuration fields provided",
        )
    return {"status": "updated", "fields": ", ".join(updated)}


def _evidence_store() -> PostgresEvidenceStore:
    """Return the evidence store, raising 503 if unavailable."""
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
    # K8/K5: attached files are formatted within the context budget; the user
    # message itself is never truncated.
    if payload.attached_files:
        from personal_ai_secretary.context.files import (  # noqa: PLC0415
            build_message_with_attachments,
        )

        _augmented = build_message_with_attachments(payload.input, payload.attached_files)
        message_payload = payload.model_copy(
            update={"session_id": session_id, "input": _augmented, "attached_files": []}
        )
    else:
        message_payload = payload.model_copy(update={"session_id": session_id})
    service = _service(db)
    try:
        result = await service.send_message(
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

    # Parse and strip __APPROVAL_REQUIRED__ prefix from the assistant message.
    # When an agent-level approval request is detected, surface it as a
    # structured field so the UI can show a confirmation dialog.
    if result.assistant_message is not None:
        content = result.assistant_message.content
        from personal_ai_secretary.agents.builtin import APPROVAL_REQUIRED_PREFIX  # noqa: PLC0415

        if content.startswith(APPROVAL_REQUIRED_PREFIX):
            import json as _json  # noqa: PLC0415

            first_newline = content.find("\n")
            end = first_newline if first_newline != -1 else len(content)
            json_part = content[len(APPROVAL_REQUIRED_PREFIX):end]
            human_part = content[first_newline + 1:].lstrip("\n") if first_newline != -1 else ""
            approval_request: dict[str, Any] | None = None
            try:
                approval_request = _json.loads(json_part)
            except Exception:  # noqa: BLE001
                pass
            # Replace the raw content with only the human-readable message
            result = result.model_copy(
                update={
                    "assistant_message": result.assistant_message.model_copy(
                        update={"content": human_part}
                    ),
                    "approval_request": approval_request,
                }
            )

    return result


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
