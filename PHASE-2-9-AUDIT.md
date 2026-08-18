# Phase 2–9 audit status

## Phase 3 — Agent foundation
- Implemented five role contracts and built-in agents.
- Implemented separation-of-duties check for reviewer.
- Implemented governed workflow with explicit blocked/completed states.
- Added unit coverage.
- Local static gate: Python compilation PASS; line-length audit PASS.

## Phase 4 — Knowledge and memory
- Implemented source/evidence identity and deterministic retrieval primitive.
- Implemented evidence validation and user-scoped memory policy with retention TTLs.
- Added unit coverage.
- Local static gate: Python compilation PASS; line-length audit PASS.

## Phase 5 — Tools and MCP
- Implemented typed tool registry.
- Critical-risk tools are denied by default.
- High-risk tools can require explicit approval.
- Added unit coverage.
- Local static gate: Python compilation PASS; line-length audit PASS.

## Phase 6 — Evaluation
- Implemented release quality gates for workflow, security and regression scores.
- Added regression unit coverage.
- Local static gate: Python compilation PASS; line-length audit PASS.

## Phase 7 — Observability (CLOSED 2026-08-14)
- Added reusable metrics counter/timer and structured audit event primitives.
- Integrated observability into the workflow, agents, service and API runtime.
- Existing correlation middleware remains the API correlation boundary.
- See "Phase 7 observability integration closure" below.

## Phase 8 — Deployment (CLOSED 2026-08-15)
- Reproducible Dockerfile with non-root user, HEALTHCHECK, and a migration entrypoint; `.dockerignore` added.
- Production guard refuses the default development JWT secret; `.env.example` documents the environment contract.
- Clean-install reproducibility verified for both `pip install .` and `pip install -e '.[dev]'` in fresh virtual environments.
- Docker image built and container run, healthcheck, and smoke test all VERIFIED in this environment.
- See "Phase 8 deployment closure" below.

## Phase 8 — Deployment
- Added reproducible Python 3.13 Docker image and CI quality workflow.
- Kubernetes remains intentionally outside current scope, matching roadmap.

## Phase 9 — Optimization
- Added timing primitive and provider timeout configuration as the baseline instrumentation for latency/reliability work.

## Important final gate
The current audit container does not have `aiosqlite`, `ruff`, or `mypy` installed, so the full project verification suite cannot be truthfully declared green here. The authoritative gate remains the user's Windows `.venv`:

```powershell
pytest
pytest --cov=src --cov-report=term-missing
mypy src
ruff check .
```

Do not treat Phases 3–9 as production-release closed until those four commands pass in the provisioned environment.

## Phase 2 closure re-verification (2026-08-14)

The per-phase "local static gate: line-length audit PASS" entries above were manual line-length checks, not full `ruff check .` runs. The first full `ruff check .` reported 4 findings (E501 x2, I001 x2) and is the gate the CI workflow actually enforces.

Authoritative gate results after Phase 2 closure (Windows, Python 3.13, project root):

- `pytest`: 51/51 PASS
- `pytest --cov=src --cov-report=term-missing`: 83%
- `mypy src`: PASS
- `ruff check .`: All checks passed!

Phase 2 closure also fixed: `httpx` as a runtime dependency (required by `providers/ollama.py` and `providers/remote.py`), removed the unused `httpx2`, repaired `alembic.ini` logging sections and the SQLite-incompatible unique constraint in migration `0001`, aligned `/api/v1/providers` with `AVAILABLE_PROVIDER_MODES`, declared the JWT Bearer scheme in OpenAPI, and made `get_session()` echo the request correlation id.

As of the Phase 7 observability integration closure below, phases 3–7 components (agents, workflow, memory, RAG, tools, evaluation, audit/metrics) are integrated into the request runtime and verified with the four commands above. Only phases 8–9 (deployment, optimization) remain unintegrated and out of scope until their phases are authorized.

## Phase 3 integration closure (2026-08-14)

Phase 3 integrates the agent/governance workflow into the real `RequestService` execution path. Previously `RequestService.execute()` called `provider.generate()` directly, bypassing all governance; the agents and `GovernedWorkflow` were isolated (unit-tested only). Authoritative gate results after Phase 3 closure (Windows, Python 3.13, project root):

- `pytest`: 64/64 PASS
- `pytest --cov=src --cov-report=term-missing`: 84%
- `mypy src`: PASS (37 source files)
- `ruff check .`: All checks passed!

Integration changes:

1. **Governance is now the only execution path.** `RequestService.execute()` builds a `GovernedWorkflow(provider=...)` and runs planner → approval gate → research → execution → reviewer → compliance for every request. LOW/MEDIUM risk completes normally; HIGH/CRITICAL risk blocks unless approval is granted.
2. **`ExecutionAgent` now produces real output.** It constructs a `RequestEnvelope` (with the workflow's `correlation_id`) and calls the bound `AIProvider.generate()`; without a provider it keeps the deterministic `planned_output` fallback used by the unit tests.
3. **Deterministic risk classification.** New `application/risk.py` derives `RiskLevel` from the input text (keyword-based, no AI): destructive/system actions are CRITICAL, externally-visible/side-effect actions are HIGH, low-impact scheduling/creation is MEDIUM, everything else is LOW. This is the Phase 3 governance input; it is not a security boundary.
4. **Approval over HTTP.** The `X-Approval-Granted: true` header on `POST /api/v1/sessions/{session_id}/messages` and `POST /api/v1/requests/{request_id}/execute` passes `approval_granted` into the workflow context. A blocked request can later be approved and re-executed.
5. **correlation_id is now enforced through the whole pipeline.** `AgentInput` gained a required `correlation_id` field and `GovernedWorkflow.run()` takes `correlation_id` explicitly; the `RequestEnvelope` built by `ExecutionAgent` carries it, so providers receive the same correlation id used by the API middleware.
6. **Blocked state is persisted and surfaced.** A blocked request records `status="blocked"` with the block reason as `result`, and the assistant message tells the user why the action was not performed. No HTTP status codes or existing contracts changed (additive only).

New/changed tests demonstrating real behavior: `tests/unit/test_risk.py` (classifier), `tests/unit/test_governed_components.py` (ExecutionAgent delegates to provider, `GovernedWorkflow` executes via provider and propagates correlation id), `tests/unit/test_service.py` (high-risk blocked without approval, completes with approval, re-run of a blocked request), `tests/integration/test_requests_api.py` (HTTP blocked message, HTTP approval header, execute with approval header).

Remaining phases 4–9 components (memory/RAG integration, tools/MCP integration, evaluation wiring, observability wiring, deployment, optimization) are still NOT integrated into the runtime and remain out of scope until their phases are authorized.

## Phase 4 knowledge/memory integration closure (2026-08-14)

Phase 4 wires the existing memory/RAG components into the real `ResearchAgent` step of `GovernedWorkflow`. Before this phase the research agent was a placeholder that only copied pre-supplied ids; `GovernedRetriever` and `MemoryPolicy` existed but were never consulted by the runtime. Authoritative gate results after Phase 4 closure (Windows, Python 3.13, project root):

- `pytest`: 82/82 PASS
- `pytest --cov=src --cov-report=term-missing`: 85%
- `mypy src`: PASS (38 source files)
- `ruff check .`: All checks passed!

Integration changes:

1. **`MemoryStore` contract + in-memory implementation.** `memory/service.py` now defines a `MemoryStore` protocol (`add`/`retrieve`) and `InMemoryMemoryStore` with per-user scoping, expiry filtering via `MemoryPolicy.can_access`, class filtering, newest-first ordering, and limit. Production can later swap in a DB-backed store implementing the same protocol.
2. **`Retriever` contract.** `rag/service.py` now defines a `Retriever` protocol; the existing `GovernedRetriever` satisfies it unchanged.
3. **`ResearchAgent` performs real retrieval.** With an injected retriever it queries RAG (`retrieve(query, limit=3)`); with an injected memory store it recalls the user's notes (`retrieve(user_id, limit=5)`). Findings are exposed as `evidence_ids` plus metadata (`evidence` details, `memory` notes). Retrieval failures degrade gracefully (logged warning, empty result) so absence of evidence is never fatal.
4. **`GovernedWorkflow` injects dependencies.** `GovernedWorkflow(provider, retriever, memory, agents)` binds a `ResearchAgent(retriever, memory)` when not overridden. The execution step receives `evidence`, `research_evidence`, and `memory_notes` in its context, so research output genuinely reaches execution.
5. **`RequestService` writes memory.** `RequestService(session, provider, memory, retriever)` passes the store/retriever into the workflow and, on each completed request, writes a user-scoped `SESSION` memory item (`user: ...\nassistant: ...`, 24h TTL). Blocked and failed requests write nothing; a re-execution of an already completed request does not duplicate memory.
6. **Runtime wiring.** `infrastructure/memory.py` holds a process-scoped store initialized in the app lifespan; the API builds every `RequestService` through `_service(db)` with the shared memory store and a `GovernedRetriever` (default empty corpus — a source repository is infrastructure reserved for later phases). No schema/migration change was needed: memory is an in-memory contract for this phase.

Behavioral guarantees now covered by tests: research queries RAG and memory; multiple ranked evidence items; user-scoped memory (no cross-user leakage); empty corpus is non-fatal; RAG errors are controlled; evidence reaches the execution agent context; provider is not called before research completes; correlation id propagates into research; completed requests write user-scoped SESSION memory; blocked requests do not.

Remaining phases 5–9 components (tools/MCP integration, evaluation wiring, observability wiring, deployment, optimization) are still NOT integrated into the runtime and remain out of scope until their phases are authorized.

## Phase 5 tools/MCP integration closure (2026-08-14)

Phase 5 makes the existing `ToolRegistry` real and wires it into the `EXECUTION` step of `GovernedWorkflow`. Before this phase the registry existed but was never used by the runtime; the planner only flagged HIGH/CRITICAL risk and execution only ever echoed or delegated to the provider. Authoritative gate results after Phase 5 closure (Windows, Python 3.13, project root):

- `pytest`: 120/120 PASS
- `pytest --cov=src --cov-report=term-missing`: 86%
- `mypy src`: PASS (39 source files)
- `ruff check .`: All checks passed!

Integration changes:

1. **`tools/registry.py` extended with invocation and validation.** Added `ToolError`, `ToolCall`, `parse_tool_call(text)` for the `@tool:<name> <json-object>` text protocol (returns `None` when no invocation is present, raises `ToolError` on malformed names/JSON/non-object arguments), and `ToolDefinition.argument_schema` with runtime type validation (string/integer/float/boolean) performed by `ToolRegistry.execute()`. Critical-risk tools are still denied at registration.
2. **`tools/builtin.py` default runtime tools.** `default_tool_registry()` ships two safe LOW-risk tools: `calculator` (AST-based arithmetic evaluator that only accepts numeric constants and arithmetic operators, returning `{"result": ...}` or `{"error": ...}` on division-by-zero/unsupported syntax) and `list_tools` (enumerates registered names). Both are deterministic and require no external infrastructure.
3. **`PlannerAgent(registry)` flags tool approval early.** It parses the input for a tool invocation, and if the tool requires explicit approval it marks `requires_approval=True` in the plan metadata, so the workflow's approval gate blocks before any side effect can run.
4. **`ExecutionAgent(provider, registry)` executes real tools.** It parses the invocation, refuses unknown tools ("Tool 'x' is not available."), rejects invalid arguments ("Tool 'x' rejected arguments: ..."), returns a blocked artifact for approval-required tools without approval, propagates handler exceptions (surfacing as status `failed` at the service layer, consistent with provider failures), and records a `tools` metadata entry carrying `user_id`, `correlation_id`, and `approved`. Results are surfaced to the user as `Tool '<name>' returned: <result>`.
5. **`GovernedWorkflow(provider, retriever, memory, tools, agents)` binds tools.** When `tools` is provided, the planner and execution agents are bound with the registry; the execution step still receives research evidence/memory context alongside tool results. Request flow remains planner → approval gate → research → execution → reviewer → compliance.
6. **Service/API wiring.** `RequestService(..., tools=...)` passes the registry into the workflow; `_service(db)` in `api/app.py` supplies `tools=default_tool_registry()`. The tool invocation travels inside the request input text, so **no HTTP contract changed** and the approval mechanism reuses the existing `X-Approval-Granted` header through the same workflow context.

Behavioral guarantees now covered by tests: parse success/absence/malformed/JSON/name errors; argument validation (missing/unknown/type mismatch); unknown tool and approval-required tools at registry level; `calculator` arithmetic, division-by-zero, and unsupported-syntax rejection; `list_tools`; execution agent tool paths (success, unknown, invalid args, malformed call, blocked unapproved sensitive tool, approved sensitive tool, handler failure propagation); planner tool-awareness; workflow integration (tool execution, unapproved sensitive tool blocked, approved sensitive tool executed, unknown tool); service-level (tool run without provider call, sensitive tool blocked then completed with approval, handler failure → `failed`); and HTTP integration (calculator message, unknown tool message, invalid-args message, sensitive tool blocked without `X-Approval-Granted` and completed with it).

Remaining phases 6–9 components (evaluation wiring, observability wiring, deployment, optimization) are still NOT integrated into the runtime and remain out of scope until their phases are authorized.

## Phase 6 evaluation/release gates integration closure (2026-08-14)

Phase 6 turns evaluation from isolated code into a real stage of `GovernedWorkflow`. Before this phase `evaluation/gates.py` only held aggregate release-level gates (`workflow_success`/`security_pass`/`regression_pass` used by `release_ready`) that were never consulted by the request runtime, and the workflow ran planner → approval → research → execution → reviewer → compliance with no evaluation stage. Authoritative gate results after Phase 6 closure (Windows, Python 3.13, project root):

- `pytest`: 148/148 PASS
- `pytest --cov=src --cov-report=term-missing`: 88%
- `mypy src`: PASS (40 source files)
- `ruff check .`: All checks passed!

Integration changes:

1. **New `evaluation/runtime.py` — per-request release gates.** `ReleaseGateEvaluator` evaluates the workflow artifacts produced up to that point and returns a deterministic `EvaluationOutcome` with an explicit `EvaluationCriterion` per gate (name, passed, detail). Criteria derive only from real contracts: `artifacts_complete` (planner/research/execution/reviewer present), `response_present` (non-empty execution output), `reviewer_passed` (reviewer not blocked), `evidence_when_required` (evidence present when a plan step requires it), `tool_approval_respected` (approval-required tools ran approved), and `tool_correlation_valid` (tool attribution matches user/correlation). Unexpected exceptions are never misreported as a rejection: the evaluator is exception-free and deterministic.
2. **Evaluation is a real runtime stage.** `GovernedWorkflow` runs `ReleaseGateEvaluator` after the reviewer and before compliance: planner → approval gate → research → execution → reviewer → **evaluation** → compliance → result. The existing `evaluation/gates.py` release-level gates are untouched (still used by `release_ready` and the CI concept).
3. **New distinct status: `rejected`.** A request that fails evaluation returns `WorkflowResult(status="rejected", rejected_reason=<which gate failed and why>)`. This is now clearly distinct from `blocked` (approval/governance), `failed` (technical error), and `completed`. The reason string is surfaced in the API `result`/assistant message, so callers can see exactly which gate(s) failed and why.
4. **Reviewer outcome now feeds the release gate.** Previously a blocked reviewer short-circuited to `blocked`. Now the reviewer artifact is consumed by evaluation (criterion `reviewer_passed`), so a reviewer rejection yields `rejected` with the reviewer's reason embedded. This matches the architecture (reviewer → evaluation → compliance) and gives a single, consistent rejection path. Pre-evaluation governance gates (planner/approval/research/execution blocks) still produce `blocked` unchanged.
5. **Evaluation metadata is attached to results.** `WorkflowResult` gained `evaluation` (the full `EvaluationOutcome`) and `rejected_reason`; completed and compliance-blocked results that reached evaluation carry the outcome. No HTTP contract changed: `status` is an open string, so `rejected` is additive and OpenAPI stays identical.
6. **Service/API wiring.** `RequestService(..., evaluator=...)` passes the evaluator into the workflow (default `ReleaseGateEvaluator()`); the API `_service(db)` is unchanged. Memory still writes only on `completed`; `rejected` and `blocked`/`failed` write nothing.

Behavioral guarantees now covered by tests: evaluation PASS and FAIL per criterion; empty response rejected; missing/blocked reviewer rejected; evidence required when planned; tool approval respected; tool attribution consistency; all-failed-gates reporting; evaluation runs after reviewer and before compliance (ordering spy); evaluation failure yields `rejected` with reason; reviewer rejection maps to `rejected`; empty execution output rejected; evaluation receives the correct correlation id/user; compliance still blocks after a passing evaluation; tools still respect approval with evaluation active; workflow defensive short-circuits (plan/research/execution blocks, unparseable plan content); service surfaces `rejected` and writes no memory; HTTP contract test proves `rejected` status and assistant message over the wire; existing OpenAPI/contract and alembic migration tests still pass.

Remaining phases 7–9 components (observability wiring, deployment, optimization) are still NOT integrated into the runtime and remain out of scope until their phases are authorized.

## Phase 7 observability integration closure (2026-08-14)

Phase 7 makes observability a real part of the request runtime. Before this phase the `observability/` package held only reusable primitives (metrics counter/timer, structured audit events, redaction) that were never wired into the request path; the API correlation middleware was the only correlation boundary. Authoritative gate results after Phase 7 closure (Windows, Python 3.13, project root):

- `pytest`: 175/175 PASS
- `pytest --cov=src --cov-report=term-missing`: 92%
- `mypy src`: PASS (42 source files)
- `ruff check .`: All checks passed!

Integration changes:

1. **Observability facade.** `observability/observer.py` provides an `Observability` object that records a structured `AuditEvent` per stage (carrying `correlation_id`, optional `session_id`, `outcome`, error, and details) and delegates counter/duration recording to `Metrics`.
2. **Redaction guard rail.** `observability/audit.py` recursively redacts values under sensitive key names (tokens, authorization, passwords, API keys, JWTs, client secrets, ...) so credentials never reach the audit trail regardless of the caller.
3. **Workflow instrumentation.** `GovernedWorkflow(observability=...)` emits a stage event for planner, approval, research, execution, reviewer, evaluation, compliance, and every terminal state (completed/blocked/rejected), plus `workflow`/`research`/`evaluation` duration metrics and `evaluation_passes`/`evaluation_rejections` counters.
4. **ExecutionAgent instrumentation.** The execution agent emits `provider` (started/ok/error) and `tool` events and records `provider_executions`/`provider_failures` and `tool_executions`/`tool_failures` counters plus `provider`/`tool` durations.
5. **RequestService instrumentation.** The service records `request_received`/`request` terminal events and `requests_total`/`requests_completed`/`requests_blocked`/`requests_rejected`/`requests_failed` counters.
6. **Runtime wiring.** `infrastructure/observability.py` holds a process-scoped `Observability` initialized in the app lifespan; the API builds every `RequestService` through `_service(db)` with `observability=get_observability()`.
7. **Read endpoints.** New `GET /api/v1/observability/audit` and `GET /api/v1/observability/metrics`, both protected by the existing JWT Bearer scheme (401 without credentials). `AuditEventResponse` and `MetricsSnapshot` are additive contracts; no existing HTTP contract changed.
8. **correlation_id end-to-end.** Every audit event carries the same `correlation_id` used by the API middleware, so a single request is fully traceable across all stages.

Behavioral guarantees now covered by tests: recursive redaction of sensitive keys; user-scoped audit queries; audit window limiting; UTC timestamps; metric accumulation and duration snapshots; workflow stage trail on completion/block/rejection; provider success/failure counting; tool success/failure counting; service-level request metrics for completed/blocked/rejected/failed; HTTP integration for both observability endpoints including Bearer auth protection and the full workflow trail over the wire; OpenAPI contract includes both endpoints behind the Bearer scheme.

Regressions: Phases 2–6 behavior is preserved — 175/175 tests PASS, including the existing unit, contract, and integration suites. Docker/CI and Kubernetes remain out of scope exactly as before; neither was touched in this phase. A temporary uvicorn smoke-test server was observed running on `127.0.0.1:8099` (PID 22396) and was not restarted; it does not block the test suite (tests use an in-memory database).

Remaining phases 8–9 components (deployment, optimization) are still NOT integrated into the runtime and remain out of scope until their phases are authorized.

## Phase 8 deployment closure (2026-08-15)

Phase 8 moves the platform from "functional and tested locally" toward "reproducible and verifiably deployable" without touching agent logic, contracts, or dependencies. Authoritative gate results (Windows, Python 3.13, project root):

- `pytest`: 177/177 PASS (175 prior + 2 new Phase 8 tests)
- `pytest --cov=src --cov-report=term-missing`: 92%
- `mypy src`: PASS (42 source files)
- `ruff check .`: All checks passed!

Changes made:

1. **`.dockerignore` (new).** Excludes virtualenvs, caches, `*.db`, `.env`, docs, tests, CI, and markdown from the build context.
2. **`Dockerfile` (rewritten).** `python:3.13-slim`; `PYTHONUNBUFFERED=1`/`PYTHONDONTWRITEBYTECODE=1`; `pip install --no-cache-dir .` (runtime deps only); dedicated non-root `app` user; `EXPOSE 8000`; a `HEALTHCHECK` against `GET /api/v1/health/live` using the stdlib (no curl installed); `ENTRYPOINT` runs the migration entrypoint.
3. **`docker-entrypoint.sh` (new).** Runs `alembic upgrade head` before `exec uvicorn personal_ai_secretary.api.app:app --host 0.0.0.0 --port 8000`, making schema initialization explicit for production. The app lifespan `create_all` bootstrap remains for dev/tests and is idempotent alongside Alembic.
4. **`.env.example` (new).** Documents every environment variable (name, purpose, default, production behavior) with secure placeholders only; no real secrets.
5. **`shared/config.py`.** Added a `model_validator` guard: `APP_ENV=production` combined with the default development `JWT_SECRET` raises a `ValueError` at startup, refusing to run production with an insecure secret. Development/test defaults are unchanged.
6. **`tests/unit/test_config.py` (new).** Two tests covering the production secret guard (rejects the default secret; accepts an overridden strong secret).

Validations performed:

- **Reproducibility (`pip install .`, Docker-equivalent).** Fresh venv, `pip install .` from the project root succeeded; `alembic upgrade head` applied `0001_initial` on a fresh SQLite file; a TestClient smoke run returned `health/live 200`, `health/ready ready`, `providers 200`, `POST /requests` + `execute` → `completed`, `observability/audit 200`.
- **Reproducibility (`pip install -e '.[dev]'`, CI-equivalent).** Fresh venv installed the editable package with dev extras; full suite `177 passed`, `mypy` PASS (42 files), `ruff` PASS.
- **HTTP smoke (live uvicorn, controlled process).** A controlled local uvicorn server (port 8200, PID recorded, stopped and DB removed afterwards) passed 18/18 checks: public health/live, health/ready, providers; `401` without Bearer on protected endpoints (audit, metrics, `POST /requests`, session messages); Bearer-authenticated message → `completed` with `X-Correlation-ID` echoed in the body and header; session history intact; `list_tools` and `calculator` tools; CRITICAL-risk message blocked without approval and completed with `X-Approval-Granted`; full audit trail per correlation id; metrics counters (`requests_total`, `requests_completed`, `requests_blocked`, `provider_executions`, `tool_executions`, `evaluation_passes`) and durations (`workflow`, `evaluation`, `research`, `provider`); a follow-up message in the same session completed (memory path exercised).
- **Docker build: VERIFIED.** `docker build -t personal-ai-secretary:test .` succeeded (image `personal-ai-secretary:test`, base `python:3.13-slim`). The Docker daemon (server 29.6.2) became available during the closure audit.
- **Docker run: VERIFIED.** `docker run -d --name personal-ai-secretary-test -p 8100:8000 personal-ai-secretary:test` started and reached **healthy** within 8 seconds. `docker inspect` confirmed `User=app` (non-root) and `PYTHONUNBUFFERED=1`/`PYTHONDONTWRITEBYTECODE=1`.
- **Docker startup behaviour: VERIFIED.** `docker logs` shows the entrypoint running `alembic upgrade head` (`0001_initial`) before uvicorn, then `Uvicorn running on http://0.0.0.0:8000` and `Application startup complete`, with no startup errors.
- **Docker healthcheck: VERIFIED.** HEALTHCHECK log shows two checks with exit code 0 (healthy); `docker ps` reported `Up ... (healthy)`.
- **Docker container smoke: VERIFIED (14/14).** Against the container on `127.0.0.1:8100`: health/live 200, health/ready 200, providers 200; `401` without Bearer on audit/metrics/`POST /requests`; authenticated message → `completed` with correlation id; calculator tool → `returned: 4`; HIGH-risk blocked → approved → completed; metrics counters and durations; full 12-stage audit trail.
- **Docker cleanup: VERIFIED.** `docker stop` → `Exited (0)` (clean termination); `docker rm` removed the container. No container left behind (the pre-existing `novaleave-sqlserver` container was untouched). The `personal-ai-secretary:test` image was retained as a build artifact.
- **CI/CD.** `.github/workflows/ci.yml` runs `pytest --cov=src --cov-report=term-missing`, `mypy src`, and `ruff check .` on Python 3.13 (Ubuntu) after `pip install -e '.[dev]'`. **CI configuration: VERIFIED BY INSPECTION; CI execution: UNVERIFIED** — GitHub Actions cannot run from this Windows environment. The exact CI install path and commands were reproduced locally in a clean venv (see reproducibility above), but a real GitHub Actions run requires a push to GitHub.

Decisions and non-changes:

- Memory, audit, and metrics remain process-scoped in-memory; this is acceptable for a single-replica FASE 8 deployment. Distributed persistence (PostgreSQL/Redis/Prometheus/OTLP export) is reserved for FASE 9.
- No PostgreSQL/asyncpg adoption, no Docker Compose, no Kubernetes, no CORS, no provider changes, no agent/contract/dependency changes. The Phase 7 audit redaction mechanism is untouched.
- CORS is intentionally not enabled: the API is consumed by Bearer-authenticated server-side clients, not browsers; revisit if a browser client is added.
- The FASE 7 temporary smoke server on `127.0.0.1:8099` (PID 22396) is still running; it was not restarted and does not affect the test suite.

Remaining phase 9 components (optimization, distributed persistence, advanced scaling) remain out of scope until FASE 9 is authorized. FASE 8 verdict: **PASS WITH ENVIRONMENT-LIMITED VERIFICATION** — all code, runtime, database, dependency, configuration, Docker, and HTTP gates were VERIFIED locally (including Docker build/run/healthcheck/smoke). The only validation that could not be executed from this environment is the GitHub Actions run (**CI execution: UNVERIFIED**); the workflow is VERIFIED BY INSPECTION and its exact commands were reproduced locally. To complete that evidence, push to GitHub and confirm the `ci` workflow succeeds. No FASE 9 work was started.

## Phase 9 persistence and production closure (2026-08-15)

Phase 9 delivers persistent, multi-worker-safe, production-ready storage. Authoritative gate results (Windows, Python 3.13, project root):

- `pytest`: 193/193 PASS (177 prior + 16 new/updated Phase 9 tests)
- `pytest --cov=src --cov-report=term-missing`: 92%
- `mypy src`: PASS (43 source files)
- `ruff check .`: All checks passed!

Changes made:

1. **Persistent storage switch.** `Settings.persistent_stores: bool = False`. When true, `infrastructure/memory.py` and `infrastructure/observability.py` construct `PostgresMemoryStore`/`PostgresAuditStore` (new `infrastructure/stores.py`) over the shared engine instead of the in-memory stores. The same store classes run on SQLite in tests and PostgreSQL in production (DB-agnostic).
2. **Async store interfaces.** `MemoryStore`/`AuditStore` protocols and both in-memory implementations became async, and `Observability.emit` is now awaited at every call site. This is strictly necessary for database-backed stores (async I/O inside the request path); callers were updated (workflow engine, builtin agents, RequestService, API audit endpoint).
3. **Schema.** `domain/models.py` adds `MemoryItemRecord` and `AuditEventRecord`; `alembic/versions/0002_memory_and_audit.py` (additive, reversible) creates `memory_items` and `audit_events` with indexes. `alembic/env.py` rewrites `+asyncpg` to `+psycopg` for the sync migration engine; `pyproject.toml` adds `psycopg[binary]>=3.2,<4`.
4. **Guarded execute.** `RequestService.execute` uses a single guarded `UPDATE ... SET status='running' WHERE status IN ('accepted','blocked','rejected','failed')`; `rowcount == 0` returns the current status. This makes double-execution impossible under multiple workers.
5. **Prometheus metrics.** `render_prometheus_text` in `observability/metrics.py`; new protected endpoint `GET /api/v1/metrics` (`text/plain; version=0.0.4`). The JSON snapshot endpoint is unchanged.
6. **Production guard extended.** `APP_ENV=production` now also rejects a SQLite `DATABASE_URL` (config test updated; new test added).

Validations performed:

- **Gates**: 193 passed / 92% / mypy PASS / ruff PASS.
- **Tests added**: persistent stores (scoping, TTL, ordering, redaction, reconnect persistence), Prometheus formatter + endpoint, service concurrency (concurrent execute runs once; idempotent re-execute), migration 0002 up/down/partial, full-HTTP persistent backend incl. restart across app lifespans, config guards. Existing async-interface tests updated.
- **Real PostgreSQL (Docker): VERIFIED.** `postgres:16-alpine` (16.15) + app image built from the current tree. Migrations `0001 -> 0002` applied on PG (`alembic_version = 0002_memory_and_audit`); tables verified.
- **HTTP smoke on PostgreSQL: VERIFIED (9/9).** health/live, health/ready, message `completed`, second message `completed`, blocked message, `GET /requests/{id}` completed, user-a audit events, **user-b audit isolated (empty)**, Prometheus `/api/v1/metrics`.
- **Restart persistence: VERIFIED.** Pre-restart completed request still `completed` with result after `docker restart f9-app`; audit trail and `memory_items` survive; re-run of `alembic upgrade head` is a no-op; post-restart smoke 9/9.
- **Multi-worker (uvicorn --workers 2): VERIFIED.** Same-key concurrent creates one request; concurrent `execute()` guarded once; 10 concurrent distinct messages all completed; shared audit and metrics across workers; **0 stuck `running`**; `memory_items` == `completed` requests (36 == 36) -> no lost writes, no duplicates.
- **Cleanup: VERIFIED.** Containers `f9-app`, `f9-mw`, `f9-pg` and network `f9net` removed; `novaleave-sqlserver` untouched; orphaned uvicorn smoke server (PID 22396, port 8099) not restarted; `personal-ai-secretary:phase9` image retained.

Decisions and non-changes:

- RAG persistence and Redis/OTLP export remain non-goals (documented as limitations; no fake wiring). `opentelemetry-*` stay declared/unused as in Phase 7.
- No HTTP contract changed; `/api/v1/metrics` is additive. No provider, agent, or dependency-behavior changes.
- CI execution remains **UNVERIFIED** (GitHub Actions cannot run from this Windows environment; workflow VERIFIED BY INSPECTION and reproduced locally).

FASE 9 verdict: **PASS - FASE 9 CERRADA**. Persistence, migrations, guarded multi-worker execution, Prometheus metrics, and production guards are implemented and verified against a real PostgreSQL instance in Docker; local gates are green. The only environment-limited item is the GitHub Actions run, unchanged from Phase 8. No FASE 10 work was started; this phase ends here pending explicit authorization.

## Phase 10 multi-worker stale-running recovery closure (2026-08-15)

Phase 10 closes the multi-worker failure mode left open by Phase 9: a worker dying while a request is `running` would leave it stuck forever. Phase 10 adds stale-running detection/recovery (`stale_running_seconds`, `requests.updated_at` via migration `0004`, `recover_stale_running()` at startup, `_recover_stale_request()` in the guarded `execute()` path). See `docs/PHASE-2-9-IMPLEMENTATION.md` for the implementation notes.

Bug found during the audit and fixed:

- In `src/personal_ai_secretary/application/service.py`, the `execute()` stale-recovery branch recovered the request but did not `await self.session.commit()`, so the `failed` transition was never persisted. Fix: commit after a successful recovery. `tests/unit/test_service.py::test_execute_recovers_stale_running_request` was updated to assert the persisted `failed` row and result.

Additional FASE 10 lint finding fixed during the audit:

- `alembic/versions/0004_request_updated_at.py` (the new FASE 10 migration) failed `ruff check .` (I001 unsorted import block), which would fail CI. Fixed with a blank line between the two third-party imports (matching `0002`); `ruff check .` re-passed. No runtime/migration behavior changed.

Independent closure-audit gate results (Windows, Python 3.13, project root):

- `pytest --cov=src --cov-report=term-missing`: **207 passed, 92%** (1491 statements).
- `mypy src`: **PASS (43 source files)**.
- `ruff check .`: **All checks passed!** (`ruff format --check` differs only in pre-existing `0002`/`0003` migrations; CI does not run `ruff format` — informational).
- **Alembic: VERIFIED.** `upgrade head → downgrade base → upgrade head` on SQLite reaches head `0004_request_updated_at`; applied on real PostgreSQL 16; tables and `requests.updated_at` verified.
- **PostgreSQL + stale-recovery persistence: VERIFIED.** On real PG, a request set to stale `running` was confirmed `running` via raw SQL, `execute()` returned `failed`, and a fresh separate connection read `failed` with the recovery result — proof the fix commits and persists. 0 stuck `running`.
- **Multi-worker concurrency (uvicorn --workers 2) on real PG: ALL PASS.** Same-key (8 concurrent → exactly 1 request, 0 duplicates, 0 HTTP 500), different-key (10 concurrent all completed, 0 stuck), session race (8 concurrent on a new session, single session, no UniqueViolation, no 500), execution guard (6 concurrent execute on one accepted request, workflow ran exactly once).
- **DB consistency: VERIFIED.** 21/21 completed, 0 running, 0 failed; `memory_items` == completed (21 == 21).
- **Restart persistence: VERIFIED.** Completed request still completed after `docker restart`; memory (21) and audit (252) survive.
- **Memory/audit isolation: VERIFIED.** user-b audit = 0, user-a = 252.
- **Observability: VERIFIED.** audit/metrics/`/api/v1/metrics` Bearer-protected (401 without), counters updated, correlation_id end-to-end.
- **Security: VERIFIED.** JWT/Bearer, production guards, non-root `app` Docker user, HEALTHCHECK, `.dockerignore`, no real secrets.
- **Docker: VERIFIED.** Build, run, healthy, entrypoint migrations, HTTP smoke 21/21, cleanup completed.
- **CI execution: UNVERIFIED.** GitHub Actions cannot run from this Windows environment; workflow VERIFIED BY INSPECTION and its commands reproduced locally.

Decisions and non-changes:

- Metrics remain in-process per-worker counters (audit/memory are DB-backed in `persistent_stores=true`); Prometheus/OTLP export and cross-worker aggregation are reserved for FASE 11.
- No Kubernetes, Redis, Kafka, OTLP, caching, RAG persistence, new providers, or scaling work was started. **FASE 11 was NOT initiated.**
- The Phase 7 orphaned uvicorn smoke server (PID 22396, port 8099) was untouched; containers/network created by this audit (`f10-mw`, `f10-pg`, `f10net`) were removed; the `personal-ai-secretary:f10` image was retained as a build artifact.

FASE 10 verdict: **PASS - FASE 10 CERRADA** (with the single environment-limited item of the GitHub Actions run, unchanged from Phases 8–9).

## FASE 11 closure audit — multi-worker metric aggregation and production readiness (2026-08-15)

FASE 11 implemented the minimal scope approved by the operator: DB-backed multi-worker metric aggregation, infrastructure DB readiness, and the missing security/performance test layers. See `docs/PHASE-2-9-IMPLEMENTATION.md` (FASE 11 section) for implementation notes. The table `metric_records` (migration `0005_metric_records`) stores per-worker absolute counter/gauge values; reads SUM live counters and keep the most recent duration gauge; `/api/v1/observability/metrics` and `/api/v1/metrics` expose the aggregate; `/api/v1/health/ready` runs `SELECT 1` and reports `database=ok|unavailable` (503 when the provider or the database is unavailable).

Independent closure-audit gate results (Windows, Python 3.13, project root):

- `pytest --cov=src --cov-report=term-missing`: **220 passed, 92%** (1591 statements, 132 missing).
- `mypy src`: **PASS (43 source files)**.
- `ruff check .`: **All checks passed!**
- **Alembic (SQLite): VERIFIED.** `upgrade head → downgrade base → upgrade head → re-upgrade head` reaches head `0005_metric_records`; downgrade is reversible; re-upgrade is idempotent; tables and `metric_records` indexes (`ix_metric_records_worker_id`, `ix_metric_records_updated_at`, `uq_metric_records_worker_name`) verified.
- **PostgreSQL 16 real (Docker): VERIFIED.** Migrations `0001→0005` applied; `alembic_version = 0005_metric_records`; tables `requests`/`sessions`/`memory_items`/`audit_events`/`metric_records` present; persistence probe confirmed; 0 blocked/waiting queries.
- **Multi-replica aggregation (two independent containers, `--workers 2` each, shared PG): VERIFIED.** One request per replica → aggregate `requests_total 2`, `requests_completed 2`, `provider_executions 2`, `evaluation_passes 2` on both replicas; `metric_records` rows keyed by distinct `worker_id`s; no duplicate rows. Reads flush the serving worker first, so counters of other workers appear once their flush loop ticks (default 10s) — documented observability behavior, not a correctness bug.
- **Restart/retention convergence: VERIFIED.** After `docker restart` of one replica, the old worker's rows remain within the retention window (documented transient) and are pruned/expired after `metrics_retention_seconds`, so the aggregate converges to live workers only.
- **Idempotency: VERIFIED.** Same session + same key replayed → 200 with the same `request_id`, no duplicate request row; same key against a different session → 409; two concurrent same-key creates across replicas → exactly one request (0 duplicates, 0 double execution); no duplicate memory/audit.
- **Readiness: VERIFIED.** PG up → `200`/`ready`/`database=ok`; PG stopped → `503`/`degraded`/`database=unavailable` with provider metadata preserved; `/health/live` stays `200` independent of the DB.
- **Observability: VERIFIED.** `/api/v1/observability/metrics` and `/api/v1/metrics` Bearer-protected (401 without, 401 with bad token); valid Prometheus text format; aggregated counters; duration gauges keep the most recent observation; `correlation_id` propagates end-to-end; `metric_records` persists; expiration follows `metrics_retention_seconds`. Documented limitation upheld: counters are summed across live workers, so a restart can produce a transient over-count bounded by the retention window; this is observability data, not a correctness boundary.
- **Security: VERIFIED.** JWT/Bearer enforced; production guard refuses the default JWT secret and SQLite; Docker runs as non-root `app` (uid 999) with HEALTHCHECK; `.dockerignore` excludes caches/secrets/docs/tests; no real secrets versionable; audit never leaks `Authorization`.
- **Docker: VERIFIED.** Built from the current tree; run; `app` non-root; HEALTHCHECK healthy; startup `alembic upgrade head` idempotent at `0005`; HTTP smoke 200; restart persistence (`completed` with result); metrics aggregation after restart.
- **Regressions Fases 2–10: VERIFIED.** Full suite (220 tests) green plus a 13/13 HTTP regression smoke on a live replica (health, providers, requests lifecycle, execute, history, blocked high-risk, audit isolation, metrics).
- **CI execution: UNVERIFIED.** GitHub Actions cannot run from this Windows environment; `.github/workflows/ci.yml` VERIFIED BY INSPECTION (Python 3.13, `pip install -e '.[dev]'`, `pytest --cov=src --cov-report=term-missing`, `mypy src`, `ruff check .`) and its exact commands reproduced locally.

Decisions and non-changes:

- OpenTelemetry remains RESERVED: the `opentelemetry-*` deps and `otel_enabled` flag are documented as reserved and not activated. No OTLP/export pipeline was added.
- F-04 (inert compliance stage) remains DEFERRED; no workflow behavior was changed and no new enforcement was introduced.
- No Kubernetes, Redis, Kafka, RAG persistence, caching, OTLP, new providers, or scaling work was started. **FASE 12 was NOT started.**
- Audit containers/network (`f11-replica-a`, `f11-replica-b`, `f11-docker-smoke`, `f11-audit-pg`, `f11-audit-net`) and stale leftovers from prior phases (`relaxed_rhodes`, `trusting_dewdney`, `stoic_jennings`, `fase10-pg`) were removed; `novaleave-sqlserver` untouched; images retained as build artifacts.
- Minor documentation note: `.env.example` documents OTEL reserved and DB-backed metrics but predates `PERSISTENT_STORES`/`METRICS_FLUSH_SECONDS`/`METRICS_RETENTION_SECONDS`; the canonical env contract remains `shared/config.py` (this gap is also noted in `docs/PHASE-2-9-IMPLEMENTATION.md`).

FASE 11 verdict: **PASS WITH ENVIRONMENT-LIMITED VERIFICATION - FASE 11 CERRADA** (with the single environment-limited item of the GitHub Actions run, unchanged from Phases 8–10).

## FASE 12A closure audit — in-process OpenTelemetry tracing with optional OTLP export (2026-08-15)

FASE 12A implements the reserved OpenTelemetry observability as in-process tracing. Spans are always captured; when `OTEL_EXPORTER_ENDPOINT` is set (e.g. a local OTLP/HTTP collector) completed spans are exported via the OTLP HTTP exporter; otherwise they are retained in an in-memory buffer and no network is used. See `docs/PHASE-2-9-IMPLEMENTATION.md` (FASE 12A section) for the implementation notes. The only new runtime dependency is `opentelemetry-exporter-otlp-proto-http`; the `opentelemetry-api`/`opentelemetry-sdk` deps were already declared since Phase 7 and are now activated.

Independent closure-audit gate results (Windows, Python 3.13, project root):

- `pytest --cov=src --cov-report=term-missing`: **233 passed, 92%** (1720 statements, 135 missing).
- `mypy src`: **PASS (44 source files)**.
- `ruff check .`: **All checks passed!**
- **Alembic (SQLite): VERIFIED.** Chain still reaches head `0005_metric_records`; downgrade/re-upgrade idempotent; tables and `metric_records` constraints verified.
- **PostgreSQL 16 real (Docker): VERIFIED.** Migrations `0001→0005`; `alembic_version = 0005_metric_records`; all six tables; persistence probe PASS; 0 blocked/waiting queries.
- **Docker: VERIFIED.** Image `personal-ai-secretary:f12a` built; container healthy; entrypoint migrations idempotent; HTTP smoke (message `completed`, metrics, request fetch, Prometheus endpoint, health) all 200.
- **Default no-endpoint path: VERIFIED.** App run without `OTEL_EXPORTER_ENDPOINT` served requests normally and made **zero** OTLP/export network calls (no exporter logs) — the in-memory default requires no configuration.
- **OTLP local end-to-end (OpenTelemetry Collector): VERIFIED.** App on `f12a-net` with `OTEL_EXPORTER_ENDPOINT=http://f12a-collector:4318` exported **10 spans** to the collector for one message: `workflow.run` root (with `request_id`, `session_id`, `user_id`, `risk_level`, `correlation_id`, `outcome=completed`) plus `planner`, `approval`, `research`, `provider.run`, `execution`, `reviewer`, `evaluation`, `compliance`, `completion`. Span payloads contained **no sensitive attributes** (no prompts, no tool arguments, no Authorization/JWT values).
- **Unit tests (tracing): 14 PASS.** In-memory capture, hierarchy, attributes/correlation, error marking, clear/shutdown/idempotency, disabled no-op, auto-init, config defaults, OTLP lazy import/endpoint handling, real exporter construction.
- **CI execution: UNVERIFIED.** GitHub Actions cannot run from this Windows environment; the workflow commands were reproduced locally (233 tests, mypy 44 files, ruff).

Decisions and non-changes:

- All FASE 12A changes are strictly additive: no provider, agent, contract, schema, or existing behavior changed; `opentelemetry-exporter-otlp-proto-http` is the only added runtime dependency and is imported lazily.
- OTLP export to an external/cloud collector was not exercised; the local collector end-to-end plus unit tests cover the exporter wiring. No auto-instrumentation was added; the root span is the workflow execution and `correlation_id` links spans across stages.
- No Kubernetes, Redis, caching, RAG persistence, new providers, or F-04 work was started. **FASE 12 was NOT implemented beyond 12A.** FASE 12B–12G are DEFINED (not implemented) in `docs/PHASE-12-ROADMAP.md`; FASE 13+ was NOT initiated.
- Audit containers/network created for this closure (`f12-pg`, `f12a-app`, `f12-app`, `f12a-collector`, `f12a-net`) were removed; `novaleave-sqlserver` untouched; the `personal-ai-secretary:f12a` image retained as a build artifact.

FASE 12A verdict: **PASS WITH ENVIRONMENT-LIMITED VERIFICATION - FASE 12A CERRADA** (the environment-limited items are the GitHub Actions run, unchanged from Phases 8–11, and external/cloud OTLP export, which was not exercised).

## FASE 12 BLOQUE 1 closure audit — FASE 12B + 12C + 12D (2026-08-16)

Implemented consecutively as a single block per the official roadmap (`docs/PHASE-12-ROADMAP.md`): 12B (HTTP request-layer tracing), 12C (trace ↔ metrics ↔ audit correlation), 12D (OTLP export hardening). No Master Closure Audit was run per subphase; the block ran the Integrated Smoke Gate once.

Changes (all strictly additive, no schema, no migration, no contract break, no new dependency):

- **12B — request-layer tracing.** `api/app.py` `correlation_middleware` starts an `http.request` span (method, route *template*, status code, `correlation_id`; ERROR on unhandled exceptions). New `http_request_attributes()` in `observability/tracing.py`. `workflow.run` + stage/provider/tool spans become children of the request span. New `tests/integration/test_request_tracing.py`.
- **12C — correlation.** `get_current_trace_ids()` in `observability/tracing.py`; `Observability.emit()` and `record_duration()` now persist `trace_id`/`span_id` in audit details and link duration gauges to their trace; `Metrics.duration_trace_snapshot()`; `MetricsSnapshot.duration_trace_ids` (additive optional field); `GET /api/v1/observability/metrics` exposes it. Audit details are JSON/JSONB — no migration.
- **12D — OTLP hardening.** New settings `otel_export_timeout_seconds`, `otel_export_headers` (JSON headers for external collectors, never logged, never on spans), `otel_export_compression`, `otel_export_batch_schedule_seconds`, `otel_export_batch_max_queue_size`, `otel_export_batch_max_export_batch_size`; `_build_otlp_exporter` honors timeout/headers/compression; `BatchSpanProcessor` configured from settings; `.env.example` updated. No-endpoint in-memory path unchanged.

Baseline comparison: **234 → 248 tests** (14 new: 5 request-tracing integration + 3 12C correlation unit + 6 12D exporter/config unit), **coverage 92% → 93%** (1774 statements, 132 missing).

Integrated Smoke Gate (BLOQUE 1) evidence:

- **pytest:** 248 passed. **mypy:** PASS (44 source files). **ruff:** All checks passed! **Alembic:** head unchanged `0005_metric_records`; no migration required by 12B/12C/12D.
- **PostgreSQL 16 real (Docker):** migrations `0001→0005` on the f12bcd container; app healthy; persistence probe PASS; audit events with `trace_id`/`span_id` read back from PG (12C correlation persisted).
- **Docker:** image `personal-ai-secretary:f12bcd` built; container healthy; entrypoint migrations idempotent; HTTP smoke (live/ready/create/execute/message/metrics/audit) all PASS. No-endpoint container made **zero** OTLP calls.
- **OTLP local e2e (OpenTelemetry Collector via Docker): VERIFIED.** The `http.request`-rooted trace tree (request + `workflow.run` + planner/approval/research/execution/reviewer/evaluation/compliance/completion + `provider.run`) exported to the collector; `http.route` uses the template (`/api/v1/requests/{request_id}/execute`), not the raw path; audit `trace_id` matches the exported trace id. **No sensitive attributes** (no prompts, tool args, Authorization/JWT) in any span.
- **Security:** Bearer/JWT paths unchanged; no secrets in spans/audit/tracing; `OTEL_EXPORT_HEADERS` read from env and never logged.
- **Regression:** full suite of FASES 2–11 + 12A green (248 passed).
- **Performance:** no observable regression; the only new per-request work is one span start/end in the middleware (no pre-existing baseline delta detected).

Reserved for later / NOT part of BLOQUE 1: 12E (sampling/resource governance), 12F (multi-worker trace verification + propagation), 12G (consolidation), FASE 13+ (Redis, Kafka, Kubernetes, RAG persistence, new providers, F-04, auto-instrumentation, external CI). FASE 12 remains OPEN pending 12E+12F+12G and the MASTER CLOSURE AUDIT.

BLOQUE 1 verdict: **PASS WITH ENVIRONMENT-LIMITED VERIFICATION** — FASE 12B = CLOSED, FASE 12C = CLOSED, FASE 12D = CLOSED (environment-limited items unchanged: GitHub Actions run and external/cloud OTLP export, which cannot be exercised from this Windows environment). FASE 12E–12G = NOT INITIATED; FASE 13+ = NOT INITIATED.

## FASE 12 BLOQUE 2 closure audit — FASE 12E + 12F + 12G (2026-08-16)

Implemented consecutively as a single block per the official roadmap (`docs/PHASE-12-ROADMAP.md`): 12E (sampling/resource/processor governance), 12F (multi-worker trace verification + trace propagation), 12G (observability consolidation). No Master Closure Audit was run per subphase; the block ran the Integrated Smoke Gate once.

Changes (all strictly additive, no schema, no migration, no contract break, no new dependency):

- **12E — governance.** `shared/config.py`: `otel_sampling_ratio` (parent-based, default 1.0), `otel_service_version`, `otel_worker_id` (auto per process when unset). `observability/tracing.py`: `_sampler_for_ratio` (AlwaysOn/AlwaysOff/ParentBased), `_resolve_worker_id`, resource attributes (`service.name`, `service.version`, `deployment.environment`, `service.instance.id`), `get_service_instance_id()`. Default behavior identical to 12A.
- **12F — multi-worker + propagation.** `extract_traceparent()` / `get_traceparent_header()` / `start_span(parent_context=)` in `tracing.py`; inbound `traceparent` parenting in the correlation middleware; outbound `traceparent` header on `providers/ollama.py` and `providers/remote.py` HTTP calls. Malformed/absent headers ignored.
- **12G — consolidation.** Consolidated attribute constants (`ATTRIBUTE_*`, `RESOURCE_*`) applied across `tracing.py`, `api/app.py`, `workflow/engine.py`, `agents/builtin.py`; span attribute guide documented; attribute-consistency integration test.

Baseline comparison: **248 → 269 tests** (21 new: 8 12E unit + 6 12F unit + 2 12F integration + 3 12F/12G tracing-branch coverage + 1 12G consistency + 1 provider header), **coverage 93%** (1853 statements, 137 missing; above the 92–93% baseline).

Integrated Smoke Gate (BLOQUE 2) evidence:

- **pytest:** 269 passed. **mypy:** PASS (44 files). **ruff:** All checks passed! **Alembic:** full cycle `upgrade head → downgrade base → upgrade head → upgrade head` PASS; head unchanged `0005_metric_records`; no migration required by 12E–12G.
- **PostgreSQL 16 real (Docker):** migrations `0001→0005` on the shared f12efg PG; two app replicas both ready (`ready`/`ok`); audit events with `trace_id` read back from PG across replicas.
- **Docker:** image `personal-ai-secretary:f12efg` built; two replicas (`worker-one`/`worker-two`) healthy; HTTP smoke PASS; no-endpoint path unchanged (in-memory, zero OTLP calls).
- **OTLP multi-worker e2e: VERIFIED.** Both replicas exported full `http.request`-rooted workflow trees to the same collector with `service.instance.id = worker-one` AND `worker-two` (distinct, distinguishable). Inbound propagation e2e: `traceparent=00-aaaa…-bbbb…-01` request produced an exported `http.request` span with `Trace ID aaaa…` / `Parent ID bbbb…`. Audit↔trace correlation: an audit `trace_id` appeared 11× in the collector's exported spans. No secrets in any span.
- **Security:** no Authorization/JWT/prompt/tool args in spans/audit/export; `traceparent` is a validated trace-context string; `OTEL_EXPORT_HEADERS` env-only, never logged.
- **Regression:** full suite of FASES 2–11 + 12A–12D green (269 passed).
- **Performance:** no observable regression; added per-request work remains one span start/end plus optional traceparent parse (no delta detected).

Reserved for later / NOT part of BLOQUE 2: FASE 13+ (Redis, Kafka, Kubernetes, RAG persistence, new providers, F-04, auto-instrumentation, external CI). FASE 12 remains OPEN pending the **FASE 12 — MASTER CLOSURE AUDIT / FINAL VERIFICATION** (independent subsequent run).

BLOQUE 2 verdict: **PASS WITH ENVIRONMENT-LIMITED VERIFICATION** — FASE 12E = CLOSED, FASE 12F = CLOSED, FASE 12G = CLOSED (environment-limited items unchanged: GitHub Actions run and external/cloud OTLP export, which cannot be exercised from this Windows environment). FASE 12 = NOT CLOSED (pending the MASTER CLOSURE AUDIT). FASE 13+ = NOT INITIATED.

## FASE 12 MASTER CLOSURE AUDIT (2026-08-16)

Independent read-only verification of the real tree (no code, migration, dependency, CI, or infrastructure changes). Enforced the audit rules: no feature work, no FASE 13+, `CODE CHANGES = NONE` (single documentation status update only).

Static gates (identical to the `.github/workflows/ci.yml` contract):
- `pytest`: 269/269 PASS (0 failed, 0 skipped, 0 xfail, 0 warnings); coverage **93%** (1853 stmts / 137 miss).
- `mypy src`: PASS (44 source files).
- `ruff check .`: All checks passed.
- Alembic (SQLite cycle): upgrade head → downgrade base → upgrade head → upgrade head; current/heads = `0005_metric_records`; 5 migrations exactly (`0001_initial` … `0005_metric_records`), no FASE 12 additions. Post-cycle schema check: tables `alembic_version`, `audit_events`, `memory_items`, `metric_records`, `requests`, `sessions` all present with expected indexes (including `uq_requests_user_id_idempotency_key`, `uq_metric_records_worker_name`) and all 10 `requests` columns.

PostgreSQL 16 real verification (Docker `postgres:16-alpine`, PG 16.15, migrations 0001→0005 applied cleanly to an empty DB): 6 tables, 22 indexes, **0 blocked / 0 lock-wait** connections; app (`personal-ai-secretary:f12efg`) ready with `DB=ok`; a real request persisted `requests`, `sessions`, and (with `PERSISTENT_STORES=true`) `audit_events` (12 rows) and `metric_records` (8 rows) into PG. API audit trace_id and the PG `audit_events.details.trace_id` matched exactly.

FASE 12A–12G verification (live OTLP collector + 2 replicas on shared PG):
- 12A spans: full trace tree exported — root `http.request` → `workflow.run` → `planner`, `research`, `execution`, `reviewer`, `evaluation`, `compliance`, `completion`; single trace id; route template `/api/v1/sessions/{session_id}/messages`; no secrets in spans (Authorization/Bearer/jwt/prompt absent from collector output).
- 12B/12C correlation: audit trace_id appeared 11× in collector output (all spans of one request); API and PG persisted audit trace_id identical; per-request traces distinct (4 completed requests → 4 distinct trace_ids).
- 12D: with no `OTEL_EXPORTER_ENDPOINT`, a full request completed with **0** OTLP/export/error log lines — no network on the no-endpoint path.
- 12E: worker resource exported as `service.instance.id = worker-a` / `worker-b` on distinct replicas (4 vs 5 span groups respectively); `service.version = 1.0.0`, `deployment.environment = development`; sampling-governance unit tests pass (ratio 0/1/in-between, worker id auto/config, resource attrs).
- 12F: inbound W3C `traceparent` (`00-cccc…-dddd…-01`) propagated — the health span exported with Trace ID `cccc…` and Parent ID `dddd…`; outbound provider propagation wired in `providers/ollama.py` (sends `traceparent` when present) and `providers/remote.py`; propagation unit+integration tests pass.
- 12G: consolidated `ATTRIBUTE_*`/`RESOURCE_*` constants present in `tracing.py` and used by the API middleware, workflow engine, builtin agents, and providers; consistency integration test passes.

Security audit: no real secrets anywhere (`src`, `tests`, `.env.example`, docs — only test fixtures and commented placeholders); production guard rejects a default `JWT_SECRET` (`APP_ENV=production` → ValueError); `tests/security` and `tests/contract` suites pass (authorization-header-never-leaked test, OpenAPI Bearer contract); Docker runs as non-root `app` user (uid 999) with `HEALTHCHECK`; `.dockerignore` excludes `.env`, tests, docs, `.git`.

Docker lifecycle: final image `personal-ai-secretary:f12efg` built; restart preserved persisted data (requests count 5 → 5, ready `DB=ok` after restart); 8 sequential requests on PG completed in ~5s (~630ms/request, in-process provider).

FASE 13+ protection gate: scan of `src/` for kafka/redis/kubernetes/k8s/celery/auto-instrumentation/vector-db/persistent-RAG artifacts returned **zero matches**; no FASE 13+ work initiated.

Verdict: **PASS WITH ENVIRONMENT-LIMITED VERIFICATION — FASE 12 CERRADA** (environment-limited items unchanged: GitHub Actions execution and external/cloud OTLP export, which cannot be exercised from this Windows environment). No bugs, regressions, or doc errors found (all checks PASS; classification E/G only — environment, non-blocking). FASE 12A–12G = CLOSED. FASE 13+ = NOT INITIATED.
