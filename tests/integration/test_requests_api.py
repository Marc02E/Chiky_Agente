from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from personal_ai_secretary.api.app import app


class FakeProvider:
    name = "fake"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def health(self):
        from personal_ai_secretary.domain.contracts import ProviderInfo
        return ProviderInfo(
            name=self.name, mode="test", available=True,
            is_ai=False, detail="test provider",
        )

    async def generate(self, request):
        from personal_ai_secretary.domain.contracts import ProviderResponse
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider failure")
        return ProviderResponse(text=f"processed:{request.input}", provider=self.name)


def test_create_and_get_request() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/requests",
            headers={"X-Correlation-ID": "corr-api-1", "Idempotency-Key": "api-key-1"},
            json={"input": "hello api"},
        )
        assert response.status_code == 202
        body = response.json()
        assert UUID(body["request_id"])
        assert body["status"] == "accepted"
        assert body["correlation_id"] == "corr-api-1"
        request_id = body["request_id"]

        fetched = client.get(f"/api/v1/requests/{request_id}")
        assert fetched.status_code == 200
        assert fetched.json()["status"] == "accepted"
        assert fetched.headers["X-Correlation-ID"]


def test_idempotency_returns_original_request() -> None:
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/requests",
            headers={"Idempotency-Key": "api-idempotency"},
            json={"input": "first"},
        ).json()
        second = client.post(
            "/api/v1/requests",
            headers={"Idempotency-Key": "api-idempotency"},
            json={"input": "second"},
        ).json()
        assert second["request_id"] == first["request_id"]
        assert second["correlation_id"] == first["correlation_id"]


def test_unknown_request_returns_structured_error() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/requests/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "HTTP_404"
    assert body["retryable"] is False


def test_execute_request_uses_deterministic_provider() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/requests",
            headers={"Idempotency-Key": "execute-api-key"},
            json={"input": "execute me"},
        ).json()
        request_id = created["request_id"]
        response = client.post(f"/api/v1/requests/{request_id}/execute")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["result"] == "DETERMINISTIC_RESPONSE: execute me"


def test_session_isolation_returns_not_found_for_other_user(monkeypatch) -> None:
    from personal_ai_secretary.shared.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)
    import time

    import jwt

    def token(sub: str) -> str:
        return jwt.encode(
            {
                "sub": sub,
                "aud": settings.jwt_audience,
                "iss": settings.jwt_issuer,
                "exp": int(time.time()) + 3600,
            },
            settings.jwt_secret,
            algorithm="HS256",
        )

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/requests",
            headers={"Authorization": f"Bearer {token('owner')}"},
            json={"input": "private"},
        ).json()
        hidden = client.get(
            f"/api/v1/requests/{first['request_id']}",
            headers={"Authorization": f"Bearer {token('other')}"},
        )

    assert hidden.status_code == 404
    monkeypatch.setattr(settings, "jwt_required", False)



def test_session_message_round_trip_and_history() -> None:
    from uuid import uuid4

    session_id = str(uuid4())
    with TestClient(app) as client:
        first = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "conversation-1"},
            json={"input": "hello"},
        )
        assert first.status_code == 200
        body = first.json()
        assert body["session_id"] == session_id
        assert body["status"] == "completed"
        assert body["user_message"]["role"] == "user"
        assert body["assistant_message"]["content"] == "DETERMINISTIC_RESPONSE: hello"

        history = client.get(f"/api/v1/sessions/{session_id}/messages")
        assert history.status_code == 200
        messages = history.json()["messages"]
        assert [message["role"] for message in messages] == ["user", "assistant"]


def test_session_message_rejects_mismatched_session() -> None:
    from uuid import uuid4

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/sessions/{uuid4()}/messages",
            json={"input": "hello", "session_id": str(uuid4())},
        )
    assert response.status_code == 400


def test_session_history_enforces_user_isolation(monkeypatch) -> None:
    import time

    import jwt

    from personal_ai_secretary.shared.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)

    def token(sub: str) -> str:
        return jwt.encode(
            {
                "sub": sub,
                "aud": settings.jwt_audience,
                "iss": settings.jwt_issuer,
                "exp": int(time.time()) + 3600,
            },
            settings.jwt_secret,
            algorithm="HS256",
        )

    session_id = str(uuid4())
    with TestClient(app) as client:
        created = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Authorization": f"Bearer {token('owner')}"},
            json={"input": "private"},
        )
        assert created.status_code == 200
        hidden = client.get(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Authorization": f"Bearer {token('other')}"},
        )
    assert hidden.status_code == 404
    monkeypatch.setattr(settings, "jwt_required", False)


