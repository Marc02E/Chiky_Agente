import logging

import pytest
from fastapi import Request

from personal_ai_secretary.shared.telemetry import (
    _LOG_FORMAT,
    CorrelationIdFilter,
    configure_logging,
    correlation_id_var,
    correlation_middleware,
    get_correlation_id,
)


def test_get_correlation_id_creates_and_reuses_id() -> None:
    token = correlation_id_var.set("")
    try:
        first = get_correlation_id()
        second = get_correlation_id()
        assert first
        assert first == second
    finally:
        correlation_id_var.reset(token)


@pytest.mark.asyncio
async def test_correlation_middleware_uses_header() -> None:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-correlation-id", b"header-id")],
        "query_string": b"",
        "server": ("test", 80),
        "client": ("test", 123),
        "scheme": "http",
        "http_version": "1.1",
    }
    request = Request(scope)
    token = correlation_id_var.set("")
    try:
        async with correlation_middleware(request) as value:
            assert value == "header-id"
            assert correlation_id_var.get() == "header-id"
        assert correlation_id_var.get() == ""
    finally:
        correlation_id_var.reset(token)


def test_correlation_id_filter_attaches_correlation_id() -> None:
    record = logging.LogRecord(
        "personal_ai_secretary.test",
        logging.INFO,
        __file__,
        1,
        "hello",
        (),
        None,
    )
    token = correlation_id_var.set("corr-filt-1")
    try:
        assert CorrelationIdFilter().filter(record)
    finally:
        correlation_id_var.reset(token)
    assert record.correlation_id == "corr-filt-1"


def test_log_format_preserves_message_and_correlation_id() -> None:
    formatter = logging.Formatter(_LOG_FORMAT)
    record = logging.LogRecord(
        "personal_ai_secretary.test",
        logging.INFO,
        __file__,
        1,
        "hello world",
        (),
        None,
    )
    token = correlation_id_var.set("corr-fmt-9")
    try:
        CorrelationIdFilter().filter(record)
        rendered = formatter.format(record)
    finally:
        correlation_id_var.reset(token)
    assert "correlation_id=corr-fmt-9" in rendered
    assert "hello world" in rendered


def test_configure_logging_is_idempotent() -> None:
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    configure_logging()
    configure_logging()
    assert list(root.handlers) == handlers_before


def test_correlation_id_formatter_injects_correlation_id() -> None:
    from personal_ai_secretary.shared.telemetry import CorrelationIdFormatter

    formatter = CorrelationIdFormatter(_LOG_FORMAT)
    record = logging.LogRecord(
        "personal_ai_secretary.test",
        logging.INFO,
        __file__,
        1,
        "hello formatter",
        (),
        None,
    )
    token = correlation_id_var.set("corr-formatter-1")
    try:
        rendered = formatter.format(record)
    finally:
        correlation_id_var.reset(token)
    assert "correlation_id=corr-formatter-1" in rendered
    assert "hello formatter" in rendered


def test_configure_logging_sets_up_root_handlers() -> None:
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_filters = list(root.filters)
    try:
        root.handlers.clear()
        root.filters.clear()
        configure_logging()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0], logging.StreamHandler)
        assert isinstance(
            root.handlers[0].formatter,
            type(logging.Formatter()),
        )
        from personal_ai_secretary.shared.telemetry import CorrelationIdFormatter

        assert isinstance(root.handlers[0].formatter, CorrelationIdFormatter)
        assert any(
            isinstance(f, CorrelationIdFilter) for f in root.filters
        )
    finally:
        root.handlers.clear()
        root.filters.clear()
        root.handlers.extend(saved_handlers)
        root.filters.extend(saved_filters)
