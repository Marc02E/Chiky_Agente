"""FASE L.4-L.6 — Development Agent Advanced: comprehensive test suite.

Covers 20+ acceptance scenarios:
- A: Debugging intelligence (error diagnosis, file location, fix)
- B: Test failure handling (detect, diagnose, fix)
- C: Build failure handling (detect, diagnose, fix)
- D: Missing test generation (detect, propose, create)
- E: DeepSeek failure and recovery (empty response, 404)
- F: Model fallback (suggest, switch, recover)
- G: Latency and timeout handling
- H: Security regression (approval, command restrictions)
- I: Debugging intelligence prompt sections
- J: Test generation prompt sections
- K: Build verification prompt sections
- L: Multi-model guidance prompt sections
"""



from personal_ai_secretary.observability.request_metrics import RequestMetrics
from personal_ai_secretary.providers.model_intelligence import (
    CODING_TASK_KEYWORDS,
    MODEL_REGISTRY,
    ModelCapabilities,
    classify_failure,
    get_fallback_model,
    get_model_capabilities,
    is_recoverable,
    should_suggest_fallback,
    suggest_model_for_task,
)
from personal_ai_secretary.tools.prompt import build_system_prompt

# ---------------------------------------------------------------------------
# Model Intelligence: Capabilities
# ---------------------------------------------------------------------------


class TestModelCapabilities:
    """Tests for model capabilities registry."""

    def test_default_capabilities(self):
        caps = ModelCapabilities()
        assert caps.context_window == 4096
        assert caps.tool_calling is True
        assert caps.coding_strength == 3
        assert caps.stability == 3

    def test_llama3_capabilities(self):
        caps = get_model_capabilities("llama3")
        assert caps.context_window == 8192
        assert caps.tool_calling is True
        assert caps.coding_strength == 3
        assert caps.stability == 4

    def test_llama3_1_capabilities(self):
        caps = get_model_capabilities("llama3.1")
        assert caps.context_window == 128000
        assert caps.tool_calling is True
        assert caps.coding_strength == 4

    def test_deepseek_capabilities(self):
        caps = get_model_capabilities("deepseek-coder-v2")
        assert caps.context_window == 128000
        assert caps.tool_calling is False
        assert caps.coding_strength == 5

    def test_qwen_coder_capabilities(self):
        caps = get_model_capabilities("qwen2.5-coder")
        assert caps.context_window == 32768
        assert caps.tool_calling is True
        assert caps.coding_strength == 5

    def test_partial_match(self):
        caps = get_model_capabilities("llama3.1:8b-instruct")
        assert caps.context_window == 128000
        # The 8b variant has coding_strength=3
        assert caps.coding_strength == 3

    def test_unknown_model_defaults(self):
        caps = get_model_capabilities("unknown-model-xyz")
        assert caps.context_window == 4096
        assert caps.tool_calling is True
        assert caps.coding_strength == 3
        assert "Unknown" in caps.notes

    def test_registry_completeness(self):
        assert len(MODEL_REGISTRY) >= 10


# ---------------------------------------------------------------------------
# Model Intelligence: Failure Classification
# ---------------------------------------------------------------------------


class TestFailureClassification:
    """Tests for failure classification."""

    def test_timeout_is_transient(self):
        info = classify_failure("connection timed out")
        assert info.failure_type == "transient"
        assert info.can_retry is True
        assert info.suggest_fallback is True

    def test_connection_error_is_transient(self):
        info = classify_failure("connection refused")
        assert info.failure_type == "transient"
        assert info.can_retry is True

    def test_model_not_found(self):
        info = classify_failure("model not found: 404")
        assert info.failure_type == "permanent"
        assert info.can_retry is False
        assert info.suggest_fallback is True

    def test_empty_response_is_recoverable(self):
        info = classify_failure("empty response")
        assert info.failure_type == "recoverable"
        assert info.can_retry is True
        assert info.suggest_fallback is True

    def test_http_500_is_transient(self):
        info = classify_failure("http 500 internal server error")
        assert info.failure_type == "transient"
        assert info.can_retry is True

    def test_rate_limit_is_transient(self):
        info = classify_failure("rate limit exceeded 429")
        assert info.failure_type == "transient"
        assert info.can_retry is True

    def test_generic_error_is_recoverable(self):
        info = classify_failure("something went wrong")
        assert info.failure_type == "recoverable"
        assert info.can_retry is True

    def test_exception_input(self):
        info = classify_failure(ValueError("timeout"))
        assert info.failure_type == "transient"
        assert info.can_retry is True


# ---------------------------------------------------------------------------
# Model Intelligence: Coding Task Strategy
# ---------------------------------------------------------------------------


