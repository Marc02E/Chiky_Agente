"""FASE L.2 — Project Intelligence module.

Pure utility module that provides project analysis capabilities:
- Project discovery (root, structure, files)
- Technology detection (languages, frameworks, package managers)
- Entry point detection
- Dependency detection
- Test location discovery
- File importance classification (critical/important/supporting/generated)
- Progressive reading strategy (5 levels)
- Task-aware inspection
- Project summary generation

This module does NOT create new tools. It provides logic that the agent
uses internally or that enhances existing tool behavior. All functions
are pure (no I/O side effects beyond reading config files for detection).

Integrates with K.5 ProjectContextTracker for metadata tracking.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Directories to always skip during discovery
_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", "env", ".env", "dist", "build",
    ".eggs", "*.egg-info", ".tox", ".nox", ".idea", ".vscode",
    "coverage", ".coverage", "target",
})

# Config files that indicate technologies
_PYTHON_CONFIGS: frozenset[str] = frozenset({
    "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg",
    "Pipfile", "poetry.lock", "pdm.lock",
})

_NODE_CONFIGS: frozenset[str] = frozenset({
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "bun.lockb",
})

_JS_TS_CONFIGS: frozenset[str] = frozenset({
    "tsconfig.json", "jsconfig.json", ".babelrc", "babel.config.js",
    "webpack.config.js", "vite.config.js", "vite.config.ts",
    "next.config.js", "next.config.mjs", "nuxt.config.js",
})

_FRONTEND_INDICATORS: frozenset[str] = frozenset({
    "react", "vue", "angular", "svelte", "next", "nuxt", "gatsby",
    "vite", "webpack", "parcel",
})

_PYTHON_FRAMEWORKS: dict[str, list[str]] = {
    "fastapi": ["fastapi", "uvicorn"],
    "flask": ["flask"],
    "django": ["django"],
    "starlette": ["starlette"],
    "aiohttp": ["aiohttp"],
    "tornado": ["tornado"],
    "sanic": ["sanic"],
    "pyramid": ["pyramid"],
    "bottle": ["bottle"],
}

_NODE_FRAMEWORKS: dict[str, list[str]] = {
    "express": ["express"],
    "fastify": ["fastify"],
    "koa": ["koa"],
    "hapi": ["@hapi/hapi"],
    "nest": ["@nestjs/core"],
    "next": ["next"],
    "nuxt": ["nuxt"],
    "gatsby": ["gatsby"],
}

# Known Python entry point file names
_PYTHON_ENTRY_POINTS: frozenset[str] = frozenset({
    "main.py", "app.py", "server.py", "run.py", "manage.py",
    "__main__.py", "wsgi.py", "asgi.py", "cli.py",
})

# Known Node entry point file names
_NODE_ENTRY_POINTS: frozenset[str] = frozenset({
    "index.js", "index.ts", "server.js", "server.ts",
    "app.js", "app.ts", "main.js", "main.ts",
})

# Test file patterns
_PYTHON_TEST_PATTERNS: frozenset[str] = frozenset({
    "test_*.py", "*_test.py", "tests.py", "conftest.py",
})

_NODE_TEST_PATTERNS: frozenset[str] = frozenset({
    "*.test.js", "*.test.ts", "*.spec.js", "*.spec.ts",
    "*.test.jsx", "*.test.tsx", "*.spec.jsx", "*.spec.tsx",
})

# Documentation files
_DOC_PATTERNS: frozenset[str] = frozenset({
    "README*", "readme*", "CHANGELOG*", "CONTRIBUTING*",
    "LICENSE*", "SECURITY*", "docs/*",
})

# File importance: critical patterns
_CRITICAL_PATTERNS: frozenset[str] = frozenset({
    "pyproject.toml", "package.json", "tsconfig.json",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".env.example", "Makefile", "CMakeLists.txt",
})

# File importance: generated patterns
_GENERATED_PATTERNS: frozenset[str] = frozenset({
    "*.pyc", "*.pyo", "*.pyd", "*.so", "*.dll", "*.exe",
    "*.egg-info", "*.whl", "*.tar.gz",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "poetry.lock", "pdm.lock", "bun.lockb",
    ".DS_Store", "Thumbs.db",
})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ProjectManifest:
    """Structured representation of a discovered project."""

    root: str = ""
    name: str = ""
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    test_locations: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    documentation: list[str] = field(default_factory=list)
    source_directories: list[str] = field(default_factory=list)
    important_files: list[str] = field(default_factory=list)
    ignored_directories: list[str] = field(default_factory=list)
    total_files: int = 0
    total_directories: int = 0
    file_extensions: dict[str, int] = field(default_factory=dict)
    dependencies: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON output."""
        return {
            "root": self.root,
            "name": self.name,
            "languages": self.languages,
            "frameworks": self.frameworks,
            "package_managers": self.package_managers,
            "entry_points": self.entry_points,
            "test_locations": self.test_locations,
            "config_files": self.config_files,
            "documentation": self.documentation,
            "source_directories": self.source_directories,
            "important_files": self.important_files,
            "ignored_directories": self.ignored_directories,
            "total_files": self.total_files,
            "total_directories": self.total_directories,
            "file_extensions": self.file_extensions,
            "dependencies": self.dependencies,
        }


