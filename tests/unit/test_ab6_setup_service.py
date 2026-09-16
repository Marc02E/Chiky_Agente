"""FASE AB.6 — Setup-service tests.

These tests prove the state MAPPING logic of :mod:`setup` (real HTTP calls are
replaced by a fake async client that record which endpoint was hit). They
assert the security-relevant property of FASE AB.6: a provider is never
AVAILABLE without a live answer, and 401 is never reported as AVAILABLE.
"""

from __future__ import annotations

import pytest

from personal_ai_secretary.providers import setup


class FakeResponse:
    def __init__(self, status_code: int = 200, json_data: dict | None = None) -> None:
        self.status_code = status_code
        self._json = json_data or {}

    def json(self) -> dict:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            request = httpx.Request("GET", "http://fake")
            raise httpx.HTTPStatusError(
                "error", request=request, response=httpx.Response(self.status_code)
            )


class FakeClient:
    """Async httpx client that records calls and returns canned responses."""

    responses: dict[str, FakeResponse] = {}
    calls: list[tuple[str, str]] = []

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        pass

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, *exc) -> None:  # noqa: ANN002
        return None

    async def get(self, url: str, **kwargs) -> FakeResponse:  # noqa: ANN003
        self.calls.append(("GET", url))
        return self._emit(url)

    async def post(self, url: str, **kwargs) -> FakeResponse:  # noqa: ANN003
        self.calls.append(("POST", url))
        return self._emit(url)

    def _emit(self, url: str) -> FakeResponse:
        for pattern, resp in self.responses.items():
            if pattern in url:
                return resp
        return FakeResponse(200, {})


@pytest.fixture(autouse=True)
def _install_fake_http(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeClient.responses = {}
    FakeClient.calls = []
    monkeypatch.setattr(setup.httpx, "AsyncClient", FakeClient)


async def _check_ollama_ok() -> tuple:
    return "AVAILABLE", "ok", ["qwen:1.5b"]


async def _check_opencode_ok(oc_user: str = "", oc_pass: str = "") -> tuple:
    return "AVAILABLE", "running"


async def _check_gemini_no_key() -> tuple:
    return "NOT_CONFIGURED", "no key"


async def _check_nvidia_no_key() -> tuple:
    return "NOT_CONFIGURED", "no key"


async def _no_models(name: str) -> list:
    return []


class TestGemini:
    async def test_no_key_is_not_configured(self) -> None:
        from personal_ai_secretary.shared.config import get_settings

        cfg = get_settings()
        original = cfg.gemini_api_key
        try:
            cfg.gemini_api_key = None
            status, detail = await setup._check_gemini_key()
            assert status == "NOT_CONFIGURED"
            assert not FakeClient.calls, "no HTTP call should happen without a key"
        finally:
            cfg.gemini_api_key = original

    async def test_401_maps_to_auth_error(self) -> None:
        from personal_ai_secretary.shared.config import get_settings

        cfg = get_settings()
        original = cfg.gemini_api_key
        FakeClient.responses["/models"] = FakeResponse(401, {})
        try:
            cfg.gemini_api_key = "fake-key-12345678"
            status, detail = await setup._check_gemini_key()
            assert status == "AUTH_ERROR"
            assert any("/models" in url for _, url in FakeClient.calls)
        finally:
            cfg.gemini_api_key = original

    async def test_200_maps_to_available(self) -> None:
        from personal_ai_secretary.shared.config import get_settings

        cfg = get_settings()
        original = cfg.gemini_api_key
        FakeClient.responses["/models"] = FakeResponse(200, {"data": []})
        try:
            cfg.gemini_api_key = "fake-key-12345678"
            status, detail = await setup._check_gemini_key()
            assert status == "AVAILABLE"
        finally:
            cfg.gemini_api_key = original


class TestNvidia:
    async def test_real_check_hits_network_with_valid_key(self) -> None:
        from personal_ai_secretary.shared.config import get_settings

        cfg = get_settings()
        original = cfg.nvidia_api_key
        FakeClient.responses["/models"] = FakeResponse(403, {})
        try:
            cfg.nvidia_api_key = "fake-key-12345678"
            status, detail = await setup._check_nvidia_key()
            assert status == "AUTH_ERROR"
            assert any("/models" in url for _, url in FakeClient.calls)
        finally:
            cfg.nvidia_api_key = original

    async def test_no_key_is_not_configured(self) -> None:
        from personal_ai_secretary.shared.config import get_settings

        cfg = get_settings()
        original = cfg.nvidia_api_key
        try:
            cfg.nvidia_api_key = None
            status, detail = await setup._check_nvidia_key()
            assert status == "NOT_CONFIGURED"
            assert not FakeClient.calls
        finally:
            cfg.nvidia_api_key = original


class TestOllama:
    async def test_running_with_models_is_available(self) -> None:
        FakeClient.responses["/api/tags"] = FakeResponse(
            200, {"models": [{"name": "qwen:1.5b"}, {"name": "llama3.2"}]}
        )
        status, detail, models = await setup._check_ollama()
        assert status == "AVAILABLE"
        assert models == ["llama3.2", "qwen:1.5b"]

    async def test_running_without_models_is_unavailable(self) -> None:
        FakeClient.responses["/api/tags"] = FakeResponse(200, {"models": []})
        status, detail, models = await setup._check_ollama()
        assert status == "UNAVAILABLE"
        assert models == []


class TestConnectionMapping:
    async def test_runtime_error_not_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _boom(provider: str, creds: dict) -> None:
            raise RuntimeError("NVIDIA provider is not configured")

        monkeypatch.setattr(setup, "_probe_provider", _boom)
        result = await setup.test_connection("nvidia")
        assert result.status == "NOT_CONFIGURED"

    async def test_runtime_error_invalid_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _boom(provider: str, creds: dict) -> None:
            raise RuntimeError("Gemini API key is invalid or expired.")

        monkeypatch.setattr(setup, "_probe_provider", _boom)
        result = await setup.test_connection("gemini")
        assert result.status == "AUTH_ERROR"

    async def test_connection_error_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _boom(provider: str, creds: dict) -> None:
            raise ConnectionError("Cannot connect to Ollama.")

        monkeypatch.setattr(setup, "_probe_provider", _boom)
        result = await setup.test_connection("ollama")
        assert result.status == "UNAVAILABLE"

    async def test_success_records_latency(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _ok(provider: str, creds: dict) -> tuple:
            return "AVAILABLE", "qwen:1.5b", "OK"

        monkeypatch.setattr(setup, "_probe_provider", _ok)
        result = await setup.test_connection("ollama")
        assert result.status == "AVAILABLE"
        assert result.model == "qwen:1.5b"
        assert result.latency_ms is not None and result.latency_ms >= 0


class TestProviderStatuses:
    async def test_aggregates_all_providers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(setup, "_check_ollama", _check_ollama_ok)
        monkeypatch.setattr(setup, "_check_opencode", _check_opencode_ok)
        monkeypatch.setattr(setup, "_check_gemini_key", _check_gemini_no_key)
        monkeypatch.setattr(setup, "_check_nvidia_key", _check_nvidia_no_key)
        monkeypatch.setattr(setup, "_discover_models", _no_models)

        results = await setup.provider_statuses()
        names = [r.provider for r in results]
        assert names == ["ollama", "opencode", "gemini", "nvidia"]
        by_name = {r.provider: r.status for r in results}
        assert by_name["ollama"] == "AVAILABLE"
        assert by_name["opencode"] == "AVAILABLE"
        assert by_name["gemini"] == "NOT_CONFIGURED"
        assert by_name["nvidia"] == "NOT_CONFIGURED"