"""Tests for Ollama model selection: provider, factory, and API."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from personal_ai_secretary.domain.contracts import RequestEnvelope
from personal_ai_secretary.providers.factory import (
    get_current_model,
    get_provider,
    set_current_model,
)
from personal_ai_secretary.providers.ollama import OllamaProvider

# ---------------------------------------------------------------------------
# OllamaProvider: constructor and model
# ---------------------------------------------------------------------------


class TestOllamaProviderModel:
    def test_default_model_from_settings(self) -> None:
        provider = OllamaProvider()
        assert provider.model is not None
        assert isinstance(provider.model, str)

    def test_custom_model(self) -> None:
        provider = OllamaProvider(model="deepseek-coder-v2:latest")
        assert provider.model == "deepseek-coder-v2:latest"

    def test_empty_string_model_falls_back_to_settings(self) -> None:
        provider = OllamaProvider(model="")
        assert provider.model is not None
        assert len(provider.model) > 0

    def test_provider_name(self) -> None:
        provider = OllamaProvider(model="test-model")
        assert provider.name == "ollama"


# ---------------------------------------------------------------------------
# OllamaProvider: health uses self.model
# ---------------------------------------------------------------------------


class TestOllamaProviderHealth:
    @pytest.mark.asyncio
    async def test_health_checks_self_model_not_settings(self) -> None:
        captured_urls: list[str] = []

        class FakeClient:
            async def __aenter__(self):  # noqa: ANN204
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def get(self, url: str) -> httpx.Response:
                captured_urls.append(url)
                return httpx.Response(
                    200,
                    request=httpx.Request("GET", url),
                    json={"models": [{"name": "my-custom-model"}]},
                )

        with patch("httpx.AsyncClient", side_effect=lambda **kwargs: FakeClient()):
            provider = OllamaProvider(model="my-custom-model")
            health = await provider.health()

        assert health.available is True
        assert "my-custom-model" in health.detail

    @pytest.mark.asyncio
    async def test_health_reports_unavailable_when_model_missing(self) -> None:
        class FakeClient:
            async def __aenter__(self):  # noqa: ANN204
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def get(self, url: str) -> httpx.Response:
                return httpx.Response(
                    200,
                    request=httpx.Request("GET", url),
                    json={"models": [{"name": "other-model"}]},
                )

        with patch("httpx.AsyncClient", side_effect=lambda **kwargs: FakeClient()):
            provider = OllamaProvider(model="my-custom-model")
            health = await provider.health()

        assert health.available is False
        assert "unavailable" in health.detail.lower()

    @pytest.mark.asyncio
    async def test_health_returns_false_when_server_down(self) -> None:
        provider = OllamaProvider(model="any")
        # Use an unreachable URL
        provider._base_url = "http://127.0.0.1:1"
        health = await provider.health()
        assert health.available is False
        assert health.is_ai is True


# ---------------------------------------------------------------------------
# OllamaProvider: generate uses self.model
# ---------------------------------------------------------------------------


class TestOllamaProviderGenerate:
    @pytest.mark.asyncio
    async def test_generate_uses_custom_model(self) -> None:
        captured_payload: dict[str, object] = {}

        class FakeClient:
            async def __aenter__(self):  # noqa: ANN204
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def post(
                self, url: str, json: dict[str, object], headers: dict[str, str] | None = None
            ) -> httpx.Response:
                captured_payload.update(json)
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", url),
                    json={"message": {"content": "custom model answer"}},
                )

        with patch("httpx.AsyncClient", side_effect=lambda **kwargs: FakeClient()):
            provider = OllamaProvider(model="deepseek-coder-v2:latest")
            request = RequestEnvelope(user_id="test", input="hello", correlation_id="c1")
            result = await provider.generate(request)

        assert result.text == "custom model answer"
        assert result.model == "deepseek-coder-v2:latest"
        assert captured_payload.get("model") == "deepseek-coder-v2:latest"

    @pytest.mark.asyncio
    async def test_generate_passes_conversation_history(self) -> None:
        captured_messages: list[dict[str, str]] = []

        class FakeClient:
            async def __aenter__(self):  # noqa: ANN204
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def post(
                self, url: str, json: dict[str, object], headers: dict[str, str] | None = None
            ) -> httpx.Response:
                captured_messages.extend(json.get("messages", []))
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", url),
                    json={"message": {"content": "ok"}},
                )

        from personal_ai_secretary.domain.contracts import ConversationTurn

        with patch("httpx.AsyncClient", side_effect=lambda **kwargs: FakeClient()):
            provider = OllamaProvider(model="test-model")
            request = RequestEnvelope(
                user_id="test",
                input="follow up",
                correlation_id="c1",
                messages=[
                    ConversationTurn(role="user", content="first message"),
                    ConversationTurn(role="assistant", content="first reply"),
                ],
                context_summary="Memory: prefers short answers",
            )
            await provider.generate(request)

        assert len(captured_messages) == 4
        assert captured_messages[0] == {"role": "system", "content": "Memory: prefers short answers"}
        assert captured_messages[1] == {"role": "user", "content": "first message"}
        assert captured_messages[2] == {"role": "assistant", "content": "first reply"}
        assert captured_messages[3] == {"role": "user", "content": "follow up"}


# ---------------------------------------------------------------------------
# OllamaProvider: list_models
# ---------------------------------------------------------------------------


class TestOllamaProviderListModels:
    @pytest.mark.asyncio
    async def test_list_models_returns_sorted_names(self) -> None:
        class FakeClient:
            async def __aenter__(self):  # noqa: ANN204
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def get(self, url: str) -> httpx.Response:
                return httpx.Response(
                    200,
                    request=httpx.Request("GET", url),
                    json={
                        "models": [
                            {"name": "llama3:latest"},
                            {"name": "deepseek-coder-v2:latest"},
                            {"name": "llama3.1:latest"},
                        ]
                    },
                )

        with patch("httpx.AsyncClient", side_effect=lambda **kwargs: FakeClient()):
            provider = OllamaProvider()
            models = await provider.list_models()

        assert models == ["deepseek-coder-v2:latest", "llama3.1:latest", "llama3:latest"]

    @pytest.mark.asyncio
    async def test_list_models_returns_empty_on_error(self) -> None:
        provider = OllamaProvider(model="any")
        provider._base_url = "http://127.0.0.1:1"
        models = await provider.list_models()
        assert models == []

    @pytest.mark.asyncio
    async def test_list_models_filters_empty_names(self) -> None:
        class FakeClient:
            async def __aenter__(self):  # noqa: ANN204
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def get(self, url: str) -> httpx.Response:
                return httpx.Response(
                    200,
                    request=httpx.Request("GET", url),
                    json={"models": [{"name": "valid-model"}, {"name": ""}]},
                )

        with patch("httpx.AsyncClient", side_effect=lambda **kwargs: FakeClient()):
            provider = OllamaProvider()
            models = await provider.list_models()

        assert models == ["valid-model"]


# ---------------------------------------------------------------------------
# Factory: global model store
# ---------------------------------------------------------------------------


class TestFactoryModelStore:
    def setup_method(self) -> None:
        set_current_model("llama3.1:latest")

    def test_get_current_model_returns_set_value(self) -> None:
        set_current_model("deepseek-coder-v2:latest")
        assert get_current_model() == "deepseek-coder-v2:latest"

    def test_set_current_model_overwrites(self) -> None:
        set_current_model("model-a")
        assert get_current_model() == "model-a"
        set_current_model("model-b")
        assert get_current_model() == "model-b"

    def test_get_provider_uses_current_model(self) -> None:
        set_current_model("deepseek-coder-v2:latest")
        with patch("personal_ai_secretary.providers.factory.get_settings") as mock_settings:
            mock_settings.return_value.ai_provider = "local"
            provider = get_provider()
        assert isinstance(provider, OllamaProvider)
        assert provider.model == "deepseek-coder-v2:latest"

    def test_get_provider_falls_back_to_settings_when_no_model_set(self) -> None:
        set_current_model("llama3.1:latest")
        with patch("personal_ai_secretary.providers.factory.get_settings") as mock_settings:
            mock_settings.return_value.ai_provider = "local"
            provider = get_provider()
        assert isinstance(provider, OllamaProvider)
        assert provider.model is not None

    def test_factory_returns_deterministic_when_configured(self) -> None:
        from personal_ai_secretary.providers.deterministic import DeterministicProvider

        with patch("personal_ai_secretary.providers.factory.get_settings") as mock_settings:
            mock_settings.return_value.ai_provider = "deterministic"
            provider = get_provider()
        assert isinstance(provider, DeterministicProvider)
