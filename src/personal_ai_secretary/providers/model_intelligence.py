"""FASE L.4-L.6 — Model Intelligence module.

Provides multi-model awareness for Chiky:
- Model capabilities (context window, tool calling, coding strength)
- Model fallback logic
- Coding model strategy (task-aware model selection)
- Failure classification (recoverable vs permanent)

This module does NOT create new providers. It provides metadata and logic
that the agent uses to make informed decisions about model selection
and fallback behavior.

Integrates with K.5 (context budgets) and existing provider infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Model Capabilities Registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelCapabilities:
    """Metadata for a specific model's capabilities."""

    context_window: int = 4096
    tool_calling: bool = True
    coding_strength: int = 3  # 1-5 scale
    stability: int = 3  # 1-5 scale
    supports_streaming: bool = True
    supports_vision: bool = False  # FASE L.7-L.9: vision capability
    supports_documents: bool = True  # FASE L.7-L.9: document processing
    max_output_tokens: int = 2048
    notes: str = ""


# Pre-defined model capabilities registry
MODEL_REGISTRY: dict[str, ModelCapabilities] = {
    # Llama 3 family
    "llama3": ModelCapabilities(
        context_window=8192,
        tool_calling=True,
        coding_strength=3,
        stability=4,
        supports_vision=False,
        notes="General purpose, good tool calling",
    ),
    "llama3:8b": ModelCapabilities(
        context_window=8192,
        tool_calling=True,
        coding_strength=3,
        stability=4,
        supports_vision=False,
        notes="8B variant, fast but limited reasoning",
    ),
    "llama3:70b": ModelCapabilities(
        context_window=8192,
        tool_calling=True,
        coding_strength=4,
        stability=3,
        supports_vision=False,
        notes="70B variant, strong but slower",
    ),
    "llama3.1": ModelCapabilities(
        context_window=128000,
        tool_calling=True,
        coding_strength=4,
        stability=4,
        supports_vision=False,
        notes="Extended context, improved tool calling",
    ),
    "llama3.1:8b": ModelCapabilities(
        context_window=128000,
        tool_calling=True,
        coding_strength=3,
        stability=4,
        supports_vision=False,
        notes="8B with extended context",
    ),
    "llama3.1:70b": ModelCapabilities(
        context_window=128000,
        tool_calling=True,
        coding_strength=4,
        stability=3,
        supports_vision=False,
        notes="70B with extended context",
    ),
    "llama3.2-vision": ModelCapabilities(
        context_window=128000,
        tool_calling=True,
        coding_strength=3,
        stability=4,
        supports_vision=True,
        notes="Vision-capable Llama",
    ),
    # DeepSeek family
    "deepseek-coder-v2": ModelCapabilities(
        context_window=128000,
        tool_calling=False,
        coding_strength=5,
        stability=3,
        supports_vision=False,
        notes="Excellent coder, limited tool calling",
    ),
    "deepseek-coder-v2:16b": ModelCapabilities(
        context_window=128000,
        tool_calling=False,
        coding_strength=5,
        stability=3,
        supports_vision=False,
        notes="16B coder variant",
    ),
    "deepseek-v2": ModelCapabilities(
        context_window=128000,
        tool_calling=False,
        coding_strength=4,
        stability=3,
        supports_vision=False,
        notes="General purpose deepseek",
    ),
    # Qwen family
    "qwen2.5-coder": ModelCapabilities(
        context_window=32768,
        tool_calling=True,
        coding_strength=5,
        stability=4,
        supports_vision=False,
        notes="Strong coder with tool calling",
    ),
    "qwen2.5": ModelCapabilities(
        context_window=32768,
        tool_calling=True,
        coding_strength=3,
        stability=4,
        supports_vision=False,
        notes="General purpose qwen",
    ),
    "qwen2-vl": ModelCapabilities(
        context_window=32768,
        tool_calling=True,
        coding_strength=3,
        stability=4,
        supports_vision=True,
        notes="Vision-language model",
    ),
    "qwen2.5-vl": ModelCapabilities(
        context_window=32768,
        tool_calling=True,
        coding_strength=3,
        stability=4,
        supports_vision=True,
        notes="Enhanced vision-language model",
    ),
    # Mistral family
    "mistral": ModelCapabilities(
        context_window=32768,
        tool_calling=True,
        coding_strength=3,
        stability=4,
        supports_vision=False,
        notes="Fast, good tool calling",
    ),
    "codestral": ModelCapabilities(
        context_window=32768,
        tool_calling=True,
        coding_strength=5,
        stability=4,
        supports_vision=False,
        notes="Mistral's coding specialist",
    ),
    # CodeLlama family
    "codellama": ModelCapabilities(
        context_window=16384,
        tool_calling=False,
        coding_strength=4,
        stability=4,
        supports_vision=False,
        notes="Meta's coding specialist",
    ),
    "codellama:34b": ModelCapabilities(
        context_window=16384,
        tool_calling=False,
        coding_strength=4,
        stability=3,
        supports_vision=False,
        notes="34B coding variant",
    ),
    # Vision models
    "llava": ModelCapabilities(
        context_window=4096,
        tool_calling=False,
        coding_strength=1,
        stability=4,
        supports_vision=True,
        notes="Vision model, no coding",
    ),
    "bakllava": ModelCapabilities(
        context_window=4096,
        tool_calling=False,
        coding_strength=1,
        stability=4,
        supports_vision=True,
        notes="BakLLaVA vision model",
    ),
    "moondream": ModelCapabilities(
        context_window=4096,
        tool_calling=False,
        coding_strength=1,
        stability=4,
        supports_vision=True,
        notes="Moondream vision model",
    ),
    "gemma3": ModelCapabilities(
        context_window=32768,
        tool_calling=True,
        coding_strength=3,
        stability=4,
        supports_vision=True,
        notes="Gemma3 with vision",
    ),
}

