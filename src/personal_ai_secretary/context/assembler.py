"""FASE K.5 — Context assembler.

Applies the context budget policy to build the final prompt pieces sent to
the LLM:

TOTAL CONTEXT BUDGET = system prompt + conversation history + tool results
                     + attached files + project context

Priority policy when the budget is tight:
1. drop disposable (oldest) history first;
2. summarize dropped turns into a compact structured summary (no LLM calls);
3. attached/project context is pre-budgeted by their formatters;
4. the current user message is NEVER truncated.

The assembler is pure: it performs no I/O and no LLM calls.
"""

from dataclasses import dataclass, field
from time import perf_counter

from personal_ai_secretary.context.budget import ContextBudget, estimate_tokens
from personal_ai_secretary.context.files import AttachedFilesContext
from personal_ai_secretary.context.history import HistoryBuildResult, build_history
from personal_ai_secretary.context.summary import (
    ConversationSummary,
    build_summary_from_turns,
    render_summary,
)
from personal_ai_secretary.domain.contracts import ConversationTurn


@dataclass(slots=True)
class ContextStats:
    """Measured context composition for one request."""

    system_prompt_chars: int = 0
    history_chars: int = 0
    tool_results_chars: int = 0
    attached_file_chars: int = 0
    project_context_chars: int = 0
    total_chars: int = 0
    estimated_tokens: int = 0
    history_dropped_turns: int = 0
    history_dropped_chars: int = 0
    truncation_applied: bool = False
    summary_used: bool = False
    build_time: float = 0.0


@dataclass(slots=True)
class AssembledContext:
    """Final context pieces plus measurement stats."""

    system_prompt: str = ""
    messages: list[ConversationTurn] = field(default_factory=list)
    stats: ContextStats = field(default_factory=ContextStats)
    summary: ConversationSummary | None = None


class ContextAssembler:
    """Builds the LLM-facing context within a :class:`ContextBudget`."""

    def __init__(self, budget: ContextBudget) -> None:
        self.budget = budget

    def assemble(
        self,
        *,
        system_prompt: str,
        history: list[dict[str, str]] | list[object],
        attached: AttachedFilesContext | None = None,
        project_context: str | None = None,
        tool_results_chars: int = 0,
    ) -> AssembledContext:
        started = perf_counter()
        stats = ContextStats()

        # --- Essential: system prompt is always kept in full. -------------
        final_system_prompt = system_prompt

        # --- Important: history within its allocation. --------------------
        # Reserve room for what else must fit in this request.
        reserved = len(system_prompt) + tool_results_chars
        reserved += attached.total_chars if attached else 0
        reserved += len(project_context) if project_context else 0
        history_budget = max(self.budget.history_chars, 0)
        headroom = max(self.budget.total_chars - reserved, 512)
        effective_history_budget = min(history_budget, headroom)

        history_result: HistoryBuildResult = build_history(history, effective_history_budget)

        # --- Disposable handled economically: summarize dropped turns. ----
        summary: ConversationSummary | None = None
        if history_result.truncated and history_result.dropped_messages:
            summary = build_summary_from_turns(history_result.dropped_messages)
            rendered = render_summary(summary)
            if rendered:
                final_system_prompt = f"{final_system_prompt}\n{rendered}"
                stats.summary_used = True

        messages = list(history_result.messages)

        # --- Measure. ------------------------------------------------------
        stats.system_prompt_chars = len(final_system_prompt)
        stats.history_chars = sum(len(m.content) for m in messages)
        stats.attached_file_chars = attached.total_chars if attached else 0
        stats.project_context_chars = len(project_context) if project_context else 0
        stats.tool_results_chars = tool_results_chars
        stats.history_dropped_turns = history_result.dropped_turns
        stats.history_dropped_chars = history_result.dropped_chars
        stats.truncation_applied = (
            history_result.truncated or bool(attached and attached.truncated)
        )
        stats.total_chars = (
            stats.system_prompt_chars
            + stats.history_chars
            + stats.tool_results_chars
            + stats.attached_file_chars
            + stats.project_context_chars
        )
        stats.estimated_tokens = estimate_tokens(stats.total_chars)
        stats.build_time = perf_counter() - started

        return AssembledContext(
            system_prompt=final_system_prompt,
            messages=messages,
            stats=stats,
            summary=summary,
        )
