# PHASE 13 — OFFICIAL MASTER PLAN / PRE-IMPLEMENTATION ARCHITECTURE AUDIT

Status: **FASE 13 — CLOSED (13A+13B+13C IMPLEMENTED; 13D IMPLEMENTED; MASTER CLOSURE AUDIT COMPLETE)**
This document is the official definition of FASE 13 subphases A–D and their
implementation blocks. It was produced from a READ-ONLY inspection of the real
tree (no code, migration, dependency, CI, or infrastructure changes). It is the
authoritative FASE 13 roadmap and supersedes the generic "RESERVED FOR FASE 13+"
labels found in `docs/PHASE-12-ROADMAP.md`.

Since then, **BLOQUE 1 (13A + 13B + 13C)** and **BLOQUE 2 (13D)** have been
implemented and the **FASE 13 MASTER CLOSURE AUDIT** has completed
(see `docs/PHASE-2-9-IMPLEMENTATION.md` and `docs/PHASE-2-9-AUDIT.md`). Sections
6.1–6.3 describe the *as-built* subphases; §24 records the post-implementation
status.

Authoritative sources: `PHASE-2-9-AUDIT.md` (FASE 12 MASTER CLOSURE AUDIT),
`docs/PHASE-12-ROADMAP.md`, `docs/PHASE-2-9-IMPLEMENTATION.md`,
`docs/PHASE-GATES.md`, `src/personal_ai_secretary/**` (real implementation),
`.github/workflows/ci.yml`, `.env.example`, `pyproject.toml`.

---

## 1. Executive Summary

FASE 12 (12A–12G) is **CLOSED** (`PASS WITH ENVIRONMENT-LIMITED VERIFICATION`).
The real tree implements: governed multi-agent workflow, persistent DB stores
(requests/sessions/memory/audit/metrics), guarded multi-worker execution with
idempotency and stale-running recovery, JWT auth, production guards, Docker
deployment (non-root, HEALTHCHECK, migration entrypoint), and a complete
OpenTelemetry observability surface (request-layer traces, trace↔metrics↔audit
correlation, OTLP export hardening, sampling, W3C propagation, consolidation).

FASE 13 is **DEFINED, NOT IMPLEMENTED**. The tree audit identified exactly **three**
real functional capabilities that were reserved for FASE 13+ and genuinely
belong to FASE 13, each independently implementable on the existing
architecture:

- **13A — F-04 compliance enforcement** (the inert `ComplianceAgent` becomes a
  deterministic, config-driven enforcement stage).
- **13B — RAG persistence (DB-backed evidence store)** (the always-empty
  `GovernedRetriever` is replaced by a persistent, user-scoped retriever over a
  new `evidence_sources` table; includes a governed ingestion path).
- **13C — Remote NVIDIA provider activation** (the implemented but blocked
  `NVIDIAProvider` is activated with production guards and security rules).
- **13D — FASE 13 consolidation and phase deliverables** (closes the phase;
  mirrors the FASE 12G pattern).

Everything else reserved (Redis, Kafka, Kubernetes, caching, auto-instrumentation,
external CI execution, scaling, distributed infrastructure) is **FASE 14+** and
explicitly out of scope.

Execution strategy: **BLOQUE 1 = 13A + 13B + 13C** (three independent additive
subphases) → BLOCK CLOSURE AUDIT 1 → **BLOQUE 2 = 13D** → **FASE 13 MASTER
CLOSURE AUDIT**. No subphase is forced into a sequence it does not need; 13A/13B/13C
have no mutual dependencies and only meet at 13D.

This run produced **documentation only** (`docs/PHASE-13-ROADMAP.md`).
`CODE CHANGES = NONE`, `MIGRATIONS = NONE`, `NEW DEPENDENCIES = NONE`,
`CI CHANGES = NONE`, `INFRASTRUCTURE CHANGES = NONE`.

---

## 2. Baseline (FASE 13 BASELINE — verified read-only, 2026-08-16)

Authoritative static gates (Windows, Python 3.13.15, real root, identical to the
`.github/workflows/ci.yml` contract):

| Gate | Result (baseline) | Result (post-BLOQUE 1) |
|---|---|---|
| `pytest --cov=src --cov-report=term-missing` | **269 passed** (26.34s), coverage **93%** (1853 statements / 137 missing) | **307 passed** (23.28s), coverage **94%** |
| `mypy src` | **PASS (44 source files)** | **PASS (46 source files)** |
| `ruff check .` | **All checks passed!** | **All checks passed!** |
| Alembic | head **`0005_metric_records`**; 5 migrations | head **`0006_evidence_sources`**; 6 migrations; upgrade→downgrade→upgrade cycle PASS |
| PostgreSQL 16 real (Docker) | **ENVIRONMENT-LIMITED UNVERIFIED this session** — Docker daemon is not running (`npipe:////./pipe/dockerDesktopLinuxEngine` unreachable). Previously VERIFIED in the FASE 12 MASTER CLOSURE AUDIT; the FASE 13 baseline records the daemon as unavailable today. | **ENVIRONMENT-LIMITED UNVERIFIED** — daemon still unavailable at BLOQUE 1 gate time; PG16 real verification deferred. |
| Docker build/run | **ENVIRONMENT-LIMITED UNVERIFIED this session** (daemon unavailable; previously VERIFIED in FASE 12 closure audits). | **ENVIRONMENT-LIMITED UNVERIFIED** (daemon unavailable). |
| Git | **No git repository** (verified at both the workspace level and the project root). No git history to consult or invent. | Unchanged — still no git repo. |

