"""OpenCode provider integration for Chiky.

FASE T: Detects OpenCode availability and exposes models through an
OpenAI-compatible interface. When OpenCode is installed and running as a
server, this provider can route requests to it.

OpenCode (https://opencode.ai) is a CLI tool that may expose an HTTP API.
This module:
1. Detects if OpenCode is installed
2. Checks if an OpenCode server is running
3. Provides a provider that routes through OpenCode when available

When OpenCode is not available, the provider reports unavailable without errors.
"""

import logging
import shutil
from typing import Any

import httpx

from personal_ai_secretary.domain.contracts import ProviderInfo, ProviderResponse, RequestEnvelope
from personal_ai_secretary.observability.tracing import get_traceparent_header

logger = logging.getLogger("personal_ai_secretary.providers.opencode")

_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 120.0
_DEFAULT_PORT = 4096


class OpenCodeProvider:
    name = "opencode"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self._base_url = base_url or f"http://127.0.0.1:{_DEFAULT_PORT}"
        self._model = model or "default"
        self._available: bool | None = None

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def is_installed() -> bool:
        return shutil.which("opencode") is not None

    async def _check_server(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=_CONNECT_TIMEOUT) as client:
                response = await client.get(f"{self._base_url}/health")
            return response.status_code == 200
        except Exception:
            return False

    async def health(self) -> ProviderInfo:
        installed = self.is_installed()
        server_running = await self._check_server()

        if not installed:
            return ProviderInfo(
                name=self.name,
                mode="local",
                available=False,
                is_ai=True,
                detail="OpenCode is not installed. Install from https://opencode.ai",
            )
        if not server_running:
            return ProviderInfo(
                name=self.name,
                mode="local",
                available=False,
                is_ai=True,
                detail="OpenCode is installed but server is not running. "
                f"Start with: opencode serve --port {_DEFAULT_PORT}",
            )
        return ProviderInfo(
            name=self.name,
            mode="local",
            available=True,
            is_ai=True,
            detail=f"OpenCode server running at {self._base_url}",
        )

    async def generate(self, request: RequestEnvelope) -> ProviderResponse:
        messages = self._build_messages(request)
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
        }
        headers: dict[str, str] = {}
        traceparent = get_traceparent_header()
        if traceparent:
            headers["traceparent"] = traceparent
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(_CONNECT_TIMEOUT, read=_READ_TIMEOUT),
            ) as client:
                response = await client.post(
                    f"{self._base_url}/v1/chat/completions",
                    json=payload,
                    headers=headers or None,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.ConnectError as exc:
            raise ConnectionError(
                "Cannot connect to OpenCode server. Ensure it is running."
            ) from exc
        except httpx.TimeoutException as exc:
            raise TimeoutError("OpenCode server timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"OpenCode server error (HTTP {exc.response.status_code})."
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("OpenCode returned an unexpected response.") from exc

        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("OpenCode returned no choices.")
        text = choices[0].get("message", {}).get("content", "")
        if not text:
            raise RuntimeError("OpenCode returned an empty response.")
        return ProviderResponse(text=str(text), provider=self.name, model=self._model)

    @staticmethod
    def _build_messages(request: RequestEnvelope) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if request.context_summary:
            messages.append({"role": "system", "content": request.context_summary})
        for turn in request.messages:
            messages.append({"role": turn.role, "content": turn.content})
        messages.append({"role": "user", "content": request.input})
        return messages

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=_CONNECT_TIMEOUT) as client:
                response = await client.get(f"{self._base_url}/v1/models")
            if response.status_code == 200:
                data = response.json()
                models = data.get("data", [])
                return [m.get("id", "") for m in models if m.get("id")]
        except Exception:
            pass
        return [self._model] if self._model else []
