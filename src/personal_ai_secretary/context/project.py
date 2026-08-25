"""FASE K.5 — Incremental project context.

Chiky must NOT push whole projects into the LLM context. Instead, project
work follows READ -> UNDERSTAND -> MODIFY: tools read only the files needed,
and this tracker keeps a compact, incremental index of what has been touched
(paths and sizes only — never file contents).

The rendered description gives the model awareness of the current working
set at a fixed, tiny character cost.
"""

from collections import OrderedDict
from typing import Any

# Caps keep describe() output tiny regardless of project size.
_MAX_ENTRIES = 12
_MAX_CHARS = 600


def _format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f}MB"
    if size >= 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size}B"


class ProjectContextTracker:
    """Tracks files touched during the current request (metadata only)."""

    def __init__(self, max_entries: int = _MAX_ENTRIES, max_chars: int = _MAX_CHARS) -> None:
        self._files: OrderedDict[str, int] = OrderedDict()
        self._max_entries = max_entries
        self._max_chars = max_chars
        self.project_path: str | None = None

    def note_tool_result(self, tool_name: str, result: dict[str, Any]) -> None:
        """Record paths/sizes from a tool result (read/modify/analyze calls)."""
        if not isinstance(result, dict):
            return
        if result.get("error"):
            return

        if tool_name == "read_file":
            self._note_single(result)
        elif tool_name == "read_files":
            files = result.get("files")
            if isinstance(files, dict):
                for path, info in files.items():
                    size = 0
                    if isinstance(info, dict):
                        size = int(info.get("size", 0) or 0)
                    self._note(str(path), size)
        elif tool_name in ("create_file", "modify_file"):
            self._note_single(result)
        elif tool_name == "analyze_project":
            path = result.get("project_path")
            if isinstance(path, str) and path:
                self.project_path = path

    def _note_single(self, result: dict[str, Any]) -> None:
        path = result.get("path")
        if not isinstance(path, str) or not path:
            return
        size = int(result.get("size", result.get("size_after", 0)) or 0)
        self._note(path, size)

    def _note(self, path: str, size: int) -> None:
        # Most-recently-touched ordering; refresh existing entries.
        if path in self._files:
            self._files.move_to_end(path)
        self._files[path] = size
        while len(self._files) > self._max_entries:
            self._files.popitem(last=False)

    @property
    def chars(self) -> int:
        """Approximate characters the description adds to context."""
        desc = self.describe()
        return len(desc) if desc else 0

    def describe(self) -> str | None:
        """Compact one-line description of the current working set."""
        if not self._files and not self.project_path:
            return None
        parts: list[str] = []
        total = len("Project context: ")
        if self.project_path:
            entry = f"root={self.project_path}"
            parts.append(entry)
            total += len(entry) + 2
        items = list(self._files.items())
        items.reverse()  # newest first
        for path, size in items:
            entry = f"{path} ({_format_size(size)})"
            if total + len(entry) + 2 > self._max_chars:
                break
            parts.append(entry)
            total += len(entry) + 2
        prefix = "Project context: "
        return prefix + ", ".join(parts)
