"""FASE K.5 — Attached-file context management.

Formats user-attached files for the LLM under a global character budget:

- small files  -> included complete
- medium files -> tail-truncated with an explicit marker
- large files  -> metadata only (name/extension/size); content is read on
  demand via tools instead of being pushed into every request

Metadata (name, extension, size) is always preserved, even when the content
is omitted. Truncation is always reported so callers can record metrics.
"""

from dataclasses import dataclass, field
from typing import Any

# Size classes (chars of content).
SMALL_FILE_CHARS = 1000
MEDIUM_FILE_CHARS = 5000

_TRUNCATION_SUFFIX = "\n... [file truncated]"


def _extension(name: str) -> str:
    if "." in name:
        return name.rsplit(".", 1)[1].lower()
    return "none"


@dataclass(slots=True)
class AttachedFilesContext:
    """Result of formatting attached files within a budget."""

    text: str = ""
    total_chars: int = 0
    files_complete: list[str] = field(default_factory=list)
    files_truncated: list[str] = field(default_factory=list)
    files_omitted: list[str] = field(default_factory=list)

    @property
    def truncated(self) -> bool:
        return bool(self.files_truncated or self.files_omitted)


def _metadata_line(name: str, size: int) -> str:
    return f"[Attached file: {name} ({_extension(name)}, {size} bytes)]"


def format_attached_files(
    files: list[Any],
    max_total_chars: int,
) -> AttachedFilesContext:
    """Format attached files within ``max_total_chars``.

    Each ``files`` item must expose ``name``, ``content`` and ``size``
    attributes (e.g. :class:`~personal_ai_secretary.domain.contracts.AttachedFile`).
    """
    result = AttachedFilesContext()
    parts: list[str] = []
    used = 0

    for file in files:
        name = str(getattr(file, "name", "file"))
        content = str(getattr(file, "content", "") or "")
        size = int(getattr(file, "size", len(content)) or len(content))
        metadata = _metadata_line(name, size)

        # Reserve room for the metadata line plus fencing overhead.
        overhead = len(metadata) + 8
        available = max_total_chars - used - overhead
        if available <= 0:
            result.files_omitted.append(name)
            continue

        if not content:
            # Metadata-only attachment (no content to include).
            parts.append(metadata)
            used += len(metadata) + 2
            result.files_omitted.append(name)
            continue

        if len(content) > MEDIUM_FILE_CHARS:
            # Large file: never pushed into context wholesale. The agent can
            # read it on demand via filesystem tools.
            note = f"{metadata}\n(content omitted due to size; use read_file if needed)"
            if len(note) + 2 <= max_total_chars - used:
                parts.append(note)
                used += len(note) + 2
            result.files_omitted.append(name)
            continue

        block = f"{metadata}\n```\n{content}\n```"
        if len(block) + 2 <= max_total_chars - used:
            # File fits whole (small or medium).
            parts.append(block)
            used += len(block) + 2
            result.files_complete.append(name)
            continue

        # Medium file that does not fit whole: truncate to the remaining space.
        body_budget = available - len(_TRUNCATION_SUFFIX)
        if body_budget >= SMALL_FILE_CHARS // 2:
            body = content[:body_budget].rstrip() + _TRUNCATION_SUFFIX
            block = f"{metadata}\n```\n{body}\n```"
            parts.append(block)
            used += len(block) + 2
            result.files_truncated.append(name)
            continue

        # No room for useful content: metadata only.
        result.files_omitted.append(name)

    result.text = "\n\n".join(parts)
    result.total_chars = len(result.text)
    return result


def build_message_with_attachments(
    user_input: str,
    files: list[Any],
    max_total_chars: int = 20_000,
) -> str:
    """Compose the final user message: attachments first, input always intact.

    The current user message is never truncated (K.5 hard rule). Files share
    whatever budget remains after the full user input is reserved.
    """
    separator = "\n\n"
    reserved = len(user_input) + (len(separator) if files else 0)
    available_for_files = max_total_chars - reserved
    if files and available_for_files > 0:
        formatted = format_attached_files(files, available_for_files)
        if formatted.text:
            return f"{formatted.text}{separator}{user_input}"
    return user_input