def test_session_message_rejects_reused_idempotency_key_for_another_session() -> None:
    with TestClient(app) as client:
        first = client.post(
            f"/api/v1/sessions/{uuid4()}/messages",
            headers={"Idempotency-Key": "same-conversation-key"},
            json={"input": "first"},
        )
        assert first.status_code == 200

        second = client.post(
            f"/api/v1/sessions/{uuid4()}/messages",
            headers={"Idempotency-Key": "same-conversation-key"},
            json={"input": "second"},
        )

    assert second.status_code == 409
    assert second.json()["code"] == "HTTP_409"


def test_session_history_has_stable_assistant_message_id() -> None:
    session_id = uuid4()
    with TestClient(app) as client:
        created = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "stable-history-key"},
            json={"input": "hello"},
        )
        assert created.status_code == 200

        first = client.get(f"/api/v1/sessions/{session_id}/messages")
        second = client.get(f"/api/v1/sessions/{session_id}/messages")

    assert first.status_code == second.status_code == 200
    first_messages = first.json()["messages"]
    second_messages = second.json()["messages"]
    assert [m["message_id"] for m in first_messages] == [
        m["message_id"] for m in second_messages
    ]


def test_high_risk_message_is_blocked_without_approval() -> None:
    session_id = uuid4()
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "governed-blocked"},
            json={"input": "send email to the board"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "blocked"
        assert body["assistant_message"]["status"] == "blocked"
        assert "approval required" in body["assistant_message"]["content"]

        history = client.get(f"/api/v1/sessions/{session_id}/messages")
        assert history.status_code == 200
        assert [m["role"] for m in history.json()["messages"]] == ["user", "assistant"]


def test_high_risk_message_completes_with_approval_header() -> None:
    session_id = uuid4()
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={
                "Idempotency-Key": "governed-approved",
                "X-Approval-Granted": "true",
            },
            json={"input": "send email to the board"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["assistant_message"]["content"] == (
            "DETERMINISTIC_RESPONSE: send email to the board"
        )


def test_execute_high_risk_request_requires_approval_header() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/requests",
            headers={"Idempotency-Key": "governed-execute-key"},
            json={"input": "send email to the board"},
        ).json()
        request_id = created["request_id"]

        blocked = client.post(f"/api/v1/requests/{request_id}/execute")
        assert blocked.status_code == 200
        assert blocked.json()["status"] == "blocked"

        approved = client.post(
            f"/api/v1/requests/{request_id}/execute",
            headers={"X-Approval-Granted": "true"},
        )
        assert approved.status_code == 200
        body = approved.json()
        assert body["status"] == "completed"
        assert body["result"] == "DETERMINISTIC_RESPONSE: send email to the board"


async def test_completed_requests_write_user_scoped_memory() -> None:
    from personal_ai_secretary.infrastructure.memory import get_memory_store
    from personal_ai_secretary.memory.service import MemoryClass

    session_id = uuid4()
    with TestClient(app) as client:
        first = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "memory-1"},
            json={"input": "hello"},
        )
        assert first.status_code == 200
        assert first.json()["status"] == "completed"

        second = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "memory-2"},
            json={"input": "hello again"},
        )
        assert second.status_code == 200
        assert second.json()["status"] == "completed"

        blocked = client.post(
            f"/api/v1/sessions/{uuid4()}/messages",
            headers={"Idempotency-Key": "memory-blocked"},
            json={"input": "send email to the board"},
        )
        assert blocked.status_code == 200
        assert blocked.json()["status"] == "blocked"

    items = await get_memory_store().retrieve("development-user")
    assert len(items) == 2
    assert all(item.memory_class == MemoryClass.SESSION for item in items)
    assert any("hello" in item.content for item in items)
    assert any("hello again" in item.content for item in items)


def test_tool_message_runs_calculator() -> None:
    session_id = uuid4()
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "tool-calculator"},
            json={"input": '@tool:calculator {"expression": "2+2"}'},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["assistant_message"]["content"] == "Tool 'calculator' returned: 4"


