from typing import Any

import httpx

from personal_ai_secretary.domain.contracts import ProviderInfo, ProviderResponse, RequestEnvelope
from personal_ai_secretary.observability.tracing import get_traceparent_header
from personal_ai_secretary.providers.base import AIProvider
from personal_ai_secretary.shared.config import get_settings


class NVIDIAProvider(AIProvider):
    name = "nvidia"

    def __init__(self, model: str | None = None) -> None:
        # FASE AB.6: instance-level model so manual selection sticks and
        # provenance can report the exact model that executed.
        self.model = model or get_settings().nvidia_model

    async def generate(self, request: RequestEnvelope) -> ProviderResponse:
        settings = get_settings()
        if not settings.nvidia_api_key:
            raise RuntimeError("NVIDIA provider is not configured")
        messages = self._build_messages(request)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        headers = {"Authorization": f"Bearer {settings.nvidia_api_key}"}
        traceparent = get_traceparent_header()
        if traceparent:
            headers["traceparent"] = traceparent
        async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
            response = await client.post(settings.nvidia_base_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        text = data["choices"][0]["message"]["content"]
        return ProviderResponse(text=text, provider=self.name, model=self.model)

    async def health(self) -> ProviderInfo:
        settings = get_settings()
        return ProviderInfo(
            name=self.name,
            mode="remote",
            available=bool(settings.nvidia_api_key),
            is_ai=True,
            detail="Configured" if settings.nvidia_api_key else "API key not configured",
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
