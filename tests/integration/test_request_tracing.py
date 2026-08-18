from uuid import uuid4

from fastapi.testclient import TestClient

from personal_ai_secretary.api.app import app
from personal_ai_secretary.observability.tracing import (
    clear_recorded_spans,
    get_recorded_spans,
    get_traceparent_header,
    init_tracing,
    shutdown_tracing,
    start_span,
)


def _http_span() -> object:
    spans = get_recorded_spans()
    return next(span for span in spans if span.name == "http.request")


def test_http_request_span_records_method_route_and_status() -> None:
    with TestClient(app) as client:
        clear_recorded_spans()
        response = client.get("/api/v1/health/live")
        assert response.status_code == 200
        span = _http_span()

    assert span.attributes["http.method"] == "GET"
    assert span.attributes["http.route"] == "/api/v1/health/live"
    assert span.attributes["http.status_code"] == 200


def test_http_request_span_uses_route_template_not_raw_path() -> None:
    session_id = uuid4()
    with TestClient(app) as client:
        clear_recorded_spans()
        response = client.get(f"/api/v1/sessions/{session_id}")
        assert response.status_code == 404
        span = _http_span()

    assert span.attributes["http.route"] == "/api/v1/sessions/{session_id}"
    assert span.attributes["http.status_code"] == 404
    assert str(session_id) not in str(span.attributes.get("http.route"))


def test_workflow_span_is_child_of_http_request_span() -> None:
    session_id = uuid4()
    with TestClient(app) as client:
        clear_recorded_spans()
        created = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "trace-child"},
            json={"input": "hello"},
        )
        assert created.status_code == 200

        spans = get_recorded_spans()
        http_span = next(span for span in spans if span.name == "http.request")
        workflow_span = next(span for span in spans if span.name == "workflow.run")

    assert workflow_span.parent is not None
    assert workflow_span.parent.span_id == http_span.context.span_id


def test_http_request_span_echoes_correlation_id() -> None:
    with TestClient(app) as client:
        clear_recorded_spans()
        response = client.get(
            "/api/v1/health/live",
            headers={"X-Correlation-ID": "corr-http-1"},
        )
        assert response.status_code == 200
        span = _http_span()

    assert span.attributes["correlation_id"] == "corr-http-1"


def test_unhandled_exception_marks_http_span_error(monkeypatch) -> None:
    import personal_ai_secretary.api.app as app_module

    class Boom:
        async def get(self, request_id, user_id) -> object:
            raise RuntimeError("boom")

    monkeypatch.setattr(app_module, "_service", lambda db: Boom())

    with TestClient(app, raise_server_exceptions=False) as client:
        clear_recorded_spans()
        response = client.get(f"/api/v1/requests/{uuid4()}")
        assert response.status_code == 500
        span = _http_span()

    assert span.status.status_code.name == "ERROR"
    assert "boom" in span.status.description


def test_valid_traceparent_links_http_span_to_incoming_trace() -> None:
    init_tracing(enabled=True, exporter_endpoint=None)
    with start_span("external.caller") as external:
        header = get_traceparent_header()
        incoming_trace_id = external.get_span_context().trace_id
    shutdown_tracing()

    with TestClient(app) as client:
        clear_recorded_spans()
        response = client.get(
            "/api/v1/health/live", headers={"traceparent": header}
        )
        assert response.status_code == 200
        span = _http_span()

    assert span.parent is not None
    assert span.parent.trace_id == incoming_trace_id


def test_malformed_traceparent_is_ignored() -> None:
    with TestClient(app) as client:
        clear_recorded_spans()
        response = client.get(
            "/api/v1/health/live", headers={"traceparent": "garbage-header"}
        )
        assert response.status_code == 200
        span = _http_span()

    assert span.parent is None


def test_consolidated_span_attribute_names_are_consistent() -> None:
    from personal_ai_secretary.observability.tracing import (
        ATTRIBUTE_CORRELATION_ID,
        ATTRIBUTE_HTTP_METHOD,
        ATTRIBUTE_HTTP_ROUTE,
        ATTRIBUTE_HTTP_STATUS_CODE,
        ATTRIBUTE_OUTCOME,
        ATTRIBUTE_REQUEST_ID,
        ATTRIBUTE_RISK_LEVEL,
        ATTRIBUTE_SESSION_ID,
        ATTRIBUTE_USER_ID,
    )

    session_id = uuid4()
    with TestClient(app) as client:
        clear_recorded_spans()
        created = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "consolidated"},
            json={"input": "hello"},
        )
        assert created.status_code == 200

        spans = get_recorded_spans()
        http_span = next(span for span in spans if span.name == "http.request")
        workflow_span = next(span for span in spans if span.name == "workflow.run")

    assert http_span.attributes[ATTRIBUTE_HTTP_METHOD] == "POST"
    assert ATTRIBUTE_HTTP_ROUTE in http_span.attributes
    assert ATTRIBUTE_HTTP_STATUS_CODE in http_span.attributes
    assert ATTRIBUTE_CORRELATION_ID in http_span.attributes

    assert ATTRIBUTE_REQUEST_ID in workflow_span.attributes
    assert ATTRIBUTE_SESSION_ID in workflow_span.attributes
    assert ATTRIBUTE_USER_ID in workflow_span.attributes
    assert ATTRIBUTE_RISK_LEVEL in workflow_span.attributes
    assert ATTRIBUTE_OUTCOME in workflow_span.attributes