def test_tool_message_reports_unknown_tool() -> None:
    session_id = uuid4()
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "tool-unknown"},
            json={"input": "@tool:ghost {}"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["assistant_message"]["content"] == "Tool 'ghost' is not available."


def test_tool_message_reports_invalid_arguments() -> None:
    session_id = uuid4()
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "tool-invalid-args"},
            json={"input": "@tool:calculator {}"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert "rejected arguments" in body["assistant_message"]["content"]


def test_sensitive_tool_requires_approval_header(monkeypatch) -> None:
    import personal_ai_secretary.api.app as app_module
    from personal_ai_secretary.tools.registry import ToolDefinition, ToolRegistry, ToolRisk

    async def send(_: dict[str, object]) -> dict[str, object]:
        return {"sent": True}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "send_email", ToolRisk.HIGH, True, send, argument_schema={"to": "string"}
        )
    )
    monkeypatch.setattr(app_module, "default_tool_registry", lambda: registry)

    session_id = uuid4()
    with TestClient(app) as client:
        blocked = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "tool-send-blocked"},
            json={"input": '@tool:send_email {"to": "boss@example.com"}'},
        )
        assert blocked.status_code == 200
        assert blocked.json()["status"] == "blocked"
        assert "approval required" in blocked.json()["assistant_message"]["content"]

        approved = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={
                "Idempotency-Key": "tool-send-approved",
                "X-Approval-Granted": "true",
            },
            json={"input": '@tool:send_email {"to": "boss@example.com"}'},
        )
        assert approved.status_code == 200
        body = approved.json()
        assert body["status"] == "completed"
        assert body["assistant_message"]["content"] == (
            'Tool \'send_email\' returned: {"sent": true}'
        )


def test_rejected_status_surfaces_over_http(monkeypatch) -> None:
    import personal_ai_secretary.api.app as app_module
    from personal_ai_secretary.application.service import RequestService
    from personal_ai_secretary.evaluation.runtime import (
        EvaluationContext,
        EvaluationCriterion,
        EvaluationOutcome,
        ReleaseGateEvaluator,
    )
    from personal_ai_secretary.infrastructure.memory import get_memory_store
    from personal_ai_secretary.providers.factory import get_provider
    from personal_ai_secretary.rag.service import GovernedRetriever
    from personal_ai_secretary.tools.builtin import default_tool_registry

    class RejectingEvaluator(ReleaseGateEvaluator):
        def evaluate(self, context: EvaluationContext) -> EvaluationOutcome:
            return EvaluationOutcome(
                passed=False,
                criteria=(
                    EvaluationCriterion("always_reject", False, "test-only rejection"),
                ),
            )

    def rejecting_service(db: object) -> RequestService:
        return RequestService(
            db,
            get_provider(),
            memory=get_memory_store(),
            retriever=GovernedRetriever(),
            tools=default_tool_registry(),
            evaluator=RejectingEvaluator(),
        )

    monkeypatch.setattr(app_module, "_service", rejecting_service)

    session_id = uuid4()
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "rejected-http"},
            json={"input": "hello"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "rejected"
        assert body["assistant_message"]["status"] == "rejected"
        assert "rejected by evaluation" in body["assistant_message"]["content"]


def test_observability_metrics_endpoint_returns_snapshot() -> None:
    session_id = uuid4()
    with TestClient(app) as client:
        created = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "obs-metrics"},
            json={"input": "hello"},
        )
        assert created.status_code == 200
        assert created.json()["status"] == "completed"

        response = client.get("/api/v1/observability/metrics")
        assert response.status_code == 200
        body = response.json()
        assert body["counters"]["requests_total"] == 1
        assert body["counters"]["requests_completed"] == 1
        assert set(body["durations"]) >= {"workflow", "research", "evaluation"}


def test_observability_audit_is_user_scoped(monkeypatch) -> None:
    import time

    import jwt

    from personal_ai_secretary.shared.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)

    def token(sub: str) -> str:
        return jwt.encode(
            {
                "sub": sub,
                "aud": settings.jwt_audience,
                "iss": settings.jwt_issuer,
                "exp": int(time.time()) + 3600,
            },
            settings.jwt_secret,
            algorithm="HS256",
        )

    with TestClient(app) as client:
        for sub in ("user-a", "user-b"):
            message = client.post(
                f"/api/v1/sessions/{uuid4()}/messages",
                headers={
                    "Authorization": f"Bearer {token(sub)}",
                    "Idempotency-Key": f"obs-{sub}",
                },
                json={"input": "hello"},
            )
            assert message.status_code == 200
            assert message.json()["status"] == "completed"

        audit_a = client.get(
            "/api/v1/observability/audit",
            headers={"Authorization": f"Bearer {token('user-a')}"},
        ).json()
        assert audit_a
        assert all(event["user_id"] == "user-a" for event in audit_a)

        audit_b = client.get(
            "/api/v1/observability/audit",
            headers={"Authorization": f"Bearer {token('user-b')}"},
        ).json()
        assert audit_b
        assert all(event["user_id"] == "user-b" for event in audit_b)

    monkeypatch.setattr(settings, "jwt_required", False)