Baseline facts confirmed:
- FASE 12 = **CLOSED** (per `PHASE-2-9-AUDIT.md`, FASE 12 MASTER CLOSURE AUDIT).
- FASE 13 = **NOT IMPLEMENTED** (no `docs/PHASE-13-ROADMAP.md` existed; no 13A–13G
  artifacts anywhere in the tree; grep for `kafka|redis|kubernetes|k8s|celery|
  vector|auto-instrumentation|persistent-RAG` in `src/` returns zero matches).
- FASE 14+ = **NOT INITIATED**.

The baseline is **green**; no modification was made to achieve it.

## 3. Current Architecture (verified read-only)

Real root: `src/personal_ai_secretary/` (44 source files).

| Layer | Modules | Verified state |
|---|---|---|
| API | `api/app.py` | FastAPI; JWT Bearer on protected endpoints; correlation middleware with `http.request` span + W3C `traceparent` parenting; `health/live`, `health/ready` (provider + DB probe), `providers`, `requests` CRUD+execute, `sessions/messages`, `observability/audit`, `observability/metrics`, `/metrics` (Prometheus text). |
| Application | `application/service.py`, `application/risk.py` | `RequestService` guarded execute (single `UPDATE ... status IN (...)`), idempotency key (user-scoped unique), stale-running recovery, `send_message`/`history`; deterministic keyword risk classifier (LOW/MEDIUM/HIGH/CRITICAL). |
| Domain | `domain/contracts.py`, `domain/models.py` | Pydantic contracts (additive-friendly); SQLAlchemy `requests`, `sessions`, `memory_items`, `audit_events`, `metric_records`. |
| Workflow | `workflow/engine.py` | `GovernedWorkflow`: planner → approval → research → execution → reviewer → evaluation → compliance → completion; `blocked`/`rejected`/`failed`/`completed`; full span + audit instrumentation per stage. |
| Agents | `agents/contracts.py`, `agents/builtin.py` | Planner, Research (RAG + memory recall), Execution (provider + tool), Reviewer (separation of duties), **Compliance (inert, F-04)**. |
| Evaluation | `evaluation/runtime.py`, `evaluation/gates.py` | Per-request `ReleaseGateEvaluator` (6 criteria) + release-level `release_ready` gates. |
| Tools | `tools/registry.py`, `tools/builtin.py` | `@tool:` text protocol, argument validation, critical-risk deny-by-default; `calculator`, `list_tools`. |
| Memory | `memory/service.py` | `MemoryStore` protocol; `InMemoryMemoryStore`; `MemoryPolicy` TTL; `PostgresMemoryStore` (persistent). |
| RAG | `rag/service.py` | `Retriever` protocol + `GovernedRetriever` over an **in-memory, always-empty** corpus at runtime (placeholder). |
| Providers | `providers/base.py`, `deterministic.py`, `ollama.py`, `remote.py`, `factory.py` | `AIProvider` protocol; deterministic + Ollama active; **NVIDIA `remote` implemented but blocked in `get_provider()`** (`AVAILABLE_PROVIDER_MODES = ("deterministic", "local")`). |
| Observability | `observability/tracing.py`, `metrics.py`, `audit.py`, `observer.py` | OTel in-process spans + OTLP export (hardened), sampling, resource attrs, W3C propagation; DB-backed metrics sink; redaction guard rail. |
| Infrastructure | `infrastructure/database.py`, `stores.py`, `memory.py`, `observability.py` | Async SQLAlchemy engine; `Postgres*` stores (memory/audit/metrics) selected by `persistent_stores`. |
| Shared | `shared/config.py`, `auth.py`, `telemetry.py` | Settings with production guards (JWT secret, SQLite rejection); JWT decode; correlation logging. |
| Config/Docker | `pyproject.toml`, `Dockerfile`, `docker-entrypoint.sh`, `.env.example`, `.dockerignore` | Python 3.13, FastAPI/asyncpg/psycopg/OTel; non-root `app` user; HEALTHCHECK; migration entrypoint. |
| CI/CD | `.github/workflows/ci.yml` | `pytest --cov`, `mypy src`, `ruff check .` on Python 3.13 (Ubuntu). **Execution UNVERIFIED** (environmental). |

Security boundary today: Authentication (JWT) → Authorization (user scoping,
permission checks) → Governance (workflow + approval + evaluation) → Execution
(provider/tools) → Observability (traces/metrics/audit, redacted).

## 4. Reserved Capabilities (recovery and classification)

All elements previously labeled "RESERVED FOR FASE 13+" (from
`docs/PHASE-12-ROADMAP.md` §12) were recovered and classified against the real
tree:

