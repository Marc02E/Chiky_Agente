"""FASE N tests — Task Profiles.

Tests for agents/profiles.py — per-task-type configuration.
"""

from __future__ import annotations

import pytest

from personal_ai_secretary.agents.classifier import TaskType
from personal_ai_secretary.agents.profiles import (
    get_all_profiles,
    get_task_profile,
)


class TestGetTaskProfile:
    def test_chat_profile(self) -> None:
        p = get_task_profile(TaskType.CHAT)
        assert p.task_type == TaskType.CHAT
        assert p.verification_strategy == "none"
        assert p.failure_strategy == "none"
        assert p.approval_required is False

    def test_file_create_profile(self) -> None:
        p = get_task_profile(TaskType.FILE_CREATE)
        assert p.task_type == TaskType.FILE_CREATE
        assert "file_ops" in p.required_capabilities
        assert p.verification_strategy == "file_exists"
        assert p.failure_strategy == "retry_fix"
        assert "create_file" in p.preferred_tools

    def test_file_read_profile(self) -> None:
        p = get_task_profile(TaskType.FILE_READ)
        assert p.verification_strategy == "none_read_only"
        assert p.failure_strategy == "report_only"

    def test_file_modify_profile(self) -> None:
        p = get_task_profile(TaskType.FILE_MODIFY)
        assert p.verification_strategy == "file_changed"
        assert p.failure_strategy == "retry_fix"
        assert "modify_file" in p.preferred_tools

    def test_project_analysis_profile(self) -> None:
        p = get_task_profile(TaskType.PROJECT_ANALYSIS)
        assert "project_mgmt" in p.required_capabilities
        assert "file_ops" in p.required_capabilities
        assert p.verification_strategy == "none_read_only"

    def test_project_create_profile(self) -> None:
        p = get_task_profile(TaskType.PROJECT_CREATE)
        assert p.approval_required is True
        assert p.verification_strategy == "structure"
        assert "create_project" in p.preferred_tools
        assert "code_exec" in p.required_capabilities

    def test_code_generation_profile(self) -> None:
        p = get_task_profile(TaskType.CODE_GENERATION)
        assert p.verification_strategy == "syntax_check"
        assert p.failure_strategy == "retry_fix"

    def test_code_debug_profile(self) -> None:
        p = get_task_profile(TaskType.CODE_DEBUG)
        assert p.verification_strategy == "test_pass"
        assert "code_exec" in p.required_capabilities

    def test_test_execution_profile(self) -> None:
        p = get_task_profile(TaskType.TEST_EXECUTION)
        assert p.verification_strategy == "exit_code"
        assert "execute_command" in p.preferred_tools

    def test_document_analysis_profile(self) -> None:
        p = get_task_profile(TaskType.DOCUMENT_ANALYSIS)
        assert p.verification_strategy == "none"
        assert p.failure_strategy == "report_only"

    def test_image_analysis_profile(self) -> None:
        p = get_task_profile(TaskType.IMAGE_ANALYSIS)
        assert "vision" in p.required_capabilities
        assert p.failure_strategy == "report_only"

    def test_command_execution_profile(self) -> None:
        p = get_task_profile(TaskType.COMMAND_EXECUTION)
        assert p.approval_required is True
        assert p.verification_strategy == "exit_code"

    def test_general_development_profile(self) -> None:
        p = get_task_profile(TaskType.GENERAL_DEVELOPMENT)
        assert p.verification_strategy == "mixed"
        assert "verify_files" in p.preferred_tools

    def test_unknown_type_defaults_to_chat(self) -> None:
        # Should never happen with current TaskType, but test robustness
        p = get_task_profile("nonexistent")  # type: ignore[arg-type]
        assert p.task_type == TaskType.CHAT


class TestGetAllProfiles:
    def test_returns_all_task_types(self) -> None:
        profiles = get_all_profiles()
        for member in TaskType:
            assert member in profiles

    def test_returns_copy(self) -> None:
        profiles1 = get_all_profiles()
        profiles2 = get_all_profiles()
        assert profiles1 is not profiles2
        assert profiles1 == profiles2


class TestTaskProfileFrozen:
    def test_immutable(self) -> None:
        p = get_task_profile(TaskType.CHAT)
        with pytest.raises(AttributeError):
            p.goal = "changed"  # type: ignore[misc]

    def test_capabilities_are_frozenset(self) -> None:
        p = get_task_profile(TaskType.PROJECT_CREATE)
        assert isinstance(p.required_capabilities, frozenset)

    def test_preferred_tools_are_tuple(self) -> None:
        p = get_task_profile(TaskType.FILE_CREATE)
        assert isinstance(p.preferred_tools, tuple)