# Fallback priority order (best coding → general purpose)
FALLBACK_PRIORITY: list[str] = [
    "qwen2.5-coder",
    "codestral",
    "deepseek-coder-v2",
    "llama3.1",
    "llama3",
    "mistral",
    "qwen2.5",
]


# ---------------------------------------------------------------------------
# Failure Classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FailureInfo:
    """Classified information about a model failure."""

    failure_type: str  # "recoverable", "permanent", "transient"
    reason: str
    suggestion: str
    can_retry: bool = True
    suggest_fallback: bool = False


def classify_failure(error: Exception | str) -> FailureInfo:
    """Classify a model failure as recoverable, permanent, or transient.

    Args:
        error: The exception or error message.

    Returns:
        FailureInfo with classification and suggestions.
    """
    error_str = str(error).lower()

    # Transient failures (retry possible)
    if any(kw in error_str for kw in ("timeout", "timed out", "connection")):
        return FailureInfo(
            failure_type="transient",
            reason="Connection or timeout issue",
            suggestion="The model may be loading or overloaded. Try again in a moment.",
            can_retry=True,
            suggest_fallback=True,
        )

    # Model not found (suggest fallback)
    if any(kw in error_str for kw in ("not found", "404", "does not exist")):
        return FailureInfo(
            failure_type="permanent",
            reason="Model not available",
            suggestion="The requested model is not installed or not found.",
            can_retry=False,
            suggest_fallback=True,
        )

    # Empty response (recoverable)
    if any(kw in error_str for kw in ("empty response", "empty", "no content")):
        return FailureInfo(
            failure_type="recoverable",
            reason="Model returned empty response",
            suggestion="Try rephrasing your question or using a different model.",
            can_retry=True,
            suggest_fallback=True,
        )

    # HTTP errors
    if any(kw in error_str for kw in ("http 5", "internal server", "500", "502", "503")):
        return FailureInfo(
            failure_type="transient",
            reason="Server error",
            suggestion="The model server encountered an error. Try again shortly.",
            can_retry=True,
            suggest_fallback=True,
        )

    # Rate limiting
    if any(kw in error_str for kw in ("rate limit", "429", "too many")):
        return FailureInfo(
            failure_type="transient",
            reason="Rate limited",
            suggestion="Too many requests. Wait a moment before retrying.",
            can_retry=True,
            suggest_fallback=True,
        )

    # Generic errors
    return FailureInfo(
        failure_type="recoverable",
        reason="Unknown error",
        suggestion="An unexpected error occurred. Try again or use a different model.",
        can_retry=True,
        suggest_fallback=False,
    )


# ---------------------------------------------------------------------------
# Coding Model Strategy
# ---------------------------------------------------------------------------

# Task types that benefit from coding-specialized models
CODING_TASK_KEYWORDS: frozenset[str] = frozenset({
    "create", "build", "implement", "code", "program", "develop",
    "debug", "fix", "refactor", "test", "generate", "write",
    "crud", "api", "game", "app", "project", "function", "class",
    "module", "package", "library", "script", "tool",
})


