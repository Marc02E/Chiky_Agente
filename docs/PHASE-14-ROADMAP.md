# PHASE 14 — OFFICIAL MASTER PLAN / PRE-IMPLEMENTATION ARCHITECTURE AUDIT

Status: **FASE 14 — CLOSED**
This document is the official definition of FASE 14 subphases and their
implementation blocks. It was produced from a READ-ONLY inspection of the real
tree (no code, migration, dependency, CI, or infrastructure changes). It is the
authoritative FASE 14 roadmap and supersedes the generic "RESERVED FOR FASE 13+"
labels found in earlier roadmaps. BLOQUE 1 (14A + 14B + 14C) has been implemented
and closed; 14D consolidation is complete.

Authoritative sources: `docs/PHASE-13-ROADMAP.md`,
`docs/PHASE-2-9-IMPLEMENTATION.md`, `PHASE-2-9-AUDIT.md` (root historical audit)
and `docs/PHASE-2-9-AUDIT.md` (FASE 13 MASTER CLOSURE AUDIT), `.github/workflows/ci.yml`,
`pyproject.toml`, `Dockerfile`, `docker-entrypoint.sh`, `.env.example`,
`alembic/versions/*`, and `src/personal_ai_secretary/**` (real implementation).
The real tree is the primary source; no document was assumed unchanged.

## 1. Executive Summary

FASE 13 is **CLOSED** (`PASS WITH ENVIRONMENT-LIMITED VERIFICATION`). Baseline
confirmed on the real tree: **309 passed / 94% coverage**, `mypy src` PASS,
`ruff check .` PASS, Alembic head `0007_evidence_retention_and_stale_index` (7
migrations `0001`-`0007`), no git repository, Docker daemon unavailable, no local
Redis/Kafka/OTel collector, `kubectl` client present with no cluster context,
`psql` absent, outbound internet connectivity verified (pypi.org:443,
github.com:443 reachable).

The pre-implementation audit of the seven previously reserved capabilities
(Redis, Kafka, Kubernetes, distributed caching, auto-instrumentation, external
CI/CD, additional scaling) reached a **no over-architecture** conclusion:

* Redis → FASE 15+ (scale-triggered reserve). The DB already provides
  cross-worker correctness (guarded `UPDATE ... status IN (...)` transitions,
  worker-id based metric aggregation in `metric_records`). No proven distributed
  state/lock/cache bottleneck exists today.
* Kafka → FASE 15+. No async consumer architecture, no ordering/replay/
  delivery requirement; the request path is synchronous by design.
* Kubernetes → FASE 15+. Single service, Docker deployment verified
  (non-root, HEALTHCHECK, migration entrypoint); no scaling evidence; large
  infrastructure investment.
* Distributed caching → FASE 15+. No proven bottleneck; cache invalidation,
  user isolation and consistency would add risk without measured benefit.
* Auto-instrumentation → FASE 15+ (explicitly NOT RECOMMENDED). FASE 12
  manual spans are tested and contract-stable; auto-instrumentation would
  duplicate spans, change contracts, add dependencies and overhead.
* External CI/CD → FASE 14 subphase (strengthening only). The existing
  `.github/workflows/ci.yml` runs quality gates only; adding a PostgreSQL 16
  service container and a Docker build/smoke job improves integration
  verification without runtime code risk. Environment-limited (requires GitHub).
* Additional scaling → FASE 15+. No throughput/latency baseline justifies it.

FASE 14 therefore focuses on the **real operational gaps** found by inspecting
the tree, not on reserved technologies:

* **14A — Data retention & lifecycle management.** `audit_events` grows
  unbounded (≈10–14 events per request, no retention); `memory_items` and
  `evidence_sources` rows with `expires_at` accumulate (read-time TTL filtering
  only, no pruning). Indexes: `audit_events.timestamp` is indexed (retention
  DELETE is index-backed); `memory_items.expires_at` and
  `evidence_sources.expires_at` are **not** indexed (conditional migration 0007).
* **14B — Operational hardening.** Configurable DB pool sizing
  (`db_pool_size`/`db_max_overflow`), graceful shutdown drain for in-flight
  requests, stale-running sweep interval knob, conditional index on
  `requests.status`/`updated_at` (the stale-recovery scan filters on
  `status == "running"` without an index).
* **14C — CI/CD verification strengthening.** Add a PostgreSQL 16 service
  container (integration tests) and a Docker build+smoke job to the existing
  workflow. Environment-limited.
* **14D — Consolidation.** Phase deliverables, documentation, final regression,
  MASTER CLOSURE AUDIT.

This run produced **documentation only** (`docs/PHASE-14-ROADMAP.md`).
CODE CHANGES = YES, MIGRATIONS = YES (0007), NEW DEPENDENCIES = NONE,
CI CHANGES = YES (workflow strengthening), INFRASTRUCTURE CHANGES = NONE.



## 2. Baseline (FASE 14 BASELINE — verified read-only, 2026-08-16)

