# FASE 19 — RELEASE ENGINEERING, PRODUCT MATURITY AND VALIDATION

**Status: CLOSED**

## Objective

Validate the FASE 18 productized project for release readiness: verify reproducibility, fix CI gaps, harden tests, and confirm end-to-end operability.

## Starting baseline

- pytest target: 365 passing
- coverage target: >= 94%
- mypy: PASS (0 errors, 46 source files)
- ruff: PASS (0 errors)
- Alembic head: `0007_evidence_retention_and_stale_index`
- CI/CD: GitHub Actions with quality, integration, and docker jobs

## 19A — Baseline and Reproducibility

- Verified: pytest 366 passed (365 + 1 new factory edge-case test)
- Verified: coverage 98% (5937 statements, 106 missed)
- Verified: mypy 0 errors / 46 source files
- Verified: ruff all checks passed
- Verified: Alembic head `0007_evidence_retention_and_stale_index` (current)
- Verified: `verify_baseline.ps1` passes all gates end-to-end

## 19B — Windows/Local Release Experience

- Verified: `setup_windows.ps1` creates venv, installs deps, generates .env, runs migrations
- Verified: `verify_baseline.ps1` runs pytest+coverage (94% threshold), mypy, ruff, alembic
- Verified: `run_local.ps1` runs alembic upgrade head + uvicorn
- Verified: .env created with safe development defaults only when absent

## 19C — Docker Real Validation

- ENVIRONMENT-LIMITED: Docker daemon not running (Docker Desktop not started)
- Static validation: Dockerfile syntax correct (python:3.13-slim, non-root user, HEALTHCHECK)
- Static validation: docker-entrypoint.sh runs alembic upgrade head + uvicorn
- Static validation: .dockerignore excludes .venv, tests, docs, .env, caches
- CI docker job validated: builds image, runs container smoke test via `python -m personal_ai_secretary.api.app health/live`

## 19D — Failure Handling

- Verified: stale-running recovery via `recover_stale_running()` (test_service.py:560-582)
- Verified: in-flight stale recovery via `execute()` (test_service.py:603-624)
- Verified: idempotency key isolation per user (test_service.py:510-525)
- Verified: idempotency key conflict across sessions (test_service.py:694-715)
- Verified: memory failure does not flip completed to failed (test_service.py:529-556)
- Verified: provider failure marks request as failed (test_service.py:150-158)

## 19E — Observability Operational Readiness

- Verified: audit events carry correlation_id, trace_id, span_id
- Verified: Prometheus metrics endpoint returns valid text format
- Verified: metrics snapshot returns counters + durations
- Verified: audit redaction: sensitive keys/values sanitized (test_security_basics.py)
- Verified: OpenTelemetry in-process tracing produces spans (test_tracing.py)

## 19F — Security Release Check

- Verified: JWT enforcement rejects expired/missing tokens (test_security_basics.py)
- Verified: cross-user request isolation (test_security_basics.py)
- Verified: cross-user audit isolation (test_security_basics.py)
- Verified: audit never leaks authorization headers (test_security_basics.py)
- Verified: evidence content never leaks into audit (test_security_basics.py)
- Verified: compliance blocks expose rule_id but not content (test_security_basics.py)
- Verified: no hardcoded secrets in source code
- Verified: production guards reject default JWT_SECRET and SQLite

## 19G — Database/Recovery Readiness

- Verified: Alembic upgrade creates all 7 tables + alembic_version
- Verified: Alembic downgrade removes all tables (full rollback to base)
- Verified: partial downgrade to 0001 removes post-0001 tables only
- Verified: database init/close lifecycle (lifespan)

## 19H — API Release Validation

- Verified: all endpoints respond with correct HTTP status codes
- Verified: ErrorEnvelope format on 404/403/401/409/422
- Verified: correlation ID in response headers
- Verified: idempotency key handling on POST /requests and POST /sessions/{id}/messages
- Verified: session message round-trip and history
- Verified: high-risk approval flow (blocked without approval, completes with approval)
- Verified: evidence CRUD with persistent_stores enabled
- Verified: observability endpoints require authentication

## 19I — Test Hardening

- Verified: 366 tests all pass (0 failures, 0 errors)
- Verified: tests use in-memory SQLite (no filesystem side effects)
- Verified: tests are independent (no shared mutable state between tests)
- Verified: conftest.py sets safe test defaults (APP_ENV=test, JWT_REQUIRED=false)
- Added: `test_unsupported_provider_mode_raises` (test_factory.py) — covers factory edge case

## 19J — CI/CD Readiness

- Fixed: `ci.yml` quality job now includes `--cov-fail-under=94` (was missing, verify_baseline.ps1 had it)
- Fixed: `ci.yml` integration job now includes `--cov-fail-under=94` (was missing)
- Verified: CI runs on ubuntu-latest with Python 3.13
- Verified: CI has quality (unit), integration (PostgreSQL), and docker jobs
- Verified: CI installs project in editable mode with dev dependencies

## 19K — Documentation

- Updated: `docs/PHASE-GATES.md` — added FASE 19 row (CLOSED)
- Created: `docs/PHASE-19-ROADMAP.md` — full subphase evidence

## 19L — Final Real-World Test

Full 12-step functional test (deterministic mode):

1. POST /api/v1/requests → 202 Accepted
2. POST /api/v1/requests/{id}/execute → 200 completed
3. GET /api/v1/requests/{id} → 200 completed
4. POST /api/v1/sessions/{id}/messages → 200 completed
5. GET /api/v1/sessions/{id}/messages → 200 with history
6. GET /api/v1/health/live → 200 alive
7. GET /api/v1/health/ready → 200 ready
8. GET /api/v1/providers → 200 deterministic
9. GET /api/v1/observability/audit → 200 events
10. GET /api/v1/observability/metrics → 200 snapshot
11. GET /api/v1/metrics → 200 prometheus
12. Compliance block: POST /api/v1/sessions/{id}/messages with prohibited input → 200 blocked

All steps pass.

## Release gates

1. Full pytest suite passes (366/366).
2. Coverage >= 94% (actual: 98%).
3. mypy passes with zero errors (46 source files).
4. ruff passes with zero errors.
5. Alembic head unchanged (`0007_evidence_retention_and_stale_index`).
6. Security review passes.
7. No prohibited/deferred capability introduced.
8. Windows setup and local startup reproducible from clean checkout.
9. CI/CD workflow enforces all gates including coverage threshold.

## Changes

| File | Change |
|---|---|
| `.github/workflows/ci.yml` | Added `--cov-fail-under=94` to quality and integration jobs |
| `tests/unit/test_factory.py` | Added `test_unsupported_provider_mode_raises` |
| `docs/PHASE-GATES.md` | Added FASE 19 row |
| `docs/PHASE-19-ROADMAP.md` | Created |

## Failure policy

If any problem is discovered during implementation, fix the smallest correct root cause immediately, add or update a regression test where applicable, rerun the affected gate, and continue. Do not stop the phase merely to report a fixable defect.
