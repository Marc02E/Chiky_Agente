from fastapi.testclient import TestClient

from personal_ai_secretary.api.app import app


def test_openapi_contains_phase_one_paths() -> None:
    with TestClient(app) as client:
        document = client.get("/openapi.json").json()
    paths = document["paths"]
    assert "/api/v1/health/live" in paths
    assert "/api/v1/health/ready" in paths
    assert "/api/v1/providers" in paths
    assert "/api/v1/requests" in paths
    assert "/api/v1/sessions/{session_id}/messages" in paths


def test_openapi_declares_bearer_security_for_protected_paths() -> None:
    with TestClient(app) as client:
        document = client.get("/openapi.json").json()

    schemes = document["components"]["securitySchemes"]
    assert schemes["HTTPBearer"] == {"type": "http", "scheme": "bearer"}

    protected = [
        ("post", "/api/v1/requests"),
        ("get", "/api/v1/requests/{request_id}"),
        ("post", "/api/v1/requests/{request_id}/execute"),
        ("get", "/api/v1/sessions/{session_id}"),
        ("post", "/api/v1/sessions/{session_id}/messages"),
        ("get", "/api/v1/sessions/{session_id}/messages"),
        ("get", "/api/v1/observability/audit"),
        ("get", "/api/v1/observability/metrics"),
    ]
    for method, path in protected:
        assert document["paths"][path][method]["security"] == [{"HTTPBearer": []}]

    public = [
        ("get", "/api/v1/health/live"),
        ("get", "/api/v1/health/ready"),
        ("get", "/api/v1/providers"),
    ]
    for method, path in public:
        assert "security" not in document["paths"][path][method]
