"""Tests for the model listing and selection API endpoints."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from personal_ai_secretary.api.app import app
from personal_ai_secretary.providers.factory import get_current_model, set_current_model
from personal_ai_secretary.shared.config import get_settings


@pytest.fixture(autouse=True)
def _set_local_provider() -> None:
    """Ensure provider mode is 'local' for model endpoint tests."""
    with patch.object(get_settings(), "ai_provider", "local"):
        yield


def _fake_ollama_tags(models: list[dict[str, str]]) -> type:
    class _FakeClient:
        async def __aenter__(self):  # noqa: ANN204
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def get(self, url: str) -> httpx.Response:
            return httpx.Response(
                200, request=httpx.Request("GET", url), json={"models": models}
            )

        async def post(
            self, url: str, json: dict = ..., headers: dict | None = None
        ) -> httpx.Response:
            return httpx.Response(
                200, request=httpx.Request("POST", url), json={"models": models}
            )

    return _FakeClient  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_list_local_models_returns_available_models() -> None:
    fake_models = [{"name": "llama3.1:latest"}, {"name": "deepseek-coder-v2:latest"}]

    with patch("httpx.AsyncClient", side_effect=lambda **kw: _fake_ollama_tags(fake_models)()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/providers/local/models",
                headers={"Authorization": "Bearer test-token"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert "llama3.1:latest" in data["models"]
    assert "deepseek-coder-v2:latest" in data["models"]


@pytest.mark.asyncio
async def test_list_local_models_includes_current_model() -> None:
    set_current_model("deepseek-coder-v2:latest")
    fake_models = [{"name": "llama3.1:latest"}, {"name": "deepseek-coder-v2:latest"}]

    with patch("httpx.AsyncClient", side_effect=lambda **kw: _fake_ollama_tags(fake_models)()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/providers/local/models",
                headers={"Authorization": "Bearer test-token"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["current"] == "deepseek-coder-v2:latest"


@pytest.mark.asyncio
async def test_set_local_model_selects_valid_model() -> None:
    fake_models = [{"name": "llama3.1:latest"}, {"name": "deepseek-coder-v2:latest"}]

    with patch("httpx.AsyncClient", side_effect=lambda **kw: _fake_ollama_tags(fake_models)()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/providers/local/model",
                json={"model": "deepseek-coder-v2:latest"},
                headers={"Authorization": "Bearer test-token"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "deepseek-coder-v2:latest"
    assert data["status"] == "selected"
    assert get_current_model() == "deepseek-coder-v2:latest"


@pytest.mark.asyncio
async def test_set_local_model_rejects_unavailable_model() -> None:
    fake_models = [{"name": "llama3.1:latest"}]

    with patch("httpx.AsyncClient", side_effect=lambda **kw: _fake_ollama_tags(fake_models)()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/providers/local/model",
                json={"model": "nonexistent-model"},
                headers={"Authorization": "Bearer test-token"},
            )

    assert resp.status_code == 400
    body = resp.json()
    assert "not available" in body.get("message", body.get("detail", "")).lower()


@pytest.mark.asyncio
async def test_set_local_model_rejects_empty_model_name() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/providers/local/model",
            json={"model": ""},
            headers={"Authorization": "Bearer test-token"},
        )

    assert resp.status_code == 400
    body = resp.json()
    assert "required" in body.get("message", body.get("detail", "")).lower()


@pytest.mark.asyncio
async def test_providers_endpoint_includes_current_model() -> None:
    set_current_model("llama3.1:latest")
    fake_models = [{"name": "llama3.1:latest"}]

    with patch("httpx.AsyncClient", side_effect=lambda **kw: _fake_ollama_tags(fake_models)()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/providers",
                headers={"Authorization": "Bearer test-token"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert "current_model" in data
    assert data["current_model"] == "llama3.1:latest"
