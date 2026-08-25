import logging
from typing import Any

import httpx

from personal_ai_secretary.domain.contracts import (
    ProviderInfo,
    ProviderResponse,
    RequestEnvelope,
)
from personal_ai_secretary.observability.tracing import get_traceparent_header
from personal_ai_secretary.shared.config import get_settings

logger = logging.getLogger("personal_ai_secretary.providers.ollama")

# Reasonable timeouts for different phases.
# _READ_TIMEOUT is per individual LLM call (not total agentic loop).
# On CPU-only hardware,7B models may take 10-30s per call; the agentic loop
# makes 5-15 sequential calls, so total time can be 50-150s+.
_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 600.0


class OllamaProvider:
    name = "ollama"

    def __init__(self, model: str | None = None) -> None:
        settings = get_settings()
        self.model: str = model or settings.ollama_model
        self._base_url: str = settings.ollama_base_url

    async def health(self) -> ProviderInfo:
        try:
            async with httpx.AsyncClient(timeout=_CONNECT_TIMEOUT) as client:
                response = await client.get(f"{self._base_url}/api/tags")
            response.raise_for_status()
            models = response.json().get("models", [])
            has_any_model = len(models) > 0
            available = any(item.get("name") == self.model for item in models)
            if has_any_model and not available:
                detail = (
                    f"Ollama reachable with {len(models)} models, "
                    f"but '{self.model}' is unavailable. "
                    f"Available: {', '.join(m.get('name', '?') for m in models[:5])}"
                )
            elif available:
                detail = f"Model '{self.model}' available."
            else:
                detail = "Ollama reachable but no models installed."
            return ProviderInfo(
                name=self.name,
                mode="local",
                available=has_any_model,
                is_ai=True,
                detail=detail,
            )
        except httpx.ConnectError:
            return ProviderInfo(
                name=self.name,
                mode="local",
                available=False,
                is_ai=True,
                detail="Ollama is not running. Start Ollama and try again.",
            )
        except httpx.TimeoutException:
            return ProviderInfo(
                name=self.name,
                mode="local",
                available=False,
                is_ai=True,
                detail="Ollama connection timed out.",
            )
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Ollama health check failed: %s", exc)
            return ProviderInfo(
                name=self.name,
                mode="local",
                available=False,
                is_ai=True,
                detail="Ollama unavailable.",
            )

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=_CONNECT_TIMEOUT) as client:
                response = await client.get(f"{self._base_url}/api/tags")
            response.raise_for_status()
            models = response.json().get("models", [])
            return sorted(item.get("name", "") for item in models if item.get("name"))
        except (httpx.HTTPError, ValueError):
            return []

    async def generate(self, request: RequestEnvelope) -> ProviderResponse:
        messages = self._build_messages(request)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(_CONNECT_TIMEOUT, read=_READ_TIMEOUT),
            ) as client:
                headers: dict[str, str] = {}
                traceparent = get_traceparent_header()
                if traceparent:
                    headers["traceparent"] = traceparent
                response = await client.post(
                    f"{self._base_url}/api/chat",
                    json=payload,
                    headers=headers or None,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.ConnectError as exc:
            logger.error("Ollama connection failed for model %s: %s", self.model, exc)
            raise ConnectionError(
                "Cannot connect to Ollama. Please verify it is running."
            ) from exc
        except httpx.TimeoutException as exc:
            logger.error("Ollama timeout for model %s: %s", self.model, exc)
            raise TimeoutError(
                "Ollama took too long to respond. The model may be loading or too slow."
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 404:
                logger.error("Ollama model not found: %s", self.model)
                raise ValueError(
                    f"Model '{self.model}' not found in Ollama. "
                    "Pull the model first or select a different one. "
                    "Try selecting a different model from the model selector."
                ) from exc
            logger.error("Ollama HTTP error %d for model %s: %s", status, self.model, exc)
            raise RuntimeError(
                f"Ollama returned an error (HTTP {status})."
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("Ollama request failed for model %s: %s", self.model, exc)
            raise RuntimeError(
                "Ollama returned an unexpected response."
            ) from exc

        reply = data.get("message", {}).get("content", "")
        if not reply:
            logger.warning("Ollama returned empty response for model %s", self.model)
            raise RuntimeError(
                "The model returned an empty response. Please try rephrasing your question."
            )
        return ProviderResponse(
            text=str(reply),
            provider=self.name,
            model=self.model,
        )

    async def warmup(self) -> None:
        """Send a minimal request to load the model into memory (eliminates cold start)."""
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(_CONNECT_TIMEOUT, read=60.0),
            ) as client:
                await client.post(
                    f"{self._base_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": False,
                    },
                )
            logger.info("Ollama model '%s' warmed up successfully.", self.model)
        except Exception as exc:
            logger.warning("Ollama warmup failed (non-fatal): %s", exc)

    @staticmethod
    def _build_messages(request: RequestEnvelope) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if request.context_summary:
            messages.append({"role": "system", "content": request.context_summary})
        for turn in request.messages:
            messages.append({"role": turn.role, "content": turn.content})
        messages.append({"role": "user", "content": request.input})
        return messages
