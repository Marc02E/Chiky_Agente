"""OpenTelemetry in-process tracing for the governed workflow (FASE 12A).

Spans are always captured in-process. When ``OTEL_EXPORTER_ENDPOINT`` is set
(e.g. a local OTLP/HTTP collector), completed spans are exported over HTTP via
the OpenTelemetry OTLP exporter; otherwise they are retained in an in-memory
buffer exposed through :func:`get_recorded_spans`. The OTLP exporter is only
imported lazily when an endpoint is configured, so the no-endpoint path makes
no network calls and requires no exporter configuration.

Tracing coexists with the Phase 7/11 metrics and audit observability: the
``opentelemetry-api``/``opentelemetry-sdk`` dependencies were already declared
in Phase 7 and are now activated; ``opentelemetry-exporter-otlp-proto-http`` is
the only runtime dependency added by FASE 12A.
"""

import json
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_OFF,
    ALWAYS_ON,
    ParentBased,
    Sampler,
    TraceIdRatioBased,
)
from opentelemetry.trace import Span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from personal_ai_secretary.shared.config import get_settings

_TRACER_NAME = "personal-ai-secretary"
_TRACER_VERSION = "0.1.0"
_TRACEPARENT_PROPAGATOR = TraceContextTextMapPropagator()

# FASE 12G: consolidated span attribute names shared by the request-layer,
# workflow, provider and tool spans. All attributes are non-sensitive:
# method/route templates/status codes, correlation/trace ids, request/session/
# user ids, risk level, stage, outcome, and provider/tool names only.
ATTRIBUTE_CORRELATION_ID = "correlation_id"
ATTRIBUTE_OUTCOME = "outcome"
ATTRIBUTE_HTTP_METHOD = "http.method"
ATTRIBUTE_HTTP_ROUTE = "http.route"
ATTRIBUTE_HTTP_STATUS_CODE = "http.status_code"
ATTRIBUTE_REQUEST_ID = "request_id"
ATTRIBUTE_SESSION_ID = "session_id"
ATTRIBUTE_USER_ID = "user_id"
ATTRIBUTE_RISK_LEVEL = "risk_level"
ATTRIBUTE_PROVIDER = "provider"
ATTRIBUTE_STAGE = "stage"
ATTRIBUTE_TOOL_NAME = "name"
ATTRIBUTE_COMPLIANCE_RULE = "compliance_rule"
ATTRIBUTE_EVIDENCE_COUNT = "evidence_count"

# Consolidated resource attribute names (FASE 12E).
RESOURCE_SERVICE_NAME = SERVICE_NAME
RESOURCE_SERVICE_VERSION = "service.version"
RESOURCE_DEPLOYMENT_ENVIRONMENT = "deployment.environment"
RESOURCE_SERVICE_INSTANCE_ID = "service.instance.id"

_lock = threading.Lock()
_provider: trace.TracerProvider | None = None
_tracer: trace.Tracer | None = None
_in_memory_exporter: InMemorySpanExporter | None = None
_auto_worker_id: str | None = None


def _sampler_for_ratio(ratio: float) -> Sampler:
    """Build the parent-based sampler for the configured sampling ratio (FASE 12E).

    Ratio 1.0 (the default) keeps the current behavior of tracing every span;
    0.0 disables sampling entirely; in-between values sample parent-based using
    a deterministic trace-id ratio so child spans follow their parent's decision.
    """
    if ratio >= 1.0:
        return ALWAYS_ON
    if ratio <= 0.0:
        return ALWAYS_OFF
    return ParentBased(TraceIdRatioBased(ratio))


def _resolve_worker_id(configured: str | None) -> str:
    """Return the configured worker id or a stable per-process auto id (FASE 12E)."""
    global _auto_worker_id
    if configured:
        return configured
    if _auto_worker_id is None:
        _auto_worker_id = uuid.uuid4().hex[:12]
    return _auto_worker_id


