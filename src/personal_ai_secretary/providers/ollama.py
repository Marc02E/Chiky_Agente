from typing import Any

import httpx

from personal_ai_secretary.domain.contracts import (
    ProviderInfo,
    ProviderResponse,
    RequestEnvelope,
)
from personal_ai_secretary.observability.tracing import get_traceparent_header
from personal_ai_secretary.shared.config import get_settings


class OllamaProvider:
    name = "ollama"

    async def health(self) -> ProviderInfo:
        settings = get_settings()
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{settings.ollama_base_url}/api/tags")
            response.raise_for_status()
            models = response.json().get("models", [])
            available = any(item.get("name") == settings.ollama_model for item in models)
            detail = (
                "Configured model available."
                if available
                else "Ollama reachable but configured model is unavailable."
            )
            return ProviderInfo(
                name=self.name,
                mode="local",
                available=available,
                is_ai=True,
                detail=detail,
            )
        except (httpx.HTTPError, ValueError):
            return ProviderInfo(
                name=self.name,
                mode="local",
                available=False,
                is_ai=True,
                detail="Ollama unavailable.",
            )

    async def generate(self, request: RequestEnvelope) -> ProviderResponse:
        settings = get_settings()
        messages = self._build_messages(request)
        payload: dict[str, Any] = {
            "model": settings.ollama_model,
            "messages": messages,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            headers = {}
            traceparent = get_traceparent_header()
            if traceparent:
                headers["traceparent"] = traceparent
            response = await client.post(
                f"{settings.ollama_base_url}/api/chat",
                json=payload,
                headers=headers or None,
            )
            response.raise_for_status()
            data = response.json()
        reply = data.get("message", {}).get("content", "")
        return ProviderResponse(
            text=str(reply),
            provider=self.name,
            model=settings.ollama_model,
        )

    @staticmethod
    def _build_messages(request: RequestEnvelope) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if request.context_summary:
            messages.append({"role": "system", "content": request.context_summary})
        for turn in request.messages:
            messages.append({"role": turn.role, "content": turn.content})
        messages.append({"role": "user", "content": request.input})
        return messages
