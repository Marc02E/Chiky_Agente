import logging
from typing import Final

from personal_ai_secretary.providers.base import AIProvider
from personal_ai_secretary.providers.deterministic import DeterministicProvider
from personal_ai_secretary.providers.ollama import OllamaProvider
from personal_ai_secretary.providers.remote import NVIDIAProvider
from personal_ai_secretary.shared.config import get_settings

AVAILABLE_PROVIDER_MODES: Final[tuple[str, ...]] = ("deterministic", "local", "remote")

_logger = logging.getLogger(__name__)


def get_provider() -> AIProvider:
    provider = get_settings().ai_provider
    if provider == "deterministic":
        return DeterministicProvider()
    if provider == "local":
        return OllamaProvider()
    if provider == "remote":
        settings = get_settings()
        if not settings.nvidia_api_key:
            _logger.warning(
                "NVIDIA provider mode requested without NVIDIA_API_KEY; "
                "remote mode will raise at generate time. "
                "Set NVIDIA_API_KEY env variable or use deterministic/local mode."
            )
        return NVIDIAProvider()
    raise RuntimeError("Unsupported AI provider mode.")