| Reserved item | Classification | Rationale (from the real tree) |
|---|---|---|
| RAG persistence (DB-backed evidence store) | **Candidate FASE 13 → 13B** | The `Retriever` protocol already exists; `_service(db)` builds `GovernedRetriever()` with an empty corpus, so research never returns real evidence. PostgreSQL persistence pattern (`Postgres*` stores, `persistent_stores`) is established and low-risk to extend. |
| F-04 compliance enforcement | **Candidate FASE 13 → 13A** | `ComplianceAgent` exists and runs as a workflow stage but is inert: it only blocks on a `policy_violation` context flag that is never set, or on empty output. Enforcement is a deterministic, additive workflow change with no new infra. |
| New providers — remote NVIDIA activation | **Candidate FASE 13 → 13C** | `providers/remote.py` is fully implemented (httpx, `traceparent` outbound, `health()`), but `get_provider()` raises `RuntimeError` for `"remote"` and `AVAILABLE_PROVIDER_MODES` excludes it. Activation is factory + config + tests; no new dependency. |
| Auto-instrumentation (`opentelemetry-instrumentation-*`) | **Candidate FASE 14+** | Would add new dependencies and replace working, fully-tested manual spans (12B–12G). Optimization of working code, not a functional gap; higher risk. Deferred. |
| Redis / distributed caching | **Candidate FASE 14+** | Requires a Redis server (new infra), a new dependency, and new failure modes. No correctness gap today: DB-backed stores already persist. Deferred. |
| Kafka / event streaming | **Candidate FASE 14+** | Requires a broker (new infra) and async consumers; current synchronous request path has no such gap. Deferred. |
| Kubernetes / orchestration | **Candidate FASE 14+** | Docker deployment is verified; K8s is a large infrastructure investment not justified by current scale. Deferred. |
| External CI execution | **Candidate FASE 14+ (environmental)** | GitHub Actions cannot run from this Windows environment. Environmental gate, not implementable here. |
| Caching (generic) | **Candidate FASE 14+** | Same infra/dependency story as Redis. Deferred. |
| Scaling / distributed infrastructure | **Candidate FASE 14+** | Umbrella of the above. Deferred. |

No "already implemented but mislabeled as reserved" item was found: everything
reserved was genuinely absent or blocked, and nothing that should be in FASE 13
was found already implemented.

## 5. Architecture Gap Analysis

| Capability | Estado actual | Gap | Dependencias | Riesgo | Fase propuesta |
|---|---|---|---|---|---|
| Application layer | Guarded, idempotent, stale-recovery `RequestService` | None blocking; wiring of a real retriever into `_service(db)` | — | LOW | 13B |
| Domain layer | Contracts/models additive-friendly | No evidence/source persistence model | SQLAlchemy | LOW | 13B |
| Persistence | DB-backed memory/audit/metrics | No DB-backed RAG evidence store | Migration 0006 | LOW | 13B |
| Request lifecycle | create → execute → history | None | — | LOW | — |
| Execution | Governed workflow complete | None | — | LOW | — |
| Provider abstraction | deterministic + local active; remote blocked | NVIDIA mode blocked in factory; no key guard | None | MEDIUM | 13C |
| Observability | Traces/metrics/audit full | None (auto-instrumentation is optimization) | — | LOW | 14+ |
| Security | JWT, guards, redaction | Compliance stage enforces nothing; NVIDIA key lifecycle on activation | None | MEDIUM | 13A/13C |
| Compliance | Inert `ComplianceAgent` | No deterministic policy enforcement | None | MEDIUM | 13A |
| Distributed coordination | Idempotency + guarded transitions + DB metric aggregation | None for current single-app topology | — | LOW | — |
| Caching | None | Not needed (DB-backed stores) | Redis | HIGH | 14+ |
| Async processing | None (synchronous path) | Not needed for current contracts | Kafka | HIGH | 14+ |
| Deployment | Docker verified | Kubernetes not justified | Cluster | HIGH | 14+ |
| CI/CD | Quality-only workflow, execution UNVERIFIED | External/integration CI | GitHub | LOW | 14+ |

## 6. Proposed Subphases

The subphase split is **not invented**: it mirrors the three real reserved
capabilities plus the closure subphase required by the project's phase discipline
(the same shape as 12G). FASE 13 has **four** subphases (A–D), not seven, because
only three implementation capabilities exist in the real tree; each is cohesive,
independently testable, and has a single clear dependency (13D consumes all).

### 6.1 FASE 13A — F-04 compliance enforcement

1. **Nombre:** FASE 13A — F-04 compliance enforcement.
2. **Objetivo:** turn the inert `ComplianceAgent` into a deterministic,
   config-driven compliance enforcement stage that actually blocks
   non-compliant requests/output.
3. **Problema que resuelve:** today `ComplianceAgent.run` only reacts to a
   `policy_violation` context flag that no caller ever sets (plus an empty-output
   check), so the compliance stage is a formality and F-04 is documented as
   deferred. Operators cannot rely on compliance as a real control.
4. **Estado actual:** `ComplianceAgent` in `agents/builtin.py`; `compliance`
   stage runs after evaluation in `workflow/engine.py`; `policy_violation` never
   set anywhere in the runtime (grep verified).
5. **Cambios esperados:** new deterministic `compliance/policy.py` module with
   explicit rules (e.g. prohibited-command keywords, credential leakage in
   output via the existing redaction detectors, risk-cap enforcement) and
   config surface to enable/disable rules; `ComplianceAgent` evaluates the
   input and produced output against the policy and returns a
   `blocked=True` artifact with the specific policy name/reason; workflow
   `blocked` outcome is preserved and audit/spans carry the rule id.
6. **Archivos potencialmente afectados:** `src/personal_ai_secretary/compliance/policy.py`
   (new), `agents/builtin.py`, `workflow/engine.py` (pass input+output context;
   already passes `output`), `shared/config.py`, `.env.example`, tests
   (`tests/unit/test_governed_components.py`, `tests/integration/test_requests_api.py`,
   new `tests/unit/test_compliance.py`, `tests/security/test_security_basics.py`),
   docs.
