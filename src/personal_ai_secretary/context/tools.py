"""FASE K.5 — Tool-result prioritization and pruning.

Not all tool results are equally important. When the running conversation
exceeds the context budget, older tool results are replaced with compact
stubs before the next LLM round, while the most recent results (needed to
continue the current operation) stay intact.

Priority order:
1. result needed to continue the operation (most recent)
2. recently executed tool results
3. results related to the current file/project
4. old results (pruned first)
"""

from dataclasses import dataclass

from personal_ai_secretary.domain.contracts import ConversationTurn

# Tool-result user messages are produced by tools.prompt.build_tool_result_prompt.
TOOL_RESULT_PREFIX = "Tool '"

_STUB_SUFFIX = ": [earlier result omitted]"


def is_tool_result_message(content: str) -> bool:
    """Check whether a message content is a tool-result injection."""
    return content.startswith(TOOL_RESULT_PREFIX) and "' result:" in content


def _tool_name(content: str) -> str:
    try:
        start = content.index("'") + 1
        end = content.index("'", start)
        return content[start:end]
    except ValueError:
        return "tool"


@dataclass(slots=True)
class PruneStats:
    """Outcome of a pruning pass."""

    pruned_count: int = 0
    chars_saved: int = 0


def tool_results_chars(messages: list[ConversationTurn]) -> int:
    """Total characters used by tool-result messages."""
    return sum(len(m.content) for m in messages if is_tool_result_message(m.content))


def prune_tool_results(
    messages: list[ConversationTurn],
    max_total_chars: int,
    keep_recent: int = 2,
) -> tuple[list[ConversationTurn], PruneStats]:
    """Prune older tool results when they exceed ``max_total_chars``.

    Keeps the ``keep_recent`` most recent tool results intact; older ones are
    replaced by one-line stubs until the total fits. Returns the (possibly
    new) message list plus stats. The input list is not mutated.
    """
    stats = PruneStats()
    indices = [i for i, m in enumerate(messages) if is_tool_result_message(m.content)]
    if len(indices) <= keep_recent:
        return messages, stats

    # Newest keep_recent indices stay intact.
    prunable = indices[:-keep_recent] if keep_recent > 0 else indices
    total = sum(len(messages[i].content) for i in indices)

    result = list(messages)
    for idx in prunable:  # oldest first
        if total <= max_total_chars:
            break
        original = result[idx].content
        stub_content = f"Tool '{_tool_name(original)}'{_STUB_SUFFIX}"
        saved = len(original) - len(stub_content)
        if saved <= 0:
            continue
        result[idx] = ConversationTurn(role=messages[idx].role, content=stub_content)
        total -= saved
        stats.pruned_count += 1
        stats.chars_saved += saved
    return result, stats
