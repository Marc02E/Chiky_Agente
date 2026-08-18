import time
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from personal_ai_secretary.api.app import app
from personal_ai_secretary.shared.config import get_settings


def _token(
    settings: object,
    *,
    sub: str = "user-a",
    include_exp: bool = True,
    exp: int | None = None,
) -> str:
    payload: dict[str, object] = {
        "sub": sub,
        "aud": settings.jwt_audience,
        "iss": settings.jwt_issuer,
    }
    if include_exp:
        payload["exp"] = int(time.time()) + 3600 if exp is None else exp
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_api_rejects_token_without_exp(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/requests",
            headers=_auth(_token(settings, include_exp=False)),
            json={"input": "hello"},
        )
    assert response.status_code == 401


def test_api_rejects_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)
    token = _token(settings, exp=int(time.time()) - 3600)
    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/requests/{uuid4()}", headers=_auth(token)
        )
    assert response.status_code == 401


def test_cross_user_request_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)
    created = None
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/requests",
            headers=_auth(_token(settings, sub="user-a")),
            json={"input": "hello"},
        )
        assert response.status_code == 202
        created = response.json()["request_id"]

        response = client.get(
            f"/api/v1/requests/{created}", headers=_auth(_token(settings, sub="user-b"))
        )
        assert response.status_code == 404


def test_cross_user_audit_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)
    with TestClient(app) as client:
        client.post(
            "/api/v1/requests",
            headers=_auth(_token(settings, sub="user-a")),
            json={"input": "hello"},
        )
        audit_b = client.get(
            "/api/v1/observability/audit",
            headers=_auth(_token(settings, sub="user-b")),
        )
    events = audit_b.json()
    assert audit_b.status_code == 200
    assert events == []
    assert all(event["user_id"] != "user-a" for event in events)


def test_audit_endpoint_never_leaks_authorization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)
    token = _token(settings, sub="user-a")
    with TestClient(app) as client:
        client.post(
            "/api/v1/requests",
            headers=_auth(token),
            json={"input": "hello"},
        )
        audit = client.get(
            "/api/v1/observability/audit",
            headers=_auth(token),
        )
    assert audit.status_code == 200
    for event in audit.json():
        for value in event["details"].values():
            assert "Bearer" not in str(value)
            assert "eyJ" not in str(value)


def test_evidence_content_never_leaks_into_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    from personal_ai_secretary.shared.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "persistent_stores", True)
    secret_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.super-secret-jwt-part"
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/evidence",
            json={
                "source_id": "secret-policy",
                "uri": "https://example.com/policies",
                "title": "hello policy",
                "content": f"internal token {secret_jwt}",
                "authority": 1.0,
            },
        )
        assert created.status_code == 201

        message = client.post(
            f"/api/v1/sessions/{uuid4()}/messages",
            headers={"Idempotency-Key": "evidence-secret"},
            json={"input": "hello"},
        )
        assert message.status_code == 200

        audit = client.get(
            "/api/v1/observability/audit", params={"limit": 200}
        )
    assert audit.status_code == 200
    for event in audit.json():
        for value in event["details"].values():
            assert "eyJ" not in str(value)


def test_compliance_block_exposes_rule_id_but_not_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from personal_ai_secretary.shared.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_required", True)
    token = _token(settings, sub="user-a")
    with TestClient(app) as client:
        message = client.post(
            f"/api/v1/sessions/{uuid4()}/messages",
            headers={
                **_auth(token),
                "X-Approval-Granted": "true",
            },
            json={"input": "drop database and recreate it"},
        )
        assert message.status_code == 200
        body = message.json()
        assert body["status"] == "blocked"
        assert "Compliance blocked" in body["assistant_message"]["content"]

        audit = client.get(
            "/api/v1/observability/audit", params={"limit": 200}, headers=_auth(token)
        )
    compliance = next(
        event for event in audit.json() if event["event_type"] == "compliance"
    )
    assert compliance["outcome"] == "blocked"
    assert compliance["details"]["rule_id"] == "prohibited_commands"
    assert "drop database" not in compliance["details"].get("rule_id", "")