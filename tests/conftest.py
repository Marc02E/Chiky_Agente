import os

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("AI_PROVIDER", "deterministic")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-long-enough-32-bytes")
os.environ.setdefault("JWT_REQUIRED", "false")


@pytest.fixture
def manual_routing_manager(monkeypatch):
    """Pin the app to MANUAL routing for this test.

    FASE AB.4/AB.5: under AUTOMATIC routing a provider failure triggers a
    *real* model fallback. Error-contract unit tests (provider raised ->
    user sees a clear error) must run in MANUAL mode, where dead providers
    are surfaced instead of silently switched. This isolates those tests
    from an initialized global ModelManager singleton that earlier
    integration tests may have created.
    """
    from personal_ai_secretary.providers.model_manager import ModelManager

    manager = ModelManager()
    manager.set_routing_mode("manual")
    monkeypatch.setattr(
        "personal_ai_secretary.providers.factory.get_model_manager",
        lambda: manager,
    )
    return manager