| Gate | Result |
|---|---|
| `pytest --cov=src --cov-report=term-missing` | **309 passed** (20.66s), coverage **94%** (2062 statements / 116 missing) |
| `mypy src` | **PASS (46 source files)** |
| `ruff check .` | **All checks passed!** |
| Alembic | head **`0007_evidence_retention_and_stale_index`**; **7 migrations** (`0001`-`0007`) |
| PostgreSQL 16 real (Docker) | **ENVIRONMENT-LIMITED UNVERIFIED** — Docker daemon unavailable |
| Docker build/run | **ENVIRONMENT-LIMITED UNVERIFIED** (daemon unavailable) |
| Local OTel collector | **UNVERIFIED / absent** — no listener on `127.0.0.1:4318` |
| Redis | **absent** — no listener on `127.0.0.1:6379`; no `redis-cli` |
| Kafka | **absent** — no listener on `127.0.0.1:9092` |
| Kubernetes | **UNVERIFIED / absent** — `kubectl` v1.36.1 client present, **no cluster contexts**; no `psql` |
| External connectivity | **VERIFIED** — pypi.org:443 and github.com:443 reachable |
| Git | **No git repository** (workspace and project root) |
| GitHub Actions CI execution | **UNVERIFIED (environmental)** — cannot run from this Windows environment; workflow exists by inspection |

Baseline facts confirmed against the real tree: FASES 0–12 CLOSED; FASE 13
CLOSED; FASE 14 **IMPLEMENTED** (BLOQUE 1 CLOSED); FASE 15+ **NOT INITIATED**. Grep
for `redis|kafka|kubernetes|k8s|pgvector|vector|celery|auto-instrumentation` in
`src/` returns zero matches; providers are `deterministic`/`local`/`remote`;
migrations are exactly 0001–0007.

The baseline is **green**; no modification was made to achieve it.



## 3. Current Architecture (verified read-only against the real tree)

Real root: `src/personal_ai_secretary/` (46 source files). The tree is a single
FastAPI service with a governed multi-agent workflow and DB-backed persistence.

| Layer | Modules | Verified state |
|---|---|---|
| API | `api/app.py` | FastAPI; JWT Bearer on protected endpoints; correlation middleware with `http.request` span + W3C `traceparent`; `health/live`, `health/ready` (provider + DB probe), `providers`, `requests` (CRUD+execute), `sessions/messages`, `observability/audit`, `observability/metrics`, `/metrics` (Prometheus), `evidence` (POST/GET). |
| Application | `application/service.py`, `application/risk.py` | Guarded execute (`UPDATE ... status IN (...)`, single-winner across workers), user-scoped idempotency key, stale-running recovery, `send_message`/`history`; deterministic keyword risk classifier (LOW/MEDIUM/HIGH/CRITICAL). |
| Domain | `domain/contracts.py`, `domain/models.py` | Pydantic contracts (additive); SQLAlchemy `requests`, `sessions`, `memory_items`, `audit_events`, `metric_records`, `evidence_sources`. Indexes: `audit_events.timestamp` indexed (retention DELETE is index-backed); `memory_items.expires_at` and `evidence_sources.expires_at` **not indexed**; `requests.status`/`requests.updated_at` **not indexed**. |
| Workflow | `workflow/engine.py` | `GovernedWorkflow`: planner → approval → research → execution → reviewer → evaluation → compliance → completion; blocked/rejected/failed/completed; per-stage span + audit + metrics. |
| Agents | `agents/contracts.py`, `agents/builtin.py` | Planner, Research (RAG + memory), Execution (provider + tool), Reviewer (separation of duties), Compliance (deterministic, FASE 13A). |
| Evaluation | `evaluation/runtime.py`, `evaluation/gates.py` | Per-request `ReleaseGateEvaluator` (6 criteria) + release-level gates. |
| Memory | `memory/service.py` | `MemoryStore` protocol, `MemoryPolicy` TTL, `PostgresMemoryStore`. |
| RAG | `rag/service.py`, `infrastructure/rag.py` | `Retriever` protocol (async, user-scoped), `GovernedRetriever` (in-memory fallback), `PostgresEvidenceStore` (persistent, FASE 13B). |
| Providers | `providers/base.py`, `deterministic.py`, `ollama.py`, `remote.py`, `factory.py` | `AIProvider` protocol; deterministic/local/remote (`NVIDIA`, FASE 13C) active; production guard for remote without key. |
| Observability | `observability/tracing.py`, `metrics.py`, `audit.py`, `observer.py` | OTel in-process spans + optional OTLP export, sampling, W3C propagation, consolidated non-sensitive attributes; DB-backed metrics (worker-id keyed, retention 60s); redacted audit. |
| Infrastructure | `infrastructure/database.py`, `stores.py`, `memory.py`, `observability.py`, `rag.py` | Async SQLAlchemy engine (`pool_pre_ping=True`), `Postgres*` stores selected by `persistent_stores`. |
| Shared | `shared/config.py`, `auth.py`, `telemetry.py` | Settings with production guards (JWT secret, SQLite, NVIDIA key); JWT decode; correlation logging. |
| Config/Docker | `pyproject.toml`, `Dockerfile`, `docker-entrypoint.sh`, `.env.example`, `.dockerignore` | Python 3.13; FastAPI/asyncpg/psycopg/OTel; non-root `app` user; HEALTHCHECK (`health/live`); entrypoint runs `alembic upgrade head` then uvicorn (single process, no workers flag). |
| CI/CD | `.github/workflows/ci.yml` | `quality` job only: `pip install -e '.[dev]'`, `pytest --cov`, `mypy src`, `ruff check .` on ubuntu-latest / Python 3.13. Execution UNVERIFIED. |

