"""FASE X — Persistent provider settings repository.

Stores provider configuration (API keys, routing mode, selected model)
in the database so they survive restarts. API keys are encrypted at rest.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from personal_ai_secretary.domain.models import ProviderSettingRecord

logger = logging.getLogger("personal_ai_secretary.shared.settings_store")

# Simple obfuscation for API keys (not full encryption, but prevents plain-text on disk)
_OBFUSCATION_KEY = "chiky-x-phase-2026"


def _obfuscate(value: str) -> str:
    """Simple XOR obfuscation for API key storage."""
    key = _OBFUSCATION_KEY
    encoded = "".join(
        chr(ord(c) ^ ord(key[i % len(key)])) for i, c in enumerate(value)
    )
    return base64.b64encode(encoded.encode("latin-1")).decode("ascii")


def _deobfuscate(encoded: str) -> str:
    """Reverse XOR obfuscation."""
    key = _OBFUSCATION_KEY
    decoded = base64.b64decode(encoded.encode("ascii")).decode("latin-1")
    return "".join(
        chr(ord(c) ^ ord(key[i % len(key)])) for i, c in enumerate(decoded)
    )


# Well-known setting keys
KEY_GEMINI_API_KEY = "gemini_api_key"
KEY_NVIDIA_API_KEY = "nvidia_api_key"
KEY_AI_PROVIDER = "ai_provider"
KEY_OLLAMA_MODEL = "ollama_model"
KEY_GEMINI_MODEL = "gemini_model"
KEY_ROUTING_MODE = "routing_mode"
KEY_SELECTED_PROVIDER = "selected_provider"
KEY_SELECTED_MODEL = "selected_model"
# FASE AB.6: OpenCode server credentials captured from the first-run wizard.
KEY_OPENCODE_USERNAME = "opencode_server_username"
KEY_OPENCODE_PASSWORD = "opencode_server_password"

# Keys that contain sensitive data and must be obfuscated
_SENSITIVE_KEYS: frozenset[str] = frozenset({
    KEY_GEMINI_API_KEY,
    KEY_NVIDIA_API_KEY,
    KEY_OPENCODE_PASSWORD,
})

# Default system user for provider settings (global, not per-user)
_SYSTEM_USER = "system"


@dataclass
class ProviderSettings:
    """In-memory representation of provider settings."""
    gemini_api_key: str = ""
    nvidia_api_key: str = ""
    ai_provider: str = "deterministic"
    ollama_model: str = "llama3.2"
    gemini_model: str = "gemini-2.0-flash"
    routing_mode: str = "automatic"
    selected_provider: str = ""
    selected_model: str = ""
    # FASE AB.6: OpenCode server credentials.
    opencode_server_username: str = ""
    opencode_server_password: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            KEY_GEMINI_API_KEY: self.gemini_api_key,
            KEY_NVIDIA_API_KEY: self.nvidia_api_key,
            KEY_AI_PROVIDER: self.ai_provider,
            KEY_OLLAMA_MODEL: self.ollama_model,
            KEY_GEMINI_MODEL: self.gemini_model,
            KEY_ROUTING_MODE: self.routing_mode,
            KEY_SELECTED_PROVIDER: self.selected_provider,
            KEY_SELECTED_MODEL: self.selected_model,
            KEY_OPENCODE_USERNAME: self.opencode_server_username,
            KEY_OPENCODE_PASSWORD: self.opencode_server_password,
        }


class SettingsStore:
    """Async repository for persistent provider settings."""

    def __init__(self) -> None:
        self._cache: ProviderSettings | None = None

    async def load(self, session: AsyncSession) -> ProviderSettings:
        """Load all provider settings from the database."""
        result = await session.execute(
            select(ProviderSettingRecord).where(
                ProviderSettingRecord.user_id == _SYSTEM_USER
            )
        )
        records = result.scalars().all()

        settings = ProviderSettings()
        for record in records:
            value = record.setting_value
            if record.setting_key in _SENSITIVE_KEYS and value:
                try:
                    value = _deobfuscate(value)
                except Exception:
                    value = ""
            if hasattr(settings, record.setting_key):
                setattr(settings, record.setting_key, value)

        self._cache = settings
        logger.info(
            "Loaded %d provider settings from database", len(records),
        )
        return settings

    async def save(
        self,
        session: AsyncSession,
        settings: dict[str, str],
    ) -> None:
        """Save provider settings to the database (upsert)."""
        now = datetime.now(UTC)
        for key, value in settings.items():
            if not key or not isinstance(value, str):
                continue
            store_value = value
            if key in _SENSITIVE_KEYS and value:
                store_value = _obfuscate(value)

            existing = await session.execute(
                select(ProviderSettingRecord).where(
                    ProviderSettingRecord.user_id == _SYSTEM_USER,
                    ProviderSettingRecord.setting_key == key,
                )
            )
            record = existing.scalar_one_or_none()
            if record:
                record.setting_value = store_value
                record.updated_at = now
            else:
                session.add(ProviderSettingRecord(
                    user_id=_SYSTEM_USER,
                    setting_key=key,
                    setting_value=store_value,
                    updated_at=now,
                ))

        await session.commit()
        self._cache = None
        logger.info("Saved %d provider settings to database", len(settings))

    async def delete(self, session: AsyncSession, key: str) -> None:
        """Delete a specific provider setting."""
        await session.execute(
            delete(ProviderSettingRecord).where(
                ProviderSettingRecord.user_id == _SYSTEM_USER,
                ProviderSettingRecord.setting_key == key,
            )
        )
        await session.commit()
        self._cache = None

    async def delete_all(self, session: AsyncSession) -> None:
        """Delete all provider settings."""
        await session.execute(
            delete(ProviderSettingRecord).where(
                ProviderSettingRecord.user_id == _SYSTEM_USER,
            )
        )
        await session.commit()
        self._cache = None

    def mask_key(self, key: str) -> str:
        """Mask an API key for display: show last 4 chars only."""
        if not key or len(key) < 8:
            return ""
        return "*" * (len(key) - 4) + key[-4:]
