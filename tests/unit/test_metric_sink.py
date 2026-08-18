from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from personal_ai_secretary.domain.models import Base
from personal_ai_secretary.infrastructure.stores import PostgresMetricSink
from personal_ai_secretary.observability.audit import InMemoryAuditStore
from personal_ai_secretary.observability.metrics import (
    InMemoryMetricSink,
    Metrics,
)
from personal_ai_secretary.observability.observer import Observability


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now = self.now + timedelta(seconds=seconds)


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker]]:
    db_path = tmp_path / "metrics.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, factory
    await engine.dispose()


async def test_sink_aggregates_counters_across_workers(db) -> None:
    _, factory = db
    worker_a = PostgresMetricSink(factory, worker_id="worker-a")
    worker_b = PostgresMetricSink(factory, worker_id="worker-b")

    metrics_a = Metrics.create()
    metrics_a.inc("requests_total", amount=3)
    await worker_a.flush(metrics_a)

    metrics_b = Metrics.create()
    metrics_b.inc("requests_total", amount=2)
    await worker_b.flush(metrics_b)

    counters, durations = await worker_a.snapshot()
    assert counters == {"requests_total": 5}
    assert durations == {}


async def test_sink_duration_gauge_keeps_latest_observation(db) -> None:
    _, factory = db
    clock = Clock(datetime(2026, 1, 1, tzinfo=UTC))
    worker_a = PostgresMetricSink(factory, worker_id="worker-a", now=clock)
    worker_b = PostgresMetricSink(factory, worker_id="worker-b", now=clock)

    metrics_a = Metrics.create()
    metrics_a.record_duration("workflow", 0.5)
    await worker_a.flush(metrics_a)

    clock.advance(1)
    metrics_b = Metrics.create()
    metrics_b.record_duration("workflow", 0.7)
    await worker_b.flush(metrics_b)

    _, durations = await worker_a.snapshot()
    assert durations == {"workflow": 0.7}


async def test_sink_prunes_stale_rows_outside_retention(db) -> None:
    _, factory = db
    clock = Clock(datetime(2026, 1, 1, tzinfo=UTC))
    worker_a = PostgresMetricSink(
        factory, worker_id="worker-a", retention_seconds=60, now=clock
    )
    worker_b = PostgresMetricSink(
        factory, worker_id="worker-b", retention_seconds=60, now=clock
    )

    metrics_a = Metrics.create()
    metrics_a.inc("requests_total", amount=4)
    await worker_a.flush(metrics_a)

    clock.advance(61)
    metrics_b = Metrics.create()
    metrics_b.inc("requests_total", amount=1)
    await worker_b.flush(metrics_b)

    counters, _ = await worker_b.snapshot()
    assert counters == {"requests_total": 1}


async def test_sink_snapshot_survives_reconnect(db, tmp_path: Path) -> None:
    _, factory = db
    worker_a = PostgresMetricSink(factory, worker_id="worker-a")
    metrics = Metrics.create()
    metrics.inc("requests_total", amount=3)
    await worker_a.flush(metrics)

    engine2 = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'metrics.db'}")
    factory2 = async_sessionmaker(engine2, expire_on_commit=False)
    try:
        reader = PostgresMetricSink(factory2, worker_id="worker-b")
        counters, _ = await reader.snapshot()
        assert counters == {"requests_total": 3}
    finally:
        await engine2.dispose()


async def test_in_memory_sink_returns_single_process_metrics() -> None:
    metrics = Metrics.create()
    metrics.inc("requests_total", amount=2)
    metrics.record_duration("workflow", 0.4)
    sink = InMemoryMetricSink(metrics)

    await sink.flush(metrics)
    counters, durations = await sink.snapshot()

    assert counters == {"requests_total": 2}
    assert durations == {"workflow": 0.4}


async def test_observability_metrics_snapshot_flushes_then_reads_sink() -> None:
    class FakeSink:
        def __init__(self) -> None:
            self.flushes = 0

        async def flush(self, metrics: Metrics) -> None:
            self.flushes += 1

        async def snapshot(self) -> tuple[dict[str, int], dict[str, float]]:
            return {"requests_total": self.flushes}, {}

    sink = FakeSink()
    observability = Observability(InMemoryAuditStore(), Metrics.create(), metrics_sink=sink)

    counters, _ = await observability.metrics_snapshot()
    assert sink.flushes == 1
    assert counters == {"requests_total": 1}


def test_observability_metrics_snapshot_without_sink_uses_process_metrics() -> None:
    metrics = Metrics.create()
    metrics.inc("requests_total", amount=1)
    observability = Observability(InMemoryAuditStore(), metrics)

    counters = observability.metrics.snapshot()
    assert counters == {"requests_total": 1}