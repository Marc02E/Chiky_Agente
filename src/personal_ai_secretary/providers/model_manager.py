"""Model Manager — core orchestrator for multi-provider intelligence.

FASE T: Central module that discovers providers, verifies capabilities,
manages model health, routes tasks to the best model, handles fallback,
and provides the unified interface used by the Agent Core.

Architecture:
  ModelManager
    ├── ProviderDiscovery (find what exists)
    ├── CapabilityVerifier (prove what works)
    ├── ModelRegistry (track state)
    ├── ConnectivityChecker (internet awareness)
    └── Routing logic (task-aware selection)
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from personal_ai_secretary.providers.base import AIProvider
from personal_ai_secretary.providers.capability_verifier import (
    CapabilityVerifier,
    VerificationReport,
)
from personal_ai_secretary.providers.connectivity import ConnectivityChecker
from personal_ai_secretary.providers.discovery import DiscoveredProvider, ProviderDiscovery
from personal_ai_secretary.providers.model_intelligence import (
    CODING_TASK_KEYWORDS,
)
from personal_ai_secretary.providers.model_registry import (
    ModelEntry,
    ModelRegistry,
    ModelStatus,
)
from personal_ai_secretary.shared.config import get_settings

logger = logging.getLogger("personal_ai_secretary.providers.model_manager")

_VERIFICATION_CACHE_TTL_HOURS = 24.0
_MAX_FALLBACK_ATTEMPTS = 3


@dataclass
class RoutingDecision:
    provider_name: str
    model_id: str
    reason: str
    fallback_chain: list[str] = field(default_factory=list)


class ModelManager:
    """Manages multi-provider model selection, verification, and routing."""

    def __init__(self) -> None:
        self._discovery = ProviderDiscovery()
        self._verifier = CapabilityVerifier()
        self._registry = ModelRegistry()
        self._connectivity = ConnectivityChecker()
        self._lock = threading.Lock()
        self._initialized = False
        self._selected_provider: str | None = None
        self._selected_model: str | None = None
        self._fallback_history: list[str] = []
        self._provider_instances: dict[str, Any] = {}

    @property
    def registry(self) -> ModelRegistry:
        return self._registry

    @property
    def connectivity(self) -> ConnectivityChecker:
        return self._connectivity

    @property
    def selected_provider(self) -> str | None:
        return self._selected_provider

    @property
    def selected_model(self) -> str | None:
        return self._selected_model

    async def initialize(self) -> None:
        if self._initialized:
            return
        logger.info("ModelManager: initializing provider discovery...")
        discovered = await self._discovery.discover_all()
        self._provider_instances = {}
        for name, dp in discovered.items():
            if dp.provider_instance is not None:
                self._provider_instances[name] = dp.provider_instance
            for model_id in dp.models:
                self._registry.register(
                    model_id=model_id,
                    provider_name=name,
                    display_name=f"{dp.display_name}: {model_id}",
                )
        self._initialized = True
        logger.info(
            "ModelManager: discovered %d providers, %d models",
            len(discovered),
            self._registry.count,
        )
        self._connectivity.check_background()

    async def discover_providers(self) -> dict[str, DiscoveredProvider]:
        await self.initialize()
        return self._discovery.discovered

    async def verify_model(
        self, provider_name: str, model_id: str
    ) -> VerificationReport:
        await self.initialize()
        entry = self._registry.get_key(model_id, provider_name)
        if entry is None:
            entry = self._registry.register(
                model_id=model_id, provider_name=provider_name
            )
        entry.status = ModelStatus.CHECKING

        provider = self._provider_instances.get(provider_name)
        if provider is None:
            entry.status = ModelStatus.UNAVAILABLE
            return VerificationReport(
                model_id=model_id,
                provider=provider_name,
            )

        report = await self._verifier.verify(provider, model_id, provider_name)

        entry.verified_capabilities.tests_passed = report.passed_count
        entry.verified_capabilities.tests_total = len(report.tests)
        entry.verified_capabilities.basic_response = any(
            t.test_name == "basic_response" and t.passed for t in report.tests
        )
        entry.verified_capabilities.system_prompt = any(
            t.test_name == "system_prompt_adherence" and t.passed for t in report.tests
        )
        entry.verified_capabilities.tool_calling = any(
            t.test_name == "tool_calling" and t.passed for t in report.tests
        )
        entry.verified_capabilities.argument_compatibility = any(
            t.test_name == "argument_compatibility" and t.passed for t in report.tests
        )
        entry.verified_capabilities.file_creation = any(
            t.test_name == "file_creation" and t.passed for t in report.tests
        )
        entry.verified_capabilities.file_modification = any(
            t.test_name == "file_modification" and t.passed for t in report.tests
        )
        entry.verified_capabilities.verification = any(
            t.test_name == "verification_understanding" and t.passed for t in report.tests
        )
        entry.verified_capabilities.coding = any(
            t.test_name == "coding" and t.passed for t in report.tests
        )
        entry.verified_capabilities.multi_step = any(
            t.test_name == "multi_step" and t.passed for t in report.tests
        )
        entry.verified_capabilities.security = any(
            t.test_name == "security" and t.passed for t in report.tests
        )
        entry.verified_capabilities.context_handling = any(
            t.test_name == "context_handling" and t.passed for t in report.tests
        )

        entry.last_verified = time.time()
        entry.measured_latency_ms = report.total_duration_seconds * 1000

        if report.overall_passed:
            entry.status = ModelStatus.VERIFIED
        elif report.passed_count >= len(report.tests) * 0.5:
            entry.status = ModelStatus.LIMITED
        else:
            entry.status = ModelStatus.FAILED

        logger.info(
            "Verification complete: %s/%s -> %s (%d/%d tests passed, %.1fs)",
            provider_name,
            model_id,
            entry.status.value,
            report.passed_count,
            len(report.tests),
            report.total_duration_seconds,
        )
        return report

    async def verify_all(self) -> dict[str, VerificationReport]:
        await self.initialize()
        reports: dict[str, VerificationReport] = {}
        for entry in self._registry.all_models():
            key = f"{entry.provider_name}:{entry.model_id}"
            report = await self.verify_model(entry.provider_name, entry.model_id)
            reports[key] = report
        return reports

    def select_for_task(
        self,
        task_description: str,
        needs_vision: bool = False,
        prefer_verified: bool = True,
    ) -> RoutingDecision | None:
        task_lower = task_description.lower()
        is_coding = any(kw in task_lower for kw in CODING_TASK_KEYWORDS)
        is_online = self._connectivity.is_online

        candidates = self._get_candidates(is_online, prefer_verified)
        if not candidates:
            return None

        scored: list[tuple[float, ModelEntry]] = []
        for entry in candidates:
            score = self._score_model(entry, is_coding, needs_vision, is_online)
            scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)

        if not scored:
            return None

        best_score, best = scored[0]
        if best_score <= 0:
            return None

        reason = self._routing_reason(best, is_coding, needs_vision, is_online)
        return RoutingDecision(
            provider_name=best.provider_name,
            model_id=best.model_id,
            reason=reason,
        )

    def _get_candidates(
        self, is_online: bool, prefer_verified: bool
    ) -> list[ModelEntry]:
        all_models = self._registry.all_models()
        if not is_online:
            return [
                m
                for m in all_models
                if m.provider_name in ("ollama", "opencode")
                and m.status != ModelStatus.UNAVAILABLE
            ]
        if prefer_verified:
            verified = [
                m for m in all_models if m.status == ModelStatus.VERIFIED
            ]
            if verified:
                return verified
        return [
            m for m in all_models if m.status != ModelStatus.UNAVAILABLE
        ]

    def _score_model(
        self,
        entry: ModelEntry,
        is_coding: bool,
        needs_vision: bool,
        is_online: bool,
    ) -> float:
        score = 0.0
        if entry.status == ModelStatus.VERIFIED:
            score += 10.0
        elif entry.status == ModelStatus.SLOW:
            score += 5.0
        elif entry.status == ModelStatus.LIMITED:
            score += 3.0
        elif entry.status == ModelStatus.UNKNOWN:
            score += 2.0
        else:
            return 0.0

        if is_coding:
            score += entry.coding_strength * 2.0
        if needs_vision and entry.supports_vision:
            score += 5.0
        elif needs_vision and not entry.supports_vision:
            score -= 5.0

        score += entry.reliability * 3.0
        if entry.measured_latency_ms > 0:
            if entry.measured_latency_ms < 5000:
                score += 3.0
            elif entry.measured_latency_ms < 15000:
                score += 1.0
            elif entry.measured_latency_ms > 60000:
                score -= 2.0

        if entry.provider_name == "gemini" and is_online:
            score += 2.0
        elif entry.provider_name == "ollama":
            score += 1.0
        return score

    def _routing_reason(
        self,
        entry: ModelEntry,
        is_coding: bool,
        needs_vision: bool,
        is_online: bool,
    ) -> str:
        parts = [f"Status={entry.status.value}"]
        if is_coding:
            parts.append(f"Coding={entry.coding_strength}/5")
        if needs_vision:
            parts.append(f"Vision={'yes' if entry.supports_vision else 'no'}")
        if entry.measured_latency_ms > 0:
            parts.append(f"Latency={entry.measured_latency_ms:.0f}ms")
        if not is_online:
            parts.append("Offline mode")
        return ", ".join(parts)

    def select_provider(self, provider_name: str, model_id: str | None = None) -> bool:
        with self._lock:
            if provider_name not in self._provider_instances:
                logger.warning("Provider '%s' not available", provider_name)
                return False
            entry = None
            if model_id:
                entry = self._registry.get_key(model_id, provider_name)
                if entry is None:
                    logger.warning(
                        "Model '%s' not found for provider '%s'",
                        model_id,
                        provider_name,
                    )
                    return False
            self._selected_provider = provider_name
            self._selected_model = model_id or (
                entry.model_id if entry else None
            )
            logger.info(
                "Selected provider=%s model=%s",
                self._selected_provider,
                self._selected_model,
            )
            return True

    def get_provider_instance(self, name: str | None = None) -> AIProvider | None:
        provider_name = name or self._selected_provider
        if provider_name is None:
            return self._auto_select()
        instance: AIProvider | None = self._provider_instances.get(provider_name)
        if instance is not None:
            return instance
        return self._auto_select()

    def _auto_select(self) -> AIProvider | None:
        settings = get_settings()
        if settings.ai_provider == "local":
            result: AIProvider | None = self._provider_instances.get("ollama")
            return result
        if settings.ai_provider == "remote":
            for name in ("gemini", "nvidia", "opencode"):
                inst: AIProvider | None = self._provider_instances.get(name)
                if inst is not None:
                    return inst
        if settings.ai_provider == "deterministic":
            from personal_ai_secretary.providers.deterministic import DeterministicProvider
            return DeterministicProvider()
        return None

    def get_fallback_provider(
        self, failed_provider: str, failed_model: str
    ) -> tuple[str, str] | None:
        self._fallback_history.append(f"{failed_provider}:{failed_model}")
        if len(self._fallback_history) > _MAX_FALLBACK_ATTEMPTS * 10:
            self._fallback_history = self._fallback_history[-50:]

        recent = self._fallback_history[-_MAX_FALLBACK_ATTEMPTS:]
        candidates = self._get_candidates(
            is_online=self._connectivity.is_online, prefer_verified=True
        )
        for entry in candidates:
            key = f"{entry.provider_name}:{entry.model_id}"
            if key in recent:
                continue
            if entry.provider_name == failed_provider and entry.model_id == failed_model:
                continue
            if entry.status in (ModelStatus.VERIFIED, ModelStatus.SLOW, ModelStatus.UNKNOWN):
                return (entry.provider_name, entry.model_id)
        return None

    def record_success(self, provider_name: str, model_id: str) -> None:
        entry = self._registry.get_key(model_id, provider_name)
        if entry:
            entry.record_success()

    def record_failure(self, provider_name: str, model_id: str) -> None:
        entry = self._registry.get_key(model_id, provider_name)
        if entry:
            entry.record_failure()
            if entry.failure_count >= 5 and entry.reliability < 0.3:
                entry.status = ModelStatus.FAILED

    def get_model_status(self, provider_name: str, model_id: str) -> dict[str, object]:
        entry = self._registry.get_key(model_id, provider_name)
        if entry is None:
            return {"status": "not_found"}
        return entry.to_dict()

    def get_all_status(self) -> list[dict[str, object]]:
        return [entry.to_dict() for entry in self._registry.all_models()]

    def get_recommended(self) -> dict[str, object] | None:
        decision = self.select_for_task("general", prefer_verified=True)
        if decision is None:
            return None
        entry = self._registry.get_key(decision.model_id, decision.provider_name)
        if entry is None:
            return None
        return entry.to_dict()