def suggest_model_for_task(
    task_description: str,
    available_models: list[str] | None = None,
    current_model: str | None = None,
) -> str | None:
    """Suggest the best model for a given task.

    Args:
        task_description: Natural language task description.
        available_models: List of available model names.
        current_model: Currently active model.

    Returns:
        Suggested model name, or None if current model is suitable.
    """
    task_lower = task_description.lower()
    is_coding_task = any(kw in task_lower for kw in CODING_TASK_KEYWORDS)

    if not is_coding_task:
        return None  # Current model is fine for non-coding tasks

    if available_models is None:
        available_models = list(MODEL_REGISTRY.keys())

    # Find the best coding model available
    best_model: str | None = None
    best_score = -1

    for model_name in available_models:
        caps = get_model_capabilities(model_name)
        if caps.coding_strength > best_score:
            if current_model and model_name == current_model:
                continue  # Don't suggest switching to same model
            best_score = caps.coding_strength
            best_model = model_name

    return best_model


# ---------------------------------------------------------------------------
# Model Lookup
# ---------------------------------------------------------------------------


def get_model_capabilities(model_name: str) -> ModelCapabilities:
    """Get capabilities for a model by name.

    Falls back to a default if the exact model is not in the registry.
    """
    if model_name in MODEL_REGISTRY:
        return MODEL_REGISTRY[model_name]

    # Try partial matching with longest match preference
    model_lower = model_name.lower()
    best_match: ModelCapabilities | None = None
    best_match_len = 0

    for key, caps in MODEL_REGISTRY.items():
        if key in model_lower or model_lower.startswith(key):
            if len(key) > best_match_len:
                best_match_len = len(key)
                best_match = caps

    if best_match is not None:
        return best_match

    # Default capabilities for unknown models
    return ModelCapabilities(
        context_window=4096,
        tool_calling=True,
        coding_strength=3,
        stability=3,
        notes="Unknown model, using default capabilities",
    )


def get_fallback_model(
    current_model: str,
    available_models: list[str] | None = None,
) -> str | None:
    """Suggest a fallback model when the current one fails.

    Args:
        current_model: The model that failed.
        available_models: List of available model names.

    Returns:
        Fallback model name, or None if no fallback available.
    """
    if available_models is None:
        available_models = list(MODEL_REGISTRY.keys())

    current_caps = get_model_capabilities(current_model)

    # Find the best available model that's not the current one
    best_model: str | None = None
    best_score = -1

    for model_name in available_models:
        if model_name == current_model:
            continue
        caps = get_model_capabilities(model_name)
        # Score: coding_strength * 2 + stability + tool_calling bonus
        score = (
            caps.coding_strength * 2
            + caps.stability
            + (2 if caps.tool_calling == current_caps.tool_calling else 0)
        )
        if score > best_score:
            best_score = score
            best_model = model_name

    return best_model


def is_recoverable(failure_info: FailureInfo) -> bool:
    """Check if a failure is recoverable (can retry or fallback)."""
    return failure_info.can_retry or failure_info.suggest_fallback


def should_suggest_fallback(
    failure_info: FailureInfo,
    current_model: str,
    available_models: list[str] | None = None,
) -> str | None:
    """Determine if a fallback should be suggested.

    Returns:
        Fallback model name if suggestion is appropriate, None otherwise.
    """
    if not failure_info.suggest_fallback:
        return None

    fallback = get_fallback_model(current_model, available_models)
    if fallback is None:
        return None

    # Don't suggest fallback for the same model
    if fallback == current_model:
        return None

    return fallback


# ---------------------------------------------------------------------------
# Vision Capability Helpers
# ---------------------------------------------------------------------------


def model_supports_vision(model_name: str) -> bool:
    """Check if a model supports vision/image analysis.

    Args:
        model_name: Name of the model.

    Returns:
        True if the model supports vision, False otherwise.
    """
    caps = get_model_capabilities(model_name)
    return caps.supports_vision


def suggest_vision_model(
    available_models: list[str] | None = None,
) -> str | None:
    """Suggest a model that supports vision.

    Args:
        available_models: List of available model names.

    Returns:
        Vision-capable model name, or None if none available.
    """
    if available_models is None:
        available_models = list(MODEL_REGISTRY.keys())

    for model_name in available_models:
        caps = get_model_capabilities(model_name)
        if caps.supports_vision:
            return model_name

    return None
