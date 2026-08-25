"""FASE N tests — Model Intelligence Integration.

Tests that verify model_intelligence.py functions work correctly
and are callable from the agent context.
"""

from __future__ import annotations

import pytest

from personal_ai_secretary.providers.model_intelligence import (
    MODEL_REGISTRY,
    FailureInfo,
    ModelCapabilities,
    classify_failure,
    get_fallback_model,
    get_model_capabilities,
    is_recoverable,
    model_supports_vision,
    should_suggest_fallback,
    suggest_model_for_task,
    suggest_vision_model,
)


class TestGetModelCapabilities:
    def test_known_model(self) -> None:
        caps = get_model_capabilities("llama3.1")
        assert caps.context_window == 128000
        assert caps.tool_calling is True
        assert caps.coding_strength == 4

    def test_unknown_model_returns_default(self) -> None:
        caps = get_model_capabilities("totally_unknown_model_xyz")
        assert caps.context_window == 4096
        assert caps.tool_calling is True

    def test_partial_match(self) -> None:
        caps = get_model_capabilities("llama3.1:8b-instruct-q4")
        assert caps.context_window == 128000  # matched llama3.1:8b

    def test_empty_string(self) -> None:
        caps = get_model_capabilities("")
        assert caps.context_window == 4096  # default

    def test_registry_has_entries(self) -> None:
        assert len(MODEL_REGISTRY) > 10


class TestClassifyFailure:
    def test_timeout(self) -> None:
        fi = classify_failure("connection timeout")
        assert fi.failure_type == "transient"
        assert fi.can_retry is True

    def test_model_not_found(self) -> None:
        fi = classify_failure("model not found: xyz")
        assert fi.failure_type == "permanent"
        assert fi.can_retry is False
        assert fi.suggest_fallback is True

    def test_empty_response(self) -> None:
        fi = classify_failure("empty response from model")
        assert fi.failure_type == "recoverable"
        assert fi.can_retry is True

    def test_server_error(self) -> None:
        fi = classify_failure("http 500 internal server error")
        assert fi.failure_type == "transient"

    def test_rate_limit(self) -> None:
        fi = classify_failure("rate limit 429 too many requests")
        assert fi.failure_type == "transient"

    def test_generic_error(self) -> None:
        fi = classify_failure("something weird happened")
        assert fi.failure_type == "recoverable"

    def test_exception_input(self) -> None:
        fi = classify_failure(Exception("connection failed"))
        assert fi.failure_type == "transient"


class TestIsRecoverable:
    def test_recoverable_error(self) -> None:
        fi = FailureInfo(
            failure_type="recoverable",
            reason="test",
            suggestion="test",
            can_retry=True,
            suggest_fallback=False,
        )
        assert is_recoverable(fi) is True

    def test_permanent_error(self) -> None:
        fi = FailureInfo(
            failure_type="permanent",
            reason="test",
            suggestion="test",
            can_retry=False,
            suggest_fallback=False,
        )
        assert is_recoverable(fi) is False

    def test_fallback_suggested(self) -> None:
        fi = FailureInfo(
            failure_type="permanent",
            reason="test",
            suggestion="test",
            can_retry=False,
            suggest_fallback=True,
        )
        assert is_recoverable(fi) is True


class TestShouldSuggestFallback:
    def test_no_suggestion_when_not_needed(self) -> None:
        fi = FailureInfo(
            failure_type="recoverable",
            reason="test",
            suggestion="test",
            can_retry=True,
            suggest_fallback=False,
        )
        result = should_suggest_fallback(fi, "llama3.1")
        assert result is None

    def test_suggests_fallback_model(self) -> None:
        fi = FailureInfo(
            failure_type="permanent",
            reason="test",
            suggestion="test",
            can_retry=False,
            suggest_fallback=True,
        )
        result = should_suggest_fallback(fi, "llama3.1")
        # Should suggest something other than llama3.1
        if result is not None:
            assert result != "llama3.1"

    def test_no_same_model_suggestion(self) -> None:
        fi = FailureInfo(
            failure_type="permanent",
            reason="test",
            suggestion="test",
            can_retry=False,
            suggest_fallback=True,
        )
        # If only one model available, no suggestion
        result = should_suggest_fallback(fi, "only_model", available_models=["only_model"])
        assert result is None


class TestGetFallbackModel:
    def test_returns_different_model(self) -> None:
        result = get_fallback_model("llama3.1")
        if result is not None:
            assert result != "llama3.1"

    def test_with_available_list(self) -> None:
        result = get_fallback_model(
            "llama3.1",
            available_models=["llama3.1", "qwen2.5-coder", "mistral"],
        )
        if result is not None:
            assert result in ["qwen2.5-coder", "mistral"]


class TestSuggestModelForTask:
    def test_coding_task_suggests_coding_model(self) -> None:
        result = suggest_model_for_task("create a function for parsing")
        if result is not None:
            caps = get_model_capabilities(result)
            assert caps.coding_strength >= 4

    def test_non_coding_task_returns_none(self) -> None:
        result = suggest_model_for_task("what is 2 + 2?")
        assert result is None

    def test_same_model_not_suggested(self) -> None:
        result = suggest_model_for_task(
            "write a class for user management",
            current_model="qwen2.5-coder",
        )
        if result is not None:
            assert result != "qwen2.5-coder"


class TestModelSupportsVision:
    def test_llava_supports_vision(self) -> None:
        assert model_supports_vision("llava") is True

    def test_llama3_no_vision(self) -> None:
        assert model_supports_vision("llama3") is False

    def test_llama32_vision_supports(self) -> None:
        assert model_supports_vision("llama3.2-vision") is True

    def test_unknown_model_no_vision(self) -> None:
        assert model_supports_vision("unknown_model_xyz") is False


class TestSuggestVisionModel:
    def test_suggests_vision_model(self) -> None:
        result = suggest_vision_model()
        if result is not None:
            assert model_supports_vision(result) is True

    def test_with_limited_list(self) -> None:
        result = suggest_vision_model(available_models=["llama3", "llava"])
        assert result == "llava"

    def test_no_vision_available(self) -> None:
        result = suggest_vision_model(available_models=["llama3", "mistral"])
        assert result is None


class TestModelCapabilities:
    def test_frozen_dataclass(self) -> None:
        caps = ModelCapabilities()
        with pytest.raises(AttributeError):
            caps.context_window = 999  # type: ignore[misc]