def test_observability_endpoints_require_authentication(monkeypatch) -> None:
    from personal_ai_secretary.shared.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)
    with TestClient(app) as client:
        audit = client.get("/api/v1/observability/audit")
        metrics = client.get("/api/v1/observability/metrics")
    assert audit.status_code == 401
    assert metrics.status_code == 401
    monkeypatch.setattr(settings, "jwt_required", False)


def test_observability_audit_records_full_workflow_trail() -> None:
    session_id = uuid4()
    with TestClient(app) as client:
        created = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "obs-trail"},
            json={"input": '@tool:calculator {"expression": "2+2"}'},
        )
        assert created.status_code == 200
        assert created.json()["status"] == "completed"

        events = client.get(
            "/api/v1/observability/audit", params={"limit": 100}
        ).json()
    stages = [event["event_type"] for event in events]
    assert "planner" in stages
    assert "execution" in stages
    assert "tool" in stages
    assert "evaluation" in stages
    assert "compliance" in stages
    assert "completed" in stages
    assert stages[-1] == "request"
    tool_events = [event for event in events if event["event_type"] == "tool"]
    assert tool_events[-1]["details"]["name"] == "calculator"


def test_same_idempotency_key_across_users_are_isolated(monkeypatch) -> None:
    import time

    import jwt

    from personal_ai_secretary.shared.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)

    def token(sub: str) -> str:
        return jwt.encode(
            {
                "sub": sub,
                "aud": settings.jwt_audience,
                "iss": settings.jwt_issuer,
                "exp": int(time.time()) + 3600,
            },
            settings.jwt_secret,
            algorithm="HS256",
        )

    with TestClient(app) as client:
        first = client.post(
            "/api/v1/requests",
            headers={
                "Authorization": f"Bearer {token('user-a')}",
                "Idempotency-Key": "shared-key",
            },
            json={"input": "for user-a"},
        )
        second = client.post(
            "/api/v1/requests",
            headers={
                "Authorization": f"Bearer {token('user-b')}",
                "Idempotency-Key": "shared-key",
            },
            json={"input": "for user-b"},
        )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["request_id"] != second.json()["request_id"]
    monkeypatch.setattr(settings, "jwt_required", False)


def test_evidence_ingestion_and_retrieval_over_http(monkeypatch) -> None:
    from personal_ai_secretary.shared.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "persistent_stores", True)

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/evidence",
            json={
                "source_id": "policy-hello",
                "uri": "https://example.com/policies",
                "title": "hello policy",
                "content": "greet users politely",
                "authority": 0.9,
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["source_id"] == "policy-hello"
        assert body["title"] == "hello policy"
        assert body["authority"] == 0.9
        assert body["created_at"]

        listed = client.get("/api/v1/evidence")
        assert listed.status_code == 200
        assert [item["source_id"] for item in listed.json()] == ["policy-hello"]

        message = client.post(
            f"/api/v1/sessions/{uuid4()}/messages",
            headers={"Idempotency-Key": "evidence-roundtrip"},
            json={"input": "hello"},
        )
        assert message.status_code == 200
        assert message.json()["status"] == "completed"

        audit = client.get(
            "/api/v1/observability/audit", params={"limit": 100}
        ).json()
        research = next(
            event for event in audit if event["event_type"] == "research"
        )
        assert research["details"]["evidence_count"] >= 1


def test_evidence_endpoints_require_authentication(monkeypatch) -> None:
    from personal_ai_secretary.shared.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)
    with TestClient(app) as client:
        created = client.post("/api/v1/evidence", json={})
        listed = client.get("/api/v1/evidence")
    assert created.status_code == 401
    assert listed.status_code == 401
    monkeypatch.setattr(settings, "jwt_required", False)


