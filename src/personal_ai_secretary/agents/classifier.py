"""FASE N — Task Classifier.

Classify user intent to select the appropriate workflow and verification
strategy.  Uses regex keyword analysis — no LLM call required.
"""

from __future__ import annotations

import re
from enum import StrEnum


class TaskType(StrEnum):
    """Supported task types for classification."""

    CHAT = "chat"
    FILE_CREATE = "file_create"
    FILE_READ = "file_read"
    FILE_MODIFY = "file_modify"
    PROJECT_ANALYSIS = "project_analysis"
    PROJECT_CREATE = "project_create"
    CODE_GENERATION = "code_generation"
    CODE_DEBUG = "code_debug"
    TEST_EXECUTION = "test_execution"
    DOCUMENT_ANALYSIS = "document_analysis"
    IMAGE_ANALYSIS = "image_analysis"
    COMMAND_EXECUTION = "command_execution"
    GENERAL_DEVELOPMENT = "general_development"


# ---------------------------------------------------------------------------
# Classification patterns (order matters — first match wins)
# ---------------------------------------------------------------------------

_FILE_EXT_RE = re.compile(
    r"\b\w+\.(py|js|ts|html|css|json|yaml|yml|toml|txt|md|csv|xml|sql|sh|bat|rs|go|java|c|cpp|h|rb|php|swift|kt)\b",
    re.IGNORECASE,
)

