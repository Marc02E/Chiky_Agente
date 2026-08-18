# Phase 2–9 implementation evidence

This release extends the Phase 1 platform with governed agent contracts, deterministic workflow orchestration, governed retrieval/evidence primitives, memory policies, a deny-by-default tool registry, evaluation gates, audit/metrics primitives, an optional NVIDIA provider adapter, and reproducible Docker/CI configuration.

> Correction (Phase 2 closure, 2026-08-14): the Docker image depends on `httpx`, which is now declared as a runtime dependency (it was dev-only, which would have broken `pip install .` inside the image). `alembic.ini` was also missing logging sections and migration `0001` used an `ALTER`-based unique constraint that SQLite cannot apply; both were fixed. The Docker image build could not be executed in this environment because the Docker daemon was not running; a fresh-venv `pip install .` plus a live-server smoke test passed with runtime dependencies only.

Provider and infrastructure boundaries remain explicit. PostgreSQL/pgvector, external MCP servers, production secret management, and Kubernetes remain infrastructure concerns rather than hidden local fallbacks.

## Phase 3 integration evidence (2026-08-14)

Phase 3 wires the governed workflow into the real request path. Before this phase `RequestService.execute()` called the provider directly and the agents/`GovernedWorkflow` only ran in unit tests. Now every request runs planner → approval gate → research → execution → reviewer → compliance.

Integration notes:

- `GovernedWorkflow(provider=...)` binds the provider to a fresh `ExecutionAgent`, which builds a `RequestEnvelope` (carrying the request's `correlation_id` and classified `RiskLevel`) and calls `AIProvider.generate()`; the reviewer/compliance agents gate its output.
- `application/risk.py` provides a deterministic keyword classifier that maps input text to `RiskLevel` (CRITICAL/HIGH/MEDIUM/LOW). It is governance input only, not a security boundary.
- HIGH/CRITICAL requests are blocked unless the caller sends `X-Approval-Granted: true` on `POST /api/v1/sessions/{session_id}/messages` or `POST /api/v1/requests/{request_id}/execute`. Blocked requests record `status="blocked"` with the block reason and can be re-executed with approval.
- `AgentInput` and `GovernedWorkflow.run()` now require `correlation_id`, so the API correlation id flows end-to-end into the provider envelope.
- Verified with 64 pytest tests (unit + HTTP integration), a live uvicorn smoke test (blocked without approval, completed with approval, LOW completes untouched), mypy PASS, and ruff PASS.

## Phase 4 knowledge/memory integration evidence (2026-08-14)

Phase 4 makes the research step of `GovernedWorkflow` consult the existing memory/RAG components instead of echoing pre-supplied ids.

Integration notes:

- `MemoryStore` protocol (`add`/`retrieve`) with `InMemoryMemoryStore`: per-user isolation enforced through `MemoryPolicy.can_access` (user + expiry), memory-class filtering, newest-first ordering, and a limit. The production path is prepared to swap a DB-backed store implementing the same protocol.
- `Retriever` protocol added in `rag/service.py`; the deterministic `GovernedRetriever` satisfies it unchanged.
- `ResearchAgent(retriever, memory)` now performs real retrieval: RAG evidence from the query plus the user's memory notes. It exposes `evidence_ids` and metadata (`evidence` details, `memory` notes) and degrades gracefully on retrieval errors.
- `GovernedWorkflow(provider, retriever, memory, agents)` binds the research agent with those dependencies; the execution context carries `evidence`, `research_evidence`, and `memory_notes` so research output reaches execution.
- `RequestService` writes a user-scoped SESSION memory item on each completed request (24h TTL) and passes the store/retriever into the workflow. Blocked/failed requests write nothing; idempotent re-execution does not duplicate memory.
- Runtime wiring: `infrastructure/memory.py` holds a process-scoped store created in the app lifespan; all API service construction goes through `_service(db)`. No schema/migration change was required in this phase (in-memory memory contract; a DB-backed store is reserved infrastructure).
- Verified with 82 pytest tests, a live uvicorn smoke test (completed message, HIGH-risk blocked, HIGH-risk approved, history intact), mypy PASS, and ruff PASS.

## Phase 5 tools/MCP integration evidence (2026-08-14)

Phase 5 makes the existing `ToolRegistry` real and wires it into the `EXECUTION` step of `GovernedWorkflow`. Before this phase the registry was never consulted by the runtime.

Integration notes:

- `tools/registry.py` now provides `ToolError`, `ToolCall`, `parse_tool_call()` for the `@tool:<name> <json-object>` text protocol, and `ToolDefinition.argument_schema` with runtime type validation (string/integer/float/boolean) enforced by `ToolRegistry.execute()`. Critical-risk tools are still denied at registration.
- `tools/builtin.py` ships `default_tool_registry()` with two safe LOW-risk tools: `calculator` (AST-based arithmetic evaluator restricted to numeric constants and arithmetic operators; returns `{"result": ...}` or `{"error": ...}`) and `list_tools`.
- `PlannerAgent(registry)` detects a tool invocation in the input and marks `requires_approval=True` when the tool needs explicit approval, so the workflow approval gate blocks before any side effect.
- `ExecutionAgent(provider, registry)` parses invocations, refuses unknown tools, rejects invalid arguments, blocks approval-required tools without approval, propagates handler exceptions (→ status `failed`), and records a `tools` metadata entry with `user_id`, `correlation_id`, and `approved`. Results are surfaced as `Tool '<name>' returned: <result>`.
- `GovernedWorkflow(provider, retriever, memory, tools, agents)` binds planner/execution with the registry when `tools` is provided. Flow remains planner → approval gate → research → execution → reviewer → compliance.
- `RequestService(..., tools=...)` and `_service(db)` in `api/app.py` (with `tools=default_tool_registry()`) wire the registry into the runtime. The tool invocation travels inside the request input text, so **no HTTP contract changed** and approval reuses the existing `X-Approval-Granted` header via the workflow context.
- Verified with 120 pytest tests (unit + HTTP integration), a live uvicorn smoke test (calculator returned 4, unknown tool not available, invalid arguments rejected, sensitive tool blocked/completed by approval header), mypy PASS, and ruff PASS.

## Phase 6 evaluation/release gates integration evidence (2026-08-14)

Phase 6 makes evaluation a real stage of `GovernedWorkflow`. Before this phase `evaluation/gates.py` only held aggregate release-level gates (`release_ready`) never consulted by the runtime.

Integration notes:

- `evaluation/runtime.py` adds `ReleaseGateEvaluator`, `EvaluationContext`, `EvaluationCriterion`, and `EvaluationOutcome`. Each criterion reports its name, pass/fail, and a deterministic detail string. Criteria derive from real contracts: `artifacts_complete`, `response_present`, `reviewer_passed`, `evidence_when_required`, `tool_approval_respected`, `tool_correlation_valid`. The evaluator is exception-free; an unexpected error can never be mistaken for an evaluation rejection.
- `GovernedWorkflow` now runs the release gate after the reviewer and before compliance: planner → approval gate → research → execution → reviewer → **evaluation** → compliance → result. A failing evaluation returns a new distinct status `rejected` with `rejected_reason` naming the failing gate(s). A blocked reviewer is now consumed by evaluation (`reviewer_passed`) and maps to `rejected` instead of `blocked`; planner/approval/research/execution blocks still map to `blocked`; provider/tool-handler exceptions still map to `failed`; `completed` is reserved for evaluation-passing executions.
- `WorkflowResult` gained `evaluation` and `rejected_reason`; completed and compliance-blocked results that reached evaluation carry the full `EvaluationOutcome`.
- `RequestService(..., evaluator=...)` injects the evaluator into the workflow; the API `_service(db)` keeps the default `ReleaseGateEvaluator()`. Memory writes still happen only on `completed` (rejected/blocked/failed write nothing).
- HTTP contract unchanged: `status` is an open string, so `rejected` is additive and OpenAPI stays identical. The `rejected` reason travels in the API `result` and the assistant message.
- `evaluation/gates.py` release-level gates are untouched and still exercised (`release_ready`).
- Verified with 148 pytest tests (unit + HTTP integration, including the `rejected` contract over the wire), a live uvicorn smoke test (completed, HIGH blocked, HIGH approved, calculator tool, unknown tool), mypy PASS (40 source files), and ruff PASS.

## Phase 7 observability integration evidence (2026-08-14)

Phase 7 makes observability a real part of the request runtime. Before this phase the `observability/` package only held reusable primitives (metrics counter/timer, structured audit events, redaction) that were never wired into the request path.

Integration notes:

- `observability/observer.py` provides an `Observability` facade that records a structured `AuditEvent` per stage (correlation id, optional session id, outcome, error, details) and delegates counter/duration recording to `Metrics`.
- `observability/audit.py` recursively redacts values under sensitive key names (tokens, authorization, passwords, API keys, JWTs, client secrets), so credentials never reach the audit trail regardless of the caller.
- `GovernedWorkflow(observability=...)` emits a stage event for planner, approval, research, execution, reviewer, evaluation, compliance, and every terminal state (completed/blocked/rejected), plus `workflow`/`research`/`evaluation` duration metrics and `evaluation_passes`/`evaluation_rejections` counters.
- `ExecutionAgent(provider, registry, observability)` emits `provider` (started/ok/error) and `tool` events and records `provider_executions`/`provider_failures` and `tool_executions`/`tool_failures` counters plus `provider`/`tool` durations.
- `RequestService(..., observability=...)` records `request_received`/`request` terminal events and `requests_total`/`requests_completed`/`requests_blocked`/`requests_rejected`/`requests_failed` counters.
- Runtime wiring: `infrastructure/observability.py` holds a process-scoped `Observability` initialized in the app lifespan; the API builds every `RequestService` through `_service(db)` with `observability=get_observability()`.
- Read endpoints: `GET /api/v1/observability/audit` and `GET /api/v1/observability/metrics`, both protected by the existing JWT Bearer scheme (401 without credentials). `AuditEventResponse` and `MetricsSnapshot` are additive contracts; no existing HTTP contract changed.
- Every audit event carries the API correlation id, so a single request is traceable end-to-end across all stages.
- Verified with 175 pytest tests (unit + HTTP integration), a live uvicorn smoke test on `127.0.0.1:8099` (PID 22396, temporary smoke-test server: message completed, full 12-event audit trail, metrics counters and durations, both endpoints auth-protected), mypy PASS (42 source files), and ruff PASS. Regressions for Phases 2–6 remain green. Docker/CI and Kubernetes remain out of scope exactly as before and were not touched in this phase.

## Phase 8 deployment evidence (2026-08-15)

Phase 8 prepares the platform for deployment and reproducibility. No agent logic, HTTP contracts, or dependencies changed.

Deployment changes:

- `.dockerignore` added: excludes virtualenvs, caches, `*.db`, `.env`, docs, tests, CI, and markdown from the build context.
- `Dockerfile` rewritten on `python:3.13-slim`: `PYTHONUNBUFFERED=1`/`PYTHONDONTWRITEBYTECODE=1`, `pip install --no-cache-dir .` (runtime deps only), dedicated non-root `app` user, `EXPOSE 8000`, `HEALTHCHECK` against `GET /api/v1/health/live` via stdlib, and an `ENTRYPOINT` that runs `docker-entrypoint.sh`.
- `docker-entrypoint.sh` added: runs `alembic upgrade head` then `exec uvicorn personal_ai_secretary.api.app:app --host 0.0.0.0 --port 8000`. Schema bootstrap for dev/tests (`create_all` in the app lifespan) remains and is idempotent alongside Alembic.
- `.env.example` added: documents the environment contract (APP_ENV, JWT_*, DATABASE_URL, AI_PROVIDER, OLLAMA_*, PROVIDER_TIMEOUT_SECONDS, LOG_LEVEL, OTEL_ENABLED) with secure placeholders only.
- `shared/config.py` gains a production guard: `APP_ENV=production` with the default development `JWT_SECRET` raises at startup. Development/test defaults are untouched.
- `tests/unit/test_config.py` added: two tests for the production secret guard.

Validation evidence:

- Clean venv, `pip install .` (Docker-equivalent runtime install): succeeded; `alembic upgrade head` applied `0001_initial` on a fresh SQLite file; TestClient smoke returned `health/live 200`, `health/ready ready`, `providers 200`, message execute `completed`, `observability/audit 200`.
- Clean venv, `pip install -e '.[dev]'` (CI-equivalent): `177 passed`, `mypy` PASS (42 files), `ruff` PASS.
- Live HTTP smoke on a controlled uvicorn server (`127.0.0.1:8200`, PID recorded, stopped afterwards, DB removed): 18/18 PASS, including `401` without Bearer on protected endpoints, Bearer-authenticated workflow `completed` with `X-Correlation-ID` echoed end-to-end (body and header), session history, tools, metrics counters/durations, and CRITICAL-risk blocked→approved governance.
- **Docker build: VERIFIED.** `docker build -t personal-ai-secretary:test .` succeeded on the now-available daemon (server 29.6.2), producing image `personal-ai-secretary:test` (base `python:3.13-slim`, `User=app`, `PYTHONUNBUFFERED=1`/`PYTHONDONTWRITEBYTECODE=1`).
- **Docker run + healthcheck: VERIFIED.** `docker run -d --name personal-ai-secretary-test -p 8100:8000 personal-ai-secretary:test` reached **healthy** within 8 seconds; HEALTHCHECK log shows exit code 0 on every check. `docker logs` shows `alembic upgrade head` (`0001_initial`) running before uvicorn on `0.0.0.0:8000`, with no startup errors.
- **Docker container smoke: VERIFIED (14/14).** Against the running container on `127.0.0.1:8100`: health/live 200, health/ready 200, providers 200; `401` without Bearer; authenticated workflow `completed` with correlation id; calculator tool; HIGH blocked→approved→completed; metrics; full 12-stage audit trail.
- **Docker cleanup: VERIFIED.** `docker stop` → `Exited (0)`; `docker rm` removed the container. The `personal-ai-secretary:test` image remains as a build artifact.
- CI workflow `ci.yml` already runs pytest + coverage + mypy + ruff; **CI execution cannot run from this environment (UNVERIFIED)**, but the workflow is VERIFIED BY INSPECTION and its exact install path and commands were reproduced locally.

FASE 9 later implemented distributed persistence for memory/audit, PostgreSQL/asyncpg adoption, and the Prometheus text metrics endpoint (see the Phase 9 section below). Still not implemented: Prometheus/OTLP export server, Kubernetes, scaling, and performance tuning; memory/audit/metrics persistence became database-backed in FASE 9, not in-memory. CORS is intentionally not enabled for Bearer-authenticated server-side clients.

## Phase 9 persistence and production closure (2026-08-15)

Phase 9 moves the platform from "functional, governed, observable, deployable locally" to "persistent, multi-worker safe, production-ready and technically scalable". It adds real database persistence for requests, sessions, memory, and audit, a guarded state transition for multi-worker execution, a Prometheus text metrics endpoint, and a migration chain, all verified against a real PostgreSQL instance in Docker.

Integration changes:

- **`shared/config.py`.** New `persistent_stores: bool = False` setting that switches the runtime between in-memory stores (development/tests) and database-backed stores (production). The production guard is extended: `APP_ENV=production` now also rejects a SQLite `DATABASE_URL`, so production must point at a real database (PostgreSQL via `postgresql+asyncpg://...`). `alembic/env.py` now rewrites `+asyncpg` to `+psycopg` (instead of stripping it) so the sync migration engine uses psycopg v3 (psycopg2 is not installed).
- **`domain/models.py`.** New `MemoryItemRecord` (`memory_items`: `memory_id` PK, `user_id`, `content`, `memory_class`, `created_at`, `expires_at`, with indexes) and `AuditEventRecord` (`audit_events`: serial `id`, `event_type`, `request_id`, `user_id`, `outcome`, `correlation_id`, `timestamp`, JSON `details`, with indexes on event_type/request_id/user_id/correlation_id/timestamp). Additive to the existing `requests`/`sessions` models.
- **`memory/service.py`, `observability/audit.py` (interface change).** `MemoryStore` and `AuditStore` protocols are now async (`await store.add(...)`, `await store.retrieve(...)`, `await audit.record(...)`, `await audit.events(...)`), and `InMemoryMemoryStore`/`InMemoryAuditStore` match. This is a strictly necessary interface change: a real database backend requires asynchronous I/O inside the request path. `Observability.emit` becomes async accordingly.
- **`infrastructure/stores.py` (new).** `PostgresMemoryStore` and `PostgresAuditStore` implement the protocols on any SQLAlchemy async backend (PostgreSQL/asyncpg in production, file-backed SQLite in tests). They are DB-agnostic: datetimes read back from SQLite are normalized to UTC-aware (`_as_utc`), the memory TTL policy (`MemoryPolicy`) is enforced at query time, audit details are redacted through the existing `sanitize_value` before persistence, and `events()` returns newest-first (ordered by `id DESC`). `clear()` is provided for memory.
- **`infrastructure/memory.py`, `infrastructure/observability.py`.** Singletons now choose the backend from `settings.persistent_stores`: in-memory stores when false, `PostgresMemoryStore`/`PostgresAuditStore` (sharing the engine from `get_session_factory()`) when true.
- **`application/service.py` (guarded execute).** `execute()` no longer reads then writes `status='running'`. It performs a single guarded `UPDATE requests SET status='running' WHERE request_id=... AND status IN ('accepted','blocked','rejected','failed')` (user-scoped when a `user_id` is provided) and commits; if `rowcount == 0` the request is already running/completed (or missing) and the current `RequestStatus` is returned. With multiple workers this makes double-execution of the same request impossible. `_remember` became async.
- **`workflow/engine.py`, `agents/builtin.py`, `api/app.py`.** All `observability.emit(...)` call sites are now awaited; `ResearchAgent._recall` and `GovernedWorkflow._blocked` are async; the audit read endpoint awaits `audit.events(...)`.
- **`observability/metrics.py`.** New `render_prometheus_text(metrics)` produces Prometheus text format (`# TYPE <name> counter` for counters, `# TYPE <name>_duration_seconds gauge` for durations), sorted and newline-terminated. `api/app.py` exposes it at `GET /api/v1/metrics` (same Bearer scheme, `text/plain; version=0.0.4`); the JSON snapshot endpoint `GET /api/v1/observability/metrics` is unchanged.
- **`alembic/versions/0002_memory_and_audit.py` (new).** Additive, reversible migration creating `memory_items` and `audit_events` with their indexes; `downgrade` drops only Phase 9 tables. `pyproject.toml` adds `psycopg[binary]>=3.2,<4` (sync driver used by Alembic online migrations in the container; the app itself still uses asyncpg).

Validation evidence:

- **Gates (Windows, Python 3.13):** `pytest --cov=src --cov-report=term-missing`: **193 passed, 92%**; `mypy src`: **PASS (43 source files)**; `ruff check .`: **All checks passed!**
- **New/updated tests.** `tests/unit/test_persistent_stores.py` (PostgresMemoryStore/PostgresAuditStore on file SQLite: user scoping, class/expiry filtering, newest-first ordering, limit, redaction, reconnect/restart persistence); `tests/unit/test_prometheus.py` (formatter + `/api/v1/metrics` endpoint + JSON endpoint regression); `tests/unit/test_service_concurrency.py` (3 concurrent `execute()` calls run the workflow exactly once; re-execute of a completed request is idempotent); `tests/integration/test_migrations.py` extended (head schema includes memory_items/audit_events; downgrade to base removes them; downgrade to 0001 removes only Phase 9 tables); `tests/integration/test_persistent_backend.py` (full HTTP stack with `persistent_stores=true` on a file DB: audit/memory persisted to disk, data survives two app lifespans/restart); `tests/unit/test_config.py` extended (production rejects SQLite; `persistent_stores` default). Existing async-interface tests (memory store, audit store, observability, service, governed components, requests API) were updated to the async protocol.
- **Real PostgreSQL via Docker: VERIFIED.** Network `f9net` + `postgres:16-alpine` (16.15) + image `personal-ai-secretary:phase9` built from the current tree. App launched with `APP_ENV=production`, strong `JWT_SECRET`, `DATABASE_URL=postgresql+asyncpg://...@f9-pg:5432/secretary`, `PERSISTENT_STORES=true`. Startup logs show `Running upgrade -> 0001_initial` then `0001_initial -> 0002_memory_and_audit`; `alembic_version = 0002_memory_and_audit`; tables `requests`, `sessions`, `memory_items`, `audit_events` present in PG.
- **HTTP smoke on PostgreSQL: 9/9.** health/live, health/ready; Bearer-authenticated message `completed` (`DETERMINISTIC_RESPONSE: hello`); second message `completed`; HIGH-risk message `blocked`; `GET /requests/{id}` completed; audit user-a has events; **audit user-b isolated (empty)**; `GET /api/v1/metrics` Prometheus text with `requests_total`.
- **Restart persistence: VERIFIED.** A completed request created before `docker restart f9-app` is still `completed` with its result after restart; the audit trail (58 user-a events) and `memory_items` (5 rows) survive the restart; the re-run `alembic upgrade head` is a no-op (already at head). Smoke after restart: 9/9.
- **Multi-worker (uvicorn --workers 2, two worker processes): ALL PASS.** 5 concurrent `POST` with the same idempotency key + same session create exactly one request (distinct ids == 1) and it finishes `completed`; 3 concurrent `execute()` calls on one accepted request (guarded transition) yield `{'completed','running'}` with the final state completed and the workflow run exactly once; 10 concurrent distinct messages all `completed`; audit shared across workers; Prometheus metrics shared across workers. Database consistency after all runs: `requests` have **0 stuck `running`**, and **`memory_items` count == `completed` request count (36 == 36)**, i.e. no lost writes and no duplicate executions.
- **Cleanup: VERIFIED.** `docker rm -f` removed `f9-app`, `f9-mw`, `f9-pg`; `docker network rm f9net`. The pre-existing `novaleave-sqlserver` container was untouched; the orphaned uvicorn smoke server (PID 22396, port 8099) was not restarted. The `personal-ai-secretary:phase9` image was retained as a build artifact.

Non-changes and limitations (documented, no fake work):

- RAG is still not persisted; memory is conversational memory only, stored with a TTL policy enforced at query time. No RAG corpus persistence was added (documented as a FASE 9 non-goal).
- No Redis cache, no Prometheus/OTLP export server, no tracing export: metrics are in-process counters exposed in Prometheus text format; the reserved `opentelemetry-*` dependencies remain declared but unused exactly as in Phase 7.
- No Kubernetes, no Docker Compose, no CORS, no provider changes. No HTTP contract changed: `GET /api/v1/observability/metrics`, `GET /api/v1/observability/audit`, and all request/session endpoints keep their schemas; `/api/v1/metrics` is additive.
- The Phase 9 verification used the deterministic provider (no external AI), matching prior phases.
- `psycopg2` is intentionally not installed; Alembic uses psycopg v3 via the `+psycopg` URL rewrite.
- SQLite remains the dev/test backend; the same `Postgres*Store` classes run against it in tests (DB-agnostic stores), while production uses PostgreSQL/asyncpg.

## Phase 10 multi-worker stale-running recovery and closure (2026-08-15)

Phase 10 hardens the FASE 9 guarded execution for the multi-worker failure mode: a worker that dies or is killed while holding a request in `status='running'` would otherwise leave that request permanently stuck. FASE 10 adds stale-running detection and recovery plus a migration basis, and is the subject of an independent closure audit.

Integration changes:

- **`shared/config.py`.** New `stale_running_seconds: int = 300` setting; a request is stale when it has been `running` for longer than this threshold.
- **`domain/models.py` + `alembic/versions/0004_request_updated_at.py` (new).** `requests.updated_at` column (UTC, indexed basis for staleness), created via a reversible migration (downgrade drops only the column). `RequestRecord` sets `updated_at` on creation and every transition.
- **`application/service.py`.** New `recover_stale_running()` batch method (marks every request `running` older than the threshold as `failed` with the deterministic `_STALE_RUNNING_RESULT`, returns the recovered count, commits) and `_recover_stale_request()` invoked from the guarded `execute()` path: when the guarded transition fails (`rowcount == 0`) and the current status is `running` and stale, the request is recovered to `failed` and the commit persists it. A fresh (non-stale) `running` request is returned unchanged, never falsely recovered.
- **`api/app.py`.** The app lifespan calls `recover_stale_running()` once at startup so any orphaned `running` request left by a previous worker is cleaned up on boot.
- **Tests.** `tests/unit/test_service.py` adds stale detection/marking/recovery tests (including a persistence assertion that the recovered `failed` row is committed and reloadable); `tests/unit/test_service_concurrency.py` keeps the execution-guard/idempotency coverage.

Stale-recovery bug found during the closure audit and fixed:

- The `execute()` recovery branch called `_recover_stale_request()` but did not `await self.session.commit()`, so the `failed` transition was rolled back and never persisted. Fixed by committing right after a successful recovery. `tests/unit/test_service.py::test_execute_recovers_stale_running_request` now asserts the persisted row is `failed` with the recovery result, and the fix was re-verified against real PostgreSQL (see below).

Validation evidence (closure audit, Windows, Python 3.13, project root):

- **Gates:** `pytest --cov=src --cov-report=term-missing`: **207 passed, 92%** (1491 statements); `mypy src`: **PASS (43 source files)**; `ruff check .`: **All checks passed!** (one FASE 10 lint finding fixed: import-block formatting in `alembic/versions/0004_request_updated_at.py`). `ruff format --check` reports pre-existing formatting differences only in `0002`/`0003` migrations; CI does not run `ruff format`, so this is informational only.
- **Alembic:** full `upgrade head → downgrade base → upgrade head` cycle on SQLite reaches `0004_request_updated_at`; idempotent re-run is a no-op. Applied against a real PostgreSQL 16 (`0001 → 0002 → 0003 → 0004`), head = `0004_request_updated_at`.
- **PostgreSQL real: VERIFIED.** Tables `requests`, `sessions`, `memory_items`, `audit_events`; `requests` includes `updated_at`.
- **Stale recovery on real PG: VERIFIED.** A request forced to `running` with an old `updated_at` was read back as `running` via raw SQL, `execute()` returned `failed`, and a **new separate connection** read `status='failed'` with the recovery result — proving the fix is committed and persisted. 0 stuck `running` after recovery.
- **Multi-worker (`uvicorn --workers 2`) on real PG: ALL PASS.** 8 concurrent same-idempotency-key messages → exactly 1 logical request, 0 duplicates, 0 HTTP 500; 10 concurrent distinct messages → all `completed`, 0 stuck `running`; 8 concurrent messages on a brand-new session → single session, all `completed`, no `UniqueViolation`; 6 concurrent `execute()` on one accepted request → statuses `{completed, running}` and the workflow ran exactly once, idempotent re-execute returns `completed`.
- **Consistency on PG:** after the multi-worker run `requests` = 21 completed, 0 `running`, 0 `failed`; `memory_items` == completed requests (21 == 21) — no lost writes, no duplicates.
- **Restart persistence: VERIFIED.** After `docker restart` of the app, a pre-restart completed request is still `completed` with its result via the API; `memory_items` (21) and `audit_events` (252) survive.
- **Audit isolation: VERIFIED.** user-b audit query returns 0 events while user-a has 252; no cross-user leakage.
- **Observability: VERIFIED.** `/api/v1/observability/audit`, `/api/v1/observability/metrics`, and `/api/v1/metrics` require Bearer (401 without); counters updated; correlation_id echoed end-to-end.
- **Security: VERIFIED.** JWT Bearer enforced; production guard refuses default JWT secret and SQLite; Docker image runs as non-root `app` user with HEALTHCHECK; `.dockerignore` excludes caches/secrets/docs/tests; no real secrets in versionable files. `.env.example` documents placeholders only (it predates `PERSISTENT_STORES`/`STALE_RUNNING_SECONDS`; the canonical reference for the production environment contract remains `shared/config.py`).
- **Docker: VERIFIED.** Image `personal-ai-secretary:f10` built; multi-worker container reached **healthy**; entrypoint ran `alembic upgrade head` before uvicorn; no startup errors; HTTP smoke 21/21 via the concurrency suite.
- **CI/CD.** `.github/workflows/ci.yml` runs `pytest --cov=src --cov-report=term-missing`, `mypy src`, and `ruff check .` on Python 3.13. **CI configuration: VERIFIED BY INSPECTION; CI execution: UNVERIFIED** — GitHub Actions cannot run from this Windows environment; the exact commands were reproduced locally.

Non-changes and limitations (documented, no fake work):

- Metrics are in-process per-worker counters (shared audit/memory are DB-backed via `persistent_stores=true`); cross-worker aggregation and Prometheus/OTLP export remain FASE 11 territory and were not implemented.
- No Kubernetes, Redis, Kafka, OTLP, caching, RAG persistence, new providers, or scaling changes were introduced. FASE 11 was NOT started.
- The stale-recovery threshold is a static configuration default; no per-request lease/keepalive was added (that is future work, not FASE 10 scope).

## FASE 11 multi-worker metric aggregation and production readiness (2026-08-15)

FASE 11 starts with a read-only audit (PHASE 11 AUDIT REPORT delivered separately) and then implements the minimal scope approved by the operator: shared multi-worker metric aggregation backed by PostgreSQL, an infrastructure readiness probe, and the missing security/performance test layers. Decisions taken: (a) metrics aggregation via a DB-backed sink (not per-replica Prometheus scraping), (b) the inert compliance stage (F-04) is deferred to a later phase, and (c) OpenTelemetry remains reserved (the `opentelemetry-*` deps and `otel_enabled` flag are explicitly documented as reserved, not activated).

Integration changes:

- **`domain/models.py` + `alembic/versions/0005_metric_records.py` (new).** `metric_records` table keyed by `(worker_id, metric_name)` with `metric_type` (`counter`|`gauge`), `value`, `updated_at`, and unique constraint `uq_metric_records_worker_name`. Reversible migration (downgrade drops the table). `MetricRecord` is also created by `Base.metadata.create_all` for dev/test.
- **`observability/metrics.py`.** New `MetricSink` protocol (`flush(metrics)` / `snapshot()`) and `InMemoryMetricSink` (aggregates within the current process; used when `persistent_stores=false` so dev/tests behave as before). `render_prometheus_snapshot(counters, durations)` added; `render_prometheus_text(metrics)` now delegates to it.
- **`infrastructure/stores.py`.** New `PostgresMetricSink`: each flush replaces this worker's rows (delete + insert under a per-sink asyncio lock, DB-agnostic) and prunes rows with `updated_at` older than the retention window. `snapshot()` SUMs the counters of live workers (rows refreshed inside the window) and, for duration gauges, keeps the most recent observation. A restarted worker gets a fresh `worker_id`, so its old rows expire within the retention window (documented transient over-count, bounded by retention).
- **`observability/observer.py`.** `Observability` gains an optional `metrics_sink` plus `flush_metrics()` and `metrics_snapshot()` (flush-then-read-aggregate). Existing two-arg constructions still work (`metrics_sink=None` falls back to the process metrics).
- **`infrastructure/observability.py`.** `init_observability()` wires `PostgresMetricSink(get_session_factory(), retention_seconds=settings.metrics_retention_seconds)` when `persistent_stores=true`, else `InMemoryMetricSink`.
- **`shared/config.py`.** New `metrics_flush_seconds: int = 10` and `metrics_retention_seconds: int = 60`; `otel_enabled` is documented as reserved for a future phase (no behavior change).
- **`api/app.py`.** Lifespan starts a background `_metrics_flush_loop` that flushes every `metrics_flush_seconds` and a final flush on shutdown (before the engine is disposed). `/api/v1/observability/metrics` and `/api/v1/metrics` now return the aggregate snapshot (flushing the serving worker first). `/api/v1/health/ready` now also checks DB connectivity (`SELECT 1`) and reports `"database": "ok" | "unavailable"`, degrading to 503 when the provider or the database is unavailable.
- **Tests.** `tests/unit/test_metric_sink.py` (aggregation across workers, gauge latest-wins, stale pruning, reconnect persistence, in-memory fallback, facade flush-then-read); `tests/security/test_security_basics.py` (JWT without `exp` rejected, expired rejected, cross-user request/audit isolation, audit never leaks `Authorization`); `tests/performance/test_smoke_performance.py` (p95 request-cycle bound). `tests/integration/test_migrations.py` and `test_health.py` extended for `metric_records` and the readiness `database` field. This satisfies the previously-empty `tests/security` and `tests/performance` layers required by `tests/README.md`.

Validation evidence (Windows, Python 3.13, project root):

- **Gates:** `pytest --cov=src --cov-report=term-missing`: **220 passed, 92%** (1591 statements); `mypy src`: **PASS (43 source files)**; `ruff check .`: **All checks passed!**
- **Alembic:** head reaches `0005_metric_records`; full upgrade/downgrade cycle and migration tests pass on SQLite.
- **PostgreSQL real (PostgreSQL 16 via Docker, dedicated `f11net`/`f11-pg`): VERIFIED.** All six tables present (`requests`, `sessions`, `memory_items`, `audit_events`, `metric_records`, `alembic_version`); migration `0005` applied.
- **Multi-replica aggregation (two independent containers `f11-replica-a`/`f11-replica-b`, separate processes, shared PG): VERIFIED.** One request per replica produced two rows for `requests_total` (different `worker_id`s); `/api/v1/observability/metrics` on replica A and `/api/v1/metrics` on replica B both reported the aggregate `requests_total 2`, `requests_completed 2`, `provider_executions 2`, `evaluation_passes 2`. Duration gauges report the most recent observation. Shared audit events are readable from both replicas.
- **Idempotency across replicas: VERIFIED.** Same session + same key replayed through the other replica returns 200 without a duplicate request row; the same key against a different session returns 409 (`"Idempotency-Key is already associated with another session"`).
- **Restart persistence and staleness: VERIFIED.** After `docker restart` of one replica the aggregate remains correct; immediately after restart the old worker's rows are still counted within the retention window (documented transient), and after the retention window they are pruned so the aggregate converges to the live workers only (`requests_total` 2 → 1 after retention, consistent on both replicas).
- **Readiness: VERIFIED.** `/api/v1/health/ready` on both replicas returns `200` with `"status":"ready"` and `"database":"ok"`.
- **CI/CD.** `.github/workflows/ci.yml` unchanged (the added test files run under the existing `pytest --cov=src` step). **CI configuration: VERIFIED BY INSPECTION; CI execution: UNVERIFIED** — GitHub Actions cannot run from this Windows environment; the exact commands were reproduced locally.

Documented limitations (no fake work):

- Metric counters are summed across live workers (fresh within the retention window); a restarted worker's pre-restart counts transiently double-count until its old rows expire (bounded by `metrics_retention_seconds`). This is observability data, not a correctness boundary.
- OpenTelemetry/OTLP, Redis, Kubernetes, caching, RAG persistence, new providers, and the compliance stage (F-04) remain explicitly reserved/deferred. The `opentelemetry-*` runtime deps and `otel_enabled` flag are documented as reserved and must not be activated.
- F-04 (inert compliance stage) is documented as a known gap to be addressed in a later phase; no workflow behavior was changed.

## FASE 12A in-process OpenTelemetry tracing with optional OTLP export (2026-08-15)

FASE 12A implements the reserved OpenTelemetry observability as in-process tracing. Spans are always captured; when `OTEL_EXPORTER_ENDPOINT` points to an OTLP/HTTP collector, completed spans are exported to it; otherwise they are retained in an in-memory buffer and **no network is used** (the default, zero-configuration path). This coexists with the Phase 7/11 metrics and audit observability — nothing existing was replaced or changed in behavior.

Integration changes (strictly additive):

- **`pyproject.toml`.** The only new runtime dependency is `opentelemetry-exporter-otlp-proto-http>=1.39,<2` (the `opentelemetry-api`/`opentelemetry-sdk` deps were already declared since Phase 7). The exporter is imported lazily and only used when an endpoint is configured.
- **`shared/config.py`.** New `otel_exporter_endpoint: str | None = None` (env `OTEL_EXPORTER_ENDPOINT`). `otel_enabled: bool = True` remains; the "reserved/unused" comment was updated to describe the now-active tracing.
- **`observability/tracing.py` (new).** `init_tracing(enabled, exporter_endpoint)`, `shutdown_tracing()`, `start_span(name, attributes)` context manager, `set_span_correlation(correlation_id)`, `mark_span_error(exception)`, `get_recorded_spans()`, `clear_recorded_spans()`. Thread-safe and idempotent (re-init shuts down the previous provider). Disabled mode installs `NoOpTracerProvider` (nothing recorded, no network). No endpoint → `SimpleSpanProcessor(InMemorySpanExporter)`; endpoint set → `BatchSpanProcessor(OTLPSpanExporter)` via lazy import. `_build_otlp_exporter` appends `/v1/traces` when the endpoint does not already include it, so a bare collector base URL works.
- **`api/app.py`.** Lifespan startup calls `init_tracing(settings.otel_enabled, settings.otel_exporter_endpoint)`; shutdown calls `shutdown_tracing()` (flushing any pending OTLP batches) before the engine is disposed.
- **`workflow/engine.py`.** Root span `workflow.run` (attributes: `request_id`, `session_id`, `user_id`, `risk_level`, `correlation_id`, `outcome`); `correlation_id` is also set as an attribute on every stage span. Stage spans (`planner`, `approval`, `research`, `execution`, `reviewer`, `evaluation`, `compliance`, `completion`) wrap the corresponding executions with an `outcome` attribute and only appear when that stage actually runs (e.g. an approval-blocked run exports `planner` + `approval`). Exceptions mark the current span as ERROR.
- **`agents/builtin.py`.** `ExecutionAgent` wraps the provider call in `provider.run` (attributes: `provider`, `stage`, `correlation_id`, `outcome`) and the tool execution in `tool.run` (attributes: `name`, `stage`, `correlation_id`, `outcome`). No tool arguments, prompt content, Authorization, or JWT values are ever placed on spans.
- **Tests.** `tests/unit/test_tracing.py` covers the in-memory exporter, span hierarchy, attributes/correlation, error marking, clear/shutdown/idempotency, disabled no-op, auto-init, config defaults, and the OTLP path (lazy import + endpoint handling + real exporter construction).

Validation evidence (Windows, Python 3.13, project root):

- **Gates:** `pytest --cov=src --cov-report=term-missing`: **233 passed, 92%** (1720 statements); `mypy src`: **PASS (44 source files)**; `ruff check .`: **All checks passed!**
- **Alembic:** unchanged head `0005_metric_records`; upgrade/downgrade/idempotent re-upgrade cycle PASS on SQLite.
- **PostgreSQL 16 real (Docker): VERIFIED.** Migrations `0001→0005`; `alembic_version = 0005_metric_records`; tables and `metric_records` constraints verified; persistence probe PASS; 0 blocked/waiting queries.
- **Docker: VERIFIED.** Image `personal-ai-secretary:f12a` built from the current tree; container healthy; entrypoint migrations idempotent; HTTP smoke (message `completed`, metrics, request fetch, Prometheus endpoint, health) all 200. The no-endpoint container made **zero** OTLP/export calls (no exporter logs), confirming the default in-memory path.
- **OTLP local end-to-end (OpenTelemetry Collector via Docker): VERIFIED.** App on `f12a-net` with `OTEL_EXPORTER_ENDPOINT=http://f12a-collector:4318`; a real message exported **10 spans** to the collector (`workflow.run` root + `planner`/`approval`/`research`/`execution`/`reviewer`/`evaluation`/`compliance`/`completion` + `provider.run`). Root span carries `request_id`, `session_id`, `user_id`, `risk_level`, `correlation_id`, `outcome=completed`; stage/provider spans carry their `outcome`. **No sensitive attributes** (no prompts, no tool args, no Authorization/JWT) appear in the exported payload.
- **CI execution: UNVERIFIED.** GitHub Actions cannot run from this Windows environment; the workflow commands were reproduced locally (233 tests, mypy 44 files, ruff).

Documented limitations (no fake work):

- OTLP export to an **external/cloud** collector (the app's own running instance sending to a remote endpoint) was not exercised; the local collector end-to-end plus unit tests cover the exporter wiring. All spans are recorded in-process regardless of export configuration.
- No auto-instrumentation, no traces on the HTTP request layer (the root span is the workflow execution), no span-to-metrics correlation beyond `correlation_id`. Redis, Kubernetes, caching, RAG persistence, and the compliance stage (F-04) remain deferred; **FASE 12 was NOT implemented beyond 12A**. FASE 12B–12G are DEFINED (not implemented) in `docs/PHASE-12-ROADMAP.md`; FASE 13+ was NOT initiated.

> Note: the "no HTTP request-layer traces" and "no span-to-metrics correlation" limitations of 12A were resolved by FASE 12B and FASE 12C (sections below); auto-instrumentation remains reserved for FASE 13+.

## FASE 12B HTTP request-layer tracing (2026-08-16)

FASE 12B closes the 12A limitation "no traces on the HTTP request layer": every API request is now rooted in an `http.request` span so the request→workflow trace tree is complete end-to-end. The `workflow.run` span recorded downstream becomes a child automatically via the current-span context.

Integration changes (strictly additive):

- **`observability/tracing.py`.** New `http_request_attributes(method, route)` builds the non-sensitive attribute set for the request-layer span: only `http.method` and the matched route **template** (e.g. `/api/v1/requests/{request_id}`) are recorded; the raw path is never used because it may embed user data.
- **`api/app.py`.** The existing `correlation_middleware` now starts an `http.request` span, sets `correlation_id` on it, and after the response adds `http.route` (the route template resolved from `request.scope`) and `http.status_code`. Unhandled exceptions mark the span as ERROR and re-raise, preserving the existing 401/404/409/503 exception paths.
- **Tests.** `tests/integration/test_request_tracing.py` (new) covers the request-layer span attributes (method/route/status), route-template-not-raw-path (session UUID never appears), the `http.request → workflow.run` parent/child relationship, correlation id echoing, and ERROR marking on an unhandled 500.

Validation evidence (Windows, Python 3.13, project root):

- Request-layer span exported with `http.method`, `http.route=/api/v1/requests/{request_id}/execute` (template, not the raw path with the request UUID), `http.status_code`, and `correlation_id`; `workflow.run` + all 8 stage spans + `provider.run` are children of `http.request` (same trace id, parent span id = the request span).
- No sensitive attributes (Authorization/JWT, prompts, tool args) appear in the exported payload.

## FASE 12C trace ↔ metrics ↔ audit correlation (2026-08-16)

FASE 12C closes the 12A limitation "no span-to-metrics correlation beyond correlation_id": audit events and duration gauges now carry `trace_id`/`span_id`, so a single request can be walked across logs, spans, metrics and audit.

Integration changes (strictly additive, no schema/migration):

- **`observability/tracing.py`.** New `get_current_trace_ids()` returns the current span's `trace_id`/`span_id` (empty when no recording span is active, e.g. disabled tracing or plain unit tests).
- **`observability/observer.py`.** `emit()` merges `trace_id`/`span_id` into the audit event metadata before sanitization; `record_duration()` records which trace produced the latest observation; new `duration_trace_ids()` exposes the in-memory correlation.
- **`observability/metrics.py`.** `record_duration(name, seconds, *, trace_id=None)` and `duration_trace_snapshot()` keep the trace association alongside each duration gauge.
- **`domain/contracts.py`.** `MetricsSnapshot` gains the optional additive field `duration_trace_ids: dict[str, str]` (default empty).
- **`api/app.py`.** `GET /api/v1/observability/metrics` now returns `duration_trace_ids` alongside the counters/durations. The audit details column is JSON/JSONB, so no migration is required and the head stays `0005_metric_records`.
- **Tests.** `tests/unit/test_observability.py` covers trace/span ids present in audit events when a span is active, duration gauges linked to a trace id, and absence of trace ids when no span is active.

Validation evidence (Windows, Python 3.13, project root):

- `GET /api/v1/observability/audit` events carry `trace_id=f6173c97343a58e08888870f1ef0e0f2` / `span_id=...`, matching the same trace exported to the collector for that request; `GET /api/v1/observability/metrics` returns `duration_trace_ids` for `research`/`provider`/`evaluation`/`workflow` pointing at the same trace.

## FASE 12D OTLP export hardening (2026-08-16)

FASE 12D makes OTLP export production-ready (configurable timeout, optional static headers, compression, batch knobs) and re-verifies export against the local collector. External/cloud export remains environment-limited UNVERIFIED (no external endpoint available in this environment).

Integration changes (strictly additive, no new dependency):

- **`shared/config.py`.** New settings (env names in parentheses): `otel_export_timeout_seconds: float = 10.0` (`OTEL_EXPORT_TIMEOUT_SECONDS`), `otel_export_headers: str | None = None` (`OTEL_EXPORT_HEADERS`, a JSON object of static headers for external/cloud collectors), `otel_export_compression: Literal["gzip","none"] = "gzip"` (`OTEL_EXPORT_COMPRESSION`), `otel_export_batch_schedule_seconds: float = 5.0` (`OTEL_EXPORT_BATCH_SCHEDULE_SECONDS`), `otel_export_batch_max_queue_size: int = 2048` (`OTEL_EXPORT_BATCH_MAX_QUEUE_SIZE`), `otel_export_batch_max_export_batch_size: int = 512` (`OTEL_EXPORT_BATCH_MAX_EXPORT_BATCH_SIZE`).
- **`observability/tracing.py`.** `_build_otlp_exporter` now passes `timeout`, parsed `headers`, and the mapped `Compression` enum to `OTLPSpanExporter`; `init_tracing` configures the `BatchSpanProcessor` from the batch settings. `_parse_export_headers` validates that the header value is a JSON object of string values and is never logged or placed on spans. The default no-endpoint in-memory path is unchanged.
- **`.env.example`.** Documented the new OTLP export settings with the security note (headers read from env, never logged, never on spans).
- **Tests.** `tests/unit/test_tracing.py` covers the new settings defaults, header parsing (default/valid/invalid), and exporter construction honoring timeout/headers/compression.

Validation evidence (Windows, Python 3.13, project root):

- Local OTLP collector e2e re-verified: the `f12bcd` app exported the full `http.request`-rooted trace tree to the collector via the hardened exporter; the no-endpoint container made **zero** OTLP calls (in-memory path unchanged).
- External/cloud export: **UNVERIFIED (environment-limited)** — no external OTLP endpoint is reachable from this environment; exporter wiring is covered by unit tests and the local e2e.

## FASE 12E tracing configuration governance (2026-08-16)

FASE 12E gives production control over trace volume and standardizes span identity via configurable sampling, service resource attributes, and the worker id.

Integration changes (strictly additive, no new dependency, no migration):

- **`shared/config.py`.** New settings: `otel_sampling_ratio: float = 1.0` (`OTEL_SAMPLING_RATIO`, parent-based; 0.0 disables sampling, in-between samples by trace id), `otel_service_version: str = "1.0.0"` (`OTEL_SERVICE_VERSION`), `otel_worker_id: str | None = None` (`OTEL_WORKER_ID`, auto-generated per process when unset).
- **`observability/tracing.py`.** `_sampler_for_ratio` maps the ratio to `AlwaysOn` (≥1), `AlwaysOff` (≤0), or `ParentBased(TraceIdRatioBased)`; `_resolve_worker_id` provides the stable per-process auto id; `init_tracing` builds a `Resource` with `service.name`, `service.version`, `deployment.environment` (from `app_env`), and `service.instance.id` (worker id). New `get_service_instance_id()` exposes the worker id. Default behavior (ratio 1, auto worker id) is identical to 12A.
- **Tests.** `tests/unit/test_tracing.py` covers ratio 0/1/default (in-memory), no-op mode, resource attributes, configured and auto worker id stability, and the sampler endpoints.

## FASE 12F multi-worker trace verification and trace propagation (2026-08-16)

FASE 12F proves each worker exports its own traces to a shared collector and adds optional W3C trace context propagation across the request boundary.

Integration changes (strictly additive, no new dependency, no migration):

- **`observability/tracing.py`.** `extract_traceparent(header)` parses a W3C `traceparent` into an OpenTelemetry context (returns `None` for absent/malformed headers); `get_traceparent_header()` builds a `traceparent` from the current span for outbound propagation; `start_span` accepts `parent_context` so a span can be rooted in a remote trace.
- **`api/app.py`.** The `http.request` span is now parented by the inbound `traceparent` header when valid; malformed or absent headers are ignored and the span stays a root.
- **`providers/ollama.py`, `providers/remote.py`.** Outbound provider calls send the current `traceparent` header when a valid span is active (no header otherwise).
- **Tests.** `tests/unit/test_tracing.py` covers header extraction (valid round-trip, absent/malformed, propagator exception), outbound header format, and `start_span` parenting; `tests/integration/test_request_tracing.py` covers valid/malformed inbound headers over the API.

Validation evidence (Windows, Python 3.13, project root, Docker):

- **Multi-worker collector e2e: VERIFIED.** Two app replicas (`f12efg-app-1`/`f12efg-app-2`, `OTEL_WORKER_ID=worker-one`/`worker-two`, shared PG16 + shared OTLP collector) each exported their full `http.request`-rooted workflow trace tree; the collector received spans with `service.instance.id = worker-one` AND `worker-two`, `service.version=1.0.0`, `deployment.environment=development`. A request carrying `traceparent=00-aaaa…-bbbb…-01` produced an `http.request` span exported with `Trace ID aaaa…` / `Parent ID bbbb…` (inbound propagation e2e). Audit `trace_id` round-tripped: an audit event trace id appeared 11× in the collector's exported spans.

## FASE 12G observability consolidation and phase deliverables (2026-08-16)

FASE 12G standardizes the span attribute contract so the MASTER CLOSURE AUDIT has a single coherent reference. No new features; consolidation + documentation + final gate evidence.

Integration changes (strictly additive):

- **`observability/tracing.py`.** Consolidated attribute-name constants: `ATTRIBUTE_CORRELATION_ID`, `ATTRIBUTE_OUTCOME`, `ATTRIBUTE_HTTP_METHOD`, `ATTRIBUTE_HTTP_ROUTE`, `ATTRIBUTE_HTTP_STATUS_CODE`, `ATTRIBUTE_REQUEST_ID`, `ATTRIBUTE_SESSION_ID`, `ATTRIBUTE_USER_ID`, `ATTRIBUTE_RISK_LEVEL`, `ATTRIBUTE_PROVIDER`, `ATTRIBUTE_STAGE`, `ATTRIBUTE_TOOL_NAME`, plus resource constants (`RESOURCE_SERVICE_NAME`, `RESOURCE_SERVICE_VERSION`, `RESOURCE_DEPLOYMENT_ENVIRONMENT`, `RESOURCE_SERVICE_INSTANCE_ID`). All are non-sensitive: method/route template/status code, ids, risk level, stage, outcome, provider/tool names only.
- **`api/app.py`, `workflow/engine.py`, `agents/builtin.py`.** All span attribute writes now use the consolidated constants (same literal values, so no behavioral change).
- **Tests.** `tests/integration/test_request_tracing.py` `test_consolidated_span_attribute_names_are_consistent` verifies request and workflow spans carry the constant attribute names end-to-end.

### Consolidated span attribute guide (FASE 12G)

| Span | Attributes |
|---|---|
| `http.request` | `http.method`, `http.route` (template, never raw path), `http.status_code`, `correlation_id` |
| `workflow.run` | `request_id`, `session_id`, `user_id`, `risk_level`, `correlation_id`, `outcome` |
| stage spans (`planner`/`approval`/`research`/`execution`/`reviewer`/`evaluation`/`compliance`/`completion`) | `correlation_id`, `outcome` |
| `provider.run` | `provider`, `stage`, `correlation_id`, `outcome` |
| `tool.run` | `name`, `stage`, `correlation_id`, `outcome` |

Resource attributes (all spans): `service.name=personal-ai-secretary`, `service.version` (default 1.0.0), `deployment.environment` (from `app_env`), `service.instance.id` (worker id). No span ever carries prompts, tool arguments, Authorization/JWT, or other secrets.

### FASE 12 BLOQUE 2 (12E+12F+12G) gate summary

- **pytest:** **269 passed** (baseline 248), **coverage 93%** (1853 statements / 137 missing), above the 12A baseline.
- **mypy:** PASS (44 source files). **ruff:** All checks passed! **Alembic:** full cycle `upgrade head → downgrade base → upgrade head → upgrade head` PASS; head unchanged `0005_metric_records` (no migration needed by 12E–12G).
- **PostgreSQL 16 real (Docker): VERIFIED** — migrations `0001→0005`, readiness `ready`/`ok`, audit events with `trace_id` read back, shared across two app replicas.
- **Docker: VERIFIED** — image `personal-ai-secretary:f12efg` built from the current tree; two replicas healthy; HTTP smoke PASS; no-endpoint path unchanged (in-memory, zero OTLP calls).
- **OTLP multi-worker e2e: VERIFIED** — see FASE 12F evidence.
- **Security:** no secrets in spans/audit/exported payloads; `OTEL_EXPORT_HEADERS` env-only, never logged; `traceparent` is a trace-context string only, validated and ignored when malformed.
- **CI execution:** UNVERIFIED (environment-limited, unchanged). **External/cloud OTLP export:** UNVERIFIED (environment-limited, unchanged).

## FASE 13 BLOQUE 1 — 13A + 13B + 13C (2026-08-16)

Authoritative definition: `docs/PHASE-13-ROADMAP.md` (sections 6.1–6.3). Three
independent, additive subphases: compliance enforcement (13A), DB-backed
user-scoped RAG evidence store (13B), and NVIDIA `remote` provider activation
(13C). No new runtime dependency, no CI change, no infrastructure change.

### 13A — F-04 compliance enforcement

Before: `ComplianceAgent` ran as a workflow stage but was inert — it only reacted
to a `policy_violation` context flag that no caller ever set, plus an empty-output
check, so the stage was a formality and F-04 was documented as deferred.

After: a deterministic, config-driven enforcement stage.

- **New `compliance/policy.py`.** `ProhibitedCommandsRule` reuses the existing
  `CRITICAL_KEYWORDS` from `application/risk.py` (`drop database`, `format disk`,
  `shutdown`, `erase`, `rm -rf`, `wipe`, …) and blocks any request that contains
  them — this is a hard block, independent of approval. `CredentialLeakageRule`
  detects credential-shaped output (`eyJ...` JWT and `://user:pass@` URL
  credentials). `DEFAULT_RULES`, `select_rules`, and `evaluate_policy()` keep the
  policy code-visible, deterministic, and additive.
- **`agents/builtin.py`.** `ComplianceAgent` now calls `evaluate_policy()` against
  the request input and produced output, preserves the `policy_violation` flag,
  and records metadata `rule_id` + `rule_reason`. It reads settings via
  `get_settings()` with constructor overrides (`rules`, `enabled`) for tests.
- **`workflow/engine.py`.** The `compliance` stage receives `user_input` in its
  context, emits a `compliance_rule` span attribute (new constant
  `ATTRIBUTE_COMPLIANCE_RULE` in `observability/tracing.py`) when a rule fires,
  records the audit event with `details={"rule_id": ...}`, and increments the
  `compliance_blocks`/`compliance_passes` counters.
- **Config.** `COMPLIANCE_ENABLED` (default true) and `COMPLIANCE_RULES` (default
  `"prohibited_commands,credential_leakage"`). Rules can be disabled via config;
  no migration.

Security: compliance blocks expose only the non-sensitive `rule_id`/short reason
in audit and spans — never the matched request text. Verified in
`tests/security/test_security_basics.py`.

### 13B — RAG persistence (DB-backed evidence store)

Before: `_service(db)` built `GovernedRetriever()` over an always-empty in-memory
corpus, so research never returned real evidence ("No additional context
available").

After: a persistent, user-scoped evidence corpus.

- **`rag/service.py`.** `Retriever` protocol became async with a `user_id`
  parameter (`retrieve(query, limit=5, user_id=None)`), following the FASE 9
  precedent (`MemoryStore`/`AuditStore` async). `GovernedRetriever.retrieve` is
  async; `EvidenceSource` dataclass added.
- **`domain/models.py`.** `EvidenceSourceRecord` (table `evidence_sources`):
  composite primary key `(user_id, source_id)`, uri/title/text, authority/score,
  created_at (indexed), optional expires_at.
- **`alembic/versions/0006_evidence_sources.py`.** Additive, reversible migration;
  head moves `0005_metric_records → 0006_evidence_sources`; downgrade drops only
  the new table. Indexes on `user_id` and `created_at`.
- **`infrastructure/stores.py`.** `PostgresEvidenceStore` implements the async
  `Retriever` protocol plus `add()` (upsert by source id) and `list_sources()`:
  deterministic scoring identical to the prior in-memory ranker, per-user
  scoping enforced at the query level, TTL expiry via `evidence_ttl_seconds`,
  evidence text = `title + uri + content`. (Method named `list_sources` to avoid
  colliding with the builtin `list` inside the class body.)
- **`infrastructure/rag.py`.** `init_retriever()`/`get_retriever()` wiring with an
  in-memory fallback when `persistent_stores` is false (dev/tests unchanged).
- **`api/app.py`.** `_service(db)` builds the DB-backed retriever through
  `get_retriever()`; new additive endpoints `POST /api/v1/evidence` (201,
  JWT-protected, validated via `EvidenceSourceCreate`, user-scoped) and
  `GET /api/v1/evidence` (lists the caller's sources; 503 when the persistent
  store is not active).
- **`domain/contracts.py`.** Additive `EvidenceSourceCreate`/`EvidenceSourceResponse`.
- **Config.** `EVIDENCE_TTL_SECONDS` (default 86400).

Security: query and ingestion are always scoped by `user_id` (no cross-user
leakage); oversized payloads rejected; evidence text is persisted but never
leaks into spans or audit details (audit records only `evidence_count`).
Verified in `tests/security/test_security_basics.py`.

### 13C — Remote NVIDIA provider activation

Before: `NVIDIAProvider` (`providers/remote.py`) was fully implemented but
`get_provider()` raised `RuntimeError` for `"remote"` and
`AVAILABLE_PROVIDER_MODES = ("deterministic", "local")` excluded it.

After: `remote` is selectable and guarded.

- **`providers/factory.py`.** `AVAILABLE_PROVIDER_MODES = ("deterministic",
  "local", "remote")`; `get_provider()` returns `NVIDIAProvider()` for
  `"remote"`; unknown modes still raise.
- **`shared/config.py`.** Production guard: when `app_env == "production"` and
  `ai_provider == "remote"` and `nvidia_api_key` is unset/empty → startup
  `ValidationError` (no credential-less production remote).
- **`.env.example`.** Documents `AI_PROVIDER=remote`, `NVIDIA_API_KEY`,
  `NVIDIA_BASE_URL`, `NVIDIA_MODEL`. The key is env-only, never logged, never in
  spans/audit (existing redaction + dedicated tests).

Security: `Authorization` header never appears in audit/spans (redaction tests
extended); outbound `traceparent` sent when a span is active (existing header
test extended); unit tests use monkeypatched httpx — no network calls.

### FASE 13 BLOQUE 1 gate summary

- **pytest:** **307 passed** (baseline 269), **coverage 94%** (≥93% target),
  no failures.
- **mypy:** PASS (46 source files). **ruff:** All checks passed! **Alembic:**
  full cycle `upgrade head → downgrade base → upgrade head` PASS; head now
  `0006_evidence_sources` (was `0005_metric_records`); migration 0006
  additive/reversible.
- **13A e2e over HTTP:** non-compliant request → `blocked` with `rule_id` in the
  message and audit details; compliant flow unchanged; compliance blocks even
  with approval granted (hard gate).
- **13B e2e over HTTP:** `POST /api/v1/evidence` → `GET /api/v1/evidence` →
  research retrieves the persisted source and reports `evidence_count` in the
  audit event; restart persistence covered by
  `test_evidence_store_survives_reconnect`; 503 when persistent stores off.
- **13C:** factory guard, production key guard, mocked generate path (bearer
  header + text), `health()` config/available states. Live NVIDIA call:
  **environment-limited UNVERIFIED** (no external credentials/network).
- **PostgreSQL 16 real (Docker): UNVERIFIED** — daemon unavailable at gate time;
  migration chain verified on SQLite, PG16 re-verification deferred.
- **Docker build/run: UNVERIFIED** — daemon unavailable.
- **Security:** no secrets in spans/audit/export; evidence content and matched
  request text never in audit; rule ids non-sensitive; NVIDIA key env-only with
  production guard.
- **CI execution:** UNVERIFIED (environment-limited, unchanged).

## FASE 13 BLOQUE 2 — 13D consolidation (2026-08-16)

13D consolidates 13A–13C without new features. Verification covered compliance
determinism and the absence of bypass routes, RAG/evidence user-scoping and
TTL contract, NVIDIA activation/guards/key handling, the full HTTP flow
(correlation/trace/audit/metrics coherence), the API contract (OpenAPI,
status codes, Bearer, no sensitive exposure, backward compatibility), the
alembic chain (no migration beyond `0006`), a security sweep, and the FASE 14+
protection gate (no Redis/Kafka/K8s/vector/providers/auto-instrumentation/CI
artifacts in the tree).

### 13D code consolidation (additive, tested)

- **`agents/builtin.py` (`ComplianceAgent._active_rules`).** The explicit
  `enabled=False` toggle now always wins over an injected rules tuple, so
  `COMPLIANCE_ENABLED=false` is unambiguous and stable and can never be
  re-enabled by a rules injection. Covered by
  `test_compliance_agent_disabled_overrides_injected_rules`.
- **`observability/tracing.py`.** New constant
  `ATTRIBUTE_EVIDENCE_COUNT = "evidence_count"` (non-sensitive integer count).
- **`workflow/engine.py`.** The research span now records `evidence_count`
  (mirroring the research audit detail) and a new `evidence_retrieved` counter
  is incremented by the number of retrieved evidence items, so audit, tracing
  and metrics tell the same story for evidence. Covered by
  `test_workflow_evidence_observability_coherence`.

### FASE 13 BLOQUE 2 gate summary

- **pytest:** **309 passed** (baseline 307), **coverage 94%** (2062 statements /
  116 missing), no failures, zero regressions FASES 2–13C.
- **mypy:** PASS (46 source files). **ruff:** All checks passed! **Alembic:**
  full cycle `upgrade head → downgrade base → upgrade head → upgrade head`
  PASS; head `0006_evidence_sources`; no migration added after 0006.
- **13D.1 compliance:** deterministic policy, centralized rules, consistent
  `rule_id`, hard-block uniform (approval cannot bypass), `COMPLIANCE_ENABLED=false`
  explicit, no alternative execution path (service always runs the workflow).
- **13D.2 RAG/evidence:** composite `(user_id, source_id)` PK, user-scoped
  ingest/list/retrieval, TTL expiry filtering, in-memory fallback when
  `persistent_stores=false`, evidence text never in spans/audit, `evidence_count`
  coherent across audit/trace/metrics.
- **13D.3 NVIDIA:** `AVAILABLE_PROVIDER_MODES` coherent, production key guard,
  key env-only and never in logs/audit/spans/errors/responses, deterministic
  timeout/error path, outbound `traceparent` intact, deterministic/local
  providers unaffected.
- **Security sweep:** grep for JWT/Authorization/Bearer/API keys/evidence/
  prompts in tracing/audit found no leak; existing security tests green.
- **FASE 14+ protection gate:** PASS — zero reserved artifacts in the tree.
- **PostgreSQL 16 real (Docker), Docker build/run, OTLP collector e2e:
  ENVIRONMENT-LIMITED UNVERIFIED** - Docker daemon unavailable at gate time.
- **Live NVIDIA call and GitHub Actions CI execution:** UNVERIFIED
  (environmental).

## FASE 14 — NOT IMPLEMENTED (reference)

FASE 14 is defined in `docs/PHASE-14-ROADMAP.md` (PRE-IMPLEMENTATION AUDIT +
MASTER PLAN, 2026-08-16). Subphases 14A (data retention/lifecycle), 14B
(operational hardening), 14C (CI/CD verification strengthening) and 14D
(consolidation) are **NOT IMPLEMENTED** in this tree. This section is a status
reference only; no FASE 14 code exists.