Security boundary: Authentication (JWT) → Authorization (user scoping) →
Governance (approval + evaluation + compliance) → Execution (provider/tools) →
Observability (traces/metrics/audit, redacted).



## 4. Inventory of reserved capabilities (classification)

| Reserved capability | Analysis result | Destination |
|---|---|---|
| Redis | No distributed state/lock/cache need today; DB provides coordination and persistence | **FASE 15+** (scale-triggered) |
| Kafka | No async consumer, ordering, replay, or delivery requirement | **FASE 15+** |
| Kubernetes | Single service; Docker verified; no scale evidence | **FASE 15+** |
| Distributed caching | No proven bottleneck; invalidation/isolation complexity | **FASE 15+** |
| Auto-instrumentation | Would duplicate FASE 12 spans, risk contracts, add deps/overhead | **FASE 15+ (NOT RECOMMENDED)** |
| External CI/CD | Strengthens integration verification (PG16 service + Docker smoke) | **FASE 14 (14C)** |
| Additional scaling | No baseline justifies it | **FASE 15+** |

## 5. Redis assessment

- Real need for distributed state: **none demonstrated**. Cross-worker
  correctness is already DB-based: guarded status transitions
  (`UPDATE ... status IN ('accepted','blocked','rejected','failed')`) ensure a
  single winner; `metric_records` aggregates per-worker absolute counters
  (worker-id keyed, retention-window prune) without a broker.
- Candidate roles analyzed: cache, lock, coordination store, session store.
  - Cache: no measured bottleneck; DB-backed stores already persist; caching
    would add invalidation + user-isolation correctness burden.
  - Lock/coordination: the guarded `UPDATE` already serializes execution;
    idempotency uses a user-scoped unique key in DB.
  - Session store: sessions live in DB with user scoping; no shared-session
    need across the single service.
- Consistency risks (Redis failover, TTL drift, stale locks) and the operational
  dependency (new container/service, credentials, network exposure) outweigh
  current benefit.
- **Decision:** Redis is NOT in FASE 14. Moved to FASE 15+ as a scale-triggered
  candidate with explicit triggers (e.g. proven DB contention, multi-region,
  measured cache hit pressure).

## 6. Kafka assessment

- Event-streaming use case: **none today**. The request path is synchronous:
  create → execute → persist, all in-request. Audit events are written
  synchronously into the audit store; metrics are flushed to the DB-backed sink.
- Candidate events (audit, metrics, workflow, provider) were analyzed: all are
  already durably persisted in PostgreSQL with per-request correlation; there
  is no consumer that would benefit from decoupling, no ordering constraint
  beyond per-request sequencing (already guaranteed by the request lifecycle),
  no replay requirement, and no delivery-semantics need.
- Adding a broker would introduce durability/ordering/idempotency complexity
  (exactly-once, replay) with no current receiver.
- **Decision:** Kafka is NOT in FASE 14. Moved to FASE 15+ (trigger: real
  async/decoupling requirement, e.g. webhook fan-out, background processing,
  or multi-service integration).

## 7. Kubernetes assessment

- Container currently: single `python:3.13-slim` image, non-root `app` user,
  HEALTHCHECK on `health/live`, `docker-entrypoint.sh` runs `alembic upgrade
  head` then uvicorn (single process). `health/ready` probes provider + DB.
- Probes: liveness/readiness split already exists in the app
  (`health/live`/`health/ready`); no K8s manifests exist.
- Persistence: PostgreSQL is external; the app is stateless (stores live in DB),
  so K8s StatefulSet/PVC story is simple — but there is no replica-count
  evidence requiring it.
- Scaling: no throughput baseline; multi-worker correctness exists via DB
  guarded transitions and worker-id metrics (FASE 10/11/12F already verified
  multi-worker/replica behavior with plain Docker).
- Secrets/config: would move env secrets to K8s Secrets; value proposition is
  operational, not functional.
- **Decision:** Kubernetes is NOT in FASE 14. Moved to FASE 15+ (trigger: real
  deployment/replica/orchestration requirement or team-driven platform need).

## 8. Distributed caching assessment

- What is cacheable today: nothing measured as a hot path. Requests, sessions,
  memory, audit, metrics, and evidence are DB-backed and user-scoped; repeated
  identical reads are not a proven cost (request status/history lookups are
  low-frequency).
