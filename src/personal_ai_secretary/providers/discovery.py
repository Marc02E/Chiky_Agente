"""Provider discovery for all configured providers.

FASE T: Detects which providers are available and what models they offer.
Runs at startup and on-demand. Never modifies external configuration.
"""

import logging
from dataclasses import dataclass
from typing import Any

from personal_ai_secretary.shared.config import get_settings

logger = logging.getLogger("personal_ai_secretary.providers.discovery")


@dataclass
class DiscoveredProvider:
    name: str
    display_name: str
    available: bool
    mode: str
    models: list[str]
    detail: str
    provider_instance: Any = None


class ProviderDiscovery:
    """Discovers available providers and their models."""

    def __init__(self) -> None:
        self._discovered: dict[str, DiscoveredProvider] = {}

    async def discover_all(self) -> dict[str, DiscoveredProvider]:
        self._discovered.clear()
        await self._discover_ollama()
        await self._discover_gemini()
        await self._discover_opencode()
        await self._discover_nvidia()
        return self._discovered

    async def _discover_ollama(self) -> None:
        from personal_ai_secretary.providers.ollama import OllamaProvider

        provider = OllamaProvider()
        health = await provider.health()
        models: list[str] = []
        if health.available:
            try:
                models = await provider.list_models()
            except Exception:
                models = []
        self._discovered["ollama"] = DiscoveredProvider(
            name="ollama",
            display_name="Ollama (Local)",
            available=health.available,
            mode="local",
            models=models,
            detail=health.detail,
            provider_instance=provider,
        )

    async def _discover_gemini(self) -> None:
        from personal_ai_secretary.providers.gemini import GeminiProvider

        settings = get_settings()
        api_key = settings.gemini_api_key
        provider = GeminiProvider(
            api_key=api_key,
            model=settings.gemini_model,
            base_url=settings.gemini_base_url,
        )
        health = await provider.health()
        models: list[str] = []
        if health.available:
            models = [settings.gemini_model]
        self._discovered["gemini"] = DiscoveredProvider(
            name="gemini",
            display_name="Google Gemini (Cloud)",
            available=health.available,
            mode="cloud",
            models=models,
            detail=health.detail,
            provider_instance=provider,
        )

    async def _discover_opencode(self) -> None:
        from personal_ai_secretary.providers.opencode_provider import OpenCodeProvider

        settings = get_settings()
        provider = OpenCodeProvider(
            base_url=settings.opencode_base_url,
            model=settings.opencode_model,
        )
        health = await provider.health()
        models: list[str] = []
        if health.available:
            try:
                models = await provider.list_models()
            except Exception:
                models = [settings.opencode_model] if settings.opencode_model else []
        self._discovered["opencode"] = DiscoveredProvider(
            name="opencode",
            display_name="OpenCode",
            available=health.available,
            mode="local",
            models=models,
            detail=health.detail,
            provider_instance=provider,
        )

    async def _discover_nvidia(self) -> None:
        from personal_ai_secretary.providers.remote import NVIDIAProvider

        settings = get_settings()
        provider = NVIDIAProvider()
        health = await provider.health()
        models: list[str] = []
        if health.available:
            models = [settings.nvidia_model]
        self._discovered["nvidia"] = DiscoveredProvider(
            name="nvidia",
            display_name="NVIDIA (Cloud)",
            available=health.available,
            mode="cloud",
            models=models,
            detail=health.detail,
            provider_instance=provider,
        )

    def get_provider(self, name: str) -> DiscoveredProvider | None:
        return self._discovered.get(name)

    @property
    def discovered(self) -> dict[str, DiscoveredProvider]:
        return self._discovered