@dataclass
class InspectionResult:
    """Result of a task-aware inspection."""

    relevant_files: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    read_candidates: list[str] = field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# Directory skip logic
# ---------------------------------------------------------------------------


def _should_skip(name: str) -> bool:
    """Check if a directory or file should be skipped."""
    for pattern in _SKIP_DIRS:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


# ---------------------------------------------------------------------------
# Project Discovery
# ---------------------------------------------------------------------------


def discover_project(
    root_path: str,
    max_depth: int = 5,
    read_configs: bool = True,
) -> ProjectManifest:
    """Walk the directory tree and build a ProjectManifest.

    Args:
        root_path: Absolute path to the project root.
        max_depth: Maximum directory depth to traverse.
        read_configs: Whether to read config files for technology detection.

    Returns:
        Populated ProjectManifest.
    """
    root = Path(root_path).resolve()
    manifest = ProjectManifest(root=str(root), name=root.name)

    if not root.exists() or not root.is_dir():
        return manifest

    # Walk the tree and collect all files
    all_files: list[str] = []
    _walk_project(root, manifest, max_depth, 0, all_files)

    # Detect technologies from config files
    if read_configs:
        config_contents = _read_config_files(root, manifest.config_files)
        _detect_technologies(manifest, config_contents)
        _detect_entry_points(manifest, config_contents, all_files)
        _detect_dependencies(manifest, config_contents)

    # Classify file importance
    _classify_all_files(manifest)

    return manifest


def _walk_project(
    current: Path,
    manifest: ProjectManifest,
    max_depth: int,
    depth: int,
    all_files: list[str],
) -> None:
    """Recursively walk the project directory."""
    if depth > max_depth:
        return

    try:
        entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    except PermissionError:
        return

    for entry in entries:
        if _should_skip(entry.name):
            if entry.is_dir():
                manifest.ignored_directories.append(
                    str(entry.relative_to(manifest.root))
                )
            continue

        rel = str(entry.relative_to(manifest.root))

        if entry.is_dir():
            manifest.total_directories += 1
            # Classify source directories
            if depth == 0 and entry.name in (
                "src", "lib", "app", "internal", "pkg", "cmd",
                "pages", "components", "routes", "handlers", "views",
            ):
                manifest.source_directories.append(rel)
            _walk_project(entry, manifest, max_depth, depth + 1, all_files)

        elif entry.is_file():
            manifest.total_files += 1
            all_files.append(rel)
            suffix = entry.suffix.lower()
            if suffix:
                manifest.file_extensions[suffix] = (
                    manifest.file_extensions.get(suffix, 0) + 1
                )

            # Collect config files
            if entry.name in _PYTHON_CONFIGS or entry.name in _NODE_CONFIGS or \
               entry.name in _JS_TS_CONFIGS or entry.name in _CRITICAL_PATTERNS:
                manifest.config_files.append(rel)

            # Collect documentation
            for pat in _DOC_PATTERNS:
                if fnmatch.fnmatch(entry.name, pat):
                    manifest.documentation.append(rel)
                    break

            # Collect test locations
            for pat in _PYTHON_TEST_PATTERNS | _NODE_TEST_PATTERNS:
                if fnmatch.fnmatch(entry.name, pat):
                    manifest.test_locations.append(rel)
                    break

            # Collect important files
            if entry.name in _CRITICAL_PATTERNS:
                manifest.important_files.append(rel)


def _read_config_files(root: Path, config_files: list[str]) -> dict[str, str]:
    """Read config file contents for technology detection."""
    contents: dict[str, str] = {}
    for rel_path in config_files[:20]:  # Limit to avoid large reads
        full = root / rel_path
        try:
            text = full.read_text(encoding="utf-8")
            contents[rel_path] = text[:10_000]  # Cap per file
        except (UnicodeDecodeError, OSError):
            continue
    return contents


