"""FASE L.2 - Unit tests for Project Intelligence.

Tests project discovery, technology detection, entry points, dependencies,
test discovery, file importance classification, progressive reading strategy,
task-aware inspection, project summary, prompt intelligence section, and
observability metrics.
"""

from __future__ import annotations

import json
from pathlib import Path

from personal_ai_secretary.context.project_intelligence import (
    LEVEL_CONFIG,
    LEVEL_ENTRY_POINTS,
    LEVEL_METADATA,
    LEVEL_RELEVANT,
    LEVEL_TESTS,
    InspectionResult,
    ProjectManifest,
    build_project_summary,
    classify_importance,
    discover_project,
    get_read_plan,
    task_inspect,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_project_tree(root: Path, structure: dict[str, str | None]) -> None:
    for rel, content in structure.items():
        full = root / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        if content is not None:
            full.write_text(content, encoding="utf-8")
        else:
            full.touch()


def _python_project() -> dict[str, str | None]:
    return {
        "pyproject.toml": "[project]\nname = 'test-project'\nversion = '0.1.0'\n",
        "requirements.txt": "fastapi>=0.100.0\nuvicorn>=0.20.0\npytest>=7.0\n",
        "src/__init__.py": None,
        "src/app.py": "from fastapi import FastAPI\napp = FastAPI()\n",
        "src/utils.py": "def helper(): pass\n",
        "tests/__init__.py": None,
        "tests/test_app.py": "from src.app import app\ndef test_app(): pass\n",
        "tests/test_utils.py": "from src.utils import helper\ndef test_helper(): pass\n",
        "README.md": "# Test Project\nA test project.\n",
        "main.py": "from src.app import app\n",
        ".gitignore": "__pycache__/\n",
    }


def _node_project() -> dict[str, str | None]:
    return {
        "package.json": json.dumps({
            "name": "test-node",
            "version": "1.0.0",
            "main": "index.js",
            "scripts": {"start": "node server.js", "test": "jest"},
            "dependencies": {"express": "^4.18.0"},
            "devDependencies": {"jest": "^29.0.0"},
        }),
        "tsconfig.json": json.dumps({"compilerOptions": {"target": "ES2020"}}),
        "index.js": "const express = require('express');\n",
        "server.js": "const app = require('./app');\n",
        "src/app.js": "module.exports = {};\n",
        "src/utils.js": "module.exports = {};\n",
        "tests/app.test.js": "describe('app', () => {});\n",
        "README.md": "# Test Node Project\n",
    }


def _empty_project() -> dict[str, str | None]:
    return {"notes.txt": "Just some notes.\n"}


def _large_project() -> dict[str, str | None]:
    files: dict[str, str | None] = {
        "pyproject.toml": "[project]\nname = 'large-project'\n",
        "README.md": "# Large\n",
    }
    for i in range(50):
        files[f"src/module_{i}.py"] = f"def func_{i}(): pass\n"
    for i in range(20):
        files[f"tests/test_module_{i}.py"] = f"def test_{i}(): pass\n"
    return files


# ---------------------------------------------------------------------------
# Project Discovery
# ---------------------------------------------------------------------------


class TestProjectDiscovery:
    def test_discover_python_project(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        assert manifest.root == str(tmp_path.resolve())
        assert manifest.name == tmp_path.name
        assert "Python" in manifest.languages
        assert "fastapi" in manifest.frameworks
        assert manifest.total_files > 0
        assert manifest.total_directories > 0
        assert len(manifest.config_files) > 0
        assert len(manifest.test_locations) > 0
        assert len(manifest.entry_points) > 0

    def test_discover_node_project(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _node_project())
        manifest = discover_project(str(tmp_path))
        assert "JavaScript" in manifest.languages
        assert "express" in manifest.frameworks
        assert "npm" in manifest.package_managers
        assert len(manifest.entry_points) > 0

    def test_discover_empty_project(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _empty_project())
        manifest = discover_project(str(tmp_path))
        assert manifest.total_files == 1
        assert manifest.languages == []
        assert manifest.frameworks == []

    def test_discover_nonexistent_path(self, tmp_path: Path) -> None:
        manifest = discover_project(str(tmp_path / "nonexistent"))
        assert manifest.total_files == 0

    def test_discover_max_depth(self, tmp_path: Path) -> None:
        structure = {
            "a/b/c/d/e/file.py": "deep\n",
            "shallow.py": "shallow\n",
        }
        _create_project_tree(tmp_path, structure)
        manifest = discover_project(str(tmp_path), max_depth=2)
        assert manifest.total_files >= 1

    def test_discover_ignored_directories(self, tmp_path: Path) -> None:
        structure = {
            "__pycache__/cache.pyc": "cache\n",
            ".git/config": "config\n",
            "node_modules/pkg/index.js": "pkg\n",
            ".venv/lib/site.py": "venv\n",
            "src/app.py": "app\n",
        }
        _create_project_tree(tmp_path, structure)
        manifest = discover_project(str(tmp_path))
        assert len(manifest.ignored_directories) > 0
        assert manifest.total_files >= 1

    def test_discover_large_project(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _large_project())
        manifest = discover_project(str(tmp_path))
        assert manifest.total_files >= 70
        assert ".py" in manifest.file_extensions
        assert manifest.file_extensions[".py"] >= 70

    def test_discover_file_extensions(self, tmp_path: Path) -> None:
        structure = {"a.py": None, "b.py": None, "c.js": None, "d.ts": None}
        _create_project_tree(tmp_path, structure)
        manifest = discover_project(str(tmp_path))
        assert manifest.file_extensions[".py"] == 2
        assert manifest.file_extensions[".js"] == 1
        assert manifest.file_extensions[".ts"] == 1

    def test_discover_source_directories(self, tmp_path: Path) -> None:
        structure = {
            "src/__init__.py": None,
            "src/main.py": "pass\n",
            "lib/__init__.py": None,
            "lib/util.py": "pass\n",
        }
        _create_project_tree(tmp_path, structure)
        manifest = discover_project(str(tmp_path))
        assert "src" in manifest.source_directories
        assert "lib" in manifest.source_directories


# ---------------------------------------------------------------------------
# Technology Detection
# ---------------------------------------------------------------------------


class TestTechnologyDetection:
    def test_python_detection(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        assert "Python" in manifest.languages

    def test_node_detection(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _node_project())
        manifest = discover_project(str(tmp_path))
        assert "JavaScript" in manifest.languages

    def test_typescript_detection(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _node_project())
        manifest = discover_project(str(tmp_path))
        assert "TypeScript" in manifest.languages

    def test_fastapi_framework_detection(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        assert "fastapi" in manifest.frameworks

    def test_express_framework_detection(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _node_project())
        manifest = discover_project(str(tmp_path))
        assert "express" in manifest.frameworks

    def test_pip_package_manager(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        assert "pip" in manifest.package_managers

    def test_npm_package_manager(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _node_project())
        manifest = discover_project(str(tmp_path))
        assert "npm" in manifest.package_managers

    def test_no_frameworks_in_empty_project(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _empty_project())
        manifest = discover_project(str(tmp_path))
        assert manifest.frameworks == []

    def test_poetry_detection(self, tmp_path: Path) -> None:
        structure = {
            "pyproject.toml": "[tool.poetry]\nname = 'test'\n",
            "poetry.lock": "# lock\n",
        }
        _create_project_tree(tmp_path, structure)
        manifest = discover_project(str(tmp_path))
        assert "poetry" in manifest.package_managers

    def test_yarn_detection(self, tmp_path: Path) -> None:
        structure = {
            "package.json": json.dumps({"name": "test"}),
            "yarn.lock": "# lock\n",
        }
        _create_project_tree(tmp_path, structure)
        manifest = discover_project(str(tmp_path))
        assert "yarn" in manifest.package_managers

    def test_py_files_without_config(self, tmp_path: Path) -> None:
        structure = {"app.py": "print('hello')\n"}
        _create_project_tree(tmp_path, structure)
        manifest = discover_project(str(tmp_path))
        assert "Python" in manifest.languages


# ---------------------------------------------------------------------------
# Entry Point Detection
# ---------------------------------------------------------------------------


class TestEntryPointDetection:
    def test_main_py_is_entry_point(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        ep_names = [Path(ep).name for ep in manifest.entry_points]
        assert "main.py" in ep_names

    def test_index_js_is_entry_point(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _node_project())
        manifest = discover_project(str(tmp_path))
        ep_names = [Path(ep).name for ep in manifest.entry_points]
        assert "index.js" in ep_names

    def test_package_json_main_field(self, tmp_path: Path) -> None:
        pkg = {"name": "test", "main": "lib/index.js"}
        _create_project_tree(tmp_path, {"package.json": json.dumps(pkg)})
        manifest = discover_project(str(tmp_path))
        assert "lib/index.js" in manifest.entry_points

    def test_server_js_from_start_script(self, tmp_path: Path) -> None:
        pkg = {
            "name": "test",
            "scripts": {"start": "node server.js"},
        }
        _create_project_tree(tmp_path, {"package.json": json.dumps(pkg)})
        manifest = discover_project(str(tmp_path))
        assert "server.js" in manifest.entry_points


# ---------------------------------------------------------------------------
# Dependency Detection
# ---------------------------------------------------------------------------


class TestDependencyDetection:
    def test_python_requirements(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        assert "python" in manifest.dependencies
        dep_names = manifest.dependencies["python"]
        assert "fastapi" in dep_names
        assert "uvicorn" in dep_names

    def test_node_dependencies(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _node_project())
        manifest = discover_project(str(tmp_path))
        assert "node" in manifest.dependencies
        assert "express" in manifest.dependencies["node"]
        assert "node_dev" in manifest.dependencies
        assert "jest" in manifest.dependencies["node_dev"]

    def test_no_deps_in_empty_project(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _empty_project())
        manifest = discover_project(str(tmp_path))
        assert manifest.dependencies == {}


# ---------------------------------------------------------------------------
# Test Location Discovery
# ---------------------------------------------------------------------------


class TestTestDiscovery:
    def test_python_test_files(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        test_names = [Path(t).name for t in manifest.test_locations]
        assert "test_app.py" in test_names
        assert "test_utils.py" in test_names

    def test_node_test_files(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _node_project())
        manifest = discover_project(str(tmp_path))
        test_names = [Path(t).name for t in manifest.test_locations]
        assert "app.test.js" in test_names

    def test_no_tests_in_bare_project(self, tmp_path: Path) -> None:
        structure = {"app.py": "pass\n"}
        _create_project_tree(tmp_path, structure)
        manifest = discover_project(str(tmp_path))
        assert manifest.test_locations == []


# ---------------------------------------------------------------------------
# File Importance Classification
# ---------------------------------------------------------------------------


class TestFileImportanceClassification:
    def test_entry_point_is_critical(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        assert classify_importance("main.py", manifest) == "critical"

    def test_config_is_critical(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        assert classify_importance("pyproject.toml", manifest) == "critical"
        assert classify_importance("package.json", manifest) == "critical"

    def test_source_is_important(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        # src/utils.py is important (not an entry point name)
        assert classify_importance("src/utils.py", manifest) == "important"

    def test_entry_point_in_source_is_critical(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        # src/app.py is critical because app.py is an entry point name
        assert classify_importance("src/app.py", manifest) == "critical"

    def test_test_is_supporting(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        assert classify_importance("tests/test_app.py", manifest) == "supporting"

    def test_readme_is_supporting(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        assert classify_importance("README.md", manifest) == "supporting"

    def test_pyc_is_generated(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        assert classify_importance("__pycache__/app.cpython-311.pyc", manifest) == "generated"

    def test_lock_file_is_generated(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        assert classify_importance("package-lock.json", manifest) == "generated"

    def test_dist_files_are_generated(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        assert classify_importance("dist/index.js", manifest) == "generated"
        assert classify_importance("build/output.py", manifest) == "generated"

    def test_env_example_is_critical(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        # .env.example is critical (in _CRITICAL_PATTERNS)
        assert classify_importance(".env.example", manifest) == "critical"

    def test_generic_config_is_important(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        assert classify_importance("config.ini", manifest) == "important"

    def test_dockerfile_is_critical(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        assert classify_importance("Dockerfile", manifest) == "critical"
        assert classify_importance("docker-compose.yml", manifest) == "critical"


# ---------------------------------------------------------------------------
# Progressive Reading Strategy
# ---------------------------------------------------------------------------


class TestProgressiveReading:
    def test_level_metadata(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        plan = get_read_plan(LEVEL_METADATA, manifest)
        assert plan["level"] == 1
        assert plan["files"] == []
        assert plan["max_chars"] == 0
        assert "analyze_project" in plan["instruction"]

    def test_level_config(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        plan = get_read_plan(LEVEL_CONFIG, manifest)
        assert plan["level"] == 2
        assert len(plan["files"]) > 0
        assert plan["max_chars"] == 5000

    def test_level_entry_points(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        plan = get_read_plan(LEVEL_ENTRY_POINTS, manifest)
        assert plan["level"] == 3
        assert plan["max_chars"] == 10000

    def test_level_relevant(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        plan = get_read_plan(LEVEL_RELEVANT, manifest, task_keywords=["app"])
        assert plan["level"] == 4
        assert plan["max_chars"] == 30000
        assert isinstance(plan.get("search_queries", []), list)

    def test_level_tests(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        plan = get_read_plan(LEVEL_TESTS, manifest)
        assert plan["level"] == 5
        assert plan["max_chars"] == 20000
        assert len(plan["files"]) > 0

    def test_unknown_level(self, tmp_path: Path) -> None:
        manifest = ProjectManifest()
        plan = get_read_plan(99, manifest)
        assert "Unknown" in plan["description"]


# ---------------------------------------------------------------------------
# Task-Aware Inspection
# ---------------------------------------------------------------------------


class TestTaskAwareInspection:
    def test_basic_inspection(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        result = task_inspect("fix the app module", manifest)
        assert isinstance(result, InspectionResult)
        assert len(result.search_queries) > 0
        assert isinstance(result.relevant_files, list)

    def test_empty_task(self, tmp_path: Path) -> None:
        manifest = ProjectManifest()
        result = task_inspect("", manifest)
        assert result.relevant_files == []

    def test_empty_manifest(self) -> None:
        result = task_inspect("fix bug", ProjectManifest())
        assert result.relevant_files == []

    def test_search_queries_extracted(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        result = task_inspect("fix the fastapi app module", manifest)
        assert len(result.search_queries) > 0
        assert any("fastapi" in q for q in result.search_queries)

    def test_test_matching(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        result = task_inspect("fix test_app", manifest)
        test_matches = [
            f for f in result.relevant_files if "test_app" in f
        ]
        assert len(test_matches) > 0


# ---------------------------------------------------------------------------
# Project Summary
# ---------------------------------------------------------------------------


class TestProjectSummary:
    def test_summary_contains_project_name(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        summary = build_project_summary(manifest)
        assert tmp_path.name in summary
        assert "## Project:" in summary

    def test_summary_contains_technology(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        summary = build_project_summary(manifest)
        assert "Python" in summary
        assert "### Technology" in summary

    def test_summary_contains_structure(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        summary = build_project_summary(manifest)
        assert "### Structure" in summary
        assert "Files:" in summary

    def test_summary_contains_entry_points(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        summary = build_project_summary(manifest)
        assert "### Entry Points" in summary

    def test_summary_contains_tests(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        summary = build_project_summary(manifest)
        assert "### Tests" in summary

    def test_summary_contains_dependencies(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        summary = build_project_summary(manifest)
        assert "### Dependencies" in summary

    def test_summary_empty_manifest(self) -> None:
        manifest = ProjectManifest(name="empty")
        summary = build_project_summary(manifest)
        assert "empty" in summary


# ---------------------------------------------------------------------------
# Manifest Serialization
# ---------------------------------------------------------------------------


class TestManifestSerialization:
    def test_to_dict(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        d = manifest.to_dict()
        assert isinstance(d, dict)
        assert d["root"] == manifest.root
        assert d["languages"] == manifest.languages
        assert d["frameworks"] == manifest.frameworks
        assert d["total_files"] == manifest.total_files
        assert "file_extensions" in d
        assert "dependencies" in d

    def test_json_serializable(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        d = manifest.to_dict()
        serialized = json.dumps(d)
        assert len(serialized) > 0


# ---------------------------------------------------------------------------
# Prompt: Project Intelligence Section
# ---------------------------------------------------------------------------


class TestPromptProjectIntelligence:
    def test_intelligence_section_present(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt
        prompt = build_system_prompt(
            tool_names=["analyze_project", "read_files", "search_files"],
            compact_descriptions=["analyze_project: Analyze project structure"],
        )
        assert "## Project Intelligence" in prompt

    def test_intelligence_steps(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt
        prompt = build_system_prompt(
            tool_names=["analyze_project", "read_files", "search_files"],
            compact_descriptions=["analyze_project: Analyze project structure"],
        )
        for step in ("DISCOVER", "CLASSIFY", "READ progressively",
                      "MODIFY safely", "VERIFY"):
            assert step in prompt

    def test_intelligence_not_in_prompt_without_tools(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt
        prompt = build_system_prompt(
            tool_names=["calculator"],
            compact_descriptions=["calculator: Evaluate math"],
        )
        assert "## Project Intelligence" not in prompt

    def test_file_importance_guidance(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt
        prompt = build_system_prompt(
            tool_names=["analyze_project", "read_files", "search_files"],
            compact_descriptions=["analyze_project: Analyze project structure"],
        )
        assert "config -> entry points -> source" in prompt

    def test_skip_dirs_guidance(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt
        prompt = build_system_prompt(
            tool_names=["analyze_project", "read_files", "search_files"],
            compact_descriptions=["analyze_project: Analyze project structure"],
        )
        assert "Skip .git" in prompt
        assert "node_modules" in prompt

    def test_l1_sections_still_present(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt
        prompt = build_system_prompt(
            tool_names=[
                "analyze_project", "read_files", "modify_file",
                "execute_command", "search_files",
            ],
            compact_descriptions=["analyze_project: Analyze project structure"],
        )
        assert "## Development Workflow" in prompt
        assert "## Project Intelligence" in prompt

    def test_readonly_analysis_no_modification(self) -> None:
        from personal_ai_secretary.tools.prompt import build_system_prompt
        prompt = build_system_prompt(
            tool_names=["analyze_project", "read_files", "search_files"],
            compact_descriptions=["analyze_project: Analyze project structure"],
        )
        assert "Read before modifying" in prompt


# ---------------------------------------------------------------------------
# RequestMetrics: L.2 Extensions
# ---------------------------------------------------------------------------


class TestRequestMetricsL2:
    def test_l2_defaults(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        assert m.project_discovery_time == 0.0
        assert m.project_technologies_detected == []
        assert m.project_files_discovered == 0
        assert m.project_entry_points == 0
        assert m.project_test_locations == 0
        assert m.progressive_read_level == 0
        assert m.progressive_read_time == 0.0
        assert m.task_inspection_time == 0.0

    def test_record_project_discovery(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        m.record_project_discovery(
            elapsed=0.05,
            technologies=["Python", "FastAPI"],
            file_count=42,
            entry_points=3,
            test_locations=10,
        )
        assert m.project_discovery_time == 0.05
        assert m.project_technologies_detected == ["Python", "FastAPI"]
        assert m.project_files_discovered == 42
        assert m.project_entry_points == 3
        assert m.project_test_locations == 10

    def test_record_progressive_read(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        m.record_progressive_read(level=3, elapsed=0.01)
        assert m.progressive_read_level == 3
        assert m.progressive_read_time == 0.01

    def test_record_task_inspection(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        m.record_task_inspection(elapsed=0.02)
        assert m.task_inspection_time == 0.02

    def test_summary_includes_l2_fields(self) -> None:
        from personal_ai_secretary.observability.request_metrics import RequestMetrics
        m = RequestMetrics()
        m.record_project_discovery(
            elapsed=0.05, technologies=["Python"],
            file_count=10, entry_points=2, test_locations=5,
        )
        m.record_progressive_read(level=2, elapsed=0.01)
        m.record_task_inspection(elapsed=0.02)
        s = m.summary()
        assert "project_discovery_time_s" in s
        assert s["project_discovery_time_s"] == 0.05
        assert s["project_technologies_detected"] == ["Python"]
        assert s["project_files_discovered"] == 10
        assert s["progressive_read_level"] == 2
        assert s["task_inspection_time_s"] == 0.02


# ---------------------------------------------------------------------------
# InspectionResult
# ---------------------------------------------------------------------------


class TestInspectionResult:
    def test_defaults(self) -> None:
        r = InspectionResult()
        assert r.relevant_files == []
        assert r.search_queries == []
        assert r.read_candidates == []
        assert r.summary == ""

    def test_with_data(self) -> None:
        r = InspectionResult(
            relevant_files=["a.py", "b.py"],
            search_queries=["fastapi"],
            summary="test summary",
        )
        assert len(r.relevant_files) == 2
        assert "fastapi" in r.search_queries


# ---------------------------------------------------------------------------
# Coverage Hardening: Edge Cases
# ---------------------------------------------------------------------------


class TestPackageManagerEdgeCases:
    def test_pdm_detection(self, tmp_path: Path) -> None:
        structure = {
            "pyproject.toml": "[project]\nname = 'test'\n",
            "pdm.lock": "# lock\n",
        }
        _create_project_tree(tmp_path, structure)
        manifest = discover_project(str(tmp_path))
        assert "pdm" in manifest.package_managers

    def test_pipenv_detection(self, tmp_path: Path) -> None:
        structure = {
            "Pipfile": "[packages]\nflask = '*'\n",
        }
        _create_project_tree(tmp_path, structure)
        manifest = discover_project(str(tmp_path))
        assert "pipenv" in manifest.package_managers

    def test_pnpm_detection(self, tmp_path: Path) -> None:
        structure = {
            "package.json": json.dumps({"name": "test"}),
            "pnpm-lock.yaml": "# lock\n",
        }
        _create_project_tree(tmp_path, structure)
        manifest = discover_project(str(tmp_path))
        assert "pnpm" in manifest.package_managers

    def test_bun_detection(self, tmp_path: Path) -> None:
        structure = {
            "package.json": json.dumps({"name": "test"}),
            "bun.lockb": "# lock\n",
        }
        _create_project_tree(tmp_path, structure)
        manifest = discover_project(str(tmp_path))
        assert "bun" in manifest.package_managers

    def test_setup_py_detection(self, tmp_path: Path) -> None:
        structure = {
            "setup.py": "from setuptools import setup\nsetup()\n",
        }
        _create_project_tree(tmp_path, structure)
        manifest = discover_project(str(tmp_path))
        assert "pip" in manifest.package_managers


class TestFileClassificationEdgeCases:
    def test_config_adjacent_yaml(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        assert classify_importance("config.yaml", manifest) == "important"

    def test_config_adjacent_cfg(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        assert classify_importance("setup.cfg", manifest) == "critical"

    def test_default_supporting(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        assert classify_importance("random_notes.txt", manifest) == "supporting"

    def test_docs_are_supporting(self, tmp_path: Path) -> None:
        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))
        assert classify_importance("CHANGELOG.md", manifest) == "supporting"


class TestTrackerIntegration:
    def test_update_tracker_from_manifest(self, tmp_path: Path) -> None:
        from personal_ai_secretary.context.project_intelligence import (
            update_tracker_from_manifest,
        )

        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))

        class FakeTracker:
            project_path: str = ""

        tracker = FakeTracker()
        update_tracker_from_manifest(tracker, manifest)
        assert tracker.project_path == str(tmp_path.resolve())

    def test_update_tracker_without_project_path(self, tmp_path: Path) -> None:
        from personal_ai_secretary.context.project_intelligence import (
            update_tracker_from_manifest,
        )

        _create_project_tree(tmp_path, _python_project())
        manifest = discover_project(str(tmp_path))

        class NoPathTracker:
            pass

        tracker = NoPathTracker()
        # Should not raise
        update_tracker_from_manifest(tracker, manifest)
