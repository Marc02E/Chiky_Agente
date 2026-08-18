from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from personal_ai_secretary.observability.audit import AuditStore, audit_event
from personal_ai_secretary.observability.metrics import Metrics, MetricSink
from personal_ai_secretary.observability.tracing import get_current_trace_ids


@dataclass(slots=True)
class Observability:
    audit: AuditStore
    metrics: Metrics
    metrics_sink: MetricSink | None = field(default=None, repr=False)

    async def emit(
        self,
        *,
        stage: str,
        request_id: UUID,
        user_id: str,
        correlation_id: str,
        session_id: UUID | None = None,
        outcome: str = "ok",
        error: object | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        metadata: dict[str, Any] = {"correlation_id": correlation_id}
        # FASE 12C: link every audit event to the current span/trace so a
        # single request can be walked across logs, spans, metrics and audit.
        metadata.update(get_current_trace_ids())
        if session_id is not None:
            metadata["session_id"] = str(session_id)
        if error is not None:
            metadata["error"] = str(error)
        if details:
            metadata.update(details)
        await self.audit.record(
            audit_event(stage, str(request_id), user_id, outcome, **metadata)
        )

    def inc(self, name: str, amount: int = 1) -> None:
        self.metrics.inc(name, amount)

    def record_duration(self, name: str, seconds: float) -> None:
        # FASE 12C: remember which trace produced the latest observation so the
        # metrics snapshot can correlate durations back to a trace.
        trace_id = get_current_trace_ids().get("trace_id")
        self.metrics.record_duration(name, seconds, trace_id=trace_id)

    def duration_trace_ids(self) -> dict[str, str]:
        return self.metrics.duration_trace_snapshot()

    async def flush_metrics(self) -> None:
        if self.metrics_sink is not None:
            await self.metrics_sink.flush(self.metrics)

    async def metrics_snapshot(self) -> tuple[dict[str, int], dict[str, float]]:
        await self.flush_metrics()
        if self.metrics_sink is not None:
            return await self.metrics_sink.snapshot()
        return self.metrics.snapshot(), self.metrics.duration_snapshot()