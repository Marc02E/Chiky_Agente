from uuid import uuid4

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SpanExportResult

from personal_ai_secretary.observability.tracing import (
    ATTRIBUTE_HTTP_METHOD,
    ATTRIBUTE_HTTP_ROUTE,
    _build_otlp_exporter,
    _parse_export_headers,
    _sampler_for_ratio,
    clear_recorded_spans,
    extract_traceparent,
    get_recorded_spans,
    get_service_instance_id,
    get_traceparent_header,
    http_request_attributes,
    init_tracing,
    mark_span_error,
    set_span_correlation,
    shutdown_tracing,
    start_span,
)
from personal_ai_secretary.shared.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _reset_tracing() -> None:
    shutdown_tracing()
    yield
    shutdown_tracing()


def _span_names() -> list[str]:
    return [span.name for span in get_recorded_spans()]


def test_in_memory_exporter_captures_finished_spans() -> None:
    init_tracing(enabled=True, exporter_endpoint=None)
    with start_span("workflow.run"):
        with start_span("planner"):
            pass

    assert _span_names() == ["planner", "workflow.run"]


def test_root_span_records_attributes_and_outcome() -> None:
    init_tracing(enabled=True, exporter_endpoint=None)
    with start_span(
        "workflow.run",
        attributes={
            "request_id": str(uuid4()),
            "session_id": str(uuid4()),
            "user_id": "user-1",
            "risk_level": "LOW",
        },
    ) as span:
        set_span_correlation("corr-1")
        span.set_attribute("outcome", "completed")

    root = get_recorded_spans()[0]
    assert root.name == "workflow.run"
    assert root.attributes["correlation_id"] == "corr-1"
    assert root.attributes["outcome"] == "completed"
    assert root.attributes["user_id"] == "user-1"


def test_set_span_correlation_tags_current_span() -> None:
    init_tracing(enabled=True, exporter_endpoint=None)
    with start_span("workflow.run"):
        set_span_correlation("corr-x")

    assert get_recorded_spans()[0].attributes["correlation_id"] == "corr-x"


def test_set_span_correlation_ignores_none() -> None:
    init_tracing(enabled=True, exporter_endpoint=None)
    with start_span("workflow.run"):
        set_span_correlation(None)

    assert "correlation_id" not in get_recorded_spans()[0].attributes


def test_mark_span_error_records_error_status() -> None:
    init_tracing(enabled=True, exporter_endpoint=None)
    with pytest.raises(RuntimeError):
        with start_span("workflow.run"):
            try:
                raise RuntimeError("boom")
            except RuntimeError as exc:
                mark_span_error(exc)
                raise

    span = get_recorded_spans()[0]
    assert span.status.status_code.name == "ERROR"
    assert "boom" in span.status.description


def test_clear_recorded_spans_empties_buffer() -> None:
    init_tracing(enabled=True, exporter_endpoint=None)
    with start_span("workflow.run"):
        pass
    assert len(get_recorded_spans()) == 1

    clear_recorded_spans()
    assert get_recorded_spans() == []


def test_shutdown_clears_exporter() -> None:
    init_tracing(enabled=True, exporter_endpoint=None)
    with start_span("workflow.run"):
        pass
    assert len(get_recorded_spans()) == 1

    shutdown_tracing()
    assert get_recorded_spans() == []


def test_shutdown_is_idempotent() -> None:
    init_tracing(enabled=True, exporter_endpoint=None)
    shutdown_tracing()
    shutdown_tracing()


def test_disabled_tracing_is_noop() -> None:
    init_tracing(enabled=False)
    with start_span("workflow.run"):
        set_span_correlation("corr-1")
        mark_span_error(RuntimeError("boom"))

    assert get_recorded_spans() == []


def test_start_span_auto_initializes_in_memory() -> None:
    with start_span("workflow.run"):
        pass

    assert _span_names() == ["workflow.run"]


def test_settings_otel_defaults_in_memory() -> None:
    settings = Settings.model_validate({"app_env": "test"})
    assert settings.otel_enabled is True
    assert settings.otel_exporter_endpoint is None


def test_otlp_exporter_used_when_endpoint_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    imported: list[str] = []

    def fake_exporter_factory(endpoint: str) -> object:
        imported.append(endpoint)
        return _FakeExporter()

    monkeypatch.setattr(
        "personal_ai_secretary.observability.tracing._build_otlp_exporter",
        fake_exporter_factory,
    )

    init_tracing(enabled=True, exporter_endpoint="http://collector:4318")
    assert imported == ["http://collector:4318"]
    assert get_recorded_spans() == []


