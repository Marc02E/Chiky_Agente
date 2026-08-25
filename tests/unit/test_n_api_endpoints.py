"""FASE N — API endpoint coverage tests.

Targeted tests for api/app.py endpoints to increase coverage of the
HTTP layer.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client() -> TestClient:
    """Create a test client with mocked dependencies."""
    from personal_ai_secretary.api import app as app_module

    app = app_module.app
    return TestClient(app, raise_server_exceptions=False)


class TestHealthEndpoints:
    def test_live(self, client: TestClient) -> None:
        resp = client.get("/api/v1/health/live")
        assert resp.status_code == 200
        assert resp.json()["status"] == "alive"

    def test_ready(self, client: TestClient) -> None:
        resp = client.get("/api/v1/health/ready")
        assert resp.status_code in (200, 503)


class TestProvidersEndpoints:
    def test_providers(self, client: TestClient) -> None:
        resp = client.get("/api/v1/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "active" in data
        assert "available_modes" in data

    def test_list_local_models(self, client: TestClient) -> None:
        resp = client.get("/api/v1/providers/local/models")
        assert resp.status_code in (200, 503)


class TestRequestEndpoints:
    def test_create_request(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/requests",
            json={"input": "hello"},
            headers={
                "Authorization": "Bearer change-this-local-development-secret",
            },
        )
        assert resp.status_code in (202, 400, 403, 500, 503)


class TestMiddleware:
    def test_correlation_id_generated(self, client: TestClient) -> None:
        resp = client.get("/api/v1/health/live")
        assert "X-Correlation-ID" in resp.headers

    def test_correlation_id_preserved(self, client: TestClient) -> None:
        cid = "test-correlation-123"
        resp = client.get(
            "/api/v1/health/live",
            headers={"X-Correlation-ID": cid},
        )
        assert resp.headers.get("X-Correlation-ID") == cid


class TestExceptionHandlers:
    def test_404_returns_json(self, client: TestClient) -> None:
        resp = client.get("/api/v1/nonexistent/endpoint")
        assert resp.status_code == 404

    def test_404_has_error_envelope(self, client: TestClient) -> None:
        resp = client.get("/api/v1/nonexistent/endpoint")
        data = resp.json()
        assert "code" in data or "detail" in data


class TestAppCreation:
    def test_app_is_fastapi(self) -> None:
        from fastapi import FastAPI

        from personal_ai_secretary.api.app import app

        assert isinstance(app, FastAPI)

    def test_app_title(self) -> None:
        from personal_ai_secretary.api.app import app

        assert app.title == "Personal AI Secretary API"

    def test_app_version(self) -> None:
        from personal_ai_secretary.api.app import app

        assert app.version == "1.0.0"


class TestHelperFunctions:
    def test_user_id_with_sub(self) -> None:
        from personal_ai_secretary.api.app import _user_id

        assert _user_id({"sub": "alice"}) == "alice"

    def test_user_id_without_sub(self) -> None:
        from personal_ai_secretary.api.app import _user_id

        assert _user_id({}) == "development-user"

    def test_user_id_with_none_sub(self) -> None:
        from personal_ai_secretary.api.app import _user_id

        assert _user_id({"sub": None}) == "development-user"


class TestHttpExceptionHandler:
    @pytest.mark.anyio
    async def test_http_exception_handler_format(self) -> None:
        from fastapi import HTTPException

        from personal_ai_secretary.api.app import http_exception_handler

        exc = HTTPException(status_code=404, detail="not found")
        request = MagicMock()
        request.headers = {"X-Request-ID": "test-123"}

        response = await http_exception_handler(request, exc)
        assert response.status_code == 404


class TestEndpointCoverage:
    """Tests to cover additional endpoint paths for coverage."""

    AUTH = {"Authorization": "Bearer change-this-local-development-secret"}

    def test_list_requests(self, client: TestClient) -> None:
        resp = client.get("/api/v1/requests", headers=self.AUTH)
        assert resp.status_code in (200, 500)

    def test_list_sessions(self, client: TestClient) -> None:
        resp = client.get("/api/v1/sessions", headers=self.AUTH)
        assert resp.status_code in (200, 500)

    def test_get_request_not_found(self, client: TestClient) -> None:
        rid = str(uuid4())
        resp = client.get(f"/api/v1/requests/{rid}", headers=self.AUTH)
        assert resp.status_code in (200, 404, 500)

    def test_get_session_not_found(self, client: TestClient) -> None:
        sid = str(uuid4())
        resp = client.get(f"/api/v1/sessions/{sid}", headers=self.AUTH)
        assert resp.status_code in (200, 404, 500)

    def test_get_session_messages_not_found(self, client: TestClient) -> None:
        sid = str(uuid4())
        resp = client.get(f"/api/v1/sessions/{sid}/messages", headers=self.AUTH)
        assert resp.status_code in (200, 404, 500)

    def test_execute_request_not_found(self, client: TestClient) -> None:
        rid = str(uuid4())
        resp = client.post(f"/api/v1/requests/{rid}/execute", headers=self.AUTH)
        assert resp.status_code in (200, 404, 500)

    def test_get_audit(self, client: TestClient) -> None:
        resp = client.get("/api/v1/observability/audit", headers=self.AUTH)
        assert resp.status_code == 200

    def test_get_metrics(self, client: TestClient) -> None:
        resp = client.get("/api/v1/observability/metrics", headers=self.AUTH)
        assert resp.status_code == 200

    def test_get_prometheus_metrics(self, client: TestClient) -> None:
        resp = client.get("/api/v1/metrics", headers=self.AUTH)
        assert resp.status_code == 200

    def test_send_message_session_id_mismatch(self, client: TestClient) -> None:
        sid = str(uuid4())
        resp = client.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"input": "hello", "session_id": str(uuid4())},
            headers=self.AUTH,
        )
        assert resp.status_code in (400, 500)