def test_evidence_validation_rejects_invalid_payload(monkeypatch) -> None:
    from personal_ai_secretary.shared.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "persistent_stores", True)
    with TestClient(app) as client:
        missing = client.post(
            "/api/v1/evidence",
            json={"source_id": "only-id"},
        )
        bad_authority = client.post(
            "/api/v1/evidence",
            json={
                "source_id": "x",
                "uri": "https://example.com",
                "title": "t",
                "content": "c",
                "authority": 1.5,
            },
        )
    assert missing.status_code == 422
    assert bad_authority.status_code == 422


def test_evidence_disabled_without_persistent_stores() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/evidence",
            json={
                "source_id": "x",
                "uri": "https://example.com",
                "title": "t",
                "content": "c",
            },
        )
    assert response.status_code == 503


def test_get_session_returns_404_for_unknown() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/sessions/00000000-0000-0000-0000-000000000000"
        )
    assert response.status_code == 404


def test_get_session_messages_returns_404_for_unknown() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/sessions/00000000-0000-0000-0000-000000000000/messages"
        )
    assert response.status_code == 404


def test_execute_returns_404_for_unknown_request() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/requests/00000000-0000-0000-0000-000000000000/execute"
        )
    assert response.status_code == 404


def test_create_request_rejects_other_users_session(monkeypatch) -> None:
    import time

    import jwt

    from personal_ai_secretary.shared.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)

    def token(sub: str) -> str:
        return jwt.encode(
            {
                "sub": sub,
                "aud": settings.jwt_audience,
                "iss": settings.jwt_issuer,
                "exp": int(time.time()) + 3600,
            },
            settings.jwt_secret,
            algorithm="HS256",
        )

    session_id = str(uuid4())
    with TestClient(app) as client:
        client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Authorization": f"Bearer {token('owner')}"},
            json={"input": "establish session"},
        )
        response = client.post(
            "/api/v1/requests",
            headers={"Authorization": f"Bearer {token('intruder')}"},
            json={"input": "hijack", "session_id": session_id},
        )

    assert response.status_code == 403
    monkeypatch.setattr(settings, "jwt_required", False)


def test_send_message_rejects_other_users_session(monkeypatch) -> None:
    import time

    import jwt

    from personal_ai_secretary.shared.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)

    def token(sub: str) -> str:
        return jwt.encode(
            {
                "sub": sub,
                "aud": settings.jwt_audience,
                "iss": settings.jwt_issuer,
                "exp": int(time.time()) + 3600,
            },
            settings.jwt_secret,
            algorithm="HS256",
        )

    session_id = str(uuid4())
    with TestClient(app) as client:
        client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Authorization": f"Bearer {token('owner')}"},
            json={"input": "establish session"},
        )
        response = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Authorization": f"Bearer {token('intruder')}"},
            json={"input": "hijack"},
        )

    assert response.status_code == 403
    monkeypatch.setattr(settings, "jwt_required", False)


def test_list_sessions_returns_user_sessions() -> None:
    with TestClient(app) as client:
        sid1 = str(uuid4())
        sid2 = str(uuid4())
        client.post(
            f"/api/v1/sessions/{sid1}/messages",
            headers={"Idempotency-Key": "list-sessions-1"},
            json={"input": "first"},
        )
        client.post(
            f"/api/v1/sessions/{sid2}/messages",
            headers={"Idempotency-Key": "list-sessions-2"},
            json={"input": "second"},
        )

        response = client.get("/api/v1/sessions")
        assert response.status_code == 200
        body = response.json()
        assert "sessions" in body
        session_ids = [s["session_id"] for s in body["sessions"]]
        assert sid1 in session_ids
        assert sid2 in session_ids
        for session in body["sessions"]:
            assert session["user_id"] == "development-user"
            assert session["request_count"] >= 1


def test_list_sessions_respects_limit() -> None:
    with TestClient(app) as client:
        for i in range(5):
            client.post(
                f"/api/v1/sessions/{uuid4()}/messages",
                headers={"Idempotency-Key": f"limit-test-{i}"},
                json={"input": f"message {i}"},
            )

        response = client.get("/api/v1/sessions", params={"limit": 3})
        assert response.status_code == 200
        assert len(response.json()["sessions"]) == 3


def test_list_sessions_requires_authentication(monkeypatch) -> None:
    from personal_ai_secretary.shared.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)
    with TestClient(app) as client:
        response = client.get("/api/v1/sessions")
    assert response.status_code == 401
    monkeypatch.setattr(settings, "jwt_required", False)


