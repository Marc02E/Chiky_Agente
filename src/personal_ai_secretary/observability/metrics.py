import threading
from collections import Counter
from dataclasses import dataclass, field
from time import perf_counter
from typing import Protocol


@dataclass(slots=True)
class Metrics:
    counters: Counter[str]
    durations: dict[str, float] = field(default_factory=dict)
    duration_trace_ids: dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @classmethod
    def create(cls) -> "Metrics":
        return cls(Counter())

    def inc(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self.counters[name] += amount

    def record_duration(self, name: str, seconds: float, *, trace_id: str | None = None) -> None:
        with self._lock:
            self.durations[name] = seconds
            if trace_id is None:
                self.duration_trace_ids.pop(name, None)
            else:
                self.duration_trace_ids[name] = trace_id

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self.counters)

    def duration_snapshot(self) -> dict[str, float]:
        with self._lock:
            return dict(self.durations)

    def duration_trace_snapshot(self) -> dict[str, str]:
        with self._lock:
            return dict(self.duration_trace_ids)


def render_prometheus_text(metrics: Metrics) -> str:
    return render_prometheus_snapshot(
        metrics.snapshot(), metrics.duration_snapshot()
    )


def render_prometheus_snapshot(
    counters: dict[str, int], durations: dict[str, float]
) -> str:
    lines: list[str] = []
    for name, count in sorted(counters.items()):
        lines.append(f"# TYPE {name} counter")
        lines.append(f"{name} {count}")
    for name, seconds in sorted(durations.items()):
        metric_name = f"{name}_duration_seconds"
        lines.append(f"# TYPE {metric_name} gauge")
        lines.append(f"{metric_name} {seconds}")
    return "\n".join(lines) + "\n"


class MetricSink(Protocol):
    async def flush(self, metrics: Metrics) -> None: ...

    async def snapshot(self) -> tuple[dict[str, int], dict[str, float]]: ...


class InMemoryMetricSink:
    """Aggregates within the current process only.

    Used when persistent stores are disabled (development/tests): each
    worker observes exactly its own counters and durations.
    """

    def __init__(self, metrics: Metrics) -> None:
        self._metrics = metrics

    async def flush(self, metrics: Metrics) -> None:
        return None

    async def snapshot(self) -> tuple[dict[str, int], dict[str, float]]:
        return self._metrics.snapshot(), self._metrics.duration_snapshot()


class Timer:
    def __enter__(self) -> "Timer":
        self.started = perf_counter()
        return self

    def __exit__(self, *_: object) -> None:
        self.elapsed_seconds = perf_counter() - self.started