def test_no_otlp_import_without_endpoint() -> None:
    init_tracing(enabled=True, exporter_endpoint=None)
    from personal_ai_secretary.observability import tracing

    assert tracing._in_memory_exporter is not None


def test_real_otlp_exporter_is_lazily_imported_and_constructed() -> None:
    import opentelemetry.exporter.otlp.proto.http.trace_exporter as otlp_module

    exporter = _build_otlp_exporter("http://collector:4318")
    assert isinstance(exporter, otlp_module.OTLPSpanExporter)


def test_settings_otel_export_defaults() -> None:
    settings = Settings.model_validate({"app_env": "test"})
    assert settings.otel_export_timeout_seconds == 10.0
    assert settings.otel_export_headers is None
    assert settings.otel_export_compression == "gzip"
    assert settings.otel_export_batch_schedule_seconds == 5.0
    assert settings.otel_export_batch_max_queue_size == 2048
    assert settings.otel_export_batch_max_export_batch_size == 512


def test_settings_otel_governance_defaults() -> None:
    settings = Settings.model_validate({"app_env": "test"})
    assert settings.otel_sampling_ratio == 1.0
    assert settings.otel_service_version == "1.0.0"
    assert settings.otel_worker_id is None


def test_sampling_ratio_zero_emits_nothing_in_memory() -> None:
    init_tracing(enabled=True, exporter_endpoint=None, sampling_ratio=0.0)
    with start_span("workflow.run"):
        with start_span("planner"):
            pass
    assert get_recorded_spans() == []


def test_sampling_ratio_one_emits_all_in_memory() -> None:
    init_tracing(enabled=True, exporter_endpoint=None, sampling_ratio=1.0)
    with start_span("workflow.run"):
        with start_span("planner"):
            pass
    assert _span_names() == ["planner", "workflow.run"]


def test_default_sampling_ratio_is_one() -> None:
    init_tracing(enabled=True, exporter_endpoint=None)
    with start_span("workflow.run"):
        pass
    assert _span_names() == ["workflow.run"]


def test_sampling_ratio_zero_still_records_noop_mode_unaffected() -> None:
    init_tracing(enabled=False, sampling_ratio=0.0)
    with start_span("workflow.run"):
        pass
    assert get_recorded_spans() == []


def test_resource_carries_service_and_environment_attributes() -> None:
    init_tracing(enabled=True, exporter_endpoint=None)
    with start_span("workflow.run"):
        pass
    settings = get_settings()
    resource = get_recorded_spans()[0].resource.attributes
    assert resource["service.name"] == "personal-ai-secretary"
    assert resource["service.version"] == "1.0.0"
    assert resource["deployment.environment"] == settings.app_env
    assert "service.instance.id" in resource


def test_worker_id_is_configurable_and_attributed() -> None:
    init_tracing(enabled=True, exporter_endpoint=None, worker_id="worker-7")
    with start_span("workflow.run"):
        pass
    resource = get_recorded_spans()[0].resource.attributes
    assert resource["service.instance.id"] == "worker-7"
    assert get_service_instance_id() == "worker-7"


def test_auto_worker_id_is_stable_per_process() -> None:
    init_tracing(enabled=True, exporter_endpoint=None)
    first = get_service_instance_id()
    with start_span("workflow.run"):
        pass
    init_tracing(enabled=True, exporter_endpoint=None)
    second = get_service_instance_id()
    assert first and second
    assert first == second


def test_sampler_for_ratio_endpoints() -> None:
    from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON, ParentBased

    assert _sampler_for_ratio(1.0) is ALWAYS_ON
    assert _sampler_for_ratio(0.0) is ALWAYS_OFF
    assert isinstance(_sampler_for_ratio(0.5), ParentBased)


def test_get_traceparent_header_outside_span_is_none() -> None:
    init_tracing(enabled=True, exporter_endpoint=None)
    assert get_traceparent_header() is None


def test_get_traceparent_header_inside_span_is_w3c() -> None:
    init_tracing(enabled=True, exporter_endpoint=None)
    with start_span("workflow.run") as span:
        header = get_traceparent_header()
        context = span.get_span_context()
    assert header is not None
    version, trace_id, span_id, flags = header.split("-")
    assert version == "00"
    assert trace_id == format(context.trace_id, "032x")
    assert span_id == format(context.span_id, "016x")
    assert len(flags) == 2
    assert int(flags, 16) & 0x01 == 0x01