7. **Dependencias:** none new.
8. **Migraciones requeridas:** NONE.
9. **Nuevas dependencias:** NONE.
10. **Tests requeridos:** each policy rule triggers a block with its rule id;
    compliant input passes; credential-like output is blocked; risk-cap rule
    blocks HIGH/CRITICAL output paths it is configured for; approval-granted
    flow still works (compliance is a hard block, not approval-gated);
    HTTP integration: compliant message completes, non-compliant is `blocked`
    with reason; audit event carries the rule id; span attributes carry the
    rule id (non-sensitive).
11. **Security gates:** no secrets in spans/audit; policy is deterministic and
    code-visible (no prompt-based judgment); rules additive; default behavior
    must not regress FASE 2–12 flows (non-blocking unless a rule matches).
12. **Performance gates:** pure in-process string/regex evaluation; add a
    p95 request-cycle bound guard (existing performance test) to prove no
    regression.
13. **Docker/infrastructure gates:** image unchanged; no new ports/services;
    re-run Docker build+smoke when the daemon is available (environment-limited
    this session).
14. **Regression gates:** full 269-test suite green; contract unchanged
    (`status` open string, reason travels in result/assistant message like
    existing `blocked` paths).
15. **Riesgos:** MEDIUM — false-positive blocks could surprise users; mitigated
    by conservative rule defaults, explicit rule ids in reasons, and opt-in
    config. Risk that a rule match changes existing test expectations → treat
    as intended behavior and update only the affected assertions.
16. **Rollback strategy:** config toggles rules off; or revert the agent/policy
    diff — no migration, no contract change, single-module revert.
17. **Definition of Done:** compliance enforces at least the documented rules;
    every rule tested; HTTP `blocked` message with reason verified; audit
    carries rule id; full suite green; docs updated.
18. **Items explícitamente fuera de alcance:** ML/LLM-based compliance, external
    policy sources, per-tenant policy engines, audit policy history, any schema
    change.

### 6.2 FASE 13B — RAG persistence (DB-backed evidence store)

1. **Nombre:** FASE 13B — RAG persistence (DB-backed evidence store).
2. **Objetivo:** persist a user-scoped evidence corpus in PostgreSQL and make
   the research stage retrieve real evidence from the database, closing the
   always-empty-retriever gap.
3. **Problema que resuelve:** `api/app.py` `_service(db)` constructs
   `GovernedRetriever()` with no sources, so research always returns
   "No additional context available"; RAG has been a placeholder since Phase 4.
4. **Estado actual:** `Retriever` protocol (`rag/service.py`), `GovernedRetriever`
   (in-memory, empty at runtime), `ResearchAgent._retrieve` degrades gracefully;
   `infrastructure/stores.py` already implements the `Postgres*` pattern.
5. **Cambios esperados:** new `EvidenceSource` model (`evidence_sources` table:
   source id, user_id, uri, title, text/content, authority, score, freshness,
   created_at, expires_at optional) with indexes; migration `0006_evidence_sources`
   (reversible, additive); `PostgresRetriever` in `infrastructure/stores.py`
   implementing the `Retriever` protocol (user-scoped, deterministic scoring
   preserved, expiry filtering via the existing `MemoryPolicy`-style TTL or a
   source-level TTL, limit); a governed ingestion path (service method + minimal
   additive `POST /api/v1/evidence` endpoint behind JWT, validated and
   user-scoped) so the corpus can be populated; `_service(db)` builds the
   DB-backed retriever when `persistent_stores` is true (in-memory fallback
   unchanged for dev/tests).
6. **Archivos potencialmente afectados:** `domain/models.py`, `alembic/versions/0006_evidence_sources.py`
   (new), `infrastructure/stores.py`, `infrastructure/memory.py` or a new
   `infrastructure/rag.py`, `rag/service.py` (persistent retriever or adapter),
   `api/app.py` (`_service`, new endpoint), `domain/contracts.py` (additive
   `EvidenceSourceCreate`/response), `shared/config.py` (evidence TTL/limit),
   `.env.example`, tests (`tests/unit/test_persistent_stores.py`,
   `tests/integration/test_migrations.py`, `tests/integration/test_requests_api.py`,
   new `tests/integration/test_rag_persistence.py`), docs.
7. **Dependencias:** none new (SQLAlchemy/asyncpg/psycopg already present).
8. **Migraciones requeridas:** YES — `0006_evidence_sources` (additive,
   reversible; downgrade drops only the new table). Head moves
   `0005_metric_records → 0006_evidence_sources`.
9. **Nuevas dependencias:** NONE.
10. **Tests requeridos:** migration up/down/partial on SQLite + PG16 (Docker,
    environment-limited this session); PostgresRetriever scoping (no cross-user
    leakage), expiry/TTL filtering, scoring order, limit, empty-corpus
    non-fatal; ingestion endpoint auth (401 without), validation, duplicate
    handling, user-scoped reads; research step retrieves real persisted
    evidence over HTTP; restart persistence (data survives app restart);
    regression of the in-memory fallback path.
11. **Security gates:** user isolation enforced at query level (retriever always
    scoped by user_id); evidence text sanitized/redacted before persistence via
    the existing `sanitize_value`; ingestion rejects oversized payloads; never
    logs prompts/evidence.
12. **Performance gates:** indexed retrieval (user_id + created_at/expires_at);
    limit bound; no N+1; add a retrieval-latency performance assertion;
    p95 request bound kept.
13. **Docker/infrastructure gates:** PG16 required for persistent mode;
    entrypoint `alembic upgrade head` applies 0006; image unchanged;
    multi-worker/replica retrieval correctness (evidence created by worker A
    retrieved by worker B) — verify when Docker is available.