def test_list_requests_returns_user_requests() -> None:
    with TestClient(app) as client:
        r1 = client.post(
            "/api/v1/requests",
            headers={"Idempotency-Key": "list-requests-1"},
            json={"input": "task one"},
        ).json()
        r2 = client.post(
            "/api/v1/requests",
            headers={"Idempotency-Key": "list-requests-2"},
            json={"input": "task two"},
        ).json()

        response = client.get("/api/v1/requests")
        assert response.status_code == 200
        body = response.json()
        assert "requests" in body
        request_ids = [r["request_id"] for r in body["requests"]]
        assert r1["request_id"] in request_ids
        assert r2["request_id"] in request_ids
        for req in body["requests"]:
            valid_statuses = {"accepted", "completed", "running", "blocked", "rejected", "failed"}
            assert req["status"] in valid_statuses


def test_list_requests_respects_limit() -> None:
    with TestClient(app) as client:
        for i in range(4):
            client.post(
                "/api/v1/requests",
                headers={"Idempotency-Key": f"req-limit-{i}"},
                json={"input": f"task {i}"},
            )

        response = client.get("/api/v1/requests", params={"limit": 2})
        assert response.status_code == 200
        assert len(response.json()["requests"]) == 2


def test_list_requests_requires_authentication(monkeypatch) -> None:
    from personal_ai_secretary.shared.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)
    with TestClient(app) as client:
        response = client.get("/api/v1/requests")
    assert response.status_code == 401
    monkeypatch.setattr(settings, "jwt_required", False)


def test_conversation_history_passed_to_provider() -> None:
    session_id = uuid4()
    with TestClient(app) as client:
        first = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "conv-hist-1"},
            json={"input": "my name is Alice"},
        )
        assert first.status_code == 200
        assert first.json()["status"] == "completed"

        second = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "conv-hist-2"},
            json={"input": "what is my name?"},
        )
        assert second.status_code == 200
        assert second.json()["status"] == "completed"

        history = client.get(f"/api/v1/sessions/{session_id}/messages")
        messages = history.json()["messages"]
        assert len(messages) == 4
        assert messages[0]["content"] == "my name is Alice"
        assert messages[1]["role"] == "assistant"
        assert messages[2]["content"] == "what is my name?"
        assert messages[3]["role"] == "assistant"


def test_different_sessions_do_not_share_history() -> None:
    sid_a = str(uuid4())
    sid_b = str(uuid4())
    with TestClient(app) as client:
        client.post(
            f"/api/v1/sessions/{sid_a}/messages",
            headers={"Idempotency-Key": "isolated-a"},
            json={"input": "session A message"},
        )
        client.post(
            f"/api/v1/sessions/{sid_b}/messages",
            headers={"Idempotency-Key": "isolated-b"},
            json={"input": "session B message"},
        )

        history_a = client.get(f"/api/v1/sessions/{sid_a}/messages").json()["messages"]
        history_b = client.get(f"/api/v1/sessions/{sid_b}/messages").json()["messages"]

    assert len(history_a) == 2
    assert len(history_b) == 2
    assert history_a[0]["content"] == "session A message"
    assert history_b[0]["content"] == "session B message"
    assert all(m["content"] != "session B message" for m in history_a)
    assert all(m["content"] != "session A message" for m in history_b)


def test_failed_requests_do_not_contaminate_history() -> None:
    session_id = uuid4()
    with TestClient(app) as client:
        success = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "fail-ok-1"},
            json={"input": "hello"},
        )
        assert success.json()["status"] == "completed"

        blocked = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "fail-blocked"},
            json={"input": "send email to the board"},
        )
        assert blocked.json()["status"] == "blocked"

        after_block = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            headers={"Idempotency-Key": "fail-ok-2"},
            json={"input": "continue"},
        )
        assert after_block.json()["status"] == "completed"

        history = client.get(f"/api/v1/sessions/{session_id}/messages").json()["messages"]
        user_inputs = [m["content"] for m in history if m["role"] == "user"]
        assert user_inputs == ["hello", "send email to the board", "continue"]


