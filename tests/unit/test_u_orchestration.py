"""FASE U: Intelligent Multi-Provider Orchestration Tests.

Tests for:
- ModelManager wiring into request flow
- Fallback logic in ExecutionAgent
- Feedback loop (success/failure recording)
- Auto-select and manual select endpoints
- Provider configuration endpoints
- Capability verifier with real filesystem tests
"""

from unittest.mock import MagicMock, patch

from personal_ai_secretary.providers.model_manager import ModelManager, RoutingDecision
from personal_ai_secretary.providers.model_registry import ModelStatus

# ═══════════════════════════════════════════════════════════════════════════
# ModelManager Feedback Loop
# ═══════════════════════════════════════════════════════════════════════════


class TestModelManagerFeedback:
    """Tests for recording success/failure feedback in ModelManager."""

    def test_record_success_increases_reliability(self) -> None:
        manager = ModelManager()
        manager._registry.register(
            model_id="test-model", provider_name="test-provider"
        )
        entry = manager._registry.get_key("test-model", "test-provider")
        assert entry is not None
        initial_reliability = entry.reliability
        manager.record_success("test-provider", "test-model")
        assert entry.reliability >= initial_reliability

    def test_record_failure_decreases_reliability(self) -> None:
        manager = ModelManager()
        manager._registry.register(
            model_id="test-model", provider_name="test-provider"
        )
        entry = manager._registry.get_key("test-model", "test-provider")
        assert entry is not None
        initial_reliability = entry.reliability
        manager.record_failure("test-provider", "test-model")
        assert entry.reliability <= initial_reliability

    def test_record_failure_sets_failed_status_after_threshold(self) -> None:
        manager = ModelManager()
        manager._registry.register(
            model_id="test-model", provider_name="test-provider"
        )
        entry = manager._registry.get_key("test-model", "test-provider")
        assert entry is not None
        # Record 5 failures with low reliability
        for _ in range(5):
            manager.record_failure("test-provider", "test-model")
        assert entry.status == ModelStatus.FAILED

    def test_record_success_nonexistent_model(self) -> None:
        manager = ModelManager()
        # Should not raise
        manager.record_success("nonexistent", "nonexistent")

    def test_record_failure_nonexistent_model(self) -> None:
        manager = ModelManager()
        # Should not raise
        manager.record_failure("nonexistent", "nonexistent")


# ═══════════════════════════════════════════════════════════════════════════
# ModelManager Fallback Logic
# ═══════════════════════════════════════════════════════════════════════════


class TestModelManagerFallback:
    """Tests for fallback provider selection."""

    def test_get_fallback_provider_returns_alternative(self) -> None:
        manager = ModelManager()
        manager._registry.register(
            model_id="model-a", provider_name="provider-a"
        )
        manager._registry.register(
            model_id="model-b", provider_name="provider-b"
        )
        # Mark both as available
        entry_a = manager._registry.get_key("model-a", "provider-a")
        entry_b = manager._registry.get_key("model-b", "provider-b")
        assert entry_a is not None
        assert entry_b is not None
        entry_a.status = ModelStatus.VERIFIED
        entry_b.status = ModelStatus.VERIFIED

        result = manager.get_fallback_provider("provider-a", "model-a")
        assert result is not None
        provider_name, model_id = result
        assert provider_name == "provider-b"
        assert model_id == "model-b"

    def test_get_fallback_provider_returns_none_when_exhausted(self) -> None:
        manager = ModelManager()
        manager._registry.register(
            model_id="model-a", provider_name="provider-a"
        )
        entry = manager._registry.get_key("model-a", "provider-a")
        assert entry is not None
        entry.status = ModelStatus.VERIFIED

        # Exhaust fallback attempts
        for _ in range(4):
            manager.get_fallback_provider("provider-a", "model-a")
        result = manager.get_fallback_provider("provider-a", "model-a")
        # Should return None when no more candidates
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# ModelManager Selection
# ═══════════════════════════════════════════════════════════════════════════


class TestModelManagerSelection:
    """Tests for provider/model selection."""

    def test_select_provider_sets_selected(self) -> None:
        manager = ModelManager()
        manager._provider_instances["test-provider"] = MagicMock()
        manager._registry.register(
            model_id="test-model", provider_name="test-provider"
        )
        result = manager.select_provider("test-provider", "test-model")
        assert result is True
        assert manager.selected_provider == "test-provider"
        assert manager.selected_model == "test-model"

    def test_select_provider_returns_false_when_unavailable(self) -> None:
        manager = ModelManager()
        result = manager.select_provider("nonexistent-provider")
        assert result is False

    def test_select_for_task_returns_routing_decision(self) -> None:
        manager = ModelManager()
        manager._registry.register(
            model_id="test-model", provider_name="test-provider"
        )
        entry = manager._registry.get_key("test-model", "test-provider")
        assert entry is not None
        entry.status = ModelStatus.VERIFIED
        manager._connectivity._status.online = True

        decision = manager.select_for_task("Write a Python function")
        assert decision is not None
        assert isinstance(decision, RoutingDecision)
        assert decision.provider_name == "test-provider"

    def test_select_for_task_returns_none_when_no_candidates(self) -> None:
        manager = ModelManager()
        manager._connectivity._status.online = True
        decision = manager.select_for_task("Write a Python function")
        assert decision is None


# ═══════════════════════════════════════════════════════════════════════════
# ModelManager Provider Instance
# ═══════════════════════════════════════════════════════════════════════════


class TestModelManagerProviderInstance:
    """Tests for getting provider instances."""

    def test_get_provider_instance_returns_selected(self) -> None:
        manager = ModelManager()
        mock_provider = MagicMock()
        manager._provider_instances["test-provider"] = mock_provider
        manager._selected_provider = "test-provider"
        result = manager.get_provider_instance()
        assert result is mock_provider

    def test_get_provider_instance_returns_none_when_empty(self) -> None:
        manager = ModelManager()
        with patch(
            "personal_ai_secretary.providers.model_manager.get_settings"
        ) as mock_settings:
            mock_settings.return_value.ai_provider = "deterministic"
            result = manager.get_provider_instance()
            # Deterministic provider should be returned
            assert result is not None


# ═══════════════════════════════════════════════════════════════════════════
# ModelManager Status
# ═══════════════════════════════════════════════════════════════════════════


class TestModelManagerStatus:
    """Tests for model status reporting."""

    def test_get_model_status_not_found(self) -> None:
        manager = ModelManager()
        result = manager.get_model_status("nonexistent", "nonexistent")
        assert result["status"] == "not_found"

    def test_get_model_status_returns_entry_dict(self) -> None:
        manager = ModelManager()
        manager._registry.register(
            model_id="test-model", provider_name="test-provider"
        )
        result = manager.get_model_status("test-provider", "test-model")
        assert "status" in result
        assert result["model_id"] == "test-model"

    def test_get_all_status_returns_list(self) -> None:
        manager = ModelManager()
        manager._registry.register(
            model_id="model-a", provider_name="provider-a"
        )
        manager._registry.register(
            model_id="model-b", provider_name="provider-b"
        )
        result = manager.get_all_status()
        assert len(result) == 2

    def test_get_recommended_returns_none_when_empty(self) -> None:
        manager = ModelManager()
        result = manager.get_recommended()
        assert result is None