14. **Regression gates:** full 269-test suite green; existing RAG unit tests and
    research graceful-degradation tests unchanged; contract additions are
    additive-only (new endpoint/fields), OpenAPI test extended.
15. **Riesgos:** LOW-MEDIUM — new table + migration (additive/reversible);
    ingestion surface increases API contract (mitigated by strict validation,
    auth, user scoping); SQLite vs PG date/time handling already handled by
    `_as_utc` pattern.
16. **Rollback strategy:** downgrade `0006_evidence_sources`; revert
    `_service(db)` wiring; ingestion endpoint removal is contract-additive and
    safe to remove before release.
17. **Definition of Done:** research retrieves persisted, user-scoped evidence
    end-to-end; ingestion path works and is protected; migration chain to
    `0006` verified on SQLite (and PG16 when Docker available); all new tests
    green; docs updated.
18. **Items explícitamente fuera de alcance:** vector embeddings / pgvector /
    semantic search, document ingestion pipelines, full-text search engine,
    cross-user corpora, Redis caching of retrieval.

### 6.3 FASE 13C — Remote NVIDIA provider activation

1. **Nombre:** FASE 13C — Remote NVIDIA provider activation.
2. **Objetivo:** activate the reserved `remote` provider mode safely, with
   production guards, secret handling, and verified behavior.
3. **Problema que resuelve:** `NVIDIAProvider` (`providers/remote.py`) is
   implemented and dependency-complete but `get_provider()` raises
   `RuntimeError` for `"remote"`, and `AVAILABLE_PROVIDER_MODES` omits it; the
   platform cannot call a remote model.
4. **Estado actual:** `NVIDIAProvider.generate` (httpx, `Authorization` Bearer
   from `nvidia_api_key`, outbound `traceparent`), `health()`; config already
   has `nvidia_base_url`, `nvidia_model`, `nvidia_api_key`; `.env.example` marks
   remote as reserved.
5. **Cambios esperados:** `providers/factory.py` — include `"remote"` in
   `AVAILABLE_PROVIDER_MODES` and return `NVIDIAProvider()` when
   `ai_provider == "remote"`; `shared/config.py` — production guard: when
   `app_env == "production"` and `ai_provider == "remote"` and
   `nvidia_api_key` is unset/empty → raise at startup (no credential-less
   production remote); `.env.example` — document `AI_PROVIDER=remote`,
   `NVIDIA_API_KEY`, `NVIDIA_BASE_URL`, `NVIDIA_MODEL`; ensure the API key is
   env-only, never logged/audited/spanned.
6. **Archivos potencialmente afectados:** `providers/factory.py`,
   `shared/config.py`, `.env.example`, `tests/unit/test_factory.py`,
   `tests/unit/test_config.py`, `tests/unit/test_provider.py`,
   `tests/security/test_security_basics.py`, docs.
7. **Dependencias:** none new (httpx is a runtime dep).
8. **Migraciones requeridas:** NONE.
9. **Nuevas dependencias:** NONE.
10. **Tests requeridos:** factory returns `NVIDIAProvider` for `remote` and still
    raises for unknown modes; production guard rejects `remote` without a key;
    `generate` without a key raises a clear error; `health()` reports
    available/config state; `Authorization` value never appears in audit/spans
    (existing redaction + tracing tests extended); outbound `traceparent` sent
    when a span is active (extend existing provider-header test); no network
    calls in unit tests (monkeypatched httpx).
11. **Security gates:** API key read from env only; never logged; never on
    spans/audit; production refuses remote without a key; `.env.example` has
    placeholders only; secret rotation is operator-side (env restart).
12. **Performance gates:** provider timeout honored (`provider_timeout_seconds`);
    no new blocking calls on the request path beyond the existing httpx call;
    p95 request bound kept for the deterministic test path.
13. **Docker/infrastructure gates:** image unchanged (httpx already in image);
    container requires `NVIDIA_API_KEY` env for remote mode; live NVIDIA call is
    **environment-limited UNVERIFIED** (no external credential/network here);
    local verification uses mocked HTTP.
14. **Regression gates:** full 269-test suite green; deterministic provider path
    and `health/ready` provider probe unchanged.
15. **Riesgos:** MEDIUM — external API introduces credential and network failure
    modes; mitigated by production guard, existing `provider_failures`/`failed`
    path, and timeouts; risk of accidental key exposure in logs/audit — covered
    by redaction + dedicated security tests; live verification cannot be
    performed locally (UNVERIFIED, documented).
16. **Rollback strategy:** revert factory + config guard (single-file diff, no
    migration); keep `remote` out of `AVAILABLE_PROVIDER_MODES` again.
17. **Definition of Done:** `remote` selectable and guarded; unit/security tests
    green; mocked remote generate path verified; live NVIDIA result documented
    as VERIFIED or UNVERIFIED with evidence; docs updated.
18. **Items explícitamente fuera de alcance:** additional providers (Azure,
    OpenAI, Anthropic, …), model routing/failover, key vault integration, token
    budget/cost controls, retry/backoff beyond existing httpx behavior.

### 6.4 FASE 13D — FASE 13 consolidation and phase deliverables

1. **Nombre:** FASE 13D — FASE 13 consolidation.
2. **Objetivo:** standardize and document the complete FASE 13 surface and
   produce the evidence pack that lets the FASE 13 MASTER CLOSURE AUDIT run.
