import pytest

from personal_ai_secretary.providers.deterministic import DeterministicProvider
from personal_ai_secretary.providers.factory import (
    AVAILABLE_PROVIDER_MODES,
    get_provider,
)
from personal_ai_secretary.providers.ollama import OllamaProvider
from personal_ai_secretary.providers.remote import NVIDIAProvider
from personal_ai_secretary.shared.config import get_settings


def test_factory_selects_deterministic_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ai_provider", "deterministic")
    assert isinstance(get_provider(), DeterministicProvider)


def test_factory_selects_local_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ai_provider", "local")
    assert isinstance(get_provider(), OllamaProvider)


def test_factory_selects_remote_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ai_provider", "remote")
    assert isinstance(get_provider(), NVIDIAProvider)


def test_every_available_mode_is_selectable(monkeypatch: pytest.MonkeyPatch) -> None:
    for mode in AVAILABLE_PROVIDER_MODES:
        monkeypatch.setattr(get_settings(), "ai_provider", mode)
        assert get_provider() is not None


def test_unsupported_provider_mode_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ai_provider", "invalid-mode")
    with pytest.raises(RuntimeError, match="Unsupported AI provider mode"):
        get_provider()
