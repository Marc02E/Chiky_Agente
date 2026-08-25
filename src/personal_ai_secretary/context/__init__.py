"""FASE K.5 — Intelligent context window management.

Centralized, model-aware context budgeting for Chiky:

- :mod:`budget` — token estimation, model profiles, per-category budgets
- :mod:`history` — pair-preserving history truncation
- :mod:`summary` — economic (no-LLM) conversation summaries
- :mod:`files` — attached-file budgeting with metadata preservation
- :mod:`tools` — tool-result prioritization and pruning
- :mod:`project` — incremental project context tracking
- :mod:`assembler` — the context budget policy orchestrator

Priority: CORRECTION > SECURITY > FUNCTIONALITY > CONTEXT > LATENCY > TOKENS.
"""

from personal_ai_secretary.context.assembler import (
    AssembledContext,
    ContextAssembler,
    ContextStats,
)
from personal_ai_secretary.context.budget import (
    CHARS_PER_TOKEN,
    DEFAULT_PROFILE,
    MODEL_PROFILES,
    BudgetTracker,
    ContextBudget,
    ContextCategory,
    ModelProfile,
    estimate_text_tokens,
    estimate_tokens,
    resolve_model_profile,
)

__all__ = [
    "CHARS_PER_TOKEN",
    "DEFAULT_PROFILE",
    "MODEL_PROFILES",
    "AssembledContext",
    "BudgetTracker",
    "ContextAssembler",
    "ContextBudget",
    "ContextCategory",
    "ContextStats",
    "ModelProfile",
    "estimate_text_tokens",
    "estimate_tokens",
    "resolve_model_profile",
]
