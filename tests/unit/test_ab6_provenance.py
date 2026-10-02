"""FASE AB.6 — Provenance & manual-mode strictness tests.

Proves the execution chain (requested/selected/attempted/executed/status/
latency) is surfaced through the agent metadata, and that MANUAL routing mode
produces an explicit, honest error instead of a silent fallback.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from personal_ai_secretary.agents.builtin import ExecutionAgent
from personal_ai_secretary.agents.contracts import AgentInput
from personal_ai_secretary.domain.contracts import ProviderResponse, RiskLevel
from personal_ai_secretary.observability.request_metrics import RequestMetrics


def _input(text: str = "hola") -> AgentInput:
    return AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test-user",
        text=text,
        correlation_id="test-corr",
        risk_level=RiskLevel.LOW,
        context={},
    )


def _mock_provider(name: str = "mock", model: str = "m1", error: Exception | None = None) -> MagicMock:
    provider = MagicMock()
    provider.name = name
    provider.model = model

    async def generate(request: Any) -> ProviderResponse:
        if error is not None:
            raise error
        return ProviderResponse(text="Done.", provider=name, model=model)

    provider.generate = AsyncMock(side_effect=generate)
    return provider


def _plain_registry() -> MagicMock:
    registry = MagicMock()
    registry.names = MagicMock(return_value=[])
    registry.execute = AsyncMock(
        side_effect=lambda name, args, approved=False: {"result": "ok"}
    )
    return registry


class TestProvenanceExposed:
    async def test_metadata_includes_full_chain(self) -> None:
        agent = ExecutionAgent(provider=_mock_provider(), registry=_plain_registry())
        artifact = await agent.run(_input())
        meta = artifact.metadata
        assert meta["executed_provider"] == "mock"
        assert meta["executed_model"] == "m1"
        assert meta["requested_provider"] == "mock"
        assert meta["selected_provider"] == "mock"
        assert meta["selected_model"] == "m1"
        assert meta["status"] == "ok"
        assert isinstance(meta["latency_ms"], int) and meta["latency_ms"] >= 0
        assert meta["fallback_chain"] == []
        assert meta["fallback_active"] is False

    async def test_provenance_recorded_in_metrics(self) -> None:
        agent = ExecutionAgent(provider=_mock_provider(), registry=_plain_registry())
        await agent.run(_input())
        m: RequestMetrics | None = agent.last_metrics
        assert m is not None
        assert m.selected_provider == "mock"
        assert m.selected_model == "m1"
        assert m.status == "ok"
        assert m.latency_ms >= 0


class TestManualModeStrict:
    async def test_manual_mode_explicit_error_and_no_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from personal_ai_secretary.providers import factory
        from personal_ai_secretary.providers.model_manager import ModelManager

        manager = ModelManager()
        manager.set_routing_mode("manual")
        monkeypatch.setattr(factory, "get_model_manager", lambda: manager)

        monkeypatch.setattr(
            ModelManager,
            "_get_candidates",
            lambda self, is_online, prefer_verified: [],
        )

        provider = _mock_provider(
            name="opencode", error=RuntimeError("OpenCode authentication failed (HTTP 401)")
        )
        agent = ExecutionAgent(provider=provider, registry=_plain_registry())
        artifact = await agent.run(_input())
        assert "Manual mode" in artifact.content
        assert "fallback" in artifact.content.lower()
        m = agent.last_metrics
        assert m is not None
        assert m.status == "unauthorized"

    async def test_manual_mode_runtime_failure_status(self) -> None:
        from personal_ai_secretary.agents.builtin import _status_for_exception

        assert _status_for_exception(TimeoutError("slow")) == "timeout"
        assert _status_for_exception(ConnectionError("down")) == "connection_error"
        assert (
            _status_for_exception(RuntimeError("Gemini API key is invalid or expired"))
            == "unauthorized"
        )
        assert _status_for_exception(RuntimeError("boom")) == "failed"
        assert _status_for_exception(ValueError("unknown")) == "unknown"