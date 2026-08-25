"""FASE K.5 — Conversation history management.

Builds the message list sent to the LLM from raw session history using a
budget-aware, pair-preserving strategy:

1. The current user message is never part of history (it is sent separately).
2. Newest turns are kept first; oldest turns are dropped first.
3. user/assistant pairs are kept together whenever possible.
4. Individual messages are not cut mid-text unless a single newest pair alone
   exceeds the whole budget (then a tail-truncation marker is applied).
5. Truncation is always reported so callers can record metrics and build a
   conversation summary from the dropped turns.
"""

from dataclasses import dataclass, field

from personal_ai_secretary.domain.contracts import ConversationTurn

TRUNCATION_MARKER = "...[earlier conversation truncated]"


@dataclass(slots=True)
class HistoryBuildResult:
    """Outcome of history assembly."""

    messages: list[ConversationTurn] = field(default_factory=list)
    kept_turns: int = 0
    dropped_turns: int = 0
    dropped_chars: int = 0
    truncated: bool = False
    dropped_messages: list[dict[str, str]] = field(default_factory=list)


def _valid_turn(turn: object) -> dict[str, str] | None:
    """Return a normalized {role, content} dict for valid turns, else None."""
    if isinstance(turn, dict):
        role = turn.get("role")
        content = turn.get("content")
        if isinstance(role, str) and isinstance(content, str) and content:
            return {"role": role, "content": content}
    return None


def _group_pairs(history: list[dict[str, str]]) -> list[list[dict[str, str]]]:
    """Group alternating turns into [user, assistant] pairs.

    An orphan leading user turn (no assistant reply yet) becomes its own group.
    """
    groups: list[list[dict[str, str]]] = []
    i = len(history)
    while i > 0:
        current = history[i - 1]
        if (
            current["role"] == "assistant"
            and i >= 2
            and history[i - 2]["role"] == "user"
        ):
            groups.append([history[i - 2], history[i - 1]])
            i -= 2
        else:
            groups.append([current])
            i -= 1
    groups.reverse()
    return groups


def _truncate_single(content: str, max_chars: int) -> str:
    """Tail-truncate one message, keeping the most recent content."""
    keep = max(max_chars - len(TRUNCATION_MARKER), 1)
    return content[-keep:] + TRUNCATION_MARKER


def build_history(
    history: list[dict[str, str]] | list[object],
    max_chars: int,
) -> HistoryBuildResult:
    """Assemble conversation history within ``max_chars`` characters.

    Walks newest -> oldest keeping complete user/assistant pairs. Dropped
    turns are reported for summary building. The result never includes the
    current request's own message.
    """
    result = HistoryBuildResult()
    if max_chars <= 0:
        result.truncated = bool(history)
        result.dropped_messages = [
            t for t in (_valid_turn(x) for x in history) if t is not None
        ]
        result.dropped_turns = len(result.dropped_messages)
        result.dropped_chars = sum(len(t["content"]) for t in result.dropped_messages)
        return result

    normalized = [t for t in (_valid_turn(x) for x in history) if t is not None]
    groups = _group_pairs(normalized)

    selected: list[list[dict[str, str]]] = []
    used = 0
    for group in reversed(groups):
        group_chars = sum(len(m["content"]) for m in group)
        if used + group_chars <= max_chars:
            selected.append(group)
            used += group_chars
            continue
        if not selected:
            # Even the newest pair does not fit: keep its tail so context is
            # never empty, preferring the assistant answer and latest content.
            flat = [m for m in group]
            remaining = max_chars
            for msg in reversed(flat):
                content_len = len(msg["content"])
                if content_len <= remaining:
                    selected.append([msg])
                    remaining -= content_len
                else:
                    selected.append([
                        {
                            "role": msg["role"],
                            "content": _truncate_single(msg["content"], remaining),
                        }
                    ])
                    remaining = 0
                    break
            break
        break

    # `selected` is newest-first; emit oldest-first preserving pair order.
    for group in reversed(selected):
        for msg in group:
            result.messages.append(ConversationTurn(role=msg["role"], content=msg["content"]))
    result.kept_turns = len(result.messages)

    kept_ids = {id(m) for g in selected for m in g}
    for group in groups:
        for msg in group:
            if id(msg) not in kept_ids:
                result.dropped_messages.append(msg)
    result.dropped_turns = len(result.dropped_messages)
    result.dropped_chars = sum(len(m["content"]) for m in result.dropped_messages)
    result.truncated = result.dropped_turns > 0
    return result
