"""FASE N tests — Task Classifier.

Tests for agents/classifier.py — keyword-based intent classification.
"""

from __future__ import annotations

from personal_ai_secretary.agents.classifier import (
    TaskType,
    classify_task,
    classify_task_with_detail,
)


class TestClassifyTask:
    # --- CHAT ---
    def test_empty_string(self) -> None:
        assert classify_task("") == TaskType.CHAT

    def test_none_like_whitespace(self) -> None:
        assert classify_task("   ") == TaskType.CHAT

    def test_plain_greeting(self) -> None:
        assert classify_task("hello there") == TaskType.CHAT

    def test_random_question(self) -> None:
        assert classify_task("what is the meaning of life?") == TaskType.CHAT

    # --- PROJECT_CREATE ---
    def test_create_project(self) -> None:
        assert classify_task("create a new project") == TaskType.PROJECT_CREATE

    def test_make_app(self) -> None:
        assert classify_task("make me an app") == TaskType.PROJECT_CREATE

    def test_build_project(self) -> None:
        assert classify_task("build a web project") == TaskType.PROJECT_CREATE

    def test_crud(self) -> None:
        assert classify_task("create a crud application") == TaskType.PROJECT_CREATE

    def test_full_stack(self) -> None:
        assert classify_task("build a full stack app") == TaskType.PROJECT_CREATE

    def test_rest_api(self) -> None:
        assert classify_task("create a rest api project") == TaskType.PROJECT_CREATE

    def test_scaffold_project(self) -> None:
        assert classify_task("scaffold a new project") == TaskType.PROJECT_CREATE

    # --- PROJECT_ANALYSIS ---
    def test_analyze_project(self) -> None:
        assert classify_task("analyze this project") == TaskType.PROJECT_ANALYSIS

    def test_review_codebase(self) -> None:
        assert classify_task("review this codebase") == TaskType.PROJECT_ANALYSIS

    def test_inspect_project_structure(self) -> None:
        assert classify_task("inspect the project structure") == TaskType.PROJECT_ANALYSIS

    def test_how_is_project_structured(self) -> None:
        assert classify_task("how is this project structured?") == TaskType.PROJECT_ANALYSIS

    def test_what_is_this_code(self) -> None:
        assert classify_task("what is this code doing?") == TaskType.PROJECT_ANALYSIS

    # --- DOCUMENT_ANALYSIS ---
    def test_pdf(self) -> None:
        assert classify_task("analyze this pdf") == TaskType.DOCUMENT_ANALYSIS

    def test_docx(self) -> None:
        assert classify_task("read this docx file") == TaskType.DOCUMENT_ANALYSIS

    def test_word_document(self) -> None:
        assert classify_task("open the word document") == TaskType.DOCUMENT_ANALYSIS

    # --- IMAGE_ANALYSIS ---
    def test_analyze_image(self) -> None:
        assert classify_task("analyze this image") == TaskType.IMAGE_ANALYSIS

    def test_describe_picture(self) -> None:
        assert classify_task("describe this picture") == TaskType.IMAGE_ANALYSIS

    def test_read_screenshot(self) -> None:
        assert classify_task("read this screenshot") == TaskType.IMAGE_ANALYSIS

    # --- TEST_EXECUTION ---
    def test_run_tests(self) -> None:
        assert classify_task("run the tests") == TaskType.TEST_EXECUTION

    def test_execute_pytest(self) -> None:
        assert classify_task("execute pytest") == TaskType.TEST_EXECUTION

    def test_npm_test(self) -> None:
        assert classify_task("npm test") == TaskType.TEST_EXECUTION

    def test_run_test_suite(self) -> None:
        assert classify_task("run the test suite") == TaskType.TEST_EXECUTION

    # --- CODE_DEBUG ---
    def test_fix_error(self) -> None:
        assert classify_task("fix this error") == TaskType.CODE_DEBUG

    def test_debug_bug(self) -> None:
        assert classify_task("debug this bug") == TaskType.CODE_DEBUG

    def test_resolve_exception(self) -> None:
        assert classify_task("resolve the exception") == TaskType.CODE_DEBUG

    def test_error_then_fix(self) -> None:
        assert classify_task("there is an error, fix it") == TaskType.CODE_DEBUG

    def test_crash_issue(self) -> None:
        assert classify_task("fix the crash issue") == TaskType.CODE_DEBUG

    # --- COMMAND_EXECUTION ---
    def test_run_command(self) -> None:
        assert classify_task("run a shell command") == TaskType.COMMAND_EXECUTION

    def test_execute_git(self) -> None:
        assert classify_task("git status") == TaskType.COMMAND_EXECUTION

    def test_python_script(self) -> None:
        assert classify_task("python main.py") == TaskType.COMMAND_EXECUTION

    def test_node_command(self) -> None:
        assert classify_task("node server.js") == TaskType.COMMAND_EXECUTION

    def test_npm_install(self) -> None:
        assert classify_task("npm install") == TaskType.COMMAND_EXECUTION

    # --- FILE_CREATE ---
    def test_create_file(self) -> None:
        assert classify_task("create a new file called test.py") == TaskType.FILE_CREATE

    def test_write_file(self) -> None:
        assert classify_task("write a file named config.yaml") == TaskType.FILE_CREATE

    def test_save_file(self) -> None:
        assert classify_task("save this as readme.md") == TaskType.FILE_CREATE

    def test_create_with_extension(self) -> None:
        assert classify_task("create main.py") == TaskType.FILE_CREATE

    # --- FILE_READ ---
    def test_read_file(self) -> None:
        assert classify_task("read this file for me") == TaskType.FILE_READ

    def test_open_file(self) -> None:
        assert classify_task("open the config.json file") == TaskType.FILE_READ

    def test_show_file_content(self) -> None:
        assert classify_task("show me the file content") == TaskType.FILE_READ

    def test_cat_file(self) -> None:
        assert classify_task("cat main.py") == TaskType.FILE_READ

    # --- FILE_MODIFY ---
    def test_edit_file(self) -> None:
        assert classify_task("edit the function in this file") == TaskType.FILE_MODIFY

    def test_modify_file(self) -> None:
        assert classify_task("modify the class definition") == TaskType.FILE_MODIFY

    def test_add_endpoint(self) -> None:
        assert classify_task("add an endpoint to the router") == TaskType.FILE_MODIFY

    def test_update_import(self) -> None:
        assert classify_task("update the import in the file") == TaskType.FILE_MODIFY

    def test_remove_line(self) -> None:
        assert classify_task("remove the unused import") == TaskType.FILE_MODIFY

    # --- CODE_GENERATION ---
    def test_create_function(self) -> None:
        assert classify_task("create a function for sorting") == TaskType.CODE_GENERATION

    def test_write_class(self) -> None:
        assert classify_task("write a class for user management") == TaskType.CODE_GENERATION

    def test_generate_api_endpoint(self) -> None:
        assert classify_task("generate an api endpoint") == TaskType.CODE_GENERATION

    def test_implement_handler(self) -> None:
        assert classify_task("implement a request handler") == TaskType.CODE_GENERATION

    def test_code_for_something(self) -> None:
        assert classify_task("code a function that parses JSON") == TaskType.CODE_GENERATION

    # --- GENERAL_DEVELOPMENT ---
    def test_develop_feature(self) -> None:
        assert classify_task("develop a new feature") == TaskType.GENERAL_DEVELOPMENT

    def test_implement_feature(self) -> None:
        assert classify_task("implement the authentication feature") == TaskType.GENERAL_DEVELOPMENT

    def test_configure_settings(self) -> None:
        assert classify_task("configure the database settings") == TaskType.GENERAL_DEVELOPMENT

    def test_setup_project(self) -> None:
        assert classify_task("set up the project structure") == TaskType.GENERAL_DEVELOPMENT

    def test_refactor_code(self) -> None:
        assert classify_task("refactor the legacy code") == TaskType.GENERAL_DEVELOPMENT

    def test_deploy_application(self) -> None:
        assert classify_task("deploy the application") == TaskType.GENERAL_DEVELOPMENT

    def test_migrate_database(self) -> None:
        assert classify_task("migrate the database schema") == TaskType.GENERAL_DEVELOPMENT


class TestClassifyTaskWithDetail:
    def test_returns_reason(self) -> None:
        task_type, reason = classify_task_with_detail("create a project")
        assert task_type == TaskType.PROJECT_CREATE
        assert "project_create" in reason

    def test_chat_default_reason(self) -> None:
        task_type, reason = classify_task_with_detail("hello")
        assert task_type == TaskType.CHAT
        assert "defaulting" in reason.lower() or "No specific" in reason

    def test_empty_input(self) -> None:
        task_type, reason = classify_task_with_detail("")
        assert task_type == TaskType.CHAT
        assert "Empty" in reason


class TestTaskTypeEnum:
    def test_all_values_are_strings(self) -> None:
        for member in TaskType:
            assert isinstance(member.value, str)

    def test_chat_is_default(self) -> None:
        assert TaskType.CHAT.value == "chat"

    def test_project_create_value(self) -> None:
        assert TaskType.PROJECT_CREATE.value == "project_create"
