"""FASE AB.4: the auto-fallback chain must surface honestly in the API.

Proves the hard requirement end-to-end through the real FastAPI app:

  requested -> attempted -> fallback -> executed

When AUTO routing selects a provider whose generation raises a model-level
error (RuntimeError), the backend must switch provider/model and the
SendMessageResponse must register fallback_active=true with the full
requested/attempted/fallback/executed chain — never a silent swap and never
an invented fallback flag.

These tests are deterministic: they patch the ModelManager singleton so no
live Ollama/OpenCode server is required. The poisoned provider only fails at
generate time (model-level), exercising the RuntimeError gate and the
cumulative blocked-provider fallback logic.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import patch

from fastapi.testclient import TestClient

from personal_ai_secretary.api.app import app
from personal_ai_secretary.domain.contracts import ProviderResponse, RequestEnvelope
from personal_ai_secretary.providers.model_manager import ModelManager


class _PoisonedProvider:
    name = "poisoned"
    model = "model-model-a"

    async def health(self):  # type: ignore[no-untyped-def]
        from personal_ai_secretary.domain.contracts import ProviderInfo

        return ProviderInfo(
            name=self.name,
            mode="remote",
            available=True,
            is_ai=True,
            detail="poisoned test provider",
        )

    async def generate(self, request: RequestEnvelope) -> ProviderResponse:
        raise RuntimeError("empty response from model-model-a")


class _HealthyProvider:
    name = "healthy"
    model = "model-model-b"

    async def health(self):  # type: ignore[no-untyped-def]
        from personal_ai_secretary.domain.contracts import ProviderInfo

        return ProviderInfo(
            name=self.name,
            mode="remote",
            available=True,
            is_ai=True,
            detail="healthy test provider",
        )

    async def generate(self, request: RequestEnvelope) -> ProviderResponse:
        return ProviderResponse(text="FALLBACK_OK:" + request.input, provider=self.name)


class _NoopRegistry:
    def get_key(self, model_id: str, provider_name: str):  # type: ignore[no-untyped-def]
        return None


class _FakeManager(ModelManager):
    _initialized: bool = True
    _routing_mode: str = "auto"
    _selected_provider: str | None = "poisoned"
    _selected_model: str | None = "model-model-a"

    def __init__(self) -> None:
        self._poisoned = _PoisonedProvider()
        self._healthy = _HealthyProvider()
        self._registry = _NoopRegistry()

    @property
    def routing_mode(self) -> str:
        return self._routing_mode

    @property
    def registry(self) -> Any:
        return cast(Any, self._registry)

    def get_provider_instance(self, name: str | None = None) -> object | None:
        if name == "healthy":
            return self._healthy
        if name == "poisoned" or name is None:
            return self._poisoned
        return None

    def get_fallback_provider(
        self, failed_provider: str, failed_model: str, blocked_providers: set[str] | None = None
    ) -> tuple[str, str] | None:
        if blocked_providers is None:
            blocked_providers = set()
        blocked_providers.add(failed_provider)
        if "healthy" in blocked_providers:
            return None
        return ("healthy", "model-model-b")

    def get_all_status(self) -> list[dict[str, object]]:  # pragma: no cover
        return []


def test_auto_fallback_chain_surfaces_in_response() -> None:
    """AUTO routing + model-level RuntimeError must register the fallback chain."""
    fake = _FakeManager()
    with TestClient(app) as client, patch(
        "personal_ai_secretary.api.app.get_model_manager", return_value=fake
    ), patch(
        "personal_ai_secretary.providers.factory.get_model_manager", return_value=fake
    ):
        resp = client.post(
            "/api/v1/sessions/00000000-0000-0000-0000-000000000001/messages",
            headers={"Authorization": "Bearer test-token"},
            json={"input": "hello fallback"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert "FALLBACK_OK" in body["assistant_message"]["content"]

    fb = body["fallback_info"]
    assert fb is not None, "fallback_info must be populated when a fallback occurred"
    assert fb["fallback_active"] is True
    # requested -> the AUTO-selected provider/model
    assert fb["requested_provider"] == "poisoned"
    assert fb["requested_model"] == "model-model-a"
    # attempted -> the provider that was actually tried and failed
    assert fb["attempted_provider"] == "poisoned"
    assert fb["attempted_model"] == "model-model-a"
    # fallback -> where we moved from
    assert fb["fallback_from"] == "poisoned"
    assert fb["fallback_from_model"] == "model-model-a"
    assert fb["fallback_model"] == "model-model-b"
    # executed -> the model that finally produced the answer
    assert fb["executed_provider"] == "healthy"
    assert fb["executed_model"] == "model-model-b"


def test_manual_mode_never_fabricates_fallback() -> None:
    """MANUAL routing must report an error, not a fallback chain."""
    fake = _FakeManager()
    fake._routing_mode = "manual"
    with TestClient(app) as client, patch(
        "personal_ai_secretary.api.app.get_model_manager", return_value=fake
    ), patch(
        "personal_ai_secretary.providers.factory.get_model_manager", return_value=fake
    ):
        resp = client.post(
            "/api/v1/sessions/00000000-0000-0000-0000-000000000002/messages",
            headers={"Authorization": "Bearer test-token"},
            json={"input": "never fallback"},
        )
    assert resp.status_code == 200
    body = resp.json()
    # FASE AB.6: the provenance contract now ALSO reports the honest manual
    # failure — explicitly "no fallback" instead of disappearing. The user
    # must never see a silent provider switch.
    fb = body["fallback_info"]
    assert fb is not None, "provenance must be present even for manual failures"
    assert fb["fallback_active"] is False
    assert fb["fallback_chain"] == []
    assert fb["requested_provider"] == "poisoned"
    assert fb["executed_provider"] == "poisoned"
    assert "no automatic fallback is allowed" in (body["assistant_message"]["content"] or "")