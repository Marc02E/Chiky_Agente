import pytest
from pydantic import ValidationError

from personal_ai_secretary.shared.config import Settings


def test_production_refuses_default_development_jwt_secret() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                "app_env": "production",
                "jwt_secret": "change-this-local-development-secret",
                "database_url": "postgresql+asyncpg://user:pass@db:5432/secretary",
            }
        )


def test_production_refuses_sqlite_database() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                "app_env": "production",
                "jwt_secret": "a-strong-production-secret-32bytes!",
                "database_url": "sqlite+aiosqlite:///./personal_ai_secretary.db",
            }
        )


def test_production_accepts_overridden_strong_jwt_secret() -> None:
    settings = Settings.model_validate(
        {
            "app_env": "production",
            "jwt_secret": "a-strong-production-secret-32bytes!",
            "database_url": "postgresql+asyncpg://user:pass@db:5432/secretary",
        }
    )
    assert settings.app_env == "production"
    assert settings.jwt_secret == "a-strong-production-secret-32bytes!"
    assert settings.database_url.startswith("postgresql+asyncpg")


def test_persistent_stores_defaults_to_false() -> None:
    settings = Settings.model_validate({"app_env": "test"})
    assert settings.persistent_stores is False


def test_production_remote_provider_requires_nvidia_key() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                "app_env": "production",
                "jwt_secret": "a-strong-production-secret-32bytes!",
                "database_url": "postgresql+asyncpg://user:pass@db:5432/secretary",
                "ai_provider": "remote",
                "nvidia_api_key": None,
            }
        )


def test_production_remote_provider_accepts_configured_nvidia_key() -> None:
    settings = Settings.model_validate(
        {
            "app_env": "production",
            "jwt_secret": "a-strong-production-secret-32bytes!",
            "database_url": "postgresql+asyncpg://user:pass@db:5432/secretary",
            "ai_provider": "remote",
            "nvidia_api_key": "nvidia-test-key",
        }
    )
    assert settings.nvidia_api_key == "nvidia-test-key"


def test_compliance_and_evidence_defaults() -> None:
    settings = Settings.model_validate({"app_env": "test"})
    assert settings.compliance_enabled is True
    assert "prohibited_commands" in settings.compliance_rules
    assert "credential_leakage" in settings.compliance_rules
    assert settings.evidence_ttl_seconds is None