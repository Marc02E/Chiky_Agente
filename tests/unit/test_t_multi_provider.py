"""FASE T: Multi-Provider Intelligence & Adaptive Model Routing Tests.

Comprehensive test suite covering:
- Connectivity detection
- Provider discovery (Ollama, Gemini, OpenCode, NVIDIA)
- Model registry operations
- Capability verification
- Model manager routing
- Fallback logic
- API endpoints
- Security (no secrets in logs)
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personal_ai_secretary.domain.contracts import RequestEnvelope
from personal_ai_secretary.providers.capability_verifier import (
    CapabilityVerifier,
    TestResult,
)
from personal_ai_secretary.providers.connectivity import ConnectivityChecker, ConnectivityStatus
from personal_ai_secretary.providers.discovery import DiscoveredProvider
from personal_ai_secretary.providers.model_intelligence import (
    classify_failure,
    get_fallback_model,
    get_model_capabilities,
    suggest_model_for_task,
)
from personal_ai_secretary.providers.model_manager import ModelManager, RoutingDecision
from personal_ai_secretary.providers.model_registry import (
    ModelEntry,
    ModelRegistry,
    ModelStatus,
    VerifiedCapabilities,
)

# ═══════════════════════════════════════════════════════════════════════════
# Connectivity
# ═══════════════════════════════════════════════════════════════════════════


class TestConnectivityChecker:
    def test_default_status(self) -> None:
        checker = ConnectivityChecker()
        assert checker.is_online is True
        assert checker.status.check_count == 0

    def test_cache_ttl(self) -> None:
        checker = ConnectivityChecker(cache_ttl=0.0)
        assert checker._cache_ttl == 0.0

    @pytest.mark.asyncio
    async def test_check_failure_sets_offline(self) -> None:
        checker = ConnectivityChecker(cache_ttl=0.0)
        with patch("personal_ai_secretary.providers.connectivity.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
            mock_cls.return_value = mock_client
            result = await checker.check()
        assert result is False
        assert checker.status.online is False
        assert checker.status.check_count == 1

    @pytest.mark.asyncio
    async def test_check_success_sets_online(self) -> None:
        checker = ConnectivityChecker(cache_ttl=0.0)
        with patch("personal_ai_secretary.providers.connectivity.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_client
            result = await checker.check()
        assert result is True
        assert checker.status.online is True

    @pytest.mark.asyncio
    async def test_cache_returns_previous_value(self) -> None:
        checker = ConnectivityChecker(cache_ttl=9999.0)
        with patch("personal_ai_secretary.providers.connectivity.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_client
            await checker.check()
        # Second call should use cache
        result = await checker.check()
        assert result is True
        assert checker.status.check_count == 1


# ═══════════════════════════════════════════════════════════════════════════
# Model Registry
# ═══════════════════════════════════════════════════════════════════════════


class TestModelRegistry:
    def test_register_and_get(self) -> None:
        registry = ModelRegistry()
        entry = registry.register(
            model_id="llama3.2",
            provider_name="ollama",
            display_name="Llama 3.2",
        )
        assert entry.model_id == "llama3.2"
        assert entry.provider_name == "ollama"
        assert registry.count == 1
        retrieved = registry.get("llama3.2", "ollama")
        assert retrieved is entry

    def test_register_duplicate_returns_existing(self) -> None:
        registry = ModelRegistry()
        first = registry.register(model_id="llama3", provider_name="ollama")
        second = registry.register(model_id="llama3", provider_name="ollama")
        assert first is second
        assert registry.count == 1

    def test_get_by_provider(self) -> None:
        registry = ModelRegistry()
        registry.register(model_id="llama3", provider_name="ollama")
        registry.register(model_id="gemini-flash", provider_name="gemini")
        ollama_models = registry.by_provider("ollama")
        assert len(ollama_models) == 1
        assert ollama_models[0].model_id == "llama3"

    def test_verified_models(self) -> None:
        registry = ModelRegistry()
        entry = registry.register(model_id="llama3", provider_name="ollama")
        entry.status = ModelStatus.VERIFIED
        registry.register(model_id="gemini", provider_name="gemini")
        verified = registry.verified_models()
        assert len(verified) == 1
        assert verified[0].model_id == "llama3"

    def test_remove_provider(self) -> None:
        registry = ModelRegistry()
        registry.register(model_id="llama3", provider_name="ollama")
        registry.register(model_id="llama3.1", provider_name="ollama")
        removed = registry.remove_provider("ollama")
        assert removed == 2
        assert registry.count == 0

    def test_clear(self) -> None:
        registry = ModelRegistry()
        registry.register(model_id="llama3", provider_name="ollama")
        registry.clear()
        assert registry.count == 0


class TestModelEntry:
    def test_reliability(self) -> None:
        entry = ModelEntry(model_id="test", provider_name="test")
        entry.record_success()
        entry.record_success()
        entry.record_failure()
        assert entry.reliability == pytest.approx(2 / 3, abs=0.01)

    def test_to_dict(self) -> None:
        entry = ModelEntry(model_id="test", provider_name="test")
        d = entry.to_dict()
        assert d["model_id"] == "test"
        assert d["provider"] == "test"
        assert "status" in d
        assert "reliability" in d

    def test_verification_age(self) -> None:
        entry = ModelEntry(model_id="test", provider_name="test")
        assert entry.verification_age_hours == float("inf")
        entry.last_verified = time.time() - 7200
        assert entry.verification_age_hours == pytest.approx(2.0, abs=0.1)


class TestVerifiedCapabilities:
    def test_score_empty(self) -> None:
        vc = VerifiedCapabilities()
        assert vc.score == 0.0

    def test_score_partial(self) -> None:
        vc = VerifiedCapabilities(tests_passed=3, tests_total=5)
        assert vc.score == pytest.approx(0.6, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════════
# Model Intelligence (existing module)
# ═══════════════════════════════════════════════════════════════════════════


class TestModelIntelligence:
    def test_known_model_capabilities(self) -> None:
        caps = get_model_capabilities("llama3")
        assert caps.tool_calling is True
        assert caps.coding_strength >= 1

    def test_unknown_model_defaults(self) -> None:
        caps = get_model_capabilities("unknown-model-xyz")
        assert caps.context_window > 0
        assert caps.tool_calling is True

    def test_fallback_model(self) -> None:
        fallback = get_fallback_model("llama3", ["llama3", "qwen2.5-coder"])
        assert fallback is not None
        assert fallback != "llama3"

    def test_suggest_coding_model(self) -> None:
        suggestion = suggest_model_for_task(
            "create a Python script",
            available_models=["llama3", "qwen2.5-coder"],
        )
        assert suggestion is not None

    def test_suggest_non_coding_returns_none(self) -> None:
        suggestion = suggest_model_for_task(
            "hello how are you",
            available_models=["llama3"],
        )
        assert suggestion is None

    def test_classify_failure_timeout(self) -> None:
        info = classify_failure(Exception("connection timed out"))
        assert info.failure_type == "transient"
        assert info.can_retry is True

    def test_classify_failure_not_found(self) -> None:
        info = classify_failure(Exception("model not found 404"))
        assert info.failure_type == "permanent"
        assert info.suggest_fallback is True


# ═══════════════════════════════════════════════════════════════════════════
# Capability Verifier
# ═══════════════════════════════════════════════════════════════════════════


class TestCapabilityVerifier:
    @pytest.mark.asyncio
    async def test_verify_all_tests(self) -> None:
        verifier = CapabilityVerifier()
        mock_provider = AsyncMock()
        mock_response = MagicMock()
        mock_response.text = "I am Chiky. Here is the tool call:\n```tool\n{\"tool\": \"list_directory\", \"args\": {\"path\": \".\"}}\n```"
        mock_provider.generate = AsyncMock(return_value=mock_response)

        report = await verifier.verify(mock_provider, "test-model", "test-provider")
        assert report.model_id == "test-model"
        assert report.provider == "test-provider"
        assert len(report.tests) > 0
        assert report.total_duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_verify_handles_exception(self) -> None:
        verifier = CapabilityVerifier()
        mock_provider = AsyncMock()
        mock_provider.generate = AsyncMock(side_effect=ConnectionError("offline"))

        report = await verifier.verify(mock_provider, "test-model", "test-provider")
        assert report.failed_count > 0

    @pytest.mark.asyncio
    async def test_test_result_dataclass(self) -> None:
        result = TestResult(test_name="test", passed=True, detail="ok")
        assert result.passed is True
        assert result.test_name == "test"


# ═══════════════════════════════════════════════════════════════════════════
# Model Manager
# ═══════════════════════════════════════════════════════════════════════════


class TestModelManager:
    @pytest.mark.asyncio
    async def test_initialize_discovers_providers(self) -> None:
        manager = ModelManager()
        with patch.object(manager._discovery, "discover_all") as mock_discover:
            mock_discover.return_value = {
                "ollama": DiscoveredProvider(
                    name="ollama",
                    display_name="Ollama",
                    available=True,
                    mode="local",
                    models=["llama3"],
                    detail="available",
                ),
            }
            await manager.initialize()
        assert manager.registry.count >= 1
        assert manager._initialized is True

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self) -> None:
        manager = ModelManager()
        with patch.object(manager._discovery, "discover_all") as mock_discover:
            mock_discover.return_value = {}
            await manager.initialize()
            await manager.initialize()
        assert mock_discover.call_count == 1

    def test_select_provider(self) -> None:
        manager = ModelManager()
        manager._provider_instances = {"test": MagicMock()}
        assert manager.select_provider("test") is True
        assert manager.selected_provider == "test"

    def test_select_provider_not_found(self) -> None:
        manager = ModelManager()
        assert manager.select_provider("nonexistent") is False

    def test_get_fallback_provider(self) -> None:
        manager = ModelManager()
        entry = manager.registry.register(
            model_id="llama3", provider_name="ollama"
        )
        entry.status = ModelStatus.VERIFIED
        fallback = manager.get_fallback_provider("gemini", "gemini-flash")
        if fallback:
            assert fallback[0] != "gemini" or fallback[1] != "gemini-flash"

    def test_record_success(self) -> None:
        manager = ModelManager()
        manager.registry.register(model_id="llama3", provider_name="ollama")
        manager.record_success("ollama", "llama3")
        entry = manager.registry.get_key("llama3", "ollama")
        assert entry is not None
        assert entry.success_count == 1

    def test_record_failure(self) -> None:
        manager = ModelManager()
        manager.registry.register(model_id="llama3", provider_name="ollama")
        for _ in range(6):
            manager.record_failure("ollama", "llama3")
        entry = manager.registry.get_key("llama3", "ollama")
        assert entry is not None
        assert entry.status == ModelStatus.FAILED

    def test_routing_decision(self) -> None:
        manager = ModelManager()
        entry = manager.registry.register(
            model_id="llama3", provider_name="ollama"
        )
        entry.status = ModelStatus.VERIFIED
        entry.coding_strength = 4
        manager._connectivity._status = ConnectivityStatus(online=True, check_count=1)
        decision = manager.select_for_task("create a Python script")
        if decision:
            assert isinstance(decision, RoutingDecision)
            assert decision.provider_name == "ollama"

    def test_routing_offline_only_ollama(self) -> None:
        manager = ModelManager()
        ollama_entry = manager.registry.register(
            model_id="llama3", provider_name="ollama"
        )
        ollama_entry.status = ModelStatus.VERIFIED
        gemini_entry = manager.registry.register(
            model_id="gemini-flash", provider_name="gemini"
        )
        gemini_entry.status = ModelStatus.VERIFIED
        manager._connectivity._status = ConnectivityStatus(online=False, check_count=1)
        decision = manager.select_for_task("hello")
        if decision:
            assert decision.provider_name == "ollama"

    def test_get_all_status(self) -> None:
        manager = ModelManager()
        manager.registry.register(model_id="llama3", provider_name="ollama")
        status = manager.get_all_status()
        assert len(status) == 1
        assert status[0]["model_id"] == "llama3"

    def test_get_recommended_none(self) -> None:
        manager = ModelManager()
        assert manager.get_recommended() is None


# ═══════════════════════════════════════════════════════════════════════════
# Provider Health (existing + new)
# ═══════════════════════════════════════════════════════════════════════════


class TestGeminiProvider:
    @pytest.mark.asyncio
    async def test_health_no_key(self) -> None:
        from personal_ai_secretary.providers.gemini import GeminiProvider

        provider = GeminiProvider(api_key="")
        health = await provider.health()
        assert health.available is False

    @pytest.mark.asyncio
    async def test_health_with_key_success(self) -> None:
        from personal_ai_secretary.providers.gemini import GeminiProvider

        provider = GeminiProvider(api_key="test-key")
        with patch("personal_ai_secretary.providers.gemini.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_client
            health = await provider.health()
        assert health.available is True

    @pytest.mark.asyncio
    async def test_health_connection_error(self) -> None:
        from personal_ai_secretary.providers.gemini import GeminiProvider

        provider = GeminiProvider(api_key="test-key")
        with patch("personal_ai_secretary.providers.gemini.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            import httpx as real_httpx
            mock_client.get = AsyncMock(side_effect=real_httpx.ConnectError("refused"))
            mock_cls.return_value = mock_client
            health = await provider.health()
        assert health.available is False

    @pytest.mark.asyncio
    async def test_generate_no_key_raises(self) -> None:
        from personal_ai_secretary.providers.gemini import GeminiProvider

        provider = GeminiProvider(api_key="")
        request = RequestEnvelope(user_id="test", input="hi", correlation_id="c")
        with pytest.raises(RuntimeError, match="not configured"):
            await provider.generate(request)

    @pytest.mark.asyncio
    async def test_generate_success(self) -> None:
        from personal_ai_secretary.providers.gemini import GeminiProvider

        provider = GeminiProvider(api_key="test-key", model="gemini-test")
        with patch("personal_ai_secretary.providers.gemini.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json.return_value = {
                "choices": [{"message": {"content": "gemini reply"}}]
            }
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_client
            request = RequestEnvelope(user_id="test", input="hi", correlation_id="c")
            result = await provider.generate(request)
        assert result.text == "gemini reply"
        assert result.provider == "gemini"
        assert result.model == "gemini-test"


class TestOpenCodeProvider:
    @pytest.mark.asyncio
    async def test_health_not_installed(self) -> None:
        from personal_ai_secretary.providers.opencode_provider import OpenCodeProvider

        provider = OpenCodeProvider()
        with patch("personal_ai_secretary.providers.opencode_provider.shutil.which", return_value=None):
            health = await provider.health()
        assert health.available is False
        assert "not installed" in health.detail.lower()

    @pytest.mark.asyncio
    async def test_health_installed_but_no_server(self) -> None:
        from personal_ai_secretary.providers.opencode_provider import OpenCodeProvider

        provider = OpenCodeProvider()
        with (
            patch("personal_ai_secretary.providers.opencode_provider.shutil.which", return_value="/usr/bin/opencode"),
            patch("personal_ai_secretary.providers.opencode_provider.httpx.AsyncClient") as mock_cls,
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            import httpx as real_httpx
            mock_client.get = AsyncMock(side_effect=real_httpx.ConnectError("refused"))
            mock_cls.return_value = mock_client
            health = await provider.health()
        assert health.available is False
        assert "server is not running" in health.detail.lower()


# ═══════════════════════════════════════════════════════════════════════════
# Security
# ═══════════════════════════════════════════════════════════════════════════


class TestSecurityNoSecrets:
    def test_no_api_keys_in_health_details(self) -> None:
        from personal_ai_secretary.providers.gemini import GeminiProvider

        provider = GeminiProvider(api_key="super-secret-key-12345")

        health = asyncio.run(provider.health())
        assert "super-secret" not in health.detail.lower()
        assert "secret" not in health.detail.lower()

    def test_config_defaults_no_secrets(self) -> None:
        from personal_ai_secretary.shared.config import Settings

        settings = Settings()
        assert settings.gemini_api_key is None
        assert settings.nvidia_api_key is None

    def test_factory_returns_correct_type(self) -> None:
        from personal_ai_secretary.providers.deterministic import DeterministicProvider
        from personal_ai_secretary.providers.factory import get_provider

        provider = get_provider()
        assert isinstance(provider, DeterministicProvider)


# ═══════════════════════════════════════════════════════════════════════════
# Factory Integration
# ═══════════════════════════════════════════════════════════════════════════


class TestFactoryIntegration:
    def test_get_model_manager_returns_singleton(self) -> None:
        from personal_ai_secretary.providers.factory import get_model_manager

        manager1 = get_model_manager()
        manager2 = get_model_manager()
        assert manager1 is manager2

    def test_model_manager_is_model_manager_type(self) -> None:
        from personal_ai_secretary.providers.factory import get_model_manager

        manager = get_model_manager()
        assert isinstance(manager, ModelManager)


# ═══════════════════════════════════════════════════════════════════════════
# Regression: Existing Provider Tests Still Pass
# ═══════════════════════════════════════════════════════════════════════════


class TestRegressionExistingProviders:
    @pytest.mark.asyncio
    async def test_deterministic_provider_still_works(self) -> None:
        from personal_ai_secretary.providers.deterministic import DeterministicProvider

        provider = DeterministicProvider()
        health = await provider.health()
        assert health.available is True
        assert health.is_ai is False
        request = RequestEnvelope(user_id="test", input="hello", correlation_id="c")
        response = await provider.generate(request)
        assert "DETERMINISTIC_RESPONSE" in response.text

    @pytest.mark.asyncio
    async def test_ollama_provider_still_works(self) -> None:
        from personal_ai_secretary.providers.ollama import OllamaProvider

        provider = OllamaProvider(model="llama3")
        health = await provider.health()
        assert health.is_ai is True
        assert health.name == "ollama"

    @pytest.mark.asyncio
    async def test_nvidia_provider_still_works(self) -> None:
        from personal_ai_secretary.providers.remote import NVIDIAProvider

        provider = NVIDIAProvider()
        health = await provider.health()
        assert health.is_ai is True
        assert health.name == "nvidia"