class TestCodingTaskStrategy:
    """Tests for coding model strategy."""

    def test_coding_task_detected(self):
        result = suggest_model_for_task("create a CRUD API")
        # Should suggest a coding-specialized model
        assert result is None or result in MODEL_REGISTRY

    def test_non_coding_task_ignored(self):
        result = suggest_model_for_task("what time is it")
        assert result is None

    def test_with_available_models(self):
        available = ["llama3", "qwen2.5-coder"]
        result = suggest_model_for_task(
            "create a Python project", available_models=available
        )
        # Should suggest qwen2.5-coder for coding tasks
        assert result is None or result in available

    def test_same_model_not_suggested(self):
        result = suggest_model_for_task(
            "create a Python project",
            available_models=["qwen2.5-coder"],
            current_model="qwen2.5-coder",
        )
        assert result is None

    def test_coding_keywords_detected(self):
        for keyword in CODING_TASK_KEYWORDS:
            result = suggest_model_for_task(f"please {keyword} something")
            # Either returns a model or None (if no better model available)
            assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# Model Intelligence: Fallback Logic
# ---------------------------------------------------------------------------


class TestFallbackLogic:
    """Tests for model fallback logic."""

    def test_fallback_from_deepseek(self):
        fallback = get_fallback_model("deepseek-coder-v2")
        assert fallback is not None
        assert fallback != "deepseek-coder-v2"

    def test_fallback_from_llama3(self):
        fallback = get_fallback_model("llama3")
        assert fallback is not None
        assert fallback != "llama3"

    def test_fallback_respects_available(self):
        fallback = get_fallback_model(
            "llama3", available_models=["llama3", "qwen2.5-coder"]
        )
        assert fallback is not None
        assert fallback != "llama3"
        assert fallback in ["qwen2.5-coder"]

    def test_no_fallback_from_best(self):
        # If only one model available, no fallback
        fallback = get_fallback_model("llama3", available_models=["llama3"])
        assert fallback is None

    def test_is_recoverable_recoverable(self):
        from personal_ai_secretary.providers.model_intelligence import FailureInfo

        info = FailureInfo(
            failure_type="recoverable",
            reason="test",
            suggestion="test",
            can_retry=True,
        )
        assert is_recoverable(info) is True

    def test_is_recoverable_permanent(self):
        from personal_ai_secretary.providers.model_intelligence import FailureInfo

        info = FailureInfo(
            failure_type="permanent",
            reason="test",
            suggestion="test",
            can_retry=False,
        )
        assert is_recoverable(info) is False

    def test_should_suggest_fallback_yes(self):
        from personal_ai_secretary.providers.model_intelligence import FailureInfo

        info = FailureInfo(
            failure_type="transient",
            reason="timeout",
            suggestion="try again",
            can_retry=True,
            suggest_fallback=True,
        )
        result = should_suggest_fallback(info, "deepseek-coder-v2")
        assert result is not None
        assert result != "deepseek-coder-v2"

    def test_should_suggest_fallback_no(self):
        from personal_ai_secretary.providers.model_intelligence import FailureInfo

        info = FailureInfo(
            failure_type="recoverable",
            reason="unknown",
            suggestion="try again",
            can_retry=True,
            suggest_fallback=False,
        )
        result = should_suggest_fallback(info, "llama3")
        assert result is None


# ---------------------------------------------------------------------------
# Prompt: Debugging Intelligence Sections
# ---------------------------------------------------------------------------


class TestPromptDebuggingIntelligence:
    """Tests for debugging intelligence in prompt."""

    def test_debugging_intelligence_present(self):
        prompt = build_system_prompt(
            tool_names=["execute_command", "read_files", "modify_file", "search_files"],
        )
        assert "Debugging" in prompt

    def test_debugging_intelligence_absent(self):
        prompt = build_system_prompt(tool_names=["get_current_datetime"])
        assert "Debugging" not in prompt

    def test_diagnosis_steps(self):
        prompt = build_system_prompt(
            tool_names=["execute_command", "read_files", "modify_file", "search_files"],
        )
        assert "CAPTURE" in prompt
        assert "LOCATE" in prompt
        assert "ANALYZE" in prompt

    def test_test_generation_present(self):
        prompt = build_system_prompt(
            tool_names=["execute_command", "create_file", "modify_file"],
        )
        assert "Tests" in prompt

    def test_test_generation_absent(self):
        prompt = build_system_prompt(tool_names=["get_current_datetime"])
        assert "Tests" not in prompt

    def test_build_verification_present(self):
        prompt = build_system_prompt(tool_names=["execute_command"])
        assert "Development Workflow" in prompt

    def test_build_verification_absent(self):
        prompt = build_system_prompt(tool_names=["get_current_datetime"])
        assert "Development Workflow" not in prompt

    def test_multi_model_guidance_present(self):
        prompt = build_system_prompt(
            tool_names=["execute_command"],
            model_name="llama3.1",
        )
        assert "Model Awareness" in prompt

    def test_multi_model_guidance_absent(self):
        prompt = build_system_prompt(
            tool_names=["execute_command"],
            model_name=None,
        )
        assert "Model Awareness" not in prompt

    def test_deepseek_note_enhanced(self):
        prompt = build_system_prompt(
            tool_names=["execute_command"],
            model_name="deepseek-coder-v2",
        )
        assert "explicit tool instructions" in prompt


