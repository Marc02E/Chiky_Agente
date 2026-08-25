"""FASE N — Task Profiles.

Per-task-type configuration defining goals, required capabilities,
preferred tools, verification strategy, and failure strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from personal_ai_secretary.agents.classifier import TaskType


@dataclass(frozen=True)
class TaskProfile:
    """Configuration for how to handle a specific task type."""

    task_type: TaskType
    goal: str
    required_capabilities: frozenset[str]
    preferred_tools: tuple[str, ...]
    verification_strategy: str
    failure_strategy: str
    approval_required: bool = False


# ---------------------------------------------------------------------------
# Profile registry
# ---------------------------------------------------------------------------

_PROFILES: dict[TaskType, TaskProfile] = {
    TaskType.CHAT: TaskProfile(
        task_type=TaskType.CHAT,
        goal="Conversational response, no file or tool operations needed.",
        required_capabilities=frozenset(),
        preferred_tools=(),
        verification_strategy="none",
        failure_strategy="none",
    ),
    TaskType.FILE_CREATE: TaskProfile(
        task_type=TaskType.FILE_CREATE,
        goal="Create a new file with the specified content.",
        required_capabilities=frozenset({"file_ops"}),
        preferred_tools=("create_file", "read_file"),
        verification_strategy="file_exists",
        failure_strategy="retry_fix",
    ),
    TaskType.FILE_READ: TaskProfile(
        task_type=TaskType.FILE_READ,
        goal="Read and present the contents of a file.",
        required_capabilities=frozenset({"file_ops"}),
        preferred_tools=("read_file", "list_directory"),
        verification_strategy="none_read_only",
        failure_strategy="report_only",
    ),
    TaskType.FILE_MODIFY: TaskProfile(
        task_type=TaskType.FILE_MODIFY,
        goal="Modify an existing file with the requested changes.",
        required_capabilities=frozenset({"file_ops"}),
        preferred_tools=("read_file", "modify_file", "verify_files"),
        verification_strategy="file_changed",
        failure_strategy="retry_fix",
    ),
    TaskType.PROJECT_ANALYSIS: TaskProfile(
        task_type=TaskType.PROJECT_ANALYSIS,
        goal="Analyze project structure, technologies, and architecture.",
        required_capabilities=frozenset({"file_ops", "project_mgmt"}),
        preferred_tools=("analyze_project", "read_file", "list_directory"),
        verification_strategy="none_read_only",
        failure_strategy="report_only",
    ),
    TaskType.PROJECT_CREATE: TaskProfile(
        task_type=TaskType.PROJECT_CREATE,
        goal="Create a complete, functional project with all required files.",
        required_capabilities=frozenset({"file_ops", "project_mgmt", "code_exec"}),
        preferred_tools=(
            "create_project", "create_file", "create_directory",
            "read_file", "execute_command", "verify_files",
        ),
        verification_strategy="structure",
        failure_strategy="retry_fix",
        approval_required=True,
    ),
    TaskType.CODE_GENERATION: TaskProfile(
        task_type=TaskType.CODE_GENERATION,
        goal="Generate code for a specific function, class, or module.",
        required_capabilities=frozenset({"file_ops"}),
        preferred_tools=("create_file", "modify_file", "read_file"),
        verification_strategy="syntax_check",
        failure_strategy="retry_fix",
    ),
    TaskType.CODE_DEBUG: TaskProfile(
        task_type=TaskType.CODE_DEBUG,
        goal="Diagnose and fix code errors.",
        required_capabilities=frozenset({"file_ops", "code_exec"}),
        preferred_tools=(
            "read_file", "search_files", "modify_file",
            "execute_command", "verify_files",
        ),
        verification_strategy="test_pass",
        failure_strategy="retry_fix",
    ),
    TaskType.TEST_EXECUTION: TaskProfile(
        task_type=TaskType.TEST_EXECUTION,
        goal="Run tests and report results.",
        required_capabilities=frozenset({"code_exec"}),
        preferred_tools=("execute_command", "read_file"),
        verification_strategy="exit_code",
        failure_strategy="retry_fix",
    ),
    TaskType.DOCUMENT_ANALYSIS: TaskProfile(
        task_type=TaskType.DOCUMENT_ANALYSIS,
        goal="Analyze a document and extract key information.",
        required_capabilities=frozenset({"file_ops"}),
        preferred_tools=("read_file",),
        verification_strategy="none",
        failure_strategy="report_only",
    ),
    TaskType.IMAGE_ANALYSIS: TaskProfile(
        task_type=TaskType.IMAGE_ANALYSIS,
        goal="Analyze an image and describe its contents.",
        required_capabilities=frozenset({"vision"}),
        preferred_tools=("read_file",),
        verification_strategy="none",
        failure_strategy="report_only",
    ),
    TaskType.COMMAND_EXECUTION: TaskProfile(
        task_type=TaskType.COMMAND_EXECUTION,
        goal="Execute a shell command and return its output.",
        required_capabilities=frozenset({"code_exec"}),
        preferred_tools=("execute_command",),
        verification_strategy="exit_code",
        failure_strategy="report_only",
        approval_required=True,
    ),
    TaskType.GENERAL_DEVELOPMENT: TaskProfile(
        task_type=TaskType.GENERAL_DEVELOPMENT,
        goal="Development task that may involve multiple tools.",
        required_capabilities=frozenset({"file_ops", "code_exec"}),
        preferred_tools=(
            "read_file", "create_file", "modify_file",
            "execute_command", "analyze_project", "verify_files",
        ),
        verification_strategy="mixed",
        failure_strategy="retry_fix",
    ),
}


def get_task_profile(task_type: TaskType) -> TaskProfile:
    """Get the task profile for a given task type."""
    return _PROFILES.get(task_type, _PROFILES[TaskType.CHAT])


def get_all_profiles() -> dict[TaskType, TaskProfile]:
    """Return all registered task profiles."""
    return dict(_PROFILES)
