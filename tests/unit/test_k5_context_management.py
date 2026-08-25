"""Tests for FASE K.5 — Intelligent Context Window Management.

Covers: token estimation, context budget, model-aware profiles, history
truncation (pair-preserving), conversation summary (no LLM calls), attached
file budgeting, tool-result prioritization, project context tracking,
assembler policy, K.5 metrics, and end-to-end agentic loop behavior.
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from personal_ai_secretary.agents.builtin import ExecutionAgent
from personal_ai_secretary.agents.contracts import AgentInput
from personal_ai_secretary.context.assembler import ContextAssembler
from personal_ai_secretary.context.budget import (
    CHARS_PER_TOKEN,
    DEFAULT_PROFILE,
    MODEL_PROFILES,
    BudgetTracker,
    ContextBudget,
    ContextCategory,
    estimate_text_tokens,
    estimate_tokens,
    resolve_model_profile,
)
from personal_ai_secretary.context.files import (
    AttachedFilesContext,
    build_message_with_attachments,
    format_attached_files,
)
from personal_ai_secretary.context.history import build_history
from personal_ai_secretary.context.project import ProjectContextTracker
from personal_ai_secretary.context.summary import (
    build_summary_from_turns,
    render_summary,
)
from personal_ai_secretary.context.tools import (
    is_tool_result_message,
    prune_tool_results,
    tool_results_chars,
)
from personal_ai_secretary.domain.contracts import ConversationTurn, ProviderResponse, RiskLevel
from personal_ai_secretary.observability.request_metrics import RequestMetrics


def _make_input(
    text: str = "hello",
    context: dict | None = None,
) -> AgentInput:
    return AgentInput(
        request_id=uuid4(),
        session_id=uuid4(),
        user_id="test",
        text=text,
        correlation_id="test",
        risk_level=RiskLevel.LOW,
        context=context or {},
    )


def _mock_provider(responses: list[str] | str = "OK") -> AsyncMock:
    mock = AsyncMock()
    mock.name = "mock"
    mock.model = "test-model"
    if isinstance(responses, str):
        responses = [responses]
    call_state = {"n": 0}

    async def generate(request: object) -> ProviderResponse:
        idx = min(call_state["n"], len(responses) - 1)
        call_state["n"] += 1
        return ProviderResponse(text=responses[idx], provider="mock")

    mock.generate = generate
    return mock


# ═══════════════════════════════════════════════════════════════════════════════
# Token estimation (centralized)
# ═══════════════════════════════════════════════════════════════════════════════


class TestTokenEstimation:
    def test_chars_per_token_constant(self) -> None:
        assert CHARS_PER_TOKEN == 4

    def test_estimate_tokens(self) -> None:
        assert estimate_tokens(0) == 0
        assert estimate_tokens(-5) == 0
        assert estimate_tokens(4) == 1
        assert estimate_tokens(100) == 25

    def test_estimate_text_tokens(self) -> None:
        assert estimate_text_tokens("a" * 8) == 2
        assert estimate_text_tokens("") == 0

    def test_request_metrics_reexports_estimate(self) -> None:
        from personal_ai_secretary.observability.request_metrics import (
            estimate_tokens as reexported,
        )

        assert reexported is estimate_tokens


# ═══════════════════════════════════════════════════════════════════════════════
# Model-aware budgets
# ═══════════════════════════════════════════════════════════════════════════════


class TestModelProfiles:
    def test_llama31_profile(self) -> None:
        profile = resolve_model_profile("llama3.1:8b")
        assert profile.key == "llama3.1"
        assert profile.context_window_tokens > 100_000

    def test_llama3_profile_is_smaller(self) -> None:
        profile = resolve_model_profile("llama3")
        assert profile.key == "llama3"
        assert profile.context_window_tokens < resolve_model_profile("llama3.1").context_window_tokens

    def test_deepseek_profile(self) -> None:
        profile = resolve_model_profile("deepseek-coder-v2:16b")
        assert profile.key == "deepseek-coder-v2"

    def test_unknown_model_uses_safe_default(self) -> None:
        profile = resolve_model_profile("totally-unknown-99b")
        assert profile is DEFAULT_PROFILE

    def test_none_model_uses_default(self) -> None:
        assert resolve_model_profile(None) is DEFAULT_PROFILE

    def test_empty_string_uses_default(self) -> None:
        assert resolve_model_profile("") is DEFAULT_PROFILE

    def test_case_insensitive(self) -> None:
        assert resolve_model_profile("LLAMA3.1").key == "llama3.1"

    def test_all_profiles_have_positive_budgets(self) -> None:
        for profile in MODEL_PROFILES.values():
            assert profile.context_window_tokens > 0
            assert profile.response_budget_tokens > 0
            assert profile.tool_budget_tokens > 0


class TestContextBudget:
    def test_from_default_profile(self) -> None:
        budget = ContextBudget.from_profile(DEFAULT_PROFILE)
        usable = DEFAULT_PROFILE.context_window_tokens - DEFAULT_PROFILE.response_budget_tokens
        assert budget.total_tokens == usable
        assert budget.total_chars == usable * CHARS_PER_TOKEN

    def test_categories_sum_within_total(self) -> None:
        budget = ContextBudget.from_profile(DEFAULT_PROFILE)
        total = (
            budget.system_prompt_chars
            + budget.history_chars
            + budget.tool_results_chars
            + budget.attached_files_chars
            + budget.project_context_chars
        )
        # Shares sum to 1.0; rounding may add a few chars.
        assert total <= budget.total_chars + 16

    def test_history_is_largest_share(self) -> None:
        budget = ContextBudget.from_profile(DEFAULT_PROFILE)
        assert budget.history_chars > budget.system_prompt_chars
        assert budget.history_chars > budget.attached_files_chars

    def test_tool_budget_floor_respected(self) -> None:
        budget = ContextBudget.from_profile(DEFAULT_PROFILE)
        floor = DEFAULT_PROFILE.tool_budget_tokens * CHARS_PER_TOKEN
        assert budget.tool_results_chars >= floor

    def test_allocation_lookup(self) -> None:
        budget = ContextBudget.from_profile(DEFAULT_PROFILE)
        assert budget.allocation(ContextCategory.HISTORY) == budget.history_chars
        assert budget.allocation(ContextCategory.TOOL_RESULTS) == budget.tool_results_chars

    def test_large_model_gets_larger_history(self) -> None:
        big = ContextBudget.from_profile(resolve_model_profile("llama3.1"))
        small = ContextBudget.from_profile(resolve_model_profile("llama3"))
        assert big.history_chars > small.history_chars


class TestBudgetTracker:
    def test_record_and_remaining(self) -> None:
        budget = ContextBudget.from_profile(DEFAULT_PROFILE)
        tracker = BudgetTracker(budget=budget)
        assert tracker.remaining(ContextCategory.HISTORY) == budget.history_chars
        tracker.record(ContextCategory.HISTORY, 100)
        assert tracker.used(ContextCategory.HISTORY) == 100
        assert tracker.remaining(ContextCategory.HISTORY) == budget.history_chars - 100

    def test_over_budget(self) -> None:
        budget = ContextBudget.from_profile(DEFAULT_PROFILE)
        tracker = BudgetTracker(budget=budget)
        assert not tracker.over_budget(ContextCategory.HISTORY)
        tracker.record(ContextCategory.HISTORY, budget.history_chars + 1)
        assert tracker.over_budget(ContextCategory.HISTORY)

    def test_remaining_never_negative(self) -> None:
        budget = ContextBudget.from_profile(DEFAULT_PROFILE)
        tracker = BudgetTracker(budget=budget)
        tracker.record(ContextCategory.HISTORY, budget.history_chars * 10)
        assert tracker.remaining(ContextCategory.HISTORY) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# History truncation
# ═══════════════════════════════════════════════════════════════════════════════


class TestHistoryTruncation:
    def _history(self, turns: int, size: int = 200) -> list[dict[str, str]]:
        history = []
        for i in range(turns):
            history.append({"role": "user", "content": f"Q{i}: " + "x" * size})
            history.append({"role": "assistant", "content": f"A{i}: " + "y" * size})
        return history

    def test_short_history_kept_whole(self) -> None:
        history = self._history(3)
        result = build_history(history, max_chars=10_000)
        assert not result.truncated
        assert result.kept_turns == 6
        assert result.dropped_turns == 0
        assert [m.role for m in result.messages] == ["user", "assistant"] * 3

    def test_drops_oldest_first(self) -> None:
        history = self._history(10, size=200)
        result = build_history(history, max_chars=2400)
        assert result.truncated
        first_kept = result.messages[0].content
        # Newest content preserved; oldest dropped.
        assert "A9" in result.messages[-1].content
        assert "Q0" not in first_kept
        assert result.dropped_turns > 0
        assert result.dropped_chars > 0

    def test_pairs_preserved(self) -> None:
        history = self._history(10, size=200)
        result = build_history(history, max_chars=2400)
        roles = [m.role for m in result.messages]
        # Every assistant must be preceded by its user.
        for i, role in enumerate(roles):
            if role == "assistant":
                assert roles[i - 1] == "user"

    def test_recent_messages_always_kept(self) -> None:
        history = self._history(20, size=300)
        result = build_history(history, max_chars=1500)
        contents = [m.content for m in result.messages]
        assert any("A19" in c for c in contents)

    def test_single_huge_pair_tail_truncated(self) -> None:
        huge = [{"role": "user", "content": "u" * 5000}, {"role": "assistant", "content": "a" * 5000}]
        result = build_history(huge, max_chars=1000)
        assert result.truncated
        assert sum(len(m.content) for m in result.messages) <= 1100
        assert all(m.content for m in result.messages)

    def test_zero_budget_truncates_everything(self) -> None:
        history = self._history(2)
        result = build_history(history, max_chars=0)
        assert result.truncated
        assert result.messages == []
        assert result.dropped_turns == 4

    def test_invalid_turns_skipped(self) -> None:
        history: list[object] = [
            {"role": "user", "content": "valid"},
            "garbage",
            {"nope": 1},
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": "ok"},
        ]
        result = build_history(history, max_chars=10_000)
        assert result.kept_turns == 2

    def test_orphan_user_turn_kept(self) -> None:
        history = [{"role": "user", "content": "unanswered question"}]
        result = build_history(history, max_chars=10_000)
        assert result.kept_turns == 1
        assert not result.truncated

    def test_dropped_messages_chronological(self) -> None:
        history = self._history(6, size=200)
        result = build_history(history, max_chars=1200)
        dropped = result.dropped_messages
        # Dropped messages come from the oldest part of the conversation.
        assert dropped[0]["content"].startswith(("Q0", "A0", "Q1", "A1"))


# ═══════════════════════════════════════════════════════════════════════════════
# Conversation summary (economic, no LLM calls)
# ═══════════════════════════════════════════════════════════════════════════════


class TestConversationSummary:
    def test_empty_summary_renders_none(self) -> None:
        summary = build_summary_from_turns([])
        assert summary.is_empty()
        assert render_summary(summary) is None

    def test_files_extracted(self) -> None:
        turns = [
            {"role": "user", "content": "Please fix the bug in src/app/main.py and check README.md"}
        ]
        summary = build_summary_from_turns(turns)
        assert "src/app/main.py" in summary.relevant_files
        assert "README.md" in summary.relevant_files

    def test_decisions_extracted(self) -> None:
        turns = [
            {"role": "user", "content": "I decided we will use SQLite for storage in this project."}
        ]
        summary = build_summary_from_turns(turns)
        assert summary.decisions

    def test_preferences_extracted(self) -> None:
        turns = [{"role": "user", "content": "I prefer concise answers without emojis."}]
        summary = build_summary_from_turns(turns)
        assert summary.preferences

    def test_pending_work_extracted(self) -> None:
        turns = [{"role": "assistant", "content": "The migration is still pending, next step is to run tests."}]
        summary = build_summary_from_turns(turns)
        assert summary.pending_work

    def test_task_state_extracted(self) -> None:
        turns = [{"role": "assistant", "content": "I created the config file and updated the schema."}]
        summary = build_summary_from_turns(turns)
        assert summary.task_state

    def test_facts_extracted(self) -> None:
        turns = [
            {
                "role": "user",
                "content": "The production server runs on Ubuntu 24.04 with 32GB of RAM installed.",
            }
        ]
        summary = build_summary_from_turns(turns)
        assert summary.facts

    def test_items_capped_and_clipped(self) -> None:
        long_line = "prefer " + "word " * 100
        turns = [{"role": "user", "content": long_line}]
        summary = build_summary_from_turns(turns)
        assert all(len(item) <= 160 for item in summary.preferences)

    def test_render_contains_sections(self) -> None:
        turns = [
            {"role": "user", "content": "I decided to use pytest. I prefer short answers. Fix main.py. The task is still pending."},
        ]
        rendered = render_summary(build_summary_from_turns(turns))
        assert rendered is not None
        assert rendered.startswith("## Earlier conversation summary")
        assert "Decisions:" in rendered
        assert "Preferences:" in rendered

    def test_no_llm_calls_pure_function(self) -> None:
        # Building a summary must not require a provider at all.
        turns = [{"role": "user", "content": "x" * 50}]
        summary = build_summary_from_turns(turns)
        assert isinstance(summary.facts, list)


# ═══════════════════════════════════════════════════════════════════════════════
# Attached files
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class FakeFile:
    name: str
    content: str
    size: int


class TestAttachedFiles:
    def test_small_file_complete(self) -> None:
        files = [FakeFile("notes.txt", "hello world", 11)]
        ctx = format_attached_files(files, max_total_chars=5000)
        assert ctx.files_complete == ["notes.txt"]
        assert not ctx.truncated
        assert "hello world" in ctx.text
        assert "[Attached file: notes.txt" in ctx.text

    def test_metadata_preserved(self) -> None:
        files = [FakeFile("data.json", "{}", 2)]
        ctx = format_attached_files(files, max_total_chars=5000)
        assert "(json, 2 bytes)" in ctx.text

    def test_large_file_metadata_only(self) -> None:
        files = [FakeFile("big.py", "x" * 6000, 6000)]
        ctx = format_attached_files(files, max_total_chars=50_000)
        assert ctx.files_omitted == ["big.py"]
        assert "x" * 100 not in ctx.text
        assert "read_file" in ctx.text
        assert ctx.truncated

    def test_medium_file_truncated_when_tight_budget(self) -> None:
        files = [FakeFile("mid.txt", "m" * 3000, 3000)]
        ctx = format_attached_files(files, max_total_chars=1200)
        assert ctx.files_truncated == ["mid.txt"]
        assert "[file truncated]" in ctx.text
        assert len(ctx.total_chars.__str__()) >= 0  # sanity
        assert ctx.total_chars <= 1300

    def test_global_budget_respected(self) -> None:
        files = [FakeFile(f"f{i}.txt", "z" * 800, 800) for i in range(20)]
        ctx = format_attached_files(files, max_total_chars=2500)
        assert ctx.total_chars <= 2600
        assert len(ctx.files_complete) < 20
        assert ctx.files_omitted  # some became metadata-only

    def test_no_files(self) -> None:
        ctx = format_attached_files([], max_total_chars=1000)
        assert ctx.text == ""
        assert ctx.total_chars == 0
        assert not ctx.truncated

    def test_build_message_small_attachment(self) -> None:
        msg = build_message_with_attachments("What does this do?", [FakeFile("a.txt", "abc", 3)])
        assert msg.endswith("What does this do?")
        assert "[Attached file: a.txt" in msg

    def test_build_message_input_never_truncated(self) -> None:
        user_input = "IMPORTANT " + "q" * 500
        files = [FakeFile(f"f{i}.txt", "z" * 4000, 4000) for i in range(10)]
        msg = build_message_with_attachments(user_input, files, max_total_chars=8000)
        assert msg.endswith(user_input)
        assert len(msg) <= 8100

    def test_build_message_without_files(self) -> None:
        assert build_message_with_attachments("just input", []) == "just input"

    def test_build_message_huge_input_no_files_room(self) -> None:
        user_input = "i" * 19_999
        files = [FakeFile("f.txt", "c", 1)]
        msg = build_message_with_attachments(user_input, files, max_total_chars=20_000)
        # Input always intact even when there is no room for file content.
        assert msg == user_input or msg.endswith(user_input)


# ═══════════════════════════════════════════════════════════════════════════════
# Tool-result prioritization
# ═══════════════════════════════════════════════════════════════════════════════


def _tool_msg(name: str, body: str) -> ConversationTurn:
    return ConversationTurn(role="user", content=f"Tool '{name}' result: {body}")


class TestToolResultPruning:
    def test_is_tool_result_message(self) -> None:
        assert is_tool_result_message("Tool 'read_file' result: {...}")
        assert not is_tool_result_message("Hello there")
        assert not is_tool_result_message("Tool 'x' was great")  # no "' result:"

    def test_under_budget_no_pruning(self) -> None:
        msgs = [_tool_msg("a", "x" * 100), _tool_msg("b", "y" * 100)]
        result, stats = prune_tool_results(msgs, max_total_chars=10_000)
        assert stats.pruned_count == 0
        assert result == msgs

    def test_old_results_pruned_newest_kept(self) -> None:
        msgs = [
            ConversationTurn(role="user", content="regular question"),
            _tool_msg("read_file", "a" * 900),
            _tool_msg("list_directory", "b" * 900),
            _tool_msg("search_files", "c" * 900),  # newest — must survive
        ]
        result, stats = prune_tool_results(msgs, max_total_chars=1000, keep_recent=1)
        assert stats.pruned_count >= 1
        assert stats.chars_saved > 0
        assert "search_files" in result[-1].content
        assert "c" * 100 in result[-1].content
        # Oldest replaced by stub.
        assert "[earlier result omitted]" in result[1].content
        # Non-tool messages untouched.
        assert result[0].content == "regular question"

    def test_keep_recent_two(self) -> None:
        msgs = [_tool_msg(f"t{i}", "x" * 500) for i in range(4)]
        result, stats = prune_tool_results(msgs, max_total_chars=600, keep_recent=2)
        assert stats.pruned_count == 2
        assert "t3" in result[3].content and "t2" in result[2].content
        assert "[earlier result omitted]" in result[0].content

    def test_input_not_mutated(self) -> None:
        msgs = [_tool_msg("t0", "x" * 500), _tool_msg("t1", "y" * 500)]
        original_first = msgs[0].content
        prune_tool_results(msgs, max_total_chars=100, keep_recent=1)
        assert msgs[0].content == original_first

    def test_tool_results_chars_helper(self) -> None:
        msgs = [
            ConversationTurn(role="user", content="hi"),
            _tool_msg("t", "12345"),
        ]
        assert tool_results_chars(msgs) == len("Tool 't' result: 12345")


# ═══════════════════════════════════════════════════════════════════════════════
# Project context tracker
# ═══════════════════════════════════════════════════════════════════════════════


class TestProjectContextTracker:
    def test_empty_describe(self) -> None:
        tracker = ProjectContextTracker()
        assert tracker.describe() is None
        assert tracker.chars == 0

    def test_read_file_noted(self) -> None:
        tracker = ProjectContextTracker()
        tracker.note_tool_result("read_file", {"path": "/home/u/proj/main.py", "size": 2048})
        desc = tracker.describe()
        assert desc is not None
        assert "main.py" in desc
        assert "2.0KB" in desc

    def test_read_files_batch_noted(self) -> None:
        tracker = ProjectContextTracker()
        tracker.note_tool_result(
            "read_files",
            {"files": {"/p/a.py": {"size": 10}, "/p/b.py": {"size": 20}}},
        )
        desc = tracker.describe()
        assert desc is not None
        assert "a.py" in desc and "b.py" in desc

    def test_analyze_project_notes_root_only(self) -> None:
        tracker = ProjectContextTracker()
        tracker.note_tool_result(
            "analyze_project",
            {"project_path": "/home/u/bigproj", "structure": [{"name": "x"}]},
        )
        desc = tracker.describe()
        assert desc is not None
        assert "bigproj" in desc
        # No file contents ever recorded.
        assert "x = 1" not in desc

    def test_error_results_ignored(self) -> None:
        tracker = ProjectContextTracker()
        tracker.note_tool_result("read_file", {"error": "not found"})
        assert tracker.describe() is None

    def test_max_entries_incremental(self) -> None:
        tracker = ProjectContextTracker(max_entries=3)
        for i in range(10):
            tracker.note_tool_result("read_file", {"path": f"/p/file{i}.py", "size": i})
        desc = tracker.describe()
        assert desc is not None
        assert "file9.py" in desc  # newest kept
        assert "file0.py" not in desc  # oldest evicted

    def test_describe_char_cap(self) -> None:
        tracker = ProjectContextTracker(max_entries=50, max_chars=200)
        for i in range(50):
            tracker.note_tool_result("read_file", {"path": f"/p/very_long_file_name_{i}.py", "size": 9999})
        desc = tracker.describe()
        assert desc is not None
        assert len(desc) <= 220

    def test_modify_file_size_after(self) -> None:
        tracker = ProjectContextTracker()
        tracker.note_tool_result("modify_file", {"path": "/p/x.py", "size_after": 4096})
        desc = tracker.describe()
        assert desc is not None
        assert "4.0KB" in desc


# ═══════════════════════════════════════════════════════════════════════════════
# Assembler policy
# ═══════════════════════════════════════════════════════════════════════════════


class TestContextAssembler:
    def _budget(self) -> ContextBudget:
        return ContextBudget.from_profile(DEFAULT_PROFILE)

    def test_system_prompt_preserved(self) -> None:
        assembler = ContextAssembler(self._budget())
        result = assembler.assemble(system_prompt="SYSTEM RULES", history=[])
        assert result.system_prompt.startswith("SYSTEM RULES")
        assert result.stats.system_prompt_chars >= len("SYSTEM RULES")

    def test_short_conversation_no_truncation(self) -> None:
        assembler = ContextAssembler(self._budget())
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = assembler.assemble(system_prompt="S", history=history)
        assert not result.stats.truncation_applied
        assert not result.stats.summary_used
        assert result.stats.history_chars > 0

    def test_long_conversation_triggers_summary(self) -> None:
        assembler = ContextAssembler(self._budget())
        history = []
        for i in range(60):
            history.append({
                "role": "user",
                "content": f"I decided to use framework{i} for the project. " + "x" * 150,
            })
            history.append({"role": "assistant", "content": "Answer " + "y" * 150})
        result = assembler.assemble(system_prompt="S", history=history)
        assert result.stats.truncation_applied
        assert result.stats.summary_used
        assert result.summary is not None
        assert "Earlier conversation summary" in result.system_prompt
        # Recent turns still present.
        assert "framework59" in result.messages[-2].content

    def test_current_message_not_part_of_history(self) -> None:
        assembler = ContextAssembler(self._budget())
        result = assembler.assemble(system_prompt="S", history=[])
        assert result.messages == []

    def test_stats_totals(self) -> None:
        assembler = ContextAssembler(self._budget())
        attached = AttachedFilesContext(text="[Attached file: a.txt]", total_chars=20)
        result = assembler.assemble(
            system_prompt="S" * 100,
            history=[{"role": "user", "content": "u" * 50}],
            attached=attached,
            project_context="Project context: x",
            tool_results_chars=30,
        )
        s = result.stats
        assert s.total_chars == (
            s.system_prompt_chars + s.history_chars + s.tool_results_chars
            + s.attached_file_chars + s.project_context_chars
        )
        assert s.estimated_tokens == estimate_tokens(s.total_chars)
        assert s.build_time >= 0.0

    def test_attached_truncation_marks_stats(self) -> None:
        assembler = ContextAssembler(self._budget())
        attached = AttachedFilesContext(
            text="[Attached file: b.txt]", total_chars=20,
            files_truncated=["b.txt"],
        )
        result = assembler.assemble(system_prompt="S", history=[], attached=attached)
        assert result.stats.truncation_applied

    def test_history_budget_shrinks_with_big_system_prompt(self) -> None:
        assembler = ContextAssembler(self._budget())
        big_prompt = "S" * (self._budget().total_chars - 512)
        history = [{"role": "user", "content": "u" * 10_000}]
        result = assembler.assemble(system_prompt=big_prompt, history=history)
        # History reduced but never empty; system prompt intact.
        assert result.messages
        assert result.stats.system_prompt_chars >= len(big_prompt)


# ═══════════════════════════════════════════════════════════════════════════════
# K.5 metrics
# ═══════════════════════════════════════════════════════════════════════════════


class TestK5Metrics:
    def test_new_fields_defaults(self) -> None:
        m = RequestMetrics()
        assert m.context_build_time == 0.0
        assert m.estimated_context_tokens == 0
        assert m.attached_file_chars == 0
        assert m.project_context_chars == 0
        assert m.truncation_applied is False
        assert m.history_truncated_turns == 0
        assert m.summary_used is False

    def test_record_round_k5_fields(self) -> None:
        m = RequestMetrics()
        m.record_round(
            1000,
            system_prompt_chars=300,
            history_chars=700,
            estimated_tokens=250,
            attached_file_chars=40,
            project_context_chars=60,
            truncation_applied=True,
            summary_used=True,
        )
        assert m.rounds == 1
        assert m.estimated_context_tokens == 250
        assert m.attached_file_chars == 40
        assert m.project_context_chars == 60
        assert m.truncation_applied is True
        assert m.summary_used is True

    def test_truncation_sticky(self) -> None:
        m = RequestMetrics()
        m.record_round(100)
        m.record_round(100, truncation_applied=True)
        assert m.truncation_applied is True

    def test_summary_includes_k5_keys(self) -> None:
        m = RequestMetrics()
        m.record_round(1000, estimated_tokens=250, truncation_applied=True, summary_used=True)
        s = m.summary()
        assert s["estimated_context_tokens"] == 250
        assert s["truncation_applied"] is True
        assert s["summary_used"] is True
        assert s["attached_file_chars"] == 0
        assert s["project_context_chars"] == 0
        assert s["history_truncated_turns"] == 0
        assert "context_build_time_s" in s

    def test_backward_compatible_summary_keys(self) -> None:
        m = RequestMetrics()
        s = m.summary()
        for key in ("provider", "model", "llm_calls", "tool_calls", "rounds",
                    "context_chars_max", "system_prompt_chars", "history_chars",
                    "tool_results_chars"):
            assert key in s


# ═══════════════════════════════════════════════════════════════════════════════
# End-to-end agentic loop with context management
# ═══════════════════════════════════════════════════════════════════════════════


class TestAgenticLoopK5:
    @pytest.mark.asyncio
    async def test_long_conversation_preserves_recent_context(self) -> None:
        """After heavy truncation the model still sees recent turns + summary."""
        history = []
        for i in range(80):
            history.append({
                "role": "user",
                "content": f"I decided the project name is project{i}. " + "filler " * 40,
            })
            history.append({"role": "assistant", "content": "Noted. " + "resp " * 40})

        captured: list[object] = []

        async def generate(request: object) -> ProviderResponse:
            captured.append(request)
            return ProviderResponse(text="All done.", provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=None)
        result = await agent.run(_make_input(context={"conversation_history": history}))

        assert result.content == "All done."
        envelope = captured[0]
        # Recent context survived truncation.
        all_content = " ".join(m.content for m in envelope.messages)  # type: ignore[attr-defined]
        assert "project79" in all_content
        # Summary of older context injected into system prompt.
        assert "Earlier conversation summary" in envelope.context_summary  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_truncation_metrics_recorded(self) -> None:
        history = [
            {"role": "user", "content": f"msg {i} " + "z" * 400}
            for i in range(60)
        ]

        async def generate(request: object) -> ProviderResponse:
            return ProviderResponse(text="OK", provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=None)
        await agent.run(_make_input(context={"conversation_history": history}))

        metrics = agent.last_metrics
        assert metrics is not None
        assert metrics.truncation_applied is True
        assert metrics.summary_used is True
        assert metrics.history_truncated_turns > 0
        assert metrics.estimated_context_tokens > 0
        assert metrics.context_build_time >= 0.0
        s = metrics.summary()
        assert s["truncation_applied"] is True
        assert s["summary_used"] is True

    @pytest.mark.asyncio
    async def test_no_truncation_metrics_clean(self) -> None:
        async def generate(request: object) -> ProviderResponse:
            return ProviderResponse(text="OK", provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=None)
        await agent.run(_make_input(text="hi"))
        metrics = agent.last_metrics
        assert metrics is not None
        assert metrics.truncation_applied is False
        assert metrics.summary_used is False

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_with_project_tracking(self) -> None:
        from personal_ai_secretary.tools.builtin import default_tool_registry

        registry = default_tool_registry()

        async def fake_read(args: dict) -> dict:
            return {"result": "file body", "path": args.get("path"), "size": 9}

        read_def = registry.get("read_file")
        assert read_def is not None
        registry._tools["read_file"] = type(read_def)(  # type: ignore[arg-type]
            name="read_file",
            risk=read_def.risk,
            requires_explicit_approval=read_def.requires_explicit_approval,
            handler=fake_read,
            argument_schema=read_def.argument_schema,
        )

        call_count = 0

        async def generate(request: object) -> ProviderResponse:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ProviderResponse(
                    text=(
                        '```tool\n{"tool": "read_file", "args": {"path": "C:/tmp/a.py"}}\n```\n'
                        '```tool\n{"tool": "calculator", "args": {"expression": "1+1"}}\n```'
                    ),
                    provider="mock",
                )
            return ProviderResponse(text="Finished both.", provider="mock")

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        result = await agent.run(_make_input(text="do two things"))
        assert result.content == "Finished both."
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_approval_flow_unaffected_by_context_changes(self) -> None:
        from personal_ai_secretary.agents.builtin import APPROVAL_REQUIRED_PREFIX
        from personal_ai_secretary.tools.builtin import default_tool_registry

        registry = default_tool_registry()
        executed = False

        async def should_not_run(args: dict) -> dict:
            nonlocal executed
            executed = True
            return {"result": "should not happen"}

        mod_def = registry.get("modify_file")
        assert mod_def is not None
        registry._tools["modify_file"] = type(mod_def)(  # type: ignore[arg-type]
            name="modify_file",
            risk=mod_def.risk,
            requires_explicit_approval=True,
            handler=should_not_run,
            argument_schema=mod_def.argument_schema,
            optional_arguments=mod_def.optional_arguments,
        )

        async def generate(request: object) -> ProviderResponse:
            return ProviderResponse(
                text=(
                    '```tool\n{"tool": "modify_file", '
                    '"args": {"path": "C:/tmp/x.py", "mode": "append", "content": "y"}}\n```'
                ),
                provider="mock",
            )

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "test-model"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=registry)
        result = await agent.run(_make_input(text="modify the file"))
        assert executed is False
        assert result.content.startswith(APPROVAL_REQUIRED_PREFIX)

    @pytest.mark.asyncio
    async def test_deepseek_sanitization_still_applies(self) -> None:
        async def generate(request: object) -> ProviderResponse:
            return ProviderResponse(
                text="<|tool_calls_begin|>Here is your answer.<|tool_calls_end|>",
                provider="mock",
            )

        provider = AsyncMock()
        provider.name = "mock"
        provider.model = "deepseek-coder-v2:16b"
        provider.generate = generate

        agent = ExecutionAgent(provider=provider, registry=None)
        result = await agent.run(_make_input())
        assert "<|tool_calls_begin|>" not in result.content
        assert "Here is your answer." in result.content

    @pytest.mark.asyncio
    async def test_model_aware_budget_via_provider_model(self) -> None:
        """llama3 (small window) truncates more aggressively than llama3.1."""
        history = [
            {"role": "user", "content": f"turn {i} " + "w" * 300}
            for i in range(40)
        ]

        async def make_agent(model: str) -> tuple[ExecutionAgent, list[object]]:
            captured: list[object] = []

            async def generate(request: object) -> ProviderResponse:
                captured.append(request)
                return ProviderResponse(text="ok", provider="mock")

            provider = AsyncMock()
            provider.name = "mock"
            provider.model = model
            provider.generate = generate
            agent = ExecutionAgent(provider=provider, registry=None)
            await agent.run(_make_input(context={"conversation_history": history}))
            return agent, captured

        _, small_caps = await make_agent("llama3")
        _, big_caps = await make_agent("llama3.1")
        small_env = small_caps[0]
        big_env = big_caps[0]
        small_chars = sum(len(m.content) for m in small_env.messages)  # type: ignore[attr-defined]
        big_chars = sum(len(m.content) for m in big_env.messages)  # type: ignore[attr-defined]
        assert big_chars >= small_chars
