from personal_ai_secretary.infrastructure.database import get_session_factory
from personal_ai_secretary.infrastructure.stores import (
    PostgresAuditStore,
    PostgresMetricSink,
)
from personal_ai_secretary.observability.audit import AuditStore, InMemoryAuditStore
from personal_ai_secretary.observability.metrics import (
    InMemoryMetricSink,
    Metrics,
    MetricSink,
)
from personal_ai_secretary.observability.observer import Observability
from personal_ai_secretary.shared.config import get_settings

_observability: Observability | None = None


def init_observability() -> Observability:
    global _observability
    settings = get_settings()
    audit: AuditStore
    metrics = Metrics.create()
    metrics_sink: MetricSink
    if settings.persistent_stores:
        audit = PostgresAuditStore(get_session_factory())
        metrics_sink = PostgresMetricSink(
            get_session_factory(),
        )
    else:
        audit = InMemoryAuditStore()
        metrics_sink = InMemoryMetricSink(metrics)
    _observability = Observability(
        audit=audit, metrics=metrics, metrics_sink=metrics_sink
    )
    return _observability


def get_observability() -> Observability:
    if _observability is None:
        return init_observability()
    return _observability