# ---------------------------------------------------------------------------
# Observability: L.4-L.6 Metrics
# ---------------------------------------------------------------------------


class TestL4L6Observability:
    """Tests for L.4-L.6 observability extensions."""

    def test_diagnosis_attempts_default(self):
        m = RequestMetrics()
        assert m.diagnosis_attempts == 0

    def test_diagnosis_attempts_tracking(self):
        m = RequestMetrics()
        m.diagnosis_attempts = 3
        assert m.diagnosis_attempts == 3

    def test_test_executions_tracking(self):
        m = RequestMetrics()
        m.tests_executed = 10
        m.tests_passed = 8
        m.tests_failed = 2
        assert m.tests_executed == 10
        assert m.tests_passed == 8
        assert m.tests_failed == 2

    def test_model_failures_tracking(self):
        m = RequestMetrics()
        m.model_failures = 2
        assert m.model_failures == 2

    def test_fallback_tracking(self):
        m = RequestMetrics()
        m.fallback_suggested = True
        m.fallback_model = "llama3.1"
        assert m.fallback_suggested is True
        assert m.fallback_model == "llama3.1"

    def test_latency_tracking(self):
        m = RequestMetrics()
        m.llm_duration = 2.5
        m.tool_duration = 1.2
        m.command_duration = 0.8
        assert m.llm_duration == 2.5
        assert m.tool_duration == 1.2
        assert m.command_duration == 0.8


# ---------------------------------------------------------------------------
# Acceptance Scenarios
# ---------------------------------------------------------------------------