- TTL/invalidation: memory and evidence already use DB-level TTL semantics
  (expires_at + read-time filtering) — introducing a cache would duplicate
  that with consistency risk (stale evidence, user isolation on cache keys,
  cache stampede on cold start).
- Correctness over speed: the platform's guarantees (idempotency, guarded
  transitions, user scoping) live in the DB; a cache would not accelerate the
  dominant cost (provider latency).
- **Decision:** Distributed caching is NOT in FASE 14. Moved to FASE 15+
  (trigger: measured read amplification or latency baseline).

## 9. Auto-instrumentation assessment

- Current manual tracing (FASE 12A–12G): consolidated attribute constants,
  request-layer span, workflow stage spans, provider/tool spans, W3C
  propagation (inbound/outbound), resource attributes (service.name/version/environment/instance.id), sampling, OTLP export hardening, trace↔metrics↔audit
  correlation. Covered by dedicated tests (`test_request_tracing.py`).
- Auto-instrumentation (opentelemetry-instrumentation packages) would:
  - duplicate spans the manual code already produces (double nesting),
  - change the span/attribute contract the consolidated test enforces,
  - add runtime dependencies and per-request overhead,
  - require re-validation of redaction (instrumented frameworks may capture
    payloads/URLs the manual layer deliberately omits).
- Value added: convenience for HTTP/framework-level span capture already
  covered by the request-layer span.
- **Decision:** Auto-instrumentation is NOT in FASE 14 and is explicitly
  **NOT RECOMMENDED** for FASE 15+ unless the manual layer is removed — a
  high-risk change with no measured benefit. Reserved only as a documented
  non-goal.



## 10. CI/CD assessment

- Existing `.github/workflows/ci.yml` (verified by inspection): a single
  `quality` job on ubuntu-latest / Python 3.13 running `pip install -e '.[dev]'`,
  `pytest --cov=src --cov-report=term-missing`, `mypy src`, `ruff check .`.
  Execution **UNVERIFIED** from this Windows environment (and the repo has no
  git remote to push to).

- What is already covered: unit/integration tests on SQLite, coverage, mypy,
  ruff.

- What is missing for stronger verification: a PostgreSQL 16 service container
  for the `Postgres*` integration/`persistent_stores` tests
  (`tests/integration/test_persistent_backend.py`, `test_migrations.py`,
  `test_requests_api.py` persistent paths), and a Docker build + smoke job
  (image build, entrypoint migrations, HEALTHCHECK) to catch regressions the
  SQLite path cannot.

- Secrets needed: none for the workflow itself (no cloud deployment, no live
  NVIDIA calls — those remain environment-limited UNVERIFIED).

- **Decision:** include **14C** in FASE 14 as *workflow strengthening* only.
  It requires GitHub execution to verify (environment-limited; cannot be run
  locally — no git remote, no GitHub access).



## 11. Bottleneck analysis (real-tree evidence, no speculative optimization)

| Finding | Evidence in tree | Class |
|---|---|---|
| Audit table unbounded growth | `AuditEventRecord` has no retention/pruning; each request emits ≈10–14 events (planner/approval/research/execution×2-3/reviewer/evaluation/compliance/completed/request/memory) | **HIGH** (operational) |
| Expired memory rows accumulate | `MemoryPolicy` filters by `expires_at` at read time; no deletion job; `memory_items.expires_at` unindexed | **MEDIUM** |
| Expired evidence rows accumulate | `PostgresEvidenceStore.retrieve` filters `expires_at`; no pruning; `evidence_sources.expires_at` unindexed | **MEDIUM** |
| Stale-running scan unindexed | `recover_stale_running` selects all `status == "running"`; `requests.status`/`updated_at` unindexed | **LOW-MEDIUM** |
| Provider latency dominates | Synchronous `await generate` in the request path (deterministic/local/remote); p95 smoke bound exists | **INFORMATIONAL** (inherent) |
| DB pool sizing not configurable | `create_async_engine(database_url, pool_pre_ping=True)` — default pool, no knobs | **LOW** |
| Metrics bounded | `metric_records` retention 60s default, worker prune on flush | OK (informational) |
| Tracing overhead low | In-process spans; in-memory exporter when no OTLP endpoint | **LOW** |
| Cross-worker correctness already DB-based | Guarded transitions + worker-id metric aggregation (FASE 10/11) | OK (informational) |
| No throughput/p99 baseline | Only `test_smoke_performance.py` p95 bound | **INFORMATIONAL** (needed for FASE 14 performance model) |

14A (retention/cleanup) and 14B (pool config, graceful shutdown, index,
stale-sweep knob) target the HIGH/MEDIUM/LOW operational findings. No other
finding justifies new infrastructure.



## 12. Architecture target

The FASE 14 target preserves the existing topology and adds only lifecycle and
operational hardening — no new services:

```
Client
  ↓  (JWT Bearer, X-Correlation-ID, W3C traceparent)
API (FastAPI: requests/sessions/evidence/observability)
  ↓
Governed Workflow (planner → approval → research → execution → reviewer → evaluation → compliance → completion)
  ↓
Persistence (PostgreSQL: requests/sessions/memory/audit/metrics/evidence)
  ↓
Observability (spans → OTLP optional; metrics → metric_records; audit → audit_events)
```

