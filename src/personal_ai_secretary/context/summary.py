"""FASE K.5 — Economic conversation summary.

Builds a structured summary of conversation turns that were dropped from the
context window. This is a pure heuristic (extractive) summarizer: it never
calls the LLM, so it adds no latency or token cost. It runs only when history
truncation actually occurred.

The summary preserves what matters across a long session:
- important facts
- decisions taken
- relevant preferences
- task state
- files/projects touched
- pending work

It deliberately drops small talk and redundant content.
"""

import re
from dataclasses import dataclass, field
from typing import Final

# Caps keep the rendered summary compact.
_MAX_ITEMS_PER_SECTION: Final[int] = 4
_MAX_ITEM_CHARS: Final[int] = 160

_DECISION_RE = re.compile(
    r"\b(decid|agreed|chosen|chose|will use|switched|moved to|approved)\w*", re.IGNORECASE
)
_PREFERENCE_RE = re.compile(
    r"\b(prefer|always|never|favorite|likes?|prefers?|style|convention)\b", re.IGNORECASE
)
_PENDING_RE = re.compile(
    r"\b(todo|to-do|pending|next step|remaining|not yet|still need|later|"
    r"follow.?up|blocked by)\b",
    re.IGNORECASE,
)
_TASK_RE = re.compile(
    r"\b(created|fixed|added|updated|removed|refactored|completed|done|"
    r"implemented|deployed|renamed)\b",
    re.IGNORECASE,
)
_FILE_HINT_RE = re.compile(
    r"[\w./\\-]+\.(?:py|js|ts|tsx|jsx|md|txt|json|ya?ml|toml|ini|cfg|html|css)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ConversationSummary:
    """Structured summary of conversation content dropped from context."""

    facts: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    task_state: list[str] = field(default_factory=list)
    relevant_files: list[str] = field(default_factory=list)
    pending_work: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(
            (
                self.facts,
                self.decisions,
                self.preferences,
                self.task_state,
                self.relevant_files,
                self.pending_work,
            )
        )


def _clip(text: str, max_chars: int = _MAX_ITEM_CHARS) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


def _add_unique(bucket: list[str], item: str) -> None:
    clipped = _clip(item)
    if clipped and clipped not in bucket and len(bucket) < _MAX_ITEMS_PER_SECTION:
        bucket.append(clipped)


def build_summary_from_turns(dropped_turns: list[dict[str, str]]) -> ConversationSummary:
    """Extractive summary of dropped turns. Pure function; no LLM calls."""
    summary = ConversationSummary()
    seen_facts: set[str] = set()

    for turn in dropped_turns:
        role = turn.get("role", "")
        content = turn.get("content", "")
        if not content:
            continue
        # Split into sentences so items stay compact and relevant.
        sentences = re.split(r"(?<=[.!?])\s+|\n+", content)
        for sentence in sentences:
            text = sentence.strip()
            if len(text) < 8:
                continue
            for match in _FILE_HINT_RE.finditer(text):
                _add_unique(summary.relevant_files, match.group(0))
            if _DECISION_RE.search(text):
                _add_unique(summary.decisions, f"{role}: {text}" if role == "user" else text)
                continue
            if _PREFERENCE_RE.search(text):
                _add_unique(summary.preferences, text)
                continue
            if _PENDING_RE.search(text):
                _add_unique(summary.pending_work, text)
                continue
            if _TASK_RE.search(text):
                _add_unique(summary.task_state, text)
                continue
            # Generic sentence: only keep a few informative facts per turn.
            if len(text) >= 25 and text[:60] not in seen_facts:
                seen_facts.add(text[:60])
                _add_unique(summary.facts, text)

    return summary


def render_summary(summary: ConversationSummary) -> str | None:
    """Render the summary as a compact prompt block, or None when empty."""
    if summary.is_empty():
        return None
    sections: list[tuple[str, list[str]]] = [
        ("Facts", summary.facts),
        ("Decisions", summary.decisions),
        ("Preferences", summary.preferences),
        ("Task state", summary.task_state),
        ("Files", summary.relevant_files),
        ("Pending", summary.pending_work),
    ]
    lines = ["## Earlier conversation summary"]
    for title, items in sections:
        if items:
            lines.append(f"{title}: " + "; ".join(items))
    return "\n".join(lines)