_PATTERNS: list[tuple[TaskType, re.Pattern[str]]] = [
    # Project creation — "create/make/build a project"
    (TaskType.PROJECT_CREATE, re.compile(
        r"\b(create|make|build|generate|scaffold)\b.*\b(project|app|application|boilerplate)\b",
        re.IGNORECASE,
    )),
    # Project creation — "CRUD" or "full stack" requests
    (TaskType.PROJECT_CREATE, re.compile(
        r"\b(crud|full\s*stack|rest\s*api|web\s*app)\b",
        re.IGNORECASE,
    )),
    # Project analysis — "analyze/review this project"
    (TaskType.PROJECT_ANALYSIS, re.compile(
        r"\b(analyze|analyse|review|inspect|examine|audit|assess)\b.*\b(project|codebase|repository|repo|code)\b",
        re.IGNORECASE,
    )),
    # Project analysis — "how is this project structured"
    (TaskType.PROJECT_ANALYSIS, re.compile(
        r"\b(how|what|structure|architecture|layout|organiz)\b.*\b(project|code|this|it)\b",
        re.IGNORECASE,
    )),
    # Document analysis — PDF/DOCX
    (TaskType.DOCUMENT_ANALYSIS, re.compile(
        r"\b(pdf|docx|document|word|powerpoint|pptx)\b",
        re.IGNORECASE,
    )),
    # Image analysis
    (TaskType.IMAGE_ANALYSIS, re.compile(
        r"\b(image|picture|screenshot|photo|vision|see|look at)\b"
        r".*\b(analyze|analyse|review|describe|what|read)\b",
        re.IGNORECASE,
    )),
    (TaskType.IMAGE_ANALYSIS, re.compile(
        r"\b(analyze|analyse|review|describe|read)\b.*\b(image|picture|screenshot|photo)\b",
        re.IGNORECASE,
    )),
    # Test execution
    (TaskType.TEST_EXECUTION, re.compile(
        r"\b(run|execute|perform|do)\b.*\b(test|tests|pytest|unittest|coverage)\b",
        re.IGNORECASE,
    )),
    (TaskType.TEST_EXECUTION, re.compile(
        r"\b(pytest|unittest|npm\s+test|yarn\s+test|cargo\s+test|go\s+test)\b",
        re.IGNORECASE,
    )),
    # Code debug — "fix this error/bug"
    (TaskType.CODE_DEBUG, re.compile(
        r"\b(debug|fix|repair|resolve|troubleshoot)\b.*\b(error|bug|crash|issue|problem|fail|broken|exception|traceback)\b",
        re.IGNORECASE,
    )),
    (TaskType.CODE_DEBUG, re.compile(
        r"\b(error|bug|crash|issue|traceback|exception)\b.*\b(fix|debug|repair|resolve)\b",
        re.IGNORECASE,
    )),
    # Command execution
    (TaskType.COMMAND_EXECUTION, re.compile(
        r"\b(execute|run|shell|terminal|command|shell)\b.*\b(command|script|cmd|terminal)\b",
        re.IGNORECASE,
    )),
    (TaskType.COMMAND_EXECUTION, re.compile(
        r"^\s*(git|python|py|node|npm|npx|pip|docker|cargo|go|java|make|cmake)\b",
        re.IGNORECASE,
    )),
    # File creation — "create/write/save" + filename
    (TaskType.FILE_CREATE, re.compile(
        r"\b(create|write|save|make|generate)\b.*\b(file|archivo)\b",
        re.IGNORECASE,
    )),
    (TaskType.FILE_CREATE, re.compile(
        r"\b(crea|crear|escribe|escribir|guarda|guardar|haz|genera|creación)\b.*\b(file|archivo|fichero|documento)\b",
        re.IGNORECASE,
    )),
    (TaskType.FILE_CREATE, re.compile(
        r"\b(create|write|save)\b.*\w+\.\w{1,5}\b",
        re.IGNORECASE,
    )),
    (TaskType.FILE_CREATE, re.compile(
        r"\b(crea|escribe|guarda)\b.*\w+\.\w{1,5}\b",
        re.IGNORECASE,
    )),
    # File read — "read/open/show/cat" + filename or path
    (TaskType.FILE_READ, re.compile(
        r"\b(read|open|show|cat|display|view|print)\b.*\b(file|content|archivo)\b",
        re.IGNORECASE,
    )),
    (TaskType.FILE_READ, re.compile(
        r"\b(read|open|show|cat)\b.*\w+\.\w{1,5}\b",
        re.IGNORECASE,
    )),
    # File modification — "edit/modify/change/update/add/fix" + file context
    (TaskType.FILE_MODIFY, re.compile(
        r"\b(edit|modify|change|update|add|remove|delete|replace|patch)\b.*\b(file|line|function|class|method|section|code)\b",
        re.IGNORECASE,
    )),
    (TaskType.FILE_MODIFY, re.compile(
        r"\b(add|remove|insert|delete)\b.*\b(endpoint|route|handler|function|class|import|module)\b",
        re.IGNORECASE,
    )),
    # Code generation — "create/write/generate" + code constructs
    (TaskType.CODE_GENERATION, re.compile(
        r"\b(create|write|generate|build|implement|develop)\b.*\b(function|class|module|api|endpoint|service|handler|component|widget|script|program)\b",
        re.IGNORECASE,
    )),
    (TaskType.CODE_GENERATION, re.compile(
        r"\b(code|function|class|api|endpoint|route|handler)\b.*\b(for|that|which|in|using|with)\b",
        re.IGNORECASE,
    )),
    # General development — anything with development keywords
    (TaskType.GENERAL_DEVELOPMENT, re.compile(
        r"\b(develop|implement|integrate|configure|setup|set up|deploy|migrate|refactor)\b",
        re.IGNORECASE,
    )),
]


def classify_task(text: str) -> TaskType:
    """Classify user input into a task type.

    Uses regex pattern matching — first match wins. Falls back to CHAT
    if no patterns match.

    Args:
        text: User input text.

    Returns:
        The classified TaskType.
    """
    if not text or not text.strip():
        return TaskType.CHAT

    text = text.strip()

    for task_type, pattern in _PATTERNS:
        if pattern.search(text):
            return task_type

    return TaskType.CHAT


def classify_task_with_detail(text: str) -> tuple[TaskType, str]:
    """Classify with a human-readable explanation of why.

    Returns (task_type, reason).
    """
    if not text or not text.strip():
        return TaskType.CHAT, "Empty input"

    text = text.strip()

    for task_type, pattern in _PATTERNS:
        match = pattern.search(text)
        if match:
            return task_type, f"Matched pattern for {task_type.value}: '{match.group()}'"

    return TaskType.CHAT, "No specific pattern matched; defaulting to chat"