def test_extract_traceparent_absent_or_empty_is_none() -> None:
    assert extract_traceparent(None) is None
    assert extract_traceparent("") is None


def test_extract_traceparent_malformed_is_none() -> None:
    assert extract_traceparent("not-a-traceparent") is None
    assert extract_traceparent("00-123-456-00") is None
    assert extract_traceparent("00" * 100) is None


def test_extract_traceparent_round_trips_valid_header() -> None:
    init_tracing(enabled=True, exporter_endpoint=None)
    with start_span("workflow.run"):
        header = get_traceparent_header()
    assert header is not None
    context = extract_traceparent(header)
    assert context is not None
    span_context = trace.get_current_span(context).get_span_context()
    assert span_context.is_valid


def test_start_span_uses_extracted_parent_context() -> None:
    init_tracing(enabled=True, exporter_endpoint=None)
    with start_span("workflow.run") as parent:
        header = get_traceparent_header()
        parent_trace_id = parent.get_span_context().trace_id
    assert header is not None
    context = extract_traceparent(header)
    with start_span("child", parent_context=context):
        pass

    children = [s for s in get_recorded_spans() if s.name == "child"]
    assert len(children) == 1
    assert children[0].parent is not None
    assert children[0].parent.trace_id == parent_trace_id


def test_http_request_attributes_with_route() -> None:
    attributes = http_request_attributes("POST", "/api/v1/requests/{request_id}")
    assert attributes == {
        ATTRIBUTE_HTTP_METHOD: "POST",
        ATTRIBUTE_HTTP_ROUTE: "/api/v1/requests/{request_id}",
    }
    assert http_request_attributes("GET", None) == {ATTRIBUTE_HTTP_METHOD: "GET"}


def test_extract_traceparent_propagator_exception_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from personal_ai_secretary.observability import tracing

    def boom(carrier: object, context: object = None, getter: object = None) -> object:
        raise RuntimeError("propagator exploded")

    monkeypatch.setattr(tracing._TRACEPARENT_PROPAGATOR, "extract", boom)
    assert extract_traceparent("00-11111111111111111111111111111111-2222222222222222-01") is None


def test_get_service_instance_id_falls_back_when_provider_is_noop() -> None:
    init_tracing(enabled=False)
    instance_id = get_service_instance_id()
    assert isinstance(instance_id, str)
    assert instance_id


def test_parse_export_headers_defaults_to_none() -> None:
    assert _parse_export_headers(None) is None
    assert _parse_export_headers("") is None


def test_parse_export_headers_parses_json_object() -> None:
    assert _parse_export_headers('{"Authorization": "Bearer x"}') == {
        "Authorization": "Bearer x"
    }


def test_parse_export_headers_rejects_non_string_values() -> None:
    with pytest.raises(ValueError):
        _parse_export_headers('{"key": 1}')


def test_parse_export_headers_rejects_invalid_json() -> None:
    with pytest.raises(ValueError):
        _parse_export_headers("not-json")


def test_build_otlp_exporter_uses_export_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class RecordingExporter:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def export(self, spans: object) -> SpanExportResult:
            return SpanExportResult.SUCCESS

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return True

        def shutdown(self) -> None:
            return None

    import opentelemetry.exporter.otlp.proto.http.trace_exporter as otlp_module
    from opentelemetry.exporter.otlp.proto.http import Compression

    monkeypatch.setattr(otlp_module, "OTLPSpanExporter", RecordingExporter)
    monkeypatch.setattr(
        "personal_ai_secretary.observability.tracing.get_settings",
        lambda: Settings.model_validate(
            {
                "app_env": "test",
                "otel_export_timeout_seconds": 3.0,
                "otel_export_headers": '{"X-Token": "abc"}',
                "otel_export_compression": "gzip",
            }
        ),
    )

    exporter = _build_otlp_exporter("http://collector:4318")
    assert isinstance(exporter, RecordingExporter)
    assert captured["endpoint"] == "http://collector:4318/v1/traces"
    assert captured["timeout"] == 3.0
    assert captured["headers"] == {"X-Token": "abc"}
    assert captured["compression"] == Compression.Gzip


class _FakeExporter:
    def export(self, spans: object) -> SpanExportResult:
        return SpanExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    def shutdown(self) -> None:
        return None