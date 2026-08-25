import logging
import threading
from typing import Final

from personal_ai_secretary.providers.base import AIProvider
from personal_ai_secretary.providers.deterministic import DeterministicProvider
from personal_ai_secretary.providers.ollama import OllamaProvider
from personal_ai_secretary.providers.remote import NVIDIAProvider
from personal_ai_secretary.shared.config import get_settings

AVAILABLE_PROVIDER_MODES: Final[tuple[str, ...]] = ("deterministic", "local", "remote")

_logger = logging.getLogger(__name__)

_model_lock = threading.Lock()
_current_ollama_model: str | None = None

# FASE T: Model manager singleton
_model_manager: object | None = None
_model_manager_lock = threading.Lock()


def get_current_model() -> str | None:
    with _model_lock:
        return _current_ollama_model


def set_current_model(model: str) -> None:
    global _current_ollama_model  # noqa: PLW0603
    with _model_lock:
        _current_ollama_model = model


def get_provider() -> AIProvider:
    provider = get_settings().ai_provider
    if provider == "deterministic":
        return DeterministicProvider()
    if provider == "local":
        with _model_lock:
            model = _current_ollama_model
        return OllamaProvider(model=model)
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


def get_model_manager() -> object:
    """Get or create the FASE T ModelManager singleton.

    Returns the ModelManager instance, creating it on first call.
    The ModelManager extends the factory with multi-provider awareness,
    capability verification, and intelligent routing.
    """
    global _model_manager  # noqa: PLW0603
    with _model_manager_lock:
        if _model_manager is None:
            from personal_ai_secretary.providers.model_manager import ModelManager

            _model_manager = ModelManager()
        return _model_manager


async def initialize_model_manager() -> None:
    """Initialize the ModelManager at startup.

    Discovers all providers and registers their models.
    This is called during app lifespan.
    """
    manager = get_model_manager()
    from personal_ai_secretary.providers.model_manager import ModelManager

    assert isinstance(manager, ModelManager)
    await manager.initialize()
    _logger.info(
        "ModelManager initialized: %d models across providers",
        manager.registry.count,
    )
