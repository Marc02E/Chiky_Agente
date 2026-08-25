"""FASE K.5 — Centralized context budget management.

Provides approximate token estimation, model-aware context profiles, and a
context budget that splits the usable window across context categories
(system prompt, history, tool results, attached files, project context).

The token estimate is intentionally simple and centralized (chars / 4) so it
can be swapped for a real tokenizer later without touching call sites.
"""

from dataclasses import dataclass, field
from enum import StrEnum

# Approximate characters per token for English/code text. Centralized here so
# the estimation strategy can be replaced by a real tokenizer in one place.
CHARS_PER_TOKEN = 4


def estimate_tokens(chars: int) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    if chars <= 0:
        return 0
    return chars // CHARS_PER_TOKEN


def estimate_text_tokens(text: str) -> int:
    """Token estimate for a string."""
    return estimate_tokens(len(text))


class ContextCategory(StrEnum):
    """Context categories tracked by the budget."""

    SYSTEM_PROMPT = "system_prompt"
    HISTORY = "history"
    TOOL_RESULTS = "tool_results"
    ATTACHED_FILES = "attached_files"
    PROJECT_CONTEXT = "project_context"


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """Context capabilities of a model.

    Values are conservative; when no reliable information exists for a model,
    ``DEFAULT_PROFILE`` is used instead of guessing a large window.
    """

    key: str
    context_window_tokens: int
    response_budget_tokens: int
    tool_budget_tokens: int


# Safe default for unknown models: small window so we never overflow.
DEFAULT_PROFILE = ModelProfile(
    key="default",
    context_window_tokens=8192,
    response_budget_tokens=2048,
    tool_budget_tokens=1024,
)

# Known model profiles. Keys are matched as substrings of the model name.
MODEL_PROFILES: dict[str, ModelProfile] = {
    "llama3.1": ModelProfile(
        key="llama3.1",
        context_window_tokens=131072,
        response_budget_tokens=4096,
        tool_budget_tokens=16384,
    ),
    "llama3": ModelProfile(
        key="llama3",
        context_window_tokens=8192,
        response_budget_tokens=2048,
        tool_budget_tokens=1024,
    ),
    "deepseek-coder-v2": ModelProfile(
        key="deepseek-coder-v2",
        context_window_tokens=131072,
        response_budget_tokens=4096,
        tool_budget_tokens=16384,
    ),
    "qwen": ModelProfile(
        key="qwen",
        context_window_tokens=32768,
        response_budget_tokens=4096,
        tool_budget_tokens=8192,
    ),
    "mistral": ModelProfile(
        key="mistral",
        context_window_tokens=32768,
        response_budget_tokens=4096,
        tool_budget_tokens=8192,
    ),
}


def resolve_model_profile(model_name: str | None) -> ModelProfile:
    """Resolve a :class:`ModelProfile` for a model name.

    Unknown or missing names resolve to ``DEFAULT_PROFILE`` (safe values).
    More specific keys win: "llama3.1" is checked before "llama3".
    """
    if not model_name:
        return DEFAULT_PROFILE
    lowered = model_name.lower().strip()
    # Longest keys first so "llama3.1" matches before "llama3".
    for key in sorted(MODEL_PROFILES, key=len, reverse=True):
        if key in lowered:
            return MODEL_PROFILES[key]
    return DEFAULT_PROFILE


# Share of the usable window allocated to each category. The current user
# message is never truncated and lives outside these allocations.
_CATEGORY_SHARES: dict[ContextCategory, float] = {
    ContextCategory.SYSTEM_PROMPT: 0.20,
    ContextCategory.HISTORY: 0.45,
    ContextCategory.TOOL_RESULTS: 0.25,
    ContextCategory.ATTACHED_FILES: 0.05,
    ContextCategory.PROJECT_CONTEXT: 0.05,
}


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """Character budgets per context category for one request.

    Derived from a model profile: the usable window is the model context
    window minus the response budget, split across categories.
    """

    profile: ModelProfile
    total_chars: int
    total_tokens: int
    system_prompt_chars: int
    history_chars: int
    tool_results_chars: int
    attached_files_chars: int
    project_context_chars: int

    @classmethod
    def from_profile(cls, profile: ModelProfile) -> "ContextBudget":
        usable_tokens = max(profile.context_window_tokens - profile.response_budget_tokens, 1024)
        usable_chars = usable_tokens * CHARS_PER_TOKEN

        def share(category: ContextCategory) -> int:
            return int(usable_chars * _CATEGORY_SHARES[category])

        return cls(
            profile=profile,
            total_chars=usable_chars,
            total_tokens=usable_tokens,
            system_prompt_chars=share(ContextCategory.SYSTEM_PROMPT),
            history_chars=share(ContextCategory.HISTORY),
            tool_results_chars=max(
                share(ContextCategory.TOOL_RESULTS), profile.tool_budget_tokens * CHARS_PER_TOKEN
            ),
            attached_files_chars=share(ContextCategory.ATTACHED_FILES),
            project_context_chars=share(ContextCategory.PROJECT_CONTEXT),
        )

    def allocation(self, category: ContextCategory) -> int:
        """Character allocation for a category."""
        mapping = {
            ContextCategory.SYSTEM_PROMPT: self.system_prompt_chars,
            ContextCategory.HISTORY: self.history_chars,
            ContextCategory.TOOL_RESULTS: self.tool_results_chars,
            ContextCategory.ATTACHED_FILES: self.attached_files_chars,
            ContextCategory.PROJECT_CONTEXT: self.project_context_chars,
        }
        return mapping[category]


@dataclass(slots=True)
class BudgetTracker:
    """Tracks estimated usage against a :class:`ContextBudget`."""

    budget: ContextBudget
    usage: dict[ContextCategory, int] = field(default_factory=dict)

    def record(self, category: ContextCategory, chars: int) -> None:
        self.usage[category] = self.usage.get(category, 0) + chars

    def used(self, category: ContextCategory) -> int:
        return self.usage.get(category, 0)

    def remaining(self, category: ContextCategory) -> int:
        return max(self.budget.allocation(category) - self.used(category), 0)

    def over_budget(self, category: ContextCategory) -> bool:
        return self.used(category) > self.budget.allocation(category)