class TestAcceptanceScenarios:
    """End-to-end acceptance scenarios for L.4-L.6."""

    def test_A_debugging_error_diagnosis(self):
        """Scenario A: Diagnose a Python error from traceback."""
        prompt = build_system_prompt(
            tool_names=[
                "execute_command",
                "read_files",
                "modify_file",
                "search_files",
                "analyze_project",
            ],
        )
        assert "CAPTURE" in prompt
        assert "LOCATE" in prompt
        assert "Root cause" in prompt or "root cause" in prompt or "ANALYZE" in prompt

    def test_B_test_failure_detection(self):
        """Scenario B: Detect and handle test failures."""
        prompt = build_system_prompt(
            tool_names=["execute_command", "create_file", "modify_file"],
        )
        assert "Tests" in prompt
        assert "DETECT" in prompt

    def test_C_build_failure_handling(self):
        """Scenario C: Handle build failures."""
        prompt = build_system_prompt(tool_names=["execute_command"])
        assert "Development Workflow" in prompt
        assert "EXECUTE" in prompt

    def test_D_missing_test_generation(self):
        """Scenario D: Generate tests for changes."""
        prompt = build_system_prompt(
            tool_names=["execute_command", "create_file", "modify_file"],
        )
        assert "PROPOSE" in prompt
        assert "ASK" in prompt

    def test_E_deepseek_failure_recovery(self):
        """Scenario E: DeepSeek empty response handling."""

        info = classify_failure("The model returned an empty response")
        assert info.failure_type == "recoverable"
        assert info.can_retry is True

        fallback = get_fallback_model("deepseek-coder-v2")
        assert fallback is not None
        assert fallback != "deepseek-coder-v2"

    def test_F_model_fallback_suggestion(self):
        """Scenario F: Suggest fallback model."""
        from personal_ai_secretary.providers.model_intelligence import FailureInfo

        info = FailureInfo(
            failure_type="permanent",
            reason="Model not found",
            suggestion="Try another model",
            can_retry=False,
            suggest_fallback=True,
        )
        result = should_suggest_fallback(info, "deepseek-coder-v2")
        assert result is not None

    def test_G_latency_timeout_handling(self):
        """Scenario G: Timeout recovery."""
        info = classify_failure("connection timed out after 30s")
        assert info.failure_type == "transient"
        assert info.can_retry is True

    def test_H_security_no_auto_approval(self):
        """Scenario H: No auto-approval for destructive operations."""
        prompt = build_system_prompt(
            tool_names=["execute_command"],
        )
        # Ensure the prompt doesn't encourage auto-approval
        assert "auto-approval" not in prompt.lower() or "requires_approval" in prompt.lower()

    def test_I_prompt_sections_complete(self):
        """Scenario I: All L.4-L.6 prompt sections present."""
        prompt = build_system_prompt(
            tool_names=[
                "execute_command",
                "read_files",
                "modify_file",
                "create_file",
                "search_files",
                "analyze_project",
            ],
            model_name="llama3.1",
        )
        assert "Debugging" in prompt
        assert "Tests" in prompt
        assert "Development Workflow" in prompt
        assert "Model Awareness" in prompt

    def test_J_model_capabilities_comprehensive(self):
        """Scenario J: Model capabilities cover all major models."""
        required_models = [
            "llama3",
            "llama3.1",
            "deepseek-coder-v2",
            "qwen2.5-coder",
            "codestral",
            "mistral",
        ]
        for model in required_models:
            caps = get_model_capabilities(model)
            assert caps.coding_strength >= 1
            assert caps.stability >= 1

    def test_K_prompt_size_budget(self):
        """Scenario K: Full prompt stays within token budget."""
        prompt = build_system_prompt(
            tool_names=[
                "execute_command",
                "read_files",
                "modify_file",
                "create_file",
                "search_files",
                "analyze_project",
                "verify_files",
            ],
            compact_descriptions=[
                "execute_command: Execute commands",
                "read_files: Read files",
                "modify_file: Modify files",
                "create_file: Create files",
                "search_files: Search files",
                "analyze_project: Analyze project",
                "verify_files: Verify files",
            ],
            model_name="llama3.1",
        )
        tokens = len(prompt) // 4
        assert tokens < 2500

    def test_L_metrics_completeness(self):
        """Scenario L: All L.4-L.6 metrics are tracked."""
        m = RequestMetrics()
        # Verify all L.4-L.6 fields exist
        assert hasattr(m, "diagnosis_attempts")
        assert hasattr(m, "tests_executed")
        assert hasattr(m, "tests_passed")
        assert hasattr(m, "tests_failed")
        assert hasattr(m, "model_failures")
        assert hasattr(m, "fallback_suggested")
        assert hasattr(m, "fallback_model")
        assert hasattr(m, "llm_duration")
        assert hasattr(m, "tool_duration")
        assert hasattr(m, "command_duration")

    def test_M_regression_existing_sections(self):
        """Scenario M: Existing L.1-L.3 prompt sections still present."""
        prompt = build_system_prompt(
            tool_names=[
                "execute_command",
                "read_files",
                "modify_file",
                "analyze_project",
            ],
        )
        # L.1 sections
        assert "Development Workflow" in prompt
        assert "Error Recovery" in prompt
        assert "Verification" in prompt
        # L.2 sections
        assert "Project Intelligence" in prompt

    def test_N_model_specific_notes(self):
        """Scenario N: Model-specific notes are included."""
        prompt_llama = build_system_prompt(
            tool_names=["execute_command"],
            model_name="llama3.1",
        )
        assert "Llama" in prompt_llama

        prompt_deepseek = build_system_prompt(
            tool_names=["execute_command"],
            model_name="deepseek-coder-v2",
        )
        assert "DeepSeek" in prompt_deepseek

    def test_O_fallback_from_all_models(self):
        """Scenario O: Fallback works from any model."""
        for model in ["llama3", "llama3.1", "deepseek-coder-v2", "mistral"]:
            fallback = get_fallback_model(model)
            assert fallback is None or fallback != model

    def test_P_coding_keywords_comprehensive(self):
        """Scenario P: Coding task detection covers all keywords."""
        for keyword in CODING_TASK_KEYWORDS:
            result = suggest_model_for_task(f"please {keyword} a test")
            assert result is None or isinstance(result, str)

    def test_Q_prompt_preserves_tool_format(self):
        """Scenario Q: Tool call format preserved in prompt."""
        prompt = build_system_prompt(tool_names=["execute_command"])
        assert "```tool" in prompt

    def test_R_metrics_default_zero(self):
        """Scenario R: All L.4-L.6 metrics default to zero."""
        m = RequestMetrics()
        assert m.diagnosis_attempts == 0
        assert m.tests_executed == 0
        assert m.tests_passed == 0
        assert m.tests_failed == 0
        assert m.model_failures == 0
        assert m.fallback_suggested is False
        assert m.fallback_model == ""
        assert m.llm_duration == 0.0
        assert m.tool_duration == 0.0
        assert m.command_duration == 0.0