Added by FASE 14 (each justified):
- **14A**: retention/cleanup sweep (audit + memory/evidence TTL pruning) as a
  config-driven background task in the app lifespan (or CLI command), with
  `audit_retention_seconds` and `cleanup_interval_seconds` settings.
- **14B**: `db_pool_size`/`db_max_overflow` settings; graceful shutdown drain in
  lifespan (`close_database` waits for in-flight requests); stale-running sweep
  interval knob; conditional index on `requests.status`/`updated_at`.
- **14C**: CI workflow strengthening (PG16 service container + Docker smoke job).
- **14D**: consolidation.

Not added (FASE 15+): Redis, Kafka, Kubernetes, distributed cache,
auto-instrumentation, additional providers, advanced RAG/vector, cloud/multi-region.



## 13. Subphase definitions

### 13.1 FASE 14A — Data retention & lifecycle management

1. **Nombre:** FASE 14A — Data retention & lifecycle management.
2. **Objetivo:** bound the growth of `audit_events`, `memory_items` and
   `evidence_sources` with config-driven retention and a pruning sweep.
3. **Problema que resuelve:** unbounded audit growth (≈10–14 events/request, no
   retention) and accumulated expired memory/evidence rows (read-time TTL
   filtering only, no deletion).
4. **Estado actual:** `audit_events.timestamp` indexed (delete is index-backed);
   `memory_items.expires_at`/`evidence_sources.expires_at` unindexed; no sweep.
5. **Cambios esperados:** settings `audit_retention_seconds`
   (default e.g. 30 days) and `cleanup_interval_seconds` (default e.g. 3600);
   a background pruning task in the app lifespan (stdlib `asyncio` — no new
   dependency) that deletes audit rows older than the window and expired
   memory/evidence rows; optional CLI entrypoint (`python -m ... cleanup`) for
   operator-driven runs; unit/integration tests for pruning + retention.
6. **Archivos potencialmente afectados:** `shared/config.py`, `.env.example`,
   `api/app.py` (lifespan), new `infrastructure/retention.py` (or
   `application/retention.py`), `infrastructure/stores.py` (pruning methods),
   tests (`tests/unit/test_retention.py`, `test_persistent_stores.py`,
   `test_requests/api.py`), docs.
7. **Dependencias:** none.
8. **Migraciones requeridas:** **CONDITIONAL 0007** — indexes on
   `memory_items.expires_at` and `evidence_sources.expires_at` IF the sweep
   query performance requires them (reversible; downgrade drops only the
   indexes). Created during implementation, never pre-created.
9. **Nuevas dependencias:** NONE (stdlib `asyncio` background task; avoids a
   scheduler dependency).
10. **Tests requeridos:** retention deletes old audit rows and keeps recent;
    expired memory/evidence rows pruned (and still filtered at read time);
    non-expired preserved; user isolation preserved (deletion is time-based,
    not user-scoped, but never touches other-user data); migration up/down if
    0007 is needed; lifespan task idempotent and non-fatal on error.
11. **Security gates:** no secrets introduced; sweep operates on timestamps
    only; audit pruning does not remove in-flight request attribution
    (deletion window excludes recent events); no new network exposure.
12. **Performance gates:** sweep is bounded (limit per run) and does not block
    the request path; p95 request bound unchanged.
13. **Docker/infrastructure gates:** image unchanged (no new deps); entrypoint
    unchanged; re-run Docker/PG16 when available (environment-limited).
14. **Regression gates:** full suite green; existing audit/metrics/memory/
    evidence tests unchanged.
15. **Riesgos:** LOW — deleting audit data reduces forensic depth (mitigated by
    configurable retention and a conservative default); sweep concurrency with
    writers (mitigated by bounded deletes and index-backed queries).
16. **Rollback strategy:** set `audit_retention_seconds=0`/disable sweep via
    config; revert the small diff; downgrade 0007 if applied (indexes only).
17. **Definition of Done:** growth bounded by config; sweep tested; docs
    updated; full suite green.
18. **Fuera de alcance:** archive/offload (15+); audit schema redesign;
    distributed retention (15+).

### 13.2 FASE 14B — Operational hardening

1. **Nombre:** FASE 14B — Operational hardening.
2. **Objetivo:** make the runtime resilient and configurable: DB pool sizing,
   graceful shutdown drain, stale-running sweep interval knob, and an index for
   the stale-recovery scan.
3. **Problema que resuelve:** fixed default DB pool, no drain on shutdown
   (in-flight requests interrupted → reliance on stale recovery), hard-coded
   stale-running threshold interval, and an unindexed `status == "running"`
   scan.
4. **Estado actual:** `get_engine()` uses defaults + `pool_pre_ping=True`;
   lifespan `finally` closes DB after cancelling the metric flush task; stale
   recovery uses `stale_running_seconds` (existing setting) but runs only at
   startup.
