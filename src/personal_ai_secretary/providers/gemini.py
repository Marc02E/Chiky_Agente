"""Gemini provider using OpenAI-compatible API endpoint.

FASE T: Google Gemini models accessed via the OpenAI-compatible API at
generativelanguage.googleapis.com/v1beta/openai/. Uses the same
RequestEnvelope/ProviderResponse contract as all other providers.
"""

import logging
from typing import Any

import httpx

from personal_ai_secretary.domain.contracts import ProviderInfo, ProviderResponse, RequestEnvelope
from personal_ai_secretary.observability.tracing import get_traceparent_header

logger = logging.getLogger("personal_ai_secretary.providers.gemini")

_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 120.0


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key or ""
        self._model = model or "gemini-2.0-flash"
        self._base_url = (
            base_url or "https://generativelanguage.googleapis.com/v1beta/openai"
        )

    @property
    def model(self) -> str:
        return self._model

    @model.setter
    def model(self, value: str) -> None:
        self._model = value

    async def health(self) -> ProviderInfo:
        if not self._api_key:
            return ProviderInfo(
                name=self.name,
                mode="cloud",
                available=False,
                is_ai=True,
                detail="API key not configured. Set GEMINI_API_KEY.",
            )
        try:
            async with httpx.AsyncClient(timeout=_CONNECT_TIMEOUT) as client:
                response = await client.get(
                    f"{self._base_url}/models",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
            if response.status_code == 200:
                return ProviderInfo(
                    name=self.name,
                    mode="cloud",
                    available=True,
                    is_ai=True,
                    detail=f"Gemini API reachable, model={self._model}",
                )
            if response.status_code == 401:
                return ProviderInfo(
                    name=self.name,
                    mode="cloud",
                    available=False,
                    is_ai=True,
                    detail="API key invalid or expired.",
                )
            return ProviderInfo(
                name=self.name,
                mode="cloud",
                available=False,
                is_ai=True,
                detail=f"Gemini API returned HTTP {response.status_code}.",
            )
        except httpx.ConnectError:
            return ProviderInfo(
                name=self.name,
                mode="cloud",
                available=False,
                is_ai=True,
                detail="Cannot connect to Gemini API. Check internet connection.",
            )
        except httpx.TimeoutException:
            return ProviderInfo(
                name=self.name,
                mode="cloud",
                available=False,
                is_ai=True,
                detail="Gemini API connection timed out.",
            )
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Gemini health check failed: %s", exc)
            return ProviderInfo(
                name=self.name,
                mode="cloud",
                available=False,
                is_ai=True,
                detail="Gemini API unavailable.",
            )

    async def generate(self, request: RequestEnvelope) -> ProviderResponse:
        if not self._api_key:
            raise RuntimeError("Gemini provider is not configured (no API key)")
        messages = self._build_messages(request)
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        headers: dict[str, str] = {"Authorization": f"Bearer {self._api_key}"}
        traceparent = get_traceparent_header()
        if traceparent:
            headers["traceparent"] = traceparent
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(_CONNECT_TIMEOUT, read=_READ_TIMEOUT),
            ) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.ConnectError as exc:
            logger.error("Gemini connection failed: %s", exc)
            raise ConnectionError(
                "Cannot connect to Gemini API. Check internet connection."
            ) from exc
        except httpx.TimeoutException as exc:
            logger.error("Gemini timeout: %s", exc)
            raise TimeoutError("Gemini API timed out.") from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                raise RuntimeError(
                    "Gemini API key is invalid or expired. Check GEMINI_API_KEY."
                ) from exc
            if status == 429:
                raise RuntimeError("Gemini API rate limit exceeded.") from exc
            logger.error("Gemini HTTP error %d: %s", status, exc)
            raise RuntimeError(f"Gemini API error (HTTP {status}).") from exc
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("Gemini request failed: %s", exc)
            raise RuntimeError("Gemini returned an unexpected response.") from exc

        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("Gemini returned no choices.")
        text = choices[0].get("message", {}).get("content", "")
        if not text:
            raise RuntimeError("Gemini returned an empty response.")
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