# ---------------------------------------------------------------------------
# Technology Detection
# ---------------------------------------------------------------------------


def _detect_technologies(
    manifest: ProjectManifest,
    config_contents: dict[str, str],
) -> None:
    """Detect languages, frameworks, and package managers from config files."""
    languages: set[str] = set()
    frameworks: set[str] = set()
    package_managers: set[str] = set()

    # Python detection
    python_files = [f for f in manifest.config_files if f in _PYTHON_CONFIGS]
    if python_files or manifest.file_extensions.get(".py", 0) > 0:
        languages.add("Python")

    # Node/JS/TS detection
    node_files = [f for f in manifest.config_files if f in _NODE_CONFIGS]
    if node_files or any(
        manifest.file_extensions.get(ext, 0) > 0
        for ext in (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")
    ):
        languages.add("JavaScript")

    if any(
        manifest.file_extensions.get(ext, 0) > 0
        for ext in (".ts", ".tsx")
    ) or "tsconfig.json" in [f.split("/")[-1] for f in manifest.config_files]:
        languages.add("TypeScript")

    # Detect frameworks from package.json dependencies
    pkg_json = config_contents.get("package.json", "")
    if pkg_json:
        try:
            pkg = json.loads(pkg_json)
            all_deps = {}
            all_deps.update(pkg.get("dependencies", {}))
            all_deps.update(pkg.get("devDependencies", {}))
            for fw, deps in _NODE_FRAMEWORKS.items():
                if any(d in all_deps for d in deps):
                    frameworks.add(fw)
        except (json.JSONDecodeError, TypeError):
            pass

    # Detect frameworks from requirements/pyproject
    for cfg_name, content in config_contents.items():
        if cfg_name in _PYTHON_CONFIGS:
            content_lower = content.lower()
            for fw, markers in _PYTHON_FRAMEWORKS.items():
                if any(m in content_lower for m in markers):
                    frameworks.add(fw)

    # Package manager detection
    if "pyproject.toml" in [f.split("/")[-1] for f in manifest.config_files]:
        package_managers.add("pip")
        if "poetry.lock" in [f.split("/")[-1] for f in manifest.config_files]:
            package_managers.add("poetry")
        elif "pdm.lock" in [f.split("/")[-1] for f in manifest.config_files]:
            package_managers.add("pdm")
    if "requirements.txt" in [f.split("/")[-1] for f in manifest.config_files]:
        package_managers.add("pip")
    if "Pipfile" in [f.split("/")[-1] for f in manifest.config_files]:
        package_managers.add("pipenv")
    if "setup.py" in [f.split("/")[-1] for f in manifest.config_files]:
        package_managers.add("pip")
    if "package.json" in [f.split("/")[-1] for f in manifest.config_files]:
        package_managers.add("npm")
    if "yarn.lock" in [f.split("/")[-1] for f in manifest.config_files]:
        package_managers.add("yarn")
    if "pnpm-lock.yaml" in [f.split("/")[-1] for f in manifest.config_files]:
        package_managers.add("pnpm")
    if "bun.lockb" in [f.split("/")[-1] for f in manifest.config_files]:
        package_managers.add("bun")

    manifest.languages = sorted(languages)
    manifest.frameworks = sorted(frameworks)
    manifest.package_managers = sorted(package_managers)


# ---------------------------------------------------------------------------
# Entry Point Detection
# ---------------------------------------------------------------------------


def _detect_entry_points(
    manifest: ProjectManifest,
    config_contents: dict[str, str],
    all_files: list[str] | None = None,
) -> None:
    """Detect entry points from known patterns and config files."""
    entry_points: list[str] = []

    # Check known entry point file names from all discovered files
    source_files = all_files or manifest.config_files
    for f in source_files:
        name = Path(f).name
        if name in _PYTHON_ENTRY_POINTS or name in _NODE_ENTRY_POINTS:
            entry_points.append(f)

    # Check package.json scripts
    pkg_json = config_contents.get("package.json", "")
    if pkg_json:
        try:
            pkg = json.loads(pkg_json)
            scripts = pkg.get("scripts", {})
            main_field = pkg.get("main", "")
            if main_field:
                entry_points.append(main_field)
            # The "start" or "dev" script often indicates the entry point
            for key in ("start", "dev", "serve"):
                if key in scripts:
                    # Extract the file from the script command
                    script_val = scripts[key]
                    for part in script_val.split():
                        if part.endswith((".js", ".ts", ".mjs")):
                            entry_points.append(part)
        except (json.JSONDecodeError, TypeError):
            pass

    # Deduplicate and sort
    manifest.entry_points = sorted(set(entry_points))


# ---------------------------------------------------------------------------
# Dependency Detection
# ---------------------------------------------------------------------------


def _detect_dependencies(
    manifest: ProjectManifest,
    config_contents: dict[str, str],
) -> None:
    """Detect dependencies from config files."""
    deps: dict[str, list[str]] = {}

    # Python: requirements.txt
    req_txt = config_contents.get("requirements.txt", "")
    if req_txt:
        reqs = []
        for line in req_txt.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("-"):
                # Extract package name (before version spec)
                name = (
                    line.split("==")[0].split(">=")[0]
                    .split("<=")[0].split("!=")[0]
                    .split("~=")[0].strip()
                )
                if name:
                    reqs.append(name)
        if reqs:
            deps["python"] = reqs

    # Python: pyproject.toml [project.dependencies]
    pyproject = config_contents.get("pyproject.toml", "")
    if pyproject:
        try:
            # Simple parsing - look for dependencies section
            in_deps = False
            for line in pyproject.splitlines():
                if line.strip().startswith("[project]") or line.strip().startswith("[tool.poetry"):
                    in_deps = True
                elif line.strip().startswith("["):
                    in_deps = False
                elif in_deps and "dependencies" in line.lower():
                    in_deps = True
                elif in_deps and line.strip().startswith('"') and "=" not in line:
                    name = (
                        line.strip().strip('"').split(">")[0]
                        .split("<")[0].split("=")[0]
                        .split("!")[0].strip()
                    )
                    if name:
                        reqs.append(name)
        except Exception:  # noqa: BLE001
            pass

    # Node: package.json
    pkg_json = config_contents.get("package.json", "")
    if pkg_json:
        try:
            pkg = json.loads(pkg_json)
            node_deps = list(pkg.get("dependencies", {}).keys())
            node_dev_deps = list(pkg.get("devDependencies", {}).keys())
            if node_deps:
                deps["node"] = node_deps
            if node_dev_deps:
                deps["node_dev"] = node_dev_deps
        except (json.JSONDecodeError, TypeError):
            pass

    manifest.dependencies = deps


# ---------------------------------------------------------------------------
# File Importance Classification
# ---------------------------------------------------------------------------


def _classify_all_files(manifest: ProjectManifest) -> None:
    """Classify files by importance based on location and name."""
    # This is metadata-only; the actual classification happens per-file
    # when the agent reads files. We pre-compute source directories
    # and important files here.
    pass  # Classification happens in classify_importance()


def classify_importance(file_path: str, manifest: ProjectManifest) -> str:
    """Classify a file's importance level.

    Returns: 'critical', 'important', 'supporting', or 'generated'.
    """
    name = Path(file_path).name
    rel = file_path

    # Generated files
    for pat in _GENERATED_PATTERNS:
        if fnmatch.fnmatch(name, pat):
            return "generated"
    if any(rel.startswith(d) for d in ("dist/", "build/", "coverage/", ".coverage/")):
        return "generated"

    # Critical files
    if name in _CRITICAL_PATTERNS:
        return "critical"
    if name in _PYTHON_ENTRY_POINTS or name in _NODE_ENTRY_POINTS:
        return "critical"
    if name in _PYTHON_CONFIGS or name in _NODE_CONFIGS or name in _JS_TS_CONFIGS:
        return "critical"

    # Test files
    for pat in _PYTHON_TEST_PATTERNS | _NODE_TEST_PATTERNS:
        if fnmatch.fnmatch(name, pat):
            return "supporting"

    # Documentation
    for pat in _DOC_PATTERNS:
        if fnmatch.fnmatch(name, pat):
            return "supporting"

    # Source files in source directories
    if any(rel.startswith(d) for d in manifest.source_directories):
        return "important"

    # Config-adjacent files
    if name.endswith((".cfg", ".ini", ".toml", ".yaml", ".yml", ".json", ".env")):
        return "important"

    # Default
    return "supporting"


# ---------------------------------------------------------------------------
# Progressive Reading Strategy
# ---------------------------------------------------------------------------

# Reading levels
LEVEL_METADATA = 1      # Structure and file listing only
LEVEL_CONFIG = 2        # Read configuration and dependencies
LEVEL_ENTRY_POINTS = 3  # Read entry points
LEVEL_RELEVANT = 4      # Read files relevant to a specific task
LEVEL_TESTS = 5         # Read relevant tests


def get_read_plan(
    level: int,
    manifest: ProjectManifest,
    task_keywords: list[str] | None = None,
) -> dict[str, Any]:
    """Generate a reading plan for a given level.

    Args:
        level: Reading level (1-5).
        manifest: Project manifest.
        task_keywords: Keywords for level 4 (task-aware) reading.

    Returns:
        Dict with 'description', 'files', and 'max_chars' guidance.
    """
    if level == LEVEL_METADATA:
        return {
            "level": 1,
            "description": "Metadata only: project structure and file listing",
            "files": [],
            "max_chars": 0,
            "instruction": (
                "Use analyze_project to get the full structure. "
                "Do not read file contents."
            ),
        }

    if level == LEVEL_CONFIG:
        config_files = [
            f for f in manifest.config_files
            if Path(f).name in (
                "pyproject.toml", "requirements.txt", "package.json",
                "tsconfig.json", "setup.py", "setup.cfg",
            )
        ]
        return {
            "level": 2,
            "description": "Configuration and dependencies",
            "files": config_files[:10],
            "max_chars": 5_000,
            "instruction": "Read config files to understand dependencies and project setup.",
        }

    if level == LEVEL_ENTRY_POINTS:
        return {
            "level": 3,
            "description": "Entry points and main application files",
            "files": manifest.entry_points[:5],
            "max_chars": 10_000,
            "instruction": "Read entry points to understand application flow.",
        }

    if level == LEVEL_RELEVANT:
        # Use task-aware inspection to find relevant files
        inspection = task_inspect(
            " ".join(task_keywords) if task_keywords else "",
            manifest,
        )
        return {
            "level": 4,
            "description": "Task-relevant source files",
            "files": inspection.read_candidates[:20],
            "max_chars": 30_000,
            "search_queries": inspection.search_queries,
            "instruction": "Read only files related to the current task.",
        }

    if level == LEVEL_TESTS:
        test_files = [
            f for f in manifest.test_locations
            if Path(f).suffix in (".py", ".js", ".ts", ".jsx", ".tsx")
        ]
        return {
            "level": 5,
            "description": "Test files",
            "files": test_files[:20],
            "max_chars": 20_000,
            "instruction": "Read test files to understand test coverage and expected behavior.",
        }

    return {
        "level": level,
        "description": f"Unknown level {level}",
        "files": [],
        "max_chars": 0,
        "instruction": "Invalid reading level.",
    }


# ---------------------------------------------------------------------------
# Task-Aware Inspection
# ---------------------------------------------------------------------------


def task_inspect(
    task_description: str,
    manifest: ProjectManifest,
) -> InspectionResult:
    """Determine which files are relevant for a given task.

    Analyzes the task description against the project manifest to identify
    likely relevant files, search queries, and reading candidates.

    Args:
        task_description: Natural language task description from the user.
        manifest: Project manifest.

    Returns:
        InspectionResult with relevant files and search suggestions.
    """
    result = InspectionResult()

    if not task_description or not manifest.root:
        return result

    task_lower = task_description.lower()
    keywords = _extract_keywords(task_lower)

    # Build search queries from keywords
    search_queries: list[str] = []
    for kw in keywords:
        if len(kw) > 2:  # Skip very short keywords
            search_queries.append(kw)
    result.search_queries = search_queries[:10]

    # Match against config files (always relevant)
    result.relevant_files.extend(manifest.config_files[:5])

    # Match against entry points
    result.relevant_files.extend(manifest.entry_points[:3])

    # Match against important files
    result.relevant_files.extend(manifest.important_files[:5])

    # Match against test locations for task-relevant tests
    for test_loc in manifest.test_locations:
        test_name = Path(test_loc).stem.lower()
        for kw in keywords:
            if kw in test_name:
                result.relevant_files.append(test_loc)
                break

    # Deduplicate
    result.relevant_files = sorted(set(result.relevant_files))

    # Read candidates: files that match keywords in their path
    for ext in (".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java"):
        count = manifest.file_extensions.get(ext, 0)
        if count > 0:
            # These are candidates that the search_files tool should investigate
            pass

    result.read_candidates = result.relevant_files[:20]

    # Build summary
    result.summary = (
        f"Task: {task_description}\n"
        f"Keywords: {', '.join(keywords[:10])}\n"
        f"Relevant files: {len(result.relevant_files)}\n"
        f"Search queries: {', '.join(search_queries[:5])}"
    )

    return result


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from task description."""
    # Simple keyword extraction: split on spaces/punctuation,
    # filter common stop words and very short words
    stop_words = frozenset({
        "a", "an", "the", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "can", "shall",
        "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "as", "into", "about", "it", "its", "this", "that", "these",
        "those", "i", "me", "my", "we", "our", "you", "your", "he",
        "him", "his", "she", "her", "they", "them", "their", "and",
        "or", "but", "not", "no", "nor", "so", "if", "then", "than",
        "too", "very", "just", "only", "also", "how", "what", "when",
        "where", "which", "who", "whom", "why", "all", "each", "every",
        "both", "few", "more", "most", "other", "some", "such",
        "same", "own", "now", "here", "there", "up", "out", "off",
        "over", "under", "again", "further", "once", "please",
        "create", "make", "give", "tell", "explain", "find",
        "show", "list", "check", "fix", "modify", "update", "delete",
        "run", "test", "install", "set", "add", "remove", "help",
        "want", "need", "like", "use", "get", "got", "see",
        "look", "try", "start", "stop", "open", "close", "read",
        "write", "file", "code",
    })

    # Split on non-alphanumeric characters
    import re
    words = re.findall(r"[a-z0-9_]+", text)

    # Filter
    keywords = []
    seen: set[str] = set()
    for w in words:
        if w not in stop_words and len(w) > 2 and w not in seen:
            keywords.append(w)
            seen.add(w)

    return keywords


# ---------------------------------------------------------------------------
# Project Summary
# ---------------------------------------------------------------------------


def build_project_summary(manifest: ProjectManifest) -> str:
    """Generate a human-readable project summary.

    Args:
        manifest: Populated ProjectManifest.

    Returns:
        Markdown-formatted project summary.
    """
    lines: list[str] = []

    lines.append(f"## Project: {manifest.name}")
    lines.append("")

    # Technology
    lines.append("### Technology")
    if manifest.languages:
        lines.append(f"- Languages: {', '.join(manifest.languages)}")
    if manifest.frameworks:
        lines.append(f"- Frameworks: {', '.join(manifest.frameworks)}")
    if manifest.package_managers:
        lines.append(f"- Package managers: {', '.join(manifest.package_managers)}")
    lines.append("")

    # Structure
    lines.append("### Structure")
    lines.append(f"- Files: {manifest.total_files}")
    lines.append(f"- Directories: {manifest.total_directories}")
    if manifest.source_directories:
        lines.append(f"- Source: {', '.join(manifest.source_directories)}")
    if manifest.config_files:
        lines.append(f"- Config: {', '.join(manifest.config_files[:8])}")
    lines.append("")

    # Entry points
    if manifest.entry_points:
        lines.append("### Entry Points")
        for ep in manifest.entry_points[:5]:
            lines.append(f"- {ep}")
        lines.append("")

    # Tests
    if manifest.test_locations:
        lines.append("### Tests")
        lines.append(f"- {len(manifest.test_locations)} test files found")
        for t in manifest.test_locations[:5]:
            lines.append(f"  - {t}")
        lines.append("")

    # Dependencies
    if manifest.dependencies:
        lines.append("### Dependencies")
        for source, deps in manifest.dependencies.items():
            count = len(deps)
            preview = ", ".join(deps[:8])
            suffix = f" (+{count - 8} more)" if count > 8 else ""
            lines.append(f"- {source}: {preview}{suffix}")
        lines.append("")

    # File extensions
    if manifest.file_extensions:
        lines.append("### File Types")
        sorted_exts = sorted(
            manifest.file_extensions.items(), key=lambda x: x[1], reverse=True
        )
        for ext, count in sorted_exts[:10]:
            lines.append(f"- {ext}: {count}")
        lines.append("")

    # Documentation
    if manifest.documentation:
        lines.append("### Documentation")
        for doc in manifest.documentation[:5]:
            lines.append(f"- {doc}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Integration with K.5 ProjectContextTracker
# ---------------------------------------------------------------------------


def update_tracker_from_manifest(
    tracker: Any,  # ProjectContextTracker from K.5
    manifest: ProjectManifest,
) -> None:
    """Update a K.5 ProjectContextTracker with manifest metadata.

    This is a lightweight integration that sets the project path
    without loading file contents.
    """
    if hasattr(tracker, "project_path"):
        tracker.project_path = manifest.root