def test_context_summary_reaches_provider_via_workflow() -> None:
    import personal_ai_secretary.api.app as app_module
    from personal_ai_secretary.application.service import RequestService
    from personal_ai_secretary.domain.contracts import RequestEnvelope
    from personal_ai_secretary.infrastructure.memory import get_memory_store
    from personal_ai_secretary.rag.service import GovernedRetriever
    from personal_ai_secretary.tools.builtin import default_tool_registry

    class RecordingProvider:
        name = "recording"

        def __init__(self) -> None:
            self.envelopes: list[RequestEnvelope] = []

        async def health(self):
            from personal_ai_secretary.domain.contracts import ProviderInfo
            return ProviderInfo(
                name=self.name, mode="test", available=True,
                is_ai=False, detail="test",
            )

        async def generate(self, request: RequestEnvelope):
            self.envelopes.append(request)
            from personal_ai_secretary.domain.contracts import ProviderResponse
            return ProviderResponse(text=f"reply:{request.input}", provider=self.name)

    recording_provider = RecordingProvider()

    def recording_service(db: object) -> RequestService:
        return RequestService(
            db,
            recording_provider,
            memory=get_memory_store(),
            retriever=GovernedRetriever(),
            tools=default_tool_registry(),
        )

    monkeypatch_target = app_module
    original_service = monkeypatch_target._service
    monkeypatch_target._service = recording_service
    try:
        session_id = uuid4()
        with TestClient(app) as client:
            client.post(
                f"/api/v1/sessions/{session_id}/messages",
                headers={"Idempotency-Key": "ctx-1"},
                json={"input": "first message"},
            )
            client.post(
                f"/api/v1/sessions/{session_id}/messages",
                headers={"Idempotency-Key": "ctx-2"},
                json={"input": "second message"},
            )

        assert len(recording_provider.envelopes) == 2
        first_envelope = recording_provider.envelopes[0]
        assert first_envelope.messages == []
        # System prompt is now always injected as context_summary
        assert first_envelope.context_summary is not None
        assert "Chiky" in first_envelope.context_summary

        second_envelope = recording_provider.envelopes[1]
        assert len(second_envelope.messages) == 2
        assert second_envelope.messages[0].role == "user"
        assert second_envelope.messages[0].content == "first message"
        assert second_envelope.messages[1].role == "assistant"
    finally:
        monkeypatch_target._service = original_service


def test_list_sessions_isolation_between_users(monkeypatch) -> None:
    import time

    import jwt

    from personal_ai_secretary.shared.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)

    def token(sub: str) -> str:
        return jwt.encode(
            {
                "sub": sub,
                "aud": settings.jwt_audience,
                "iss": settings.jwt_issuer,
                "exp": int(time.time()) + 3600,
            },
            settings.jwt_secret,
            algorithm="HS256",
        )

    with TestClient(app) as client:
        sid = str(uuid4())
        client.post(
            f"/api/v1/sessions/{sid}/messages",
            headers={
                "Authorization": f"Bearer {token('owner-list')}",
                "Idempotency-Key": "list-iso-owner",
            },
            json={"input": "private message"},
        )

        owner_list = client.get(
            "/api/v1/sessions",
            headers={"Authorization": f"Bearer {token('owner-list')}"},
        ).json()
        other_list = client.get(
            "/api/v1/sessions",
            headers={"Authorization": f"Bearer {token('other-list')}"},
        ).json()

    owner_ids = [s["session_id"] for s in owner_list["sessions"]]
    other_ids = [s["session_id"] for s in other_list["sessions"]]
    assert sid in owner_ids
    assert sid not in other_ids
    monkeypatch.setattr(settings, "jwt_required", False)


def test_list_requests_isolation_between_users(monkeypatch) -> None:
    import time

    import jwt

    from personal_ai_secretary.shared.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)

    def token(sub: str) -> str:
        return jwt.encode(
            {
                "sub": sub,
                "aud": settings.jwt_audience,
                "iss": settings.jwt_issuer,
                "exp": int(time.time()) + 3600,
            },
            settings.jwt_secret,
            algorithm="HS256",
        )

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/requests",
            headers={
                "Authorization": f"Bearer {token('owner-req')}",
                "Idempotency-Key": "list-req-iso",
            },
            json={"input": "private task"},
        ).json()

        owner_list = client.get(
            "/api/v1/requests",
            headers={"Authorization": f"Bearer {token('owner-req')}"},
        ).json()
        other_list = client.get(
            "/api/v1/requests",
            headers={"Authorization": f"Bearer {token('other-req')}"},
        ).json()

    owner_ids = [r["request_id"] for r in owner_list["requests"]]
    other_ids = [r["request_id"] for r in other_list["requests"]]
    assert created["request_id"] in owner_ids
    assert created["request_id"] not in other_ids
    monkeypatch.setattr(settings, "jwt_required", False)


