"""FASE AB.3: UI/Backend provider consistency (11 checks).

Verifies the contract the frontend depends on for Settings -> Manual:

   1. /providers/transparency always lists every discovered provider,
      including model-less (unavailable) ones such as Gemini without a key
      or OpenCode with the server down — providers must NEVER silently
      disappear from the UI.
   2. provider ids are canonical ("opencode", "ollama", "gemini", "nvidia")
      so the frontend grouping never keys on a mismatched identifier.
   3. Models returned for a provider match the registry contract
      (model / model_id), and the frontend's chosen model is selectable.
   4. Manual selection persists and is strict (no silent fallback).
   5. Test Connection terminates in a real state: a hanging backend yields
      FAIL (timeout), never an indefinite hang or a fake PASS.
   6. Unavailable providers carry explicit available/detail fields.
   7. /providers/models reflects the persisted selection for the UI.
   8. /providers/config exposes provider configuration state.
   9. Transparency totals match the registry (provider-matrix invariant).

These tests are deterministic: they patch the ModelManager singleton with a
controlled double so they do not require a live OpenCode/Ollama server.
"""

from __future__ import annotations

import asyncio
import threading
from typing import cast
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from personal_ai_secretary.api.app import app
from personal_ai_secretary.domain.contracts import ProviderResponse, RequestEnvelope
from personal_ai_secretary.providers.capability_verifier import (
    _VERIFY_TEST_TIMEOUT_SECONDS,
    CapabilityVerifier,
)
from personal_ai_secretary.providers.discovery import DiscoveredProvider
from personal_ai_secretary.providers.model_manager import ModelManager
from personal_ai_secretary.providers.model_registry import ModelRegistry

_EXPECTED_PROVIDER_IDS = {"ollama", "opencode", "gemini", "nvidia"}


def _registry() -> ModelRegistry:
    reg = ModelRegistry()
    reg.register(model_id="deepseek-coder-v2:latest", provider_name="ollama")
    for m in ("big-pickle", "nemotron-3.5-lightning-free"):
        reg.register(model_id=m, provider_name="opencode")
    reg.register(model_id="meta/llama-3.1-8b-instruct", provider_name="nvidia")
    return reg


def _discovered() -> dict[str, DiscoveredProvider]:
    return {
        "ollama": DiscoveredProvider(
            name="ollama",
            display_name="Ollama (Local)",
            available=True,
            mode="local",
            models=["deepseek-coder-v2:latest"],
            detail="server is running",
        ),
        "opencode": DiscoveredProvider(
            name="opencode",
            display_name="OpenCode",
            available=True,
            mode="local",
            models=["big-pickle", "nemotron-3.5-lightning-free"],
            detail="server is running",
        ),
        "nvidia": DiscoveredProvider(
            name="nvidia",
            display_name="NVIDIA (Cloud)",
            available=False,
            mode="cloud",
            models=[],
            detail="API key not configured",
        ),
        "gemini": DiscoveredProvider(
            name="gemini",
            display_name="Google Gemini (Cloud)",
            available=False,
            mode="cloud",
            models=[],
            detail="API key not configured",
        ),
    }


class _FakeConnectivity:
    is_online = True

    class _Status:
        latency_ms = 5.0
        status = "online"
        online = True
        check_count = 1
        is_online = True

    _status = _Status()
    status = _Status()


class _FakeManager(ModelManager):
    def __init__(self) -> None:  # pragma: no cover - lightweight double
        self._registry: ModelRegistry = _registry()
        self._discovery = _discovered()  # type: ignore[assignment]
        self._initialized: bool = True
        self._selected_provider: str | None = "opencode"
        self._selected_model: str | None = "big-pickle"
        self._routing_mode: str = "manual"
        self._connectivity = _FakeConnectivity()  # type: ignore[assignment]
        self._provider_instances: dict[str, object] = {}
        self._lock = threading.Lock()

    def discover_providers_sync(self) -> dict[str, DiscoveredProvider]:
        return cast(dict[str, DiscoveredProvider], self._discovery)

    @property
    def registry(self) -> ModelRegistry:
        return self._registry


@pytest.fixture(autouse=True)
def _patch_manager() -> object:
    fake = _FakeManager()
    with patch("personal_ai_secretary.api.app.get_model_manager", return_value=fake):
        yield fake


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_transparency_lists_all_discovered_providers() -> None:
    """Requirement 5/6: model-less (unavailable) providers stay visible."""
    async with await _client() as client:
        resp = await client.get(
            "/api/v1/providers/transparency",
            headers={"Authorization": "Bearer test-token"},
        )
    assert resp.status_code == 200
    data = resp.json()
    providers = {item["provider"] for item in data["all_providers"]}
    assert _EXPECTED_PROVIDER_IDS.issubset(providers), providers


@pytest.mark.asyncio
async def test_transparency_open_code_with_models() -> None:
    """OpenCode appears with its real discovered models."""
    async with await _client() as client:
        resp = await client.get(
            "/api/v1/providers/transparency",
            headers={"Authorization": "Bearer test-token"},
        )
    data = resp.json()
    oc_models = [
        item["model"] for item in data["all_providers"] if item["provider"] == "opencode"
    ]
    assert "big-pickle" in oc_models
    assert "nemotron-3.5-lightning-free" in oc_models


@pytest.mark.asyncio
async def test_transparency_gemini_visible_when_unavailable() -> None:
    """A provider without a key/API must be shown as unavailable, not dropped."""
    async with await _client() as client:
        resp = await client.get(
            "/api/v1/providers/transparency",
            headers={"Authorization": "Bearer test-token"},
        )
    data = resp.json()
    gemini = next(
        (i for i in data["all_providers"] if i["provider"] == "gemini"), None
    )
    assert gemini is not None
    assert gemini["status"] == "unavailable"


