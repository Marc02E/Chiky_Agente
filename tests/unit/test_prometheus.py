from uuid import uuid4

from fastapi.testclient import TestClient

from personal_ai_secretary.api.app import app
from personal_ai_secretary.observability.metrics import Metrics, render_prometheus_text


def test_render_prometheus_text_formats_counters_and_durations() -> None:
    metrics = Metrics.create()
    metrics.inc("requests_total")
    metrics.inc("requests_completed")
    metrics.record_duration("workflow", 0.5)

    text = render_prometheus_text(metrics)

    assert "# TYPE requests_total counter" in text
    assert "requests_total 1" in text
    assert "# TYPE workflow_duration_seconds gauge" in text
    assert "workflow_duration_seconds 0.5" in text


def test_render_prometheus_text_is_sorted_and_newline_terminated() -> None:
    metrics = Metrics.create()
    metrics.inc("requests_completed")
    metrics.inc("requests_total")
    metrics.record_duration("provider", 0.1)
    metrics.record_duration("workflow", 0.2)

    lines = render_prometheus_text(metrics).splitlines()
    counter_names = [
        line.split(" ")[2]
        for line in lines
        if line.startswith("# TYPE") and line.endswith("counter")
    ]
    assert counter_names == ["requests_completed", "requests_total"]
    assert render_prometheus_text(metrics).endswith("\n")


def test_metrics_endpoint_returns_prometheus_text() -> None:
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/sessions/{uuid4()}/messages",
            headers={"Idempotency-Key": "prom-1"},
            json={"input": "hello"},
        )
        assert response.status_code == 200

        response = client.get("/api/v1/metrics")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "# TYPE requests_total counter" in response.text
        assert "requests_total 1" in response.text


def test_observability_metrics_endpoint_still_returns_json_snapshot() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/observability/metrics")

        assert response.status_code == 200
        body = response.json()
        assert "counters" in body
        assert "durations" in body