5. **Cambios esperados:** settings `db_pool_size`/`db_max_overflow` passed to
   `create_async_engine`; graceful shutdown drain in lifespan (wait up to a
   configurable bound for in-flight requests before `close_database`); periodic
   `recover_stale_running` loop (background task, interval knob) so stale
   requests recover without a restart; **CONDITIONAL migration 0007** index on
   `requests.status`/`requests.updated_at`; tests.
6. **Archivos potencialmente afectados:** `shared/config.py`, `.env.example`,
   `infrastructure/database.py`, `api/app.py` (lifespan), `application/service.py`
   (reuse `recover_stale_running`), `alembic/versions/0007_*` (conditional),
   tests (`test_database.py`, `test_service_concurrency.py`, `test_migrations.py`), docs.
7. **Dependencias:** none.
8. **Migraciones requeridas:** **CONDITIONAL 0007** (requests index) — merged
   with 14A's conditional 0007 if both are needed; reversible/idempotent.
9. **Nuevas dependencias:** NONE.
10. **Tests requeridos:** pool config applied; shutdown drain waits for
    in-flight then closes; periodic stale recovery marks stale running requests
    failed (concurrency test extended); migration up/down.
11. **Security gates:** drain must not extend shutdown indefinitely (bound);
    no new secrets; DB credentials unchanged.
12. **Performance gates:** no added latency on the request path; p95 bound kept.
13. **Docker/infrastructure gates:** image unchanged; entrypoint unchanged;
    multi-worker behavior re-verified when Docker available.
14. **Regression gates:** full suite green; stale-recovery/idempotency tests
    unchanged.
15. **Riesgos:** LOW-MEDIUM — drain timing (mitigated by bound + config);
    periodic sweeper competing with guarded transitions (mitigated by the
    existing single-winner `UPDATE`).
16. **Rollback strategy:** config defaults; revert diff; downgrade 0007.
17. **Definition of Done:** runtime hardening configurable and tested; docs
    updated; full suite green.
18. **Fuera de alcance:** auto-scaling, supervisor/orchestrator (15+), circuit
    breakers beyond provider timeouts.

### 13.3 FASE 14C — CI/CD verification strengthening

1. **Nombre:** FASE 14C — CI/CD verification strengthening.
2. **Objetivo:** strengthen `.github/workflows/ci.yml` so integration
   verification runs against real PostgreSQL 16 and a Docker build smoke, in
   addition to the current SQLite quality gates.
3. **Problema que resuelve:** `Postgres*` persistent-store tests
   (`tests/integration/test_persistent_backend.py`, migrations, evidence HTTP
   persistent path) currently run only against SQLite locally; the Docker
   deployment (entrypoint migrations, HEALTHCHECK) is never built in CI.
4. **Estado actual:** single `quality` job (SQLite) — see §10.
5. **Cambios esperados:** add a `postgres` service container (postgres:16) and
   a `integration` job running the suite with `DATABASE_URL` pointing at it
   (persistent stores on) + `pytest`; add a `docker` job building the image and
   running a smoke (entrypoint + `health/live`). Keep the existing `quality`
   job unchanged.
6. **Archivos potencialmente afectados:** `.github/workflows/ci.yml`, docs.
   No runtime code.
7. **Dependencias:** consumes 14A/14B (CI exercises their new surfaces) — merged
   within the same block.
8. **Migraciones requeridas:** NONE (CI applies existing 0001–0007).
9. **Nuevas dependencias:** NONE (GitHub service containers use the
   `postgres:16` image; no pip additions).
10. **Tests requeridos:** none new in-repo beyond 14A/14B tests; the workflow
    change is verified by GitHub execution (environment-limited; cannot be run
    locally — no git remote, no GitHub access).
11. **Security gates:** no secrets in the workflow (no live NVIDIA, no cloud);
    `DATABASE_URL` for the service container is ephemeral; no repo secrets
    added.
12. **Performance gates:** CI runtime bound not specified (GitHub-managed).
13. **Docker/infrastructure gates:** the `docker` job exercises build +
    entrypoint migrations + HEALTHCHECK; environment-limited UNVERIFIED locally.
14. **Regression gates:** workflow is additive; `quality` job unchanged.
15. **Riesgos:** LOW-MEDIUM — YAML cannot be executed locally before push
    (mitigated by minimal, well-understood additions); CI-only failures
    possible (mitigated by mirroring the local SQLite+persistent test matrix).
16. **Rollback strategy:** revert `.github/workflows/ci.yml` (single-file diff).
17. **Definition of Done:** workflow strengthened; CI execution documented as
    VERIFIED once it runs on GitHub (or UNVERIFIED/environment-limited).
18. **Fuera de alcance:** real CI/CD deployment pipelines, artifact publishing,
    cloud secrets, external CI providers.

### 13.4 FASE 14D — Consolidation and phase deliverables

1. **Nombre:** FASE 14D — Consolidation.
2. **Objetivo:** finalize FASE 14 docs, gate matrix, and MASTER CLOSURE AUDIT
   readiness.
