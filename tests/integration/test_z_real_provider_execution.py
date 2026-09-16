"""Tests for FASE Z real provider execution, routing strictness, and provenance."""

from __future__ import annotations

import pytest

from personal_ai_secretary.providers.routing import Router, RoutingMode


class TestPhaseZRoutingAndProviders:
    """Test suite for FASE Z objectives: manual strictness, auto-routing, provenance."""

    @pytest.mark.asyncio
    async def test_manual_strictness_no_silent_fallback(self) -> None:
        """Verify that in manual mode with an unavailable provider, routing does not fall back."""
        # Register a fake/unavailable instance or none for provider "missing"
        router = Router(mode=RoutingMode.MANUAL, provider_instances={})
        target = await router.select_manual("missing", "some-model")
        assert target.locked
        assert target.available is False
        assert router.verify_match() is True

    @pytest.mark.asyncio
    async def test_automatic_routing_prefers_available(self) -> None:
        """Verify automatic selection scores and picks the best available provider."""
        from personal_ai_secretary.providers.deterministic import DeterministicProvider
        router = Router(
            mode=RoutingMode.AUTOMATIC,
            provider_instances={"deterministic": DeterministicProvider()},
        )
        target = await router.select_automatic("general task")
        assert target.provider == "deterministic"

    @pytest.mark.asyncio
    async def test_provenance_tracking(self) -> None:
        """Verify execution provenance captures requested vs actual provider/model."""
        router = Router(mode=RoutingMode.MANUAL, provider_instances={})
        router.provenance.requested_provider = "opencode"
        router.provenance.requested_model = "big-pickle"
        router.provenance.resolved_provider = "opencode"
        router.provenance.resolved_model = "big-pickle"
        d = router.provenance.to_dict()
        assert d["requested_provider"] == "opencode"
        assert d["requested_model"] == "big-pickle"
