# FASE L.2 — Project Intelligence — Closure Report

**Date:** 2026-08-21
**Status:** COMPLETED
**Tests:** 95 passing (all new)
**Full suite:** 920+ passing
**Ruff:** 0 errors
**MyPy:** 0 errors (strict, 65 files)
**Coverage:** 93% overall, 95% project_intelligence.py

---

## Summary

FASE L.2 adds project intelligence capabilities to Chiky — the ability to
inspect, understand, and reason about existing projects before modifying them.
This enables the agent to work safely with real codebases by understanding
their structure, technology stack, entry points, dependencies, and test locations.

No new tools were created. The intelligence module is a pure utility that
enhances existing tool behavior and prompt guidance.

## What Was Implemented

### 1. Project Intelligence Module (`context/project_intelligence.py`)

**371 statements, 95% coverage**

New module providing:
- **Project Discovery** — walks directory tree, collects files, identifies structure
- **Technology Detection** — detects Python/JavaScript/TypeScript from config files
- **Framework Detection** — identifies FastAPI, Flask, Django, Express, Next.js, etc.
- **Package Manager Detection** — pip, poetry, pdm, pipenv, npm, yarn, pnpm, bun
- **Entry Point Detection** — finds main.py, app.py, index.js, server.js, etc.
- **Dependency Detection** — parses requirements.txt, package.json
- **Test Discovery** — finds test_*.py, *.test.js, *.spec.ts, etc.
- **File Importance Classification** — critical/important/supporting/generated
- **Progressive Reading Strategy** — 5 levels (metadata → config → entry → relevant → tests)
- **Task-Aware Inspection** — extracts keywords from task description, matches relevant files
- **Project Summary Generation** — human-readable markdown summary
- **K.5 Integration** — `update_tracker_from_manifest()` for ProjectContextTracker

Key data structures:
- `ProjectManifest` — full project metadata with `to_dict()` serialization
- `InspectionResult` — task-aware inspection output

### 2. Enhanced Prompt (`tools/prompt.py`)

Added "## Project Intelligence" section with:
- 6-step workflow: DISCOVER → CLASSIFY → READ PROGRESSIVELY → TASK-AWARE → MODIFY SAFELY → VERIFY
- File importance guidance: entry points > config > source > tests > docs > generated
- Skip directories guidance: .git, node_modules, __pycache__, dist, build, .venv
- Read-only analysis guidance: "Never read entire projects without a specific reason"

### 3. Observability Extensions (`observability/request_metrics.py`)

New metrics fields:
- `project_discovery_time` — time to discover project structure
- `project_technologies_detected` — list of detected technologies
- `project_files_discovered` — total files found
- `project_entry_points` — number of entry points
- `project_test_locations` — number of test files
- `progressive_read_level` — current reading level (1-5)
- `progressive_read_time` — time spent reading
- `task_inspection_time` — time for task-aware inspection

New methods:
- `record_project_discovery(elapsed, technologies, file_count, entry_points, test_locations)`
- `record_progressive_read(level, elapsed)`
- `record_task_inspection(elapsed)`

### 4. Tests (`tests/unit/test_l2_project_intelligence.py`)

**95 tests across 15 test classes:**

| Class | Tests | Description |
|-------|-------|-------------|
| TestProjectDiscovery | 8 | Directory tree walking, max depth, ignored dirs, large projects |
| TestTechnologyDetection | 10 | Python/JS/TS detection, frameworks, package managers |
| TestEntryPointDetection | 4 | main.py, index.js, package.json main, start script |
| TestDependencyDetection | 3 | requirements.txt, package.json deps |
| TestTestDiscovery | 3 | Python test files, Node test files, bare project |
| TestFileImportanceClassification | 10 | Critical/important/supporting/generated |
| TestProgressiveReading | 6 | All 5 levels + unknown level |
| TestTaskAwareInspection | 5 | Basic inspection, empty task, keyword extraction |
| TestProjectSummary | 7 | Name, technology, structure, entry points, tests, deps |
| TestManifestSerialization | 2 | to_dict, JSON serializable |
| TestPromptProjectIntelligence | 7 | Section presence, steps, no tools, L.1 preservation |
| TestRequestMetricsL2 | 5 | Defaults, recording, summary fields |
| TestInspectionResult | 2 | Defaults, with data |
| TestPackageManagerEdgeCases | 5 | pdm, pipenv, pnpm, bun, setup.py |
| TestFileClassificationEdgeCases | 4 | YAML, cfg, default, docs |
| TestTrackerIntegration | 2 | update_tracker_from_manifest |

## Quality Gates

| Gate | Result |
|------|--------|
| Tests passing | 920+ (0 failures) |
| Ruff | 0 errors |
| MyPy strict | 0 errors (65 files) |
| Coverage | 93% overall, 95% project_intelligence.py |
| Regressions K.1.1/K.4/K.5/K.6/L.1 | None |

## Files Modified

| File | Change |
|------|--------|
| `src/personal_ai_secretary/context/project_intelligence.py` | NEW — 896 lines |
| `src/personal_ai_secretary/observability/request_metrics.py` | Added L.2 metrics fields + methods |
| `src/personal_ai_secretary/tools/prompt.py` | Added Project Intelligence section |
| `tests/unit/test_l2_project_intelligence.py` | NEW — 830+ lines, 95 tests |
| `tests/unit/test_k4_optimization.py` | Updated prompt threshold (4000→4500) |
| `tests/unit/test_l1_development.py` | Updated token threshold (1200→1250) |

## Design Decisions

1. **No new tools** — Intelligence is a pure utility module, not exposed as tools to the LLM. The agent uses existing tools (analyze_project, search_files, read_files) guided by the enhanced prompt.

2. **Technology detection via config files only** — Not folder names. Config files (pyproject.toml, package.json, etc.) are authoritative signals.

3. **5-level progressive reading** — Metadata → Config → Entry points → Task-relevant → Tests. This prevents reading entire projects without reason.

4. **Task-aware inspection** — Extracts keywords from user request, matches against file paths and test names to find relevant files.

5. **File importance classification** — 4 tiers: critical (entry points, config) > important (source) > supporting (tests, docs) > generated (build artifacts, lock files).

6. **K.5 integration** — `update_tracker_from_manifest()` provides lightweight bridge to ProjectContextTracker without duplicating tracking logic.

7. **Minimal prompt impact** — L.2 adds ~400 chars / ~100 tokens to the system prompt. Threshold tests updated accordingly.