def init_tracing(
    enabled: bool = True,
    exporter_endpoint: str | None = None,
    sampling_ratio: float | None = None,
    worker_id: str | None = None,
) -> None:
    """Install the process-wide tracer provider.

    Idempotent in the sense that any previous provider is shut down and
    replaced, so callers may re-initialize (e.g. between tests). Disabled mode
    installs a no-op provider: spans record nothing and no network is used.

    ``sampling_ratio`` and ``worker_id`` default to the configured settings
    (``otel_sampling_ratio`` / ``otel_worker_id``) when not given; resource
    attributes standardize span identity (service.version, deployment
    environment, service.instance.id).
    """
    global _provider, _tracer, _in_memory_exporter
    with _lock:
        if _provider is not None:
            shutdown: Callable[[], None] | None = getattr(_provider, "shutdown", None)
            if shutdown is not None:
                shutdown()
        _in_memory_exporter = None
        if not enabled:
            provider: trace.TracerProvider = trace.NoOpTracerProvider()
            trace.set_tracer_provider(provider)
            _provider = provider
            _tracer = trace.get_tracer(_TRACER_NAME)
            return
        settings = get_settings()
        ratio = settings.otel_sampling_ratio if sampling_ratio is None else sampling_ratio
        worker = _resolve_worker_id(settings.otel_worker_id if worker_id is None else worker_id)
        resource = Resource.create(
            {
                RESOURCE_SERVICE_NAME: "personal-ai-secretary",
                RESOURCE_SERVICE_VERSION: settings.otel_service_version,
                RESOURCE_DEPLOYMENT_ENVIRONMENT: settings.app_env,
                RESOURCE_SERVICE_INSTANCE_ID: worker,
            }
        )
        sdk_provider = TracerProvider(resource=resource, sampler=_sampler_for_ratio(ratio))
        if exporter_endpoint:
            exporter: SpanExporter = _build_otlp_exporter(exporter_endpoint)
            sdk_provider.add_span_processor(
                BatchSpanProcessor(
                    exporter,
                    schedule_delay_millis=int(settings.otel_export_batch_schedule_seconds * 1000),
                    max_queue_size=settings.otel_export_batch_max_queue_size,
                    max_export_batch_size=settings.otel_export_batch_max_export_batch_size,
                )
            )
        else:
            exporter = InMemorySpanExporter()
            sdk_provider.add_span_processor(SimpleSpanProcessor(exporter))
            _in_memory_exporter = exporter
        trace.set_tracer_provider(sdk_provider)
        _provider = sdk_provider
        _tracer = sdk_provider.get_tracer(_TRACER_NAME, _TRACER_VERSION)


def _parse_export_headers(raw: str | None) -> dict[str, str] | None:
    """Parse the ``OTEL_EXPORT_HEADERS`` JSON object (FASE 12D).

    Headers may carry credentials for external/cloud collectors; they are read
    from the environment only and are never logged or placed on spans. An
    empty/default value means no headers are sent.
    """
    if not raw:
        return None
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in parsed.items()
    ):
        raise ValueError("OTEL_EXPORT_HEADERS must be a JSON object of string values")
    return parsed


def _build_otlp_exporter(endpoint: str) -> SpanExporter:
    from opentelemetry.exporter.otlp.proto.http import Compression
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )

    settings = get_settings()
    # The OTLP HTTP exporter appends "/v1/traces" only when it derives the
    # endpoint from the default env var; a caller-provided endpoint is used
    # verbatim. Append the signal path when it is not already present so a
    # bare collector base URL (e.g. http://collector:4318) works as expected.
    if not endpoint.rstrip("/").endswith("/v1/traces"):
        endpoint = f"{endpoint.rstrip('/')}/v1/traces"
    compression = (
        Compression.Gzip
        if settings.otel_export_compression == "gzip"
        else Compression.NoCompression
    )
    return OTLPSpanExporter(
        endpoint=endpoint,
        timeout=settings.otel_export_timeout_seconds,
        headers=_parse_export_headers(settings.otel_export_headers),
        compression=compression,
    )


def shutdown_tracing() -> None:
    """Shut down the current tracer provider, flushing any pending exports."""
    global _provider, _tracer, _in_memory_exporter
    with _lock:
        provider = _provider
        _provider = None
        _tracer = None
        _in_memory_exporter = None
        if provider is not None:
            shutdown: Callable[[], None] | None = getattr(provider, "shutdown", None)
            if shutdown is not None:
                shutdown()


