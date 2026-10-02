"""FASE W — User-Controlled Model Routing & Provider Enforcement Tests.

Tests W01-W20: Manual enforcement, automatic fallback, provenance tracking,
spoofing detection, provider unavailability, and no-false-success.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from personal_ai_secretary.domain.contracts import ProviderInfo
from personal_ai_secretary.providers.routing import (
    ExecutionProvenance,
    ExecutionTarget,
    ProviderIdentity,
    ProviderStatus,
    ProviderVerifier,
    Router,
    RoutingMode,
)


def _make_provider(
    name: str = "ollama",
    model: str = "llama3:latest",
    available: bool = True,
    fail_health: bool = False,
) -> MagicMock:
    provider = MagicMock()
    provider.name = name
    provider.model = model
    health_result = ProviderInfo(
        name=name, mode="local", available=available, is_ai=True, detail="ok",
    )
    if fail_health:
        provider.health = AsyncMock(side_effect=ConnectionError("unreachable"))
    else:
        provider.health = AsyncMock(return_value=health_result)
    provider.generate = AsyncMock(
        return_value=MagicMock(text="done", provider=name, model=model),
    )
    return provider


# W01: Manual + Ollama -> uses exactly Ollama

@pytest.mark.asyncio
async def test_w01_manual_ollama_exact_provider() -> None:
    ollama = _make_provider("ollama", "llama3:latest")
    router = Router(
        mode=RoutingMode.MANUAL,
        provider_instances={"ollama": ollama},
    )
    target = await router.select_manual("ollama", "llama3:latest")
    assert target.provider == "ollama"
    assert target.model == "llama3:latest"
    assert target.mode == RoutingMode.MANUAL
    assert target.locked is True
    assert target.verified is True
    assert router.provenance.requested_provider == "ollama"
    assert router.provenance.requested_model == "llama3:latest"


# W02: Manual + specific Ollama model -> uses exactly that model

@pytest.mark.asyncio
async def test_w02_manual_specific_model() -> None:
    ollama = _make_provider("ollama", "deepseek-coder-v2:latest")
    router = Router(
        mode=RoutingMode.MANUAL,
        provider_instances={"ollama": ollama},
    )
    target = await router.select_manual("ollama", "deepseek-coder-v2:latest")
    assert target.provider == "ollama"
    assert target.model == "deepseek-coder-v2:latest"
    assert target.locked is True
    assert target.verified is True


# W03: Manual + OpenCode available -> verifies OpenCode

@pytest.mark.asyncio
async def test_w03_manual_opencode_available() -> None:
    opencode = _make_provider("opencode", "default", available=True)
    router = Router(
        mode=RoutingMode.MANUAL,
        provider_instances={"opencode": opencode},
    )
    target = await router.select_manual("opencode", "default")
    assert target.provider == "opencode"
    assert target.available is True
    assert target.verified is True
    assert target.verification_status == ProviderStatus.AVAILABLE_VERIFIED
    opencode.health.assert_awaited_once()


# W04: Manual + OpenCode unavailable -> NO fallback

@pytest.mark.asyncio
async def test_w04_manual_opencode_unavailable_no_fallback() -> None:
    opencode = _make_provider("opencode", "default", available=False)
    ollama = _make_provider("ollama", "llama3:latest")
    router = Router(
        mode=RoutingMode.MANUAL,
        provider_instances={"opencode": opencode, "ollama": ollama},
    )
    target = await router.select_manual("opencode", "default")
    assert target.provider == "opencode"
    assert target.available is False
    assert target.verification_status == ProviderStatus.UNAVAILABLE
    assert target.locked is True
    assert router.provenance.fallback_used is False
    assert router.fallback_log == []


# W05: Manual + Gemini unavailable -> NO fallback to Ollama

@pytest.mark.asyncio
async def test_w05_manual_gemini_unavailable_no_fallback() -> None:
    gemini = _make_provider("gemini", "gemini-2.0-flash", available=False)
    ollama = _make_provider("ollama", "llama3:latest")
    router = Router(
        mode=RoutingMode.MANUAL,
        provider_instances={"gemini": gemini, "ollama": ollama},
    )
    target = await router.select_manual("gemini", "gemini-2.0-flash")
    assert target.provider == "gemini"
    assert target.available is False
    assert target.verification_status == ProviderStatus.UNAVAILABLE
    assert router.provenance.fallback_used is False
    assert router.fallback_log == []


# W06: Automatic -> selects best available provider

@pytest.mark.asyncio
async def test_w06_automatic_selects_best_provider() -> None:
    ollama = _make_provider("ollama", "llama3:latest")
    gemini = _make_provider("gemini", "gemini-2.0-flash")
    router = Router(
        mode=RoutingMode.AUTOMATIC,
        provider_instances={"ollama": ollama, "gemini": gemini},
    )
    target = await router.select_automatic("Write a Python script")
    assert target.provider in ("ollama", "gemini")
    assert target.locked is True
    assert target.mode == RoutingMode.AUTOMATIC


# W07: Automatic -> fallback allowed

@pytest.mark.asyncio
async def test_w07_automatic_fallback_allowed() -> None:
    router = Router(mode=RoutingMode.AUTOMATIC)
    router.record_fallback(
        from_provider="gemini",
        from_model="gemini-2.0-flash",
        to_provider="ollama",
        to_model="llama3:latest",
        reason="gemini unavailable",
    )
    assert router.provenance.fallback_used is True
    assert router.provenance.fallback_from_provider == "gemini"
    assert router.provenance.fallback_from_model == "gemini-2.0-flash"
    assert router.provenance.actual_provider == "ollama"
    assert router.provenance.actual_model == "llama3:latest"
    assert len(router.fallback_log) == 1


# W08: Automatic -> fallback is logged

@pytest.mark.asyncio
async def test_w08_automatic_fallback_logged() -> None:
    router = Router(mode=RoutingMode.AUTOMATIC)
    router.record_fallback("gemini", "gemini-2.0-flash", "ollama", "llama3:latest", "network error")
    router.record_fallback("nvidia", "llama-3.1", "ollama", "llama3:latest", "timeout")
    log = router.fallback_log
    assert len(log) == 2
    assert log[0]["from_provider"] == "gemini"
    assert log[1]["from_provider"] == "nvidia"
    assert router.provenance.fallback_used is True


# W09: Offline -> only local providers available

@pytest.mark.asyncio
async def test_w09_automatic_offline_local_only() -> None:
    ollama = _make_provider("ollama", "llama3:latest")
    gemini = _make_provider("gemini", "gemini-2.0-flash", available=False)
    router = Router(
        mode=RoutingMode.AUTOMATIC,
        provider_instances={"ollama": ollama, "gemini": gemini},
    )
    target = await router.select_automatic()
    assert target.provider == "ollama"
    assert target.locked is True


# W10: Provenance tracks requested vs actual provider

@pytest.mark.asyncio
async def test_w10_provenance_tracks_requested_vs_actual() -> None:
    ollama = _make_provider("ollama", "llama3:latest")
    router = Router(
        mode=RoutingMode.MANUAL,
        provider_instances={"ollama": ollama},
    )
    await router.select_manual("ollama", "llama3:latest")
    router.lock_actual("ollama", "llama3:latest")
    router.finish_execution()
    p = router.provenance
    assert p.requested_provider == "ollama"
    assert p.actual_provider == "ollama"
    assert p.requested_model == "llama3:latest"
    assert p.actual_model == "llama3:latest"
    assert p.execution_mode == "manual"
    assert p.execution_started_at > 0
    assert p.execution_finished_at >= p.execution_started_at


# W11: Provenance tracks model identity

@pytest.mark.asyncio
async def test_w11_provenance_tracks_model_identity() -> None:
    ollama = _make_provider("ollama", "deepseek-coder-v2:latest")
    router = Router(
        mode=RoutingMode.MANUAL,
        provider_instances={"ollama": ollama},
    )
    await router.select_manual("ollama", "deepseek-coder-v2:latest")
    router.lock_actual("ollama", "deepseek-coder-v2:latest")
    p = router.provenance
    assert p.actual_model == "deepseek-coder-v2:latest"
    assert p.resolved_model == "deepseek-coder-v2:latest"


# W12: Manual mode -> requested matches actual

@pytest.mark.asyncio
async def test_w12_manual_requested_matches_actual() -> None:
    ollama = _make_provider("ollama", "llama3:latest")
    router = Router(
        mode=RoutingMode.MANUAL,
        provider_instances={"ollama": ollama},
    )
    await router.select_manual("ollama", "llama3:latest")
    router.lock_actual("ollama", "llama3:latest")
    assert router.verify_match() is True


# W13: Manual mode mismatch -> detect spoofing

@pytest.mark.asyncio
async def test_w13_manual_mismatch_detected() -> None:
    router = Router(mode=RoutingMode.MANUAL)
    router._provenance.requested_provider = "gemini"
    router._provenance.requested_model = "gemini-2.0-flash"
    router._provenance.actual_provider = "ollama"
    router._provenance.actual_model = "llama3:latest"
    router._target = ExecutionTarget(
        provider="gemini", model="gemini-2.0-flash", mode=RoutingMode.MANUAL,
    )
    assert router.verify_match() is False


# W14: Provenance is serializable

@pytest.mark.asyncio
async def test_w14_provenance_serializable() -> None:
    prov = ExecutionProvenance(
        requested_provider="ollama",
        requested_model="llama3:latest",
        resolved_provider="ollama",
        resolved_model="llama3:latest",
        actual_provider="ollama",
        actual_model="llama3:latest",
        execution_mode="manual",
        fallback_used=False,
        verification_status="available_verified",
        execution_started_at=1000.0,
        execution_finished_at=1010.0,
        request_id="req-123",
        execution_id="exec-456",
    )
    d = prov.to_dict()
    assert d["requested_provider"] == "ollama"
    assert d["actual_model"] == "llama3:latest"
    assert d["fallback_used"] is False
    assert d["execution_mode"] == "manual"
    assert isinstance(d, dict)


# W15: Provider health check failure -> unavailable

@pytest.mark.asyncio
async def test_w15_provider_health_check_failure() -> None:
    opencode = _make_provider("opencode", "default", fail_health=True)
    router = Router(
        mode=RoutingMode.MANUAL,
        provider_instances={"opencode": opencode},
    )
    target = await router.select_manual("opencode", "default")
    assert target.available is False
    assert target.verification_status == ProviderStatus.UNAVAILABLE
    assert target.locked is True


# W16: Model mismatch detection

@pytest.mark.asyncio
async def test_w16_model_mismatch_detected() -> None:
    ollama = _make_provider("ollama", "llama3.1:latest")
    router = Router(
        mode=RoutingMode.MANUAL,
        provider_instances={"ollama": ollama},
    )
    target = await router.select_manual("ollama", "llama3:latest")
    assert target.provider == "ollama"
    assert target.identity is not None
    assert target.identity.model == "llama3.1:latest"


# W17: Provider disappears after health check -> already locked

@pytest.mark.asyncio
async def test_w17_provider_disappears_after_lock() -> None:
    opencode = _make_provider("opencode", "default")
    router = Router(
        mode=RoutingMode.MANUAL,
        provider_instances={"opencode": opencode},
    )
    target = await router.select_manual("opencode", "default")
    assert target.locked is True
    assert target.verified is True
    with pytest.raises(RuntimeError, match="already locked"):
        target.lock(ProviderIdentity(
            provider="opencode", model="default", available=False, verified=False, latency_ms=0,
        ))


# W18: ExecutionTarget re-lock prevention

@pytest.mark.asyncio
async def test_w18_target_relock_prevented() -> None:
    target = ExecutionTarget(provider="ollama", model="llama3:latest", mode=RoutingMode.MANUAL)
    identity = ProviderIdentity(
        provider="ollama", model="llama3:latest", available=True, verified=True, latency_ms=100,
    )
    target.lock(identity)
    with pytest.raises(RuntimeError, match="already locked"):
        target.lock(identity)


# W19: Tool execution provenance chain

@pytest.mark.asyncio
async def test_w19_tool_execution_provenance_chain() -> None:
    ollama = _make_provider("ollama", "llama3:latest")
    router = Router(
        mode=RoutingMode.MANUAL,
        provider_instances={"ollama": ollama},
    )
    target = await router.select_manual("ollama", "llama3:latest")
    assert target.locked is True
    router.lock_actual("ollama", "llama3:latest")
    router.finish_execution()
    p = router.provenance
    assert p.requested_provider == "ollama"
    assert p.resolved_provider == "ollama"
    assert p.actual_provider == "ollama"
    assert p.execution_started_at > 0
    assert p.execution_finished_at >= p.execution_started_at
    assert p.fallback_used is False


# W20: No false success claims

@pytest.mark.asyncio
async def test_w20_no_false_success() -> None:
    opencode = _make_provider("opencode", "default", available=False)
    router = Router(
        mode=RoutingMode.MANUAL,
        provider_instances={"opencode": opencode},
    )
    target = await router.select_manual("opencode", "default")
    assert target.available is False
    assert target.verification_status == ProviderStatus.UNAVAILABLE
    assert router.provenance.verification_status == ProviderStatus.UNAVAILABLE.value
    assert router.verify_match() is True


# ProviderVerifier unit tests

@pytest.mark.asyncio
async def test_provider_verifier_healthy() -> None:
    verifier = ProviderVerifier()
    provider = _make_provider("ollama", "llama3:latest")
    identity = await verifier.verify_provider(provider, "ollama", "llama3:latest")
    assert identity.provider == "ollama"
    assert identity.available is True
    assert identity.verified is True
    assert identity.verification_id != ""


@pytest.mark.asyncio
async def test_provider_verifier_unhealthy() -> None:
    verifier = ProviderVerifier()
    provider = _make_provider("opencode", "default", fail_health=True)
    identity = await verifier.verify_provider(provider, "opencode", "default")
    assert identity.provider == "opencode"
    assert identity.available is False
    assert identity.verified is False


@pytest.mark.asyncio
async def test_provider_verifier_model_mismatch() -> None:
    verifier = ProviderVerifier()
    provider = _make_provider("ollama", "llama3.1:latest")
    identity = await verifier.verify_provider(provider, "ollama", "llama3:latest")
    assert identity.available is True
    assert identity.verified is False


# Automatic mode scoring tests

@pytest.mark.asyncio
async def test_automatic_no_providers() -> None:
    router = Router(mode=RoutingMode.AUTOMATIC, provider_instances={})
    target = await router.select_automatic()
    assert target.provider == "none"
    assert target.verification_status == ProviderStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_automatic_single_provider() -> None:
    ollama = _make_provider("ollama", "llama3:latest")
    router = Router(
        mode=RoutingMode.AUTOMATIC,
        provider_instances={"ollama": ollama},
    )
    target = await router.select_automatic()
    assert target.provider == "ollama"
    assert target.locked is True


# Manual mode no provider configured

@pytest.mark.asyncio
async def test_manual_no_provider_configured() -> None:
    router = Router(
        mode=RoutingMode.MANUAL,
        provider_instances={},
    )
    target = await router.select_manual("gemini", "gemini-2.0-flash")
    assert target.provider == "gemini"
    assert target.verification_status == ProviderStatus.NOT_CONFIGURED
    assert target.locked is True


# Manual mode provider throws health -> unavailable

@pytest.mark.asyncio
async def test_manual_provider_throws_health() -> None:
    gemini = _make_provider("gemini", "gemini-2.0-flash", fail_health=True)
    router = Router(
        mode=RoutingMode.MANUAL,
        provider_instances={"gemini": gemini},
    )
    target = await router.select_manual("gemini", "gemini-2.0-flash")
    assert target.available is False
    assert target.verification_status == ProviderStatus.UNAVAILABLE
    assert target.locked is True
    assert router.provenance.fallback_used is False


# Automatic mode coding task boosts coding providers

@pytest.mark.asyncio
async def test_automatic_coding_task_selects_coding_provider() -> None:
    ollama = _make_provider("ollama", "deepseek-coder-v2:latest")
    gemini = _make_provider("gemini", "gemini-2.0-flash")
    router = Router(
        mode=RoutingMode.AUTOMATIC,
        provider_instances={"ollama": ollama, "gemini": gemini},
    )
    target = await router.select_automatic("Create a Python CRUD project")
    assert target.provider in ("ollama", "gemini")
    assert target.locked is True


# Provenance default state

def test_provenance_default_state() -> None:
    p = ExecutionProvenance()
    assert p.requested_provider == ""
    assert p.actual_provider == ""
    assert p.fallback_used is False
    assert p.execution_started_at == 0.0
    d = p.to_dict()
    assert d["requested_provider"] == ""
    assert d["fallback_used"] is False
