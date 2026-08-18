"""End-to-end smoke tests for application startup, health, API and shutdown.

These tests exercise the full lifespan lifecycle via TestClient and verify
the application is functionally usable from a clean start.
"""

from uuid import uuid4

from fastapi.testclient import TestClient

from personal_ai_secretary.api.app import app


def test_startup_and_shutdown_smoke() -> None:
    """Verify the app starts (lifespan init), serves requests, and shuts down cleanly."""
    with TestClient(app) as client:
        live = client.get("/api/v1/health/live")
        assert live.status_code == 200
        assert live.json()["status"] == "alive"

        ready = client.get("/api/v1/health/ready")
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
        assert ready.json()["database"] == "ok"


def test_full_request_lifecycle() -> None:
    """Verify create -> execute -> get request round-trip."""
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/requests",
            headers={"Idempotency-Key": "smoke-lifecycle"},
            json={"input": "lifecycle smoke test"},
        )
        assert created.status_code == 202
        request_id = created.json()["request_id"]

        executed = client.post(f"/api/v1/requests/{request_id}/execute")
        assert executed.status_code == 200
        assert executed.json()["status"] == "completed"
        assert "DETERMINISTIC_RESPONSE" in executed.json()["result"]

        fetched = client.get(f"/api/v1/requests/{request_id}")
        assert fetched.status_code == 200
        assert fetched.json()["status"] == "completed"


def test_session_message_smoke() -> None:
    """Verify session message round-trip with history."""
    session_id = str(uuid4())
    with TestClient(app) as client:
        msg = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "smoke-session"},
            json={"input": "hello smoke"},
        )
        assert msg.status_code == 200
        body = msg.json()
        assert body["status"] == "completed"
        assert body["session_id"] == session_id
        assert body["assistant_message"]["role"] == "assistant"

        history = client.get(f"/api/v1/sessions/{session_id}/messages")
        assert history.status_code == 200
        messages = history.json()["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"


def test_observability_smoke() -> None:
    """Verify audit and metrics endpoints return valid data."""
    with TestClient(app) as client:
        session_id = str(uuid4())
        msg = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "smoke-observability"},
            json={"input": "observe me"},
        )
        assert msg.status_code == 200

        audit = client.get("/api/v1/observability/audit", params={"limit": 10})
        assert audit.status_code == 200
        assert isinstance(audit.json(), list)

        metrics = client.get("/api/v1/observability/metrics")
        assert metrics.status_code == 200
        assert "counters" in metrics.json()
        assert "durations" in metrics.json()

        prometheus = client.get("/api/v1/metrics")
        assert prometheus.status_code == 200
        assert "text/plain" in prometheus.headers["content-type"]


def test_providers_endpoint_smoke() -> None:
    """Verify providers endpoint returns correct mode info."""
    with TestClient(app) as client:
        providers = client.get("/api/v1/providers")
        assert providers.status_code == 200
        body = providers.json()
        assert body["active"]["name"] == "deterministic"
        assert "deterministic" in body["available_modes"]
        assert "local" in body["available_modes"]
        assert "remote" in body["available_modes"]


def test_tool_invocation_smoke() -> None:
    """Verify tool invocation via @tool: prefix works end-to-end."""
    session_id = str(uuid4())
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "smoke-tool"},
            json={"input": '@tool:calculator {"expression": "2+2"}'},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert "Tool 'calculator' returned: 4" in body["assistant_message"]["content"]


def test_high_risk_approval_smoke() -> None:
    """Verify high-risk request is blocked without approval and completes with approval."""
    session_id = str(uuid4())
    with TestClient(app) as client:
        blocked = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "smoke-blocked"},
            json={"input": "send email to the board"},
        )
        assert blocked.status_code == 200
        assert blocked.json()["status"] == "blocked"

        approved = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={
                "Idempotency-Key": "smoke-approved",
                "X-Approval-Granted": "true",
            },
            json={"input": "send email to the board"},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "completed"