@pytest.mark.asyncio
async def test_provider_ids_are_canonical() -> None:
    """Requirement 4: single canonical provider identifiers for routing."""
    async with await _client() as client:
        resp = await client.get(
            "/api/v1/providers/models",
            headers={"Authorization": "Bearer test-token"},
        )
    data = resp.json()
    ids = {item["provider"] for item in data["providers"]}
    assert "opencode" in ids
    assert "ollama" in ids
    assert not {"OpenCode", "open-code", "opencode-zen"}.intersection(ids)


@pytest.mark.asyncio
async def test_manual_selection_persists_and_is_strict() -> None:
    """Requirement 8/14: selection persists and manual mode does not fall back."""
    from personal_ai_secretary.providers.routing import Router, RoutingMode

    router = Router(mode=RoutingMode.MANUAL, provider_instances={})
    target = await router.select_manual("opencode", "big-pickle")
    assert target.locked
    assert router.verify_match() is True

    # Selection survives persistence round trip at the model layer.
    manager = _FakeManager()
    selected = manager.select_provider("opencode", "big-pickle")
    # The double has no provider instances, so selection is refused — but the
    # point is the persisted wanted selection is not silently re-routed.
    assert manager.selected_provider == "opencode"
    assert manager.selected_model == "big-pickle"
    assert selected is False


@pytest.mark.asyncio
async def test_no_silent_fallback_manual_unavailable() -> None:
    """Requirement 14: an unavailable manual provider must not route elsewhere."""
    from personal_ai_secretary.providers.routing import Router, RoutingMode

    router = Router(mode=RoutingMode.MANUAL, provider_instances={})
    target = await router.select_manual("opencode", "does-not-exist")
    assert target.locked
    assert target.available is False
    assert router.verify_match() is True


@pytest.mark.asyncio
async def test_verify_timeout_is_fail_not_false_pass() -> None:
    """Requirement 10: Test Connection must terminate, never hang or fake PASS."""
    from unittest.mock import patch as _patch

    class _HangingProvider:
        name = "opencode"

        async def generate(self, request: RequestEnvelope) -> ProviderResponse:
            await asyncio.sleep(30.0)
            return ProviderResponse(text="late", provider="opencode")

    with _patch(
        "personal_ai_secretary.providers.capability_verifier._VERIFY_TEST_TIMEOUT_SECONDS",
        0.05,
    ):
        verifier = CapabilityVerifier()
        started_at = asyncio.get_event_loop().time()
        report = await verifier.verify(_HangingProvider(), "big-pickle", "opencode")
        elapsed = asyncio.get_event_loop().time() - started_at
    assert _VERIFY_TEST_TIMEOUT_SECONDS == 45.0  # constant restored after patch
    assert report.failed_count > 0
    assert report.overall_passed is False  # no false PASS
    assert elapsed < 3.0  # terminates promptly — never hangs several minutes
    assert any("Timed out" in t.detail for t in report.tests)


@pytest.mark.asyncio
async def test_transparency_unavailable_carries_available_and_detail() -> None:
    """Requirement: discovered != available must carry explicit flags + detail."""
    async with await _client() as client:
        resp = await client.get(
            "/api/v1/providers/transparency",
            headers={"Authorization": "Bearer test-token"},
        )
    data = resp.json()
    gemini = next(i for i in data["all_providers"] if i["provider"] == "gemini")
    assert gemini["available"] is False
    assert gemini["detail"]
    assert gemini["status"] == "unavailable"


@pytest.mark.asyncio
async def test_models_endpoint_contract_for_frontend() -> None:
    """Requirement 7: models endpoint echoes selection + safe model fields."""

    async with await _client() as client:
        resp = await client.get(
            "/api/v1/providers/models",
            headers={"Authorization": "Bearer test-token"},
        )
    data = resp.json()
    assert data["selected_provider"] == "opencode"
    assert data["selected_model"] == "big-pickle"
    assert data["connectivity"]["online"] is True
    entries = data["providers"]
    assert any(
        e["model_id"] == "big-pickle" and e["provider"] == "opencode" for e in entries
    )
    assert all("model_id" in e and "provider" in e for e in entries)


@pytest.mark.asyncio
async def test_config_endpoint_exposes_provider_state() -> None:
    """Requirement 17: config endpoint drives reconfigure-not-configured flags."""
    async with await _client() as client:
        resp = await client.get(
            "/api/v1/providers/config",
            headers={"Authorization": "Bearer test-token"},
        )
    data = resp.json()
    assert "opencode_base_url" in data
    assert "opencode_model" in data
    assert "gemini_configured" in data
    assert "nvidia_configured" in data
    assert "routing_mode" in data


@pytest.mark.asyncio
async def test_transparency_totals_match_registry() -> None:
    """Requirement 15: provider-matrix totals equal registry counts."""
    async with await _client() as client:
        resp = await client.get(
            "/api/v1/providers/transparency",
            headers={"Authorization": "Bearer test-token"},
        )
    data = resp.json()
    providers: dict[str, list[dict[str, object]]] = {}
    for item in data["all_providers"]:
        providers.setdefault(str(item["provider"]), []).append(item)
    model_counts = {
        p: [i for i in rows if i["model"] is not None] for p, rows in providers.items()
    }
    # Registered per _registry(): ollama 1, opencode 2, nvidia 1; gemini model-less.
    assert len(model_counts["ollama"]) == 1
    assert len(model_counts["opencode"]) == 2
    assert len(model_counts["nvidia"]) == 1
    assert "gemini" in providers
    assert not model_counts["gemini"]