3. **Problema que resuelve:** after 14A–14C the retention/hardening/CI surface
   needs to be documented as one coherent contract.
4. **Estado actual:** BLOQUE 1 (14A+14B+14C) implemented; each subphase has
   tests/evidence.
5. **Cambios esperados:** finalize `docs/PHASE-2-9-IMPLEMENTATION.md` FASE 14
   sections and `docs/PHASE-2-9-AUDIT.md` block/phase audits; this roadmap's
   status → IMPLEMENTED; full regression; Docker/PG16/CI evidence capture when
   available.
6. **Archivos potencialmente afectados:** docs only; no new features.
7. **Dependencias:** consumes 14A, 14B, 14C.
8. **Migraciones requeridas:** re-verify chain (0001–0007 if applied).
9. **Nuevas dependencias:** NONE.
10. **Tests requeridos:** full suite; cross-subphase integration; final
    security review.
11. **Security gates:** full redaction/guard review; no secrets in spans/audit/
    export.
12. **Performance gates:** final p95/p99 and retention-sweep latency
    assertions.
13. **Docker/infrastructure gates:** image build + PG16 + smoke when available;
    otherwise documented UNVERIFIED.
14. **Regression gates:** FASES 0–13 + 14A–14C green.
15. **Riesgos:** LOW — consolidation only.
16. **Rollback strategy:** N/A (docs/consolidation only).
17. **Definition of Done:** FASE 14 gate matrix green and documented; ready for
    MASTER CLOSURE AUDIT.
18. **Fuera de alcance:** any new feature; FASE 15+ items.



## 14. Dependency graph

```
14A ──┐
      ├──> 14C ──> 14D
14B ──┘
```

- **14A** and **14B** are independent (different config/surfaces; both may
  share the conditional migration 0007, merged as one migration during
  implementation).
- **14C** depends on 14A/14B being merged (CI exercises their surfaces).
- **14D** consumes 14A/14B/14C.
- No artificial sequence beyond the above; 14A and 14B are parallelizable
  within BLOQUE 1.

## 15. Block strategy

Per the agreed strategy (~3 subphases per block when technically compatible):

### BLOQUE 1: 14A + 14B + 14C
Retention/lifecycle (14A), operational hardening (14B), and CI strengthening
(14C). 14A/14B are independent code/config subphases; 14C is a `.github`-only
change merged after them. Coherent as one block (implementation, testing,
smoke, one closure audit).

### BLOQUE 2: 14D
Consolidation/closure (mirrors the FASE 13 BLOQUE 2 pattern).

Blocking rule (same as prior phases): if any subphase in BLOQUE 1 reveals an
architectural, security, migration, or infrastructure risk that makes
continuing unsafe, stop the block and run an isolated closure audit for the
completed subphases before resuming.



## 16. Gates per subphase

| Gate | 14A | 14B | 14C | 14D |
|---|---|---|---|---|
| Code: relevant tests + mypy + ruff | required | required | n/a (YAML) | full suite |
| Database: migrations reversible/idempotent | 0007 (conditional) | 0007 (conditional) | 0001–0007 | re-verify |
| Security: no secrets, auth, isolation | time-based deletes preserve isolation | drain bound; no new secrets | no repo secrets | full review |
| API: backward compatibility | additive config only | additive config only | none | contract unchanged |
| Observability: audit/metrics/traces | retention visible in metrics | no regression | n/a | coherence review |
| Infrastructure: Docker/PostgreSQL/CI | re-run when available | re-run when available | GitHub execution | re-run when available |
| Regression FASES 0–13 | full suite | full suite | quality job | full suite |

## 17. Block Closure Audit strategy

After each block: `BLOCK N CLOSURE AUDIT` verifying all subphases of the block,
regressions, integration, security, documentation, infrastructure, rollback.
Verdict: `BLOCK N CLOSED` / `BLOCK N NOT CLOSED`.

## 18. Master Closure Audit strategy

After all subphases: `FASE 14 — MASTER CLOSURE AUDIT / FINAL VERIFICATION`
covering subphases, blocks, regressions F0–13, security, API, database,
observability, Docker, PostgreSQL, CI/CD, performance, documentation. Only then
`FASE 14 = CLOSED`.

## 19. Security model (FASE 14)

- **14A retention sweep:** deletes by timestamp only; never user-scoped reads
  are written; audit pruning window excludes recent events so in-flight
  attribution is preserved; sweep is idempotent and non-fatal on failure.
- **14B hardening:** shutdown drain is bounded; DB pool settings are not
  secrets; no new network exposure.
- **14C CI:** no repo secrets; ephemeral service-container `DATABASE_URL`;
  no live NVIDIA/cloud credentials in the workflow.
- Reserved candidates (Redis/Kafka/K8s/caching/auto-instrumentation) stay in
  FASE 15+ with their credential/cache-poisoning/event-replay/duplicate-event
  risks documented there; none is introduced now.

## 20. Performance / scale model

