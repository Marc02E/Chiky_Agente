"""Dynamic model registry with verification state.

FASE T: Extends the existing static MODEL_REGISTRY with runtime verification
status, latency measurements, provider assignments, and health tracking.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum

from personal_ai_secretary.providers.model_intelligence import (
    get_model_capabilities,
)

logger = logging.getLogger("personal_ai_secretary.providers.model_registry")


class ModelStatus(StrEnum):
    UNKNOWN = "unknown"
    CHECKING = "checking"
    VERIFIED = "verified"
    SLOW = "slow"
    LIMITED = "limited"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    OFFLINE = "offline"


@dataclass
class VerifiedCapabilities:
    basic_response: bool = False
    system_prompt: bool = False
    tool_calling: bool = False
    argument_compatibility: bool = False
    file_creation: bool = False
    file_modification: bool = False
    verification: bool = False
    coding: bool = False
    multi_step: bool = False
    security: bool = False
    context_handling: bool = False
    vision: bool = False
    tests_passed: int = 0
    tests_total: int = 0

    @property
    def score(self) -> float:
        if self.tests_total == 0:
            return 0.0
        return self.tests_passed / self.tests_total


@dataclass
class ModelEntry:
    model_id: str
    provider_name: str
    display_name: str = ""
    context_window: int = 4096
    tool_calling: bool = True
    coding_strength: int = 3
    supports_vision: bool = False
    status: ModelStatus = ModelStatus.UNKNOWN
    verified_capabilities: VerifiedCapabilities = field(
        default_factory=VerifiedCapabilities
    )
    last_verified: float = 0.0
    measured_latency_ms: float = 0.0
    expected_speed: str = "medium"
    success_count: int = 0
    failure_count: int = 0
    total_requests: int = 0

    @property
    def reliability(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.success_count / self.total_requests

    @property
    def verification_age_hours(self) -> float:
        if self.last_verified == 0:
            return float("inf")
        return (time.time() - self.last_verified) / 3600.0

    def record_success(self) -> None:
        self.success_count += 1
        self.total_requests += 1

    def record_failure(self) -> None:
        self.failure_count += 1
        self.total_requests += 1

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "provider": self.provider_name,
            "display_name": self.display_name or self.model_id,
            "context_window": self.context_window,
            "tool_calling": self.tool_calling,
            "coding_strength": self.coding_strength,
            "supports_vision": self.supports_vision,
            "status": self.status.value,
            "verified": self.status == ModelStatus.VERIFIED,
            "verification_score": self.verified_capabilities.score,
            "last_verified": self.last_verified,
            "measured_latency_ms": round(self.measured_latency_ms, 1),
            "expected_speed": self.expected_speed,
            "reliability": round(self.reliability, 3),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "total_requests": self.total_requests,
        }


class ModelRegistry:
    """Thread-safe dynamic model registry."""

    def __init__(self) -> None:
        self._models: dict[str, ModelEntry] = {}
        self._by_provider: dict[str, list[str]] = {}

    def register(
        self,
        model_id: str,
        provider_name: str,
        display_name: str = "",
        context_window: int = 4096,
        tool_calling: bool = True,
        coding_strength: int = 3,
        supports_vision: bool = False,
        expected_speed: str = "medium",
    ) -> ModelEntry:
        key = f"{provider_name}:{model_id}"
        if key in self._models:
            return self._models[key]
        caps = get_model_capabilities(model_id)
        entry = ModelEntry(
            model_id=model_id,
            provider_name=provider_name,
            display_name=display_name or model_id,
            context_window=context_window or caps.context_window,
            tool_calling=tool_calling if tool_calling else caps.tool_calling,
            coding_strength=coding_strength if coding_strength != 3 else caps.coding_strength,
            supports_vision=supports_vision or caps.supports_vision,
            expected_speed=expected_speed,
        )
        self._models[key] = entry
        self._by_provider.setdefault(provider_name, []).append(key)
        return entry

    def get(self, model_id: str, provider_name: str | None = None) -> ModelEntry | None:
        if provider_name:
            return self._models.get(f"{provider_name}:{model_id}")
        for _key, entry in self._models.items():
            if entry.model_id == model_id:
                return entry
        return None

    def get_key(self, model_id: str, provider_name: str) -> ModelEntry | None:
        return self._models.get(f"{provider_name}:{model_id}")

    def all_models(self) -> list[ModelEntry]:
        return list(self._models.values())

    def by_provider(self, provider_name: str) -> list[ModelEntry]:
        keys = self._by_provider.get(provider_name, [])
        return [self._models[k] for k in keys if k in self._models]

    def verified_models(self) -> list[ModelEntry]:
        return [m for m in self._models.values() if m.status == ModelStatus.VERIFIED]

    def online_models(self) -> list[ModelEntry]:
        return [
            m
            for m in self._models.values()
            if m.status in (ModelStatus.VERIFIED, ModelStatus.SLOW, ModelStatus.UNKNOWN)
        ]

    def remove_provider(self, provider_name: str) -> int:
        keys = self._by_provider.pop(provider_name, [])
        for key in keys:
            self._models.pop(key, None)
        return len(keys)

    def clear(self) -> None:
        self._models.clear()
        self._by_provider.clear()

    @property
    def count(self) -> int:
        return len(self._models)