@contextmanager
def start_span(
    name: str,
    attributes: Mapping[str, str | bool | int | float] | None = None,
    parent_context: Context | None = None,
) -> Iterator[Span]:
    """Start a span, make it current, and end it on context exit.

    ``parent_context`` (FASE 12F) optionally provides an extracted remote trace
    context (e.g. from a W3C ``traceparent`` header) so the new span becomes a
    child of a distributed trace; ``None`` starts a root span.

    Falls back to default in-process tracing if :func:`init_tracing` has not
    been called explicitly.
    """
    tracer = _tracer
    if tracer is None:
        init_tracing()
        tracer = _tracer
    assert tracer is not None
    span = tracer.start_span(name, attributes=attributes, context=parent_context)
    with trace.use_span(span, end_on_exit=True) as active:
        yield active


def http_request_attributes(method: str, route: str | None) -> dict[str, str]:
    """Build non-sensitive attributes for the HTTP request-layer span (FASE 12B).

    Only the HTTP method and the matched route *template* (e.g.
    ``/api/v1/requests/{request_id}``) are recorded; the raw path is never used
    because it may embed user data.
    """
    attributes: dict[str, str] = {ATTRIBUTE_HTTP_METHOD: method}
    if route:
        attributes[ATTRIBUTE_HTTP_ROUTE] = route
    return attributes


def set_span_correlation(correlation_id: str | None) -> None:
    """Tag the current span with the correlation id (no-op when disabled)."""
    if correlation_id is None:
        return
    trace.get_current_span().set_attribute(ATTRIBUTE_CORRELATION_ID, correlation_id)


def get_current_trace_ids() -> dict[str, str]:
    """Return ``trace_id``/``span_id`` of the current span (FASE 12C).

    Empty when no recording span is active (disabled tracing or plain unit
    tests), so callers simply merge the result into their metadata.
    """
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return {}
    return {
        "trace_id": format(span_context.trace_id, "032x"),
        "span_id": format(span_context.span_id, "016x"),
    }


def mark_span_error(exception: BaseException) -> None:
    """Record an exception and error status on the current span (no-op when disabled)."""
    span = trace.get_current_span()
    span.record_exception(exception)
    span.set_status(trace.Status(trace.StatusCode.ERROR, str(exception)))


def get_recorded_spans() -> list[ReadableSpan]:
    """Return finished spans retained by the in-memory exporter, if any."""
    exporter = _in_memory_exporter
    if exporter is None:
        return []
    return list(exporter.get_finished_spans())


def get_service_instance_id() -> str:
    """Return the current worker id embedded in the tracer resource (FASE 12E).

    Falls back to the configured ``otel_worker_id`` or the stable per-process
    auto id. Used to attribute spans to a specific worker/replica.
    """
    settings = get_settings()
    provider = _provider
    if isinstance(provider, TracerProvider):
        resource = provider.resource.attributes
        instance_id = resource.get(RESOURCE_SERVICE_INSTANCE_ID)
        if isinstance(instance_id, str) and instance_id:
            return instance_id
    return _resolve_worker_id(settings.otel_worker_id)


def extract_traceparent(header: str | None) -> Context | None:
    """Extract a W3C ``traceparent`` header into an OpenTelemetry context (FASE 12F).

    Returns ``None`` when the header is absent or malformed so callers can
    simply fall back to starting a root span. Only a trace-context string is
    consumed; nothing sensitive is ever read or logged.
    """
    if not header:
        return None
    try:
        context = _TRACEPARENT_PROPAGATOR.extract({"traceparent": header})
    except Exception:
        return None
    if not context:
        return None
    span = trace.get_current_span(context)
    if not span.get_span_context().is_valid:
        return None
    return context


def get_traceparent_header() -> str | None:
    """Build a W3C ``traceparent`` header from the current span (FASE 12F).

    Used for outbound propagation on provider/tool HTTP calls; returns
    ``None`` when there is no valid current span, so callers send no header.
    """
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None
    carrier: dict[str, str] = {}
    context = trace.set_span_in_context(trace.get_current_span())
    _TRACEPARENT_PROPAGATOR.inject(carrier, context=context)
    return carrier.get("traceparent")


def clear_recorded_spans() -> None:
    """Clear spans retained by the in-memory exporter (no-op when not in use)."""
    exporter = _in_memory_exporter
    if exporter is not None:
        exporter.clear()