- Metrics that will justify future work (not arbitrary targets): requests/sec,
  p95/p99 latency (extend the existing p95 smoke with p99), DB connection count
  (14B exposes pool knobs to measure), provider latency, worker utilization,
  memory, audit/evidence growth rates (14A exposes retention to bound them),
  and (for 15+ candidates) queue depth, cache hit ratio, event throughput.
- Baseline reference: `tests/performance/test_smoke_performance.py` (p95
  request-cycle bound). No targets are set before a baseline measurement in
  14A/14B.

## 21. Migration policy

- No migration is created during this audit.
- 14A/14B may require a single **conditional 0007** during implementation:
  indexes on `memory_items.expires_at`, `evidence_sources.expires_at`
  (14A) and/or `requests.status`/`requests.updated_at` (14B). It must be
  additive, reversible, and idempotent; downgrade drops only the indexes.
- All other subphases: `NO MIGRATION`.

## 22. Dependency policy

- Reused existing: `sqlalchemy`, `asyncpg`, `psycopg`, `asyncio` (stdlib),
  FastAPI, OTel stack. 14A/14B/14D add **zero** new pip dependencies (the sweep
  uses stdlib `asyncio`; no scheduler package).
- 14C adds no pip dependency (GitHub `postgres:16` service container).
- Potential future (FASE 15+): Redis client, Kafka client, K8s manifests —
  documented, not added.

## 23. Rollback strategy

| Subphase | Rollback |
|---|---|
| 14A | config disable (`audit_retention_seconds=0` / cleanup off); revert diff; downgrade 0007 (indexes) |
| 14B | config defaults (pool/drain/sweep); revert diff; downgrade 0007 |
| 14C | revert `.github/workflows/ci.yml` |
| 14D | docs-only; revertible per file |

No distributed infrastructure to roll back; never assume automatic rollback of
infrastructure not introduced.

## 24. FASE 15+ reserved work

Moved from FASE 14 candidates by this audit (scale-triggered, not automatic):
- Redis (distributed cache/lock/coordination; triggers: proven DB contention,
  multi-region, measured cache pressure).
- Kafka (event streaming; trigger: real async/decoupling/webhook/background
  requirement).
- Kubernetes (deployment/orchestration; trigger: replica/rollout/platform need).
- Distributed caching (trigger: measured read amplification/latency baseline).
- Auto-instrumentation (explicitly NOT RECOMMENDED unless the manual FASE 12
  layer is removed).

Plus future logical work (not invented as a plan, only documented as reserved):
advanced agent features, new providers, advanced RAG (pgvector/embeddings/
semantic search), multi-region/cloud deployment, billing, frontend, autonomous
agent loops, advanced orchestration, audit archive/offload.

## 25. Environment limitations

| Item | Classification |
|---|---|
| pytest / mypy / ruff / alembic (SQLite) | VERIFIED (this session) |
| PostgreSQL 16 real (Docker) | ENVIRONMENT-LIMITED UNVERIFIED (daemon down) |
| Docker build/run/healthcheck | ENVIRONMENT-LIMITED UNVERIFIED |
| Local OTel collector e2e | UNVERIFIED / absent (no listener on 4318) |
| Redis / Kafka | absent locally |
| Kubernetes | no cluster context (client only) |
| GitHub Actions CI execution | UNVERIFIED (environmental) |
| Live NVIDIA call | UNVERIFIED (environmental) |
| External connectivity | VERIFIED (pypi.org/github.com reachable) |

Evidence will never be invented for external/cloud items.

## 26. Explicitly out of scope (FASE 14)

- Redis, Kafka, Kubernetes, distributed caching, auto-instrumentation (15+).
- New providers, vector/embeddings, advanced RAG (15+).
- Multi-region/cloud deployment, billing, frontend, autonomous loops (15+).
- Real CI/CD deployment pipelines or publishing (outside FASE 14).
- Any HTTP contract break, any removal of existing guards.

## 27. Recommended execution order

1. **BLOQUE 1: FASE 14A + 14B + 14C** (implement in order; 14A/14B first,
   14C after; conditional migration 0007 merged).
2. **BLOCK CLOSURE AUDIT 1** (smoke gate per §16–17).
3. **BLOQUE 2: FASE 14D** (consolidation).
4. **FASE 14 MASTER CLOSURE AUDIT** (independent subsequent run; criteria §18).
5. STOP. Await explicit authorization for FASE 15+.

## 28. Confirmation / Status

```
FASE 12 = CLOSED
FASE 13 = CLOSED
FASE 14 = CLOSED

FASE 15+ = NOT INITIATED

CODE CHANGES = YES
MIGRATIONS = YES (0007_evidence_retention_and_stale_index, additive/reversible;
  conditional index on memory_items.expires_at, evidence_sources.expires_at,
  and requests.status/updated_at; head 0007_evidence_retention_and_stale_index)
NEW DEPENDENCIES = NONE
CI CHANGES = YES (.github/workflows/ci.yml strengthened with PostgreSQL 16 service
  container and Docker build/smoke job; environment-limited UNVERIFIED locally)
INFRASTRUCTURE CHANGES = NONE

DOCUMENTATION CHANGES = YES
```
