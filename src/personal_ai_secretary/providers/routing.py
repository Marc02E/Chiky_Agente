"""FASE W — User-Controlled Model Routing & Provider Enforcement.

Ensures that when a user manually selects a provider/model, Chiky uses
exactly that provider/model or reports it cannot. No silent fallback.

Architecture:
  Router
    ├── ProviderVerifier (health + model check)
    ├── ExecutionTarget (locked routing decision)
    ├── ExecutionProvenance (evidence chain)
    └── Mode enforcement (manual=strict, auto=flexible)
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger("personal_ai_secretary.providers.routing")


class RoutingMode(StrEnum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"


class ProviderStatus(StrEnum):
    AVAILABLE_VERIFIED = "available_verified"
    AVAILABLE_NOT_VERIFIED = "available_not_verified"
    UNAVAILABLE = "unavailable"
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class ProviderIdentity:
    """Verified identity of a provider after health check."""
    provider: str
    model: str | None
    available: bool
    verified: bool
    latency_ms: float
    capabilities: dict[str, Any] = field(default_factory=dict)
    endpoint: str = ""
    verification_id: str = ""


@dataclass
class ExecutionTarget:
    """Locked execution target — once locked, cannot be silently changed."""
    provider: str
    model: str | None
    mode: RoutingMode
    locked: bool = False
    verified: bool = False
    available: bool = False
    verification_id: str = ""
    verification_status: ProviderStatus = ProviderStatus.NOT_CONFIGURED
    latency_ms: float = 0.0
    identity: ProviderIdentity | None = None

    def lock(self, identity: ProviderIdentity) -> None:
        """Lock the target after successful verification."""
        if self.locked:
            raise RuntimeError(
                f"ExecutionTarget already locked to {self.provider}/{self.model}"
            )
        self.verified = identity.verified
        self.available = identity.available
        self.verification_id = identity.verification_id
        self.latency_ms = identity.latency_ms
        self.identity = identity
        if identity.available and identity.verified:
            self.verification_status = ProviderStatus.AVAILABLE_VERIFIED
        elif identity.available:
            self.verification_status = ProviderStatus.AVAILABLE_NOT_VERIFIED
        else:
            self.verification_status = ProviderStatus.UNAVAILABLE
        self.locked = True


@dataclass
class ExecutionProvenance:
    """Complete evidence chain for what provider actually executed."""
    requested_provider: str = ""
    requested_model: str | None = None
    resolved_provider: str = ""
    resolved_model: str | None = None
    actual_provider: str = ""
    actual_model: str | None = None
    execution_mode: str = ""
    fallback_used: bool = False
    fallback_from_provider: str = ""
    fallback_from_model: str = ""
    verification_status: str = ""
    execution_started_at: float = 0.0
    execution_finished_at: float = 0.0
    request_id: str = ""
    execution_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_provider": self.requested_provider,
            "requested_model": self.requested_model,
            "resolved_provider": self.resolved_provider,
            "resolved_model": self.resolved_model,
            "actual_provider": self.actual_provider,
            "actual_model": self.actual_model,
            "execution_mode": self.execution_mode,
            "fallback_used": self.fallback_used,
            "fallback_from_provider": self.fallback_from_provider,
            "fallback_from_model": self.fallback_from_model,
            "verification_status": self.verification_status,
            "execution_started_at": self.execution_started_at,
            "execution_finished_at": self.execution_finished_at,
            "request_id": self.request_id,
            "execution_id": self.execution_id,
        }


class ProviderVerifier:
    """Verifies provider availability and model identity."""

    async def verify_provider(
        self,
        provider_instance: Any,
        provider_name: str,
        model_id: str | None = None,
    ) -> ProviderIdentity:
        """Run health check and optional model verification."""
        verification_id = str(uuid.uuid4())[:8]
        start = time.monotonic()

        try:
            health = await provider_instance.health()
            latency_ms = (time.monotonic() - start) * 1000
            available = health.available

            model_match = True
            if model_id and hasattr(provider_instance, "model"):
                actual_model = getattr(provider_instance, "model", None)
                if actual_model and actual_model != model_id:
                    model_match = False
                    logger.warning(
                        "Model mismatch: requested=%s actual=%s",
                        model_id, actual_model,
                    )

            return ProviderIdentity(
                provider=provider_name,
                model=actual_model if hasattr(provider_instance, "model") else model_id,
                available=available,
                verified=available and model_match,
                latency_ms=latency_ms,
                endpoint=health.detail if hasattr(health, "detail") else "",
                verification_id=verification_id,
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - start) * 1000
            logger.warning(
                "Provider verification failed: %s/%s: %s",
                provider_name, model_id, exc,
            )
            return ProviderIdentity(
                provider=provider_name,
                model=model_id,
                available=False,
                verified=False,
                latency_ms=latency_ms,
                verification_id=verification_id,
            )


class Router:
    """Enforces user-controlled model routing.

    MANUAL mode: verify target, lock it, no fallback.
    AUTOMATIC mode: score candidates, allow fallback with logging.
    """

    def __init__(
        self,
        mode: RoutingMode = RoutingMode.AUTOMATIC,
        provider_instances: dict[str, Any] | None = None,
    ) -> None:
        self.mode = mode
        self._provider_instances = provider_instances or {}
        self._verifier = ProviderVerifier()
        self._provenance = ExecutionProvenance()
        self._target: ExecutionTarget | None = None
        self._fallback_log: list[dict[str, str]] = []

    @property
    def provenance(self) -> ExecutionProvenance:
        return self._provenance

    @property
    def target(self) -> ExecutionTarget | None:
        return self._target

    @property
    def fallback_log(self) -> list[dict[str, str]]:
        return list(self._fallback_log)

    async def select_manual(
        self,
        provider_name: str,
        model_id: str | None = None,
    ) -> ExecutionTarget:
        """MANUAL mode: verify and lock target. No fallback allowed."""
        self._provenance.requested_provider = provider_name
        self._provenance.requested_model = model_id
        self._provenance.execution_mode = RoutingMode.MANUAL

        target = ExecutionTarget(
            provider=provider_name,
            model=model_id,
            mode=RoutingMode.MANUAL,
        )

        instance = self._provider_instances.get(provider_name)
        if instance is None:
            target.verification_status = ProviderStatus.NOT_CONFIGURED
            target.locked = True
            self._target = target
            self._provenance.resolved_provider = provider_name
            self._provenance.resolved_model = model_id
            self._provenance.verification_status = ProviderStatus.NOT_CONFIGURED
            logger.warning(
                "MANUAL: provider '%s' not configured", provider_name,
            )
            return target

        identity = await self._verifier.verify_provider(
            instance, provider_name, model_id,
        )
        target.lock(identity)
        self._target = target

        self._provenance.resolved_provider = provider_name
        self._provenance.resolved_model = identity.model or model_id
        self._provenance.verification_status = target.verification_status.value

        if not identity.available:
            logger.warning(
                "MANUAL: provider '%s' unavailable — execution blocked", provider_name,
            )
        elif not identity.verified:
            logger.warning(
                "MANUAL: provider '%s' available but model mismatch", provider_name,
            )
        else:
            logger.info(
                "MANUAL: target locked %s/%s (verified=%s)",
                provider_name, identity.model or model_id, identity.verified,
            )

        return target

    async def select_automatic(
        self,
        task_description: str = "",
        needs_vision: bool = False,
    ) -> ExecutionTarget:
        """AUTOMATIC mode: score candidates, select best, allow fallback."""
        self._provenance.execution_mode = RoutingMode.AUTOMATIC

        from personal_ai_secretary.providers.model_intelligence import CODING_TASK_KEYWORDS

        task_lower = task_description.lower()
        is_coding = any(kw in task_lower for kw in CODING_TASK_KEYWORDS)

        best_name: str | None = None
        best_model: str | None = None
        best_score = -1.0
        best_instance: Any = None

        for name, instance in self._provider_instances.items():
            score = self._score_provider(name, instance, is_coding, needs_vision)
            if score > best_score:
                best_score = score
                best_name = name
                best_instance = instance
                best_model = getattr(instance, "model", None)

        if best_name is None or best_score <= 0:
            target = ExecutionTarget(
                provider="none",
                model=None,
                mode=RoutingMode.AUTOMATIC,
                verification_status=ProviderStatus.UNAVAILABLE,
            )
            target.locked = True
            self._target = target
            self._provenance.verification_status = ProviderStatus.UNAVAILABLE.value
            return target

        identity = await self._verifier.verify_provider(
            best_instance, best_name, best_model,
        )
        target = ExecutionTarget(
            provider=best_name,
            model=best_model,
            mode=RoutingMode.AUTOMATIC,
        )
        target.lock(identity)
        self._target = target

        self._provenance.requested_provider = best_name
        self._provenance.requested_model = best_model
        self._provenance.resolved_provider = best_name
        self._provenance.resolved_model = best_model
        self._provenance.verification_status = target.verification_status.value

        logger.info(
            "AUTOMATIC: selected %s/%s (score=%.1f)",
            best_name, best_model, best_score,
        )
        return target

    def _score_provider(
        self,
        name: str,
        instance: Any,
        is_coding: bool,
        needs_vision: bool,
    ) -> float:
        score = 1.0
        if name == "ollama":
            score += 3.0
        elif name == "gemini":
            score += 2.0
        elif name == "opencode":
            score += 1.5

        model_name = getattr(instance, "model", "")
        if model_name:
            from personal_ai_secretary.providers.model_intelligence import (
                get_model_capabilities,
            )
            caps = get_model_capabilities(model_name)
            if is_coding:
                score += caps.coding_strength * 1.5
            if needs_vision and caps.supports_vision:
                score += 3.0
            elif needs_vision:
                score -= 2.0
            score += caps.stability * 0.5

        return score

    def record_fallback(
        self,
        from_provider: str,
        from_model: str,
        to_provider: str,
        to_model: str,
        reason: str,
    ) -> None:
        """Record a fallback event (AUTOMATIC mode only)."""
        entry = {
            "from_provider": from_provider,
            "from_model": from_model,
            "to_provider": to_provider,
            "to_model": to_model,
            "reason": reason,
        }
        self._fallback_log.append(entry)
        self._provenance.fallback_used = True
        self._provenance.fallback_from_provider = from_provider
        self._provenance.fallback_from_model = from_model
        self._provenance.actual_provider = to_provider
        self._provenance.actual_model = to_model
        logger.warning(
            "FALLBACK: %s/%s -> %s/%s (reason: %s)",
            from_provider, from_model, to_provider, to_model, reason,
        )

    def lock_actual(self, provider: str, model: str | None) -> None:
        """Lock the actual provider/model after execution."""
        self._provenance.actual_provider = provider
        self._provenance.actual_model = model
        self._provenance.execution_started_at = time.time()

    def finish_execution(self) -> None:
        """Mark execution as finished."""
        self._provenance.execution_finished_at = time.time()

    def verify_match(self) -> bool:
        """Verify requested matches actual (for manual mode)."""
        if self.mode != RoutingMode.MANUAL:
            return True
        if not self._provenance.requested_provider:
            return True
        if not self._provenance.actual_provider:
            return True
        return (
            self._provenance.requested_provider == self._provenance.actual_provider
            and self._provenance.requested_model == self._provenance.actual_model
        )