3. **Problema que resuelve:** after 13A–13C the compliance, RAG, and provider
   surfaces are complete but not documented as a single coherent contract.
4. **Estado actual:** BLOQUE 1 (13A+13B+13C) implemented; each subphase has its
   own tests/evidence.
5. **Cambios esperados:** consolidate naming/config across the three subphases;
   finalize docs (`docs/PHASE-2-9-IMPLEMENTATION.md` FASE 13 sections,
   `PHASE-2-9-AUDIT.md` block audits, this roadmap's status → IMPLEMENTED);
   full regression; Docker/PG16/OTLP evidence capture when the daemon is
   available; produce the gate matrix for the Master Closure Audit.
6. **Archivos potencialmente afectados:** docs, consolidated config/tests from
   13A–13C, `.env.example` (final form). No new features.
7. **Dependencias:** consumes 13A, 13B, 13C.
8. **Migraciones requeridas:** NONE beyond `0006` (from 13B); re-verify the
   chain end-to-end.
9. **Nuevas dependencias:** NONE.
10. **Tests requeridos:** full suite; cross-subphase integration (compliance +
    RAG + remote mode co-exist in one runtime); attribute/rule-id consistency
    tests; final security review.
11. **Security gates:** full redaction/guard review across the three features;
    no secrets in spans/audit/export; production guards (JWT, SQLite, NVIDIA key)
    verified.
12. **Performance gates:** final p95 and retrieval-latency assertions; no
    regression vs the 269-test baseline.
13. **Docker/infrastructure gates:** image build + run + HEALTHCHECK + migrations
    to `0006` + PG16 smoke (when daemon available; otherwise documented as
    environment-limited UNVERIFIED).
14. **Regression gates:** FASES 2–12 + 13A–13C green.
15. **Riesgos:** LOW — consolidation only; the only risk is doc/attribute drift,
    covered by consistency tests.
16. **Rollback strategy:** N/A (no feature code in 13D beyond consolidation);
    any doc/naming change is revertible individually.
17. **Definition of Done:** the whole FASE 13 gate matrix is green and
    documented; the phase is ready for the MASTER CLOSURE AUDIT.
18. **Items explícitamente fuera de alcance:** any new feature; FASE 14+ items.

## 7. Dependency Graph

```
13A (compliance) ──┐
                   ├── 13D (consolidation)
13B (RAG) ─────────┤
                   │
13C (NVIDIA) ──────┘
```

13A/13B/13C are **independent**: distinct files, no shared schema, no shared
dependency beyond existing infra. They only meet at 13D. No artificial sequence
is imposed. (The FASE 12 roadmap had a comparable branching shape between 12B→12C
and 12D→12E.)

## 8. Implementation Blocks

Per the mandatory strategy and the real dependency graph:

### BLOQUE 1: 13A + 13B + 13C
Three independent, additive subphases — coherent as one block (implementation,
testing, smoke, and one closure audit), no cross-block feature leakage.

### BLOQUE 2: 13D
The single consolidation/closure subphase (mirrors FASE 12's 12G-only final
block shape).

Blocking rule (same as FASE 12): if any subphase within BLOQUE 1 reveals an
architectural, security, migration, or infrastructure risk that makes continuing
unsafe, stop the block and run an isolated closure audit for the completed
subphases before resuming.

## 9. BLOQUE 1 — 13A + 13B + 13C (Smoke Gate)

- pytest `--cov=src` (no regression, ≥93%).
- mypy src PASS; ruff check . PASS.
- Alembic head `0006_evidence_sources`; upgrade→downgrade→upgrade cycle PASS.
- Docker build + run + HTTP smoke + PG16 migrations (environment-limited this
  session; must be re-run when the daemon is available).
- Local OTLP e2e: compliance rule-id and RAG evidence flows traced; NVIDIA
  mocked-call trace (no secrets in spans).
- Security Spot-check: no secrets in spans/audit/export; NVIDIA key guard;
  RAG user isolation; compliance rule ids non-sensitive.
- Contract: OpenAPI additions are additive-only; existing paths unchanged.

Stop rule as §8.

## 10. BLOQUE 2 — 13D (Smoke Gate)

- Full regression (FASES 2–13C).
- mypy src PASS; ruff check . PASS; alembic chain final.
- Docker/PG16/OTLP evidence capture (when available; documented otherwise).
- Full security review + docs review.

## 11. BLOQUE 3

Not required: FASE 13 has exactly four subphases (13A–13D) distributed as
BLOQUE 1 (A+B+C) and BLOQUE 2 (D). A third block would only reduce audit
granularity without technical justification.

## 12. Security Strategy

FASE 13 must preserve the boundary:
Authentication (JWT) → Authorization (user scoping) → Governance (approval +
evaluation + **new compliance enforcement**) → Execution (provider/tools,
**now including remote NVIDIA**) → Observability (traces/metrics/audit).

Specific rules:
- No secrets in spans, audit details, exports, or logs — enforce with the
  existing `sanitize_value` redaction and extend tests (NVIDIA key, evidence
  text, rule reasons are non-secret).
- NVIDIA `nvidia_api_key` is env-only; production refuses `remote` without a key.
- RAG evidence is user-scoped at query and ingestion time; never cross-user.
- Compliance rules are deterministic, code-visible, config-enabled, and never
  prompt-dependent.
- All new endpoints reuse the existing JWT Bearer scheme.
- Production guards (JWT secret, SQLite, now NVIDIA key) are startup-enforced.

## 13. Data/Persistence Strategy

| Data | Source of truth | Transient/Persistent | Cacheable | Consistency | Isolation | TTL |
|---|---|---|---|---|---|---|
| Requests/sessions | PostgreSQL | Persistent | No | Yes (guarded transitions) | Per-user | No |
| Memory items | PostgreSQL | Persistent | No | Yes | Per-user | Yes (`MemoryPolicy`) |
| Audit events | PostgreSQL | Persistent | No | No | Per-user reads | No (retention TBD) |
| Metrics | PostgreSQL (`metric_records`) | Persistent (windowed) | No | Sum/latest-wins | Global | Yes (`metrics_retention_seconds`) |
| **Evidence sources (13B)** | **PostgreSQL (`evidence_sources`)** | **Persistent** | **No (defer Redis to 14+)** | **Yes** | **Per-user** | **Optional source TTL** |
| NVIDIA API key (13C) | Environment | Transient (runtime) | Never | — | Global config | — |
| Compliance rules (13A) | Code/config | Persistent (code) | No | Deterministic | Global policy | — |

No new transaction boundaries beyond existing per-session commits; the ingestion
endpoint commits atomically per evidence item.

## 14. Failure / Recovery Strategy

For each FASE 13 capability:

| Failure | 13A compliance | 13B RAG | 13C NVIDIA |
|---|---|---|---|
| Startup failure | Policy module imports at init; fail-fast if invalid config | Migration 0006 must be applied; readiness DB probe covers it | Production guard raises without key |
| Dependency unavailable | None | DB down → retriever degrades gracefully (existing `_retrieve` catch) | Provider down/timeout → `provider_failures` + request `failed` |
| Timeout | N/A | N/A | `provider_timeout_seconds` honored by httpx |
| Connection loss | N/A | store reconnects via engine pool (`pool_pre_ping`) | new httpx client per call |
| Process/worker/replica restart | No state | evidence persists (DB); `alembic upgrade head` idempotent | key from env re-read |
| Stale state | N/A | N/A | N/A |
| Partial failure | Per-request, transactional | per-item commit; no partial corpus | provider error → request `failed`, no memory write |
| Retry / duplicate | N/A | ingestion idempotent by source id | existing idempotency at request layer |
| Recovery | N/A | existing stale-running recovery unaffected | existing `failed` recovery path |

Redis/Kafka/K8s distributed failure modes are explicitly **not** part of FASE 13
(§11 roadmap; see §20).

## 15. Test Strategy

| Category | Existing | FASE 13 additions (planned, NOT written) |
|---|---|---|
| `tests/unit/` | 18 files | `test_compliance.py`; factory/config/provider additions for NVIDIA; persistent retriever unit tests |
| `tests/integration/` | 5 files | `test_rag_persistence.py`; compliance-over-HTTP additions; evidence ingestion; migrations 0006 |
| `tests/contract/` | 1 file | OpenAPI additions (evidence endpoint, additive fields) |
| `tests/security/` | 1 file | NVIDIA key never leaked; RAG user isolation; compliance rule ids non-sensitive |
| `tests/performance/` | 1 file | retrieval latency + p95 bounds |
| `tests/e2e/` | 0 (empty `.gitkeep`) | remains empty; e2e verification is done via Docker smoke gates, not committed e2e tests (no browser/CLI harness exists) |

## 16. Docker / Infrastructure Strategy

- No new containers/services in FASE 13 (Redis/Kafka/K8s are 14+).
- PostgreSQL remains the only required service for `persistent_stores`/RAG.
- The image is unchanged in content: no new runtime dependency. Env passthrough
  grows by `NVIDIA_API_KEY` etc.
- Entrypoint `alembic upgrade head` will apply `0006_evidence_sources` in 13B.
- Gates: build, HEALTHCHECK, restart persistence, multi-worker/replica evidence
  and remote-mode smoke — all environment-limited this session (daemon down);
  must be re-verified when the daemon is available, as in prior closures.

## 17. CI/CD Strategy

`.github/workflows/ci.yml` (verified by inspection, execution UNVERIFIED) runs
`pytest --cov`, `mypy src`, `ruff check .`. FASE 13 implications:

- **No CI change is required** for 13A/13C (no new deps, no services).
- 13B adds a migration; the existing migration tests run on SQLite and the
  `Postgres*` stores are DB-agnostic, so the quality job still covers them.
- Optional (deferred to 14+, not FASE 13): a GitHub Actions service container
  for PostgreSQL and/or a real-CI integration job. Not added now.
- External CI execution remains **UNVERIFIED (environmental)**.

## 18. Performance Strategy

- Keep the p95 request-cycle bound (existing `test_smoke_performance.py`).
- 13A: compliance rule evaluation is in-process; assert negligible delta.
- 13B: indexed retrieval (user_id, created_at/expires_at), bounded limit, no
  N+1; assert retrieval latency bound.
- 13C: timeout enforced; mocked-path unit tests prove no added blocking I/O in
  the deterministic path.
- No pre-emptive caching (Redis is 14+).

## 19. Environment-Limited Verification

| Item | Classification | Note |
|---|---|---|
| pytest / mypy / ruff / alembic (SQLite) | VERIFIED | Local (this session) |
| PostgreSQL 16 real (Docker) | UNVERIFIED this session | Docker daemon unavailable now; previously VERIFIED in FASE 12 closures; re-run in 13B/13D gates when available |
| Docker build/run/healthcheck | UNVERIFIED this session | daemon unavailable; re-run in block gates |
| Local OTLP collector e2e | UNVERIFIED this session | daemon unavailable; re-run in block gates |
| Live NVIDIA provider call | UNVERIFIED (environmental) | requires external credentials/network; mocked-path tests only |
| GitHub Actions CI execution | UNVERIFIED (environmental) | cannot run from this Windows environment |
| External/cloud OTLP export | UNVERIFIED (environmental) | unchanged from FASE 12 |

Evidence will never be invented for external/cloud items.

## 20. Explicitly Out of Scope (FASE 13)

- Redis / distributed caching (14+).
- Kafka / event streaming (14+).
- Kubernetes / orchestration (14+).
- Auto-instrumentation (14+).
- External CI execution / GitHub Actions real runs (14+, environmental).
- Scaling / distributed infrastructure (14+).
- New providers beyond NVIDIA activation (14+).
- Vector embeddings / pgvector / semantic search (14+).
- Document ingestion pipelines / external corpora (14+).
- Any HTTP contract break; any removal of existing guards.

## 21. FASE 14+ Reserved Work

Explicitly reserved and NOT initiated: Redis caching, Kafka streaming, Kubernetes,
auto-instrumentation (opentelemetry-instrumentation packages), external/cloud CI,
scaling, distributed infrastructure, additional AI providers, pgvector/embeddings,
and any production deployment target beyond the verified Docker/PostgreSQL path.

## 22. Definition of Done — FASE 13

### Code
- `pytest --cov=src` full suite green, coverage ≥93%.
- `mypy src` PASS; `ruff check .` PASS.

### Database
- Alembic head `0006_evidence_sources`; upgrade→downgrade→upgrade cycle PASS;
  applied on real PostgreSQL 16 (Docker, environment-limited re-verified);
  evidence persistence + restart survival verified.

### API
- Contracts additive-only; new evidence endpoint Bearer-protected; compliance
  block messages and reasons; idempotency/correlation/tracing preserved;
  `X-Correlation-ID` and `traceparent` behavior unchanged.

### Security
- No secrets in spans/audit/export; NVIDIA key production guard; JWT/SQLite
  guards unchanged; RAG user isolation verified; compliance rule ids
  non-sensitive; Docker non-root + HEALTHCHECK unchanged.

### Distributed behavior (as applicable)
- Multi-worker/replica: evidence created on one replica retrieved on another;
  remote-mode guarded execute still runs once; metric aggregation unaffected.

### Docker
- Build, HEALTHCHECK, startup migrations to 0006, restart, persistence,
  non-root verified (environment-limited; re-run when daemon available).

### Performance
- p95 request-cycle bound kept; retrieval latency bound added; no blocking
  regressions.

### Regression
- FASES 2–12 + 13A–13D fully green.

## 23. Recommended Execution Order

1. **BLOQUE 1: FASE 13A + 13B + 13C** (consecutive within the block; 13A and
   13C first for security/config, 13B for the migration surface — order within
   the block is flexible since they are independent).
2. **BLOCK CLOSURE AUDIT 1** (integrated smoke gate per §9).
3. **BLOQUE 2: FASE 13D** (consolidation).
4. **FASE 13 MASTER CLOSURE AUDIT** (independent subsequent run; criteria §22).
5. STOP. Await explicit authorization for FASE 14+.

## 24. Confirmation / Status

- FASE 12 = **CLOSED**.
- FASE 13 = **MASTER PLAN DEFINED** (`docs/PHASE-13-ROADMAP.md`).
- FASE 13 BLOQUE 1 = **IMPLEMENTED** (13A compliance enforcement, 13B RAG
  persistence, 13C NVIDIA activation — code, tests, migrations, docs done;
  full suite 307 passed / 94% coverage; see `docs/PHASE-2-9-IMPLEMENTATION.md`
  FASE 13 BLOQUE 1 section).
- FASE 13 BLOQUE 2 (13D) = **IMPLEMENTED** (consolidation: explicit
  `COMPLIANCE_ENABLED=false` precedence; `evidence_count` span attribute and
  `evidence_retrieved` counter for audit↔trace↔metrics coherence; docs and
  audit trail finalized; full suite 309 passed / 94% coverage).
- FASE 13 = **CLOSED** after the MASTER CLOSURE AUDIT
  (verdict: PASS WITH ENVIRONMENT-LIMITED VERIFICATION — Docker/PG16/OTLP/CI
  external execution unavailable; see `docs/PHASE-2-9-AUDIT.md`).
- FASE 14+ = **NOT INITIATED**.
- CODE CHANGES = **YES** (BLOQUE 1: 13A/13B/13C; BLOQUE 2: 13D consolidation;
  see IMPLEMENTATION doc).
- MIGRATIONS = **YES** (`0006_evidence_sources`, additive/reversible; head now
  `0006_evidence_sources`; no migration added after 0006).
- NEW DEPENDENCIES = **NONE**.
- CI CHANGES = **NONE**.
- INFRASTRUCTURE CHANGES = **NONE**.
- DOCUMENTATION CHANGES = **YES** (this roadmap's status + baseline; FASE 13
  sections in `PHASE-2-9-IMPLEMENTATION.md`; `docs/PHASE-2-9-AUDIT.md` created
  with the FASE 13 MASTER CLOSURE AUDIT report).

The next authorized execution will be **BLOQUE 1 — FASE 13A + 13B + 13C** (or the
operator's explicit instruction). No BLOQUE 1 work begins without authorization.