from fastapi.testclient import TestClient

from personal_ai_secretary.api.app import app
from personal_ai_secretary.providers.factory import AVAILABLE_PROVIDER_MODES


def test_liveness() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_readiness_and_provider_metadata() -> None:
    with TestClient(app) as client:
        ready = client.get("/api/v1/health/ready")
        providers = client.get("/api/v1/providers")

    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["provider"]["name"] == "deterministic"
    assert ready.json()["database"] == "ok"
    assert providers.status_code == 200
    assert providers.json()["active"]["name"] == "deterministic"
    assert providers.json()["available_modes"] == list(AVAILABLE_PROVIDER_MODES)