def test_provider_failure_mid_conversation_returns_failed() -> None:
    import personal_ai_secretary.api.app as app_module
    from personal_ai_secretary.application.service import RequestService
    from personal_ai_secretary.domain.contracts import ProviderInfo, ProviderResponse
    from personal_ai_secretary.infrastructure.memory import get_memory_store
    from personal_ai_secretary.tools.builtin import default_tool_registry

    call_count = 0

    class FailingProvider:
        name = "failing"

        async def health(self) -> ProviderInfo:
            return ProviderInfo(
                name=self.name, mode="test", available=True,
                is_ai=False, detail="test",
            )

        async def generate(self, request):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("provider crashed")
            return ProviderResponse(text=f"ok:{request.input}", provider=self.name)

    failing_provider = FailingProvider()

    def failing_service(db: object) -> RequestService:
        return RequestService(
            db,
            failing_provider,
            memory=get_memory_store(),
            tools=default_tool_registry(),
        )

    original = app_module._service
    app_module._service = failing_service
    try:
        session_id = uuid4()
        with TestClient(app) as client:
            first = client.post(
                f"/api/v1/sessions/{session_id}/messages",
                headers={"Idempotency-Key": "fail-mid-1"},
                json={"input": "hello"},
            )
            assert first.json()["status"] == "completed"

            second = client.post(
                f"/api/v1/sessions/{session_id}/messages",
                headers={"Idempotency-Key": "fail-mid-2"},
                json={"input": "trigger crash"},
            )
            assert second.json()["status"] == "completed"
            assert second.json()["assistant_message"] is not None

            third = client.post(
                f"/api/v1/sessions/{session_id}/messages",
                headers={"Idempotency-Key": "fail-mid-3"},
                json={"input": "recover"},
            )
            assert third.json()["status"] == "completed"
    finally:
        app_module._service = original


@pytest.mark.asyncio
async def test_malformed_conversation_history_handled_gracefully() -> None:
    from personal_ai_secretary.agents.builtin import ExecutionAgent
    from personal_ai_secretary.agents.contracts import AgentInput
    from personal_ai_secretary.domain.contracts import (
        ProviderInfo,
        ProviderResponse,
        RequestEnvelope,
    )

    class RecordingProvider:
        name = "recording"

        def __init__(self) -> None:
            self.envelopes: list[RequestEnvelope] = []

        async def health(self) -> ProviderInfo:
            return ProviderInfo(
                name=self.name, mode="test", available=True,
                is_ai=False, detail="test",
            )

        async def generate(self, request: RequestEnvelope) -> ProviderResponse:
            self.envelopes.append(request)
            return ProviderResponse(text="ok", provider=self.name)

    provider = RecordingProvider()
    agent = ExecutionAgent(provider)

    bad_history = [
        {"role": "user", "content": "valid turn"},
        "not a dict",
        {"role": "assistant"},
        {"content": "no role"},
        42,
        None,
    ]

    artifact = await agent.run(
        AgentInput(
            request_id=uuid4(),
            session_id=uuid4(),
            user_id="u",
            text="hello",
            correlation_id="corr",
            context={"conversation_history": bad_history},
        )
    )

    assert artifact.content == "ok"
    envelope = provider.envelopes[-1]
    assert len(envelope.messages) == 1
    assert envelope.messages[0].role == "user"
    assert envelope.messages[0].content == "valid turn"


@pytest.mark.asyncio
async def test_history_limit_caps_at_20_turns() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from personal_ai_secretary.application.service import RequestService
    from personal_ai_secretary.domain.contracts import RequestCreate
    from personal_ai_secretary.domain.models import Base

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        provider = FakeProvider()
        service = RequestService(db, provider)
        session_id = uuid4()
        for i in range(25):
            accepted = await service.create(
                RequestCreate(input=f"msg-{i}", session_id=session_id),
                "user-1",
                f"corr-{i}",
                f"hl-{i}",
            )
            await service.execute(accepted.request_id)
        history = await service._fetch_session_history(session_id, "user-1", limit=20)
    await engine.dispose()

    assert len(history) == 20
    assert history[0]["content"] == "msg-15"
    assert history[-1]["content"] == "processed:msg-24"
