# FASE 18 — PRODUCTIZATION, OPERATIONAL READINESS AND REAL-WORLD USABILITY

**Status: CLOSED**

## Objective

Convert the verified post-FASE 17 project into a reproducible, operator-friendly local product without introducing deferred capabilities that have not been authorized.

## Starting baseline

- pytest target: 358 passing
- coverage target: >= 94%
- mypy: PASS
- ruff: PASS
- Alembic head: `0007_evidence_retention_and_stale_index`
- SQLite local development remains supported
- Deterministic provider remains the zero-dependency functional baseline
- Redis, Kafka, Kubernetes, pgvector/embeddings, multi-region, distributed caching, auto-instrumentation, billing, frontend and autonomous loops remain out of scope unless explicitly authorized

## 18A — Reproducible Windows setup

- Provide a PowerShell bootstrap script.
- Create `.venv` when absent.
- Install the project in editable mode with development dependencies.
- Create a safe development `.env` only when absent.
- Run Alembic migrations.

## 18B — Baseline verification automation

- Provide one PowerShell verification command for pytest + coverage, mypy, ruff and Alembic.
- Preserve the existing gates; do not weaken thresholds or exclude tests.

## 18C — Local operator startup

- Provide a PowerShell launcher for the API.
- Keep deterministic mode as the default local path.
- Preserve the existing documented health and smoke-test flow.

## 18D — Documentation consolidation

- Make the setup path explicit for Windows/PowerShell.
- Keep provider terminology consistent: `deterministic`, `local`, `remote`.
- Keep secrets out of distributable project archives.

## 18E — Full productization work

After the pre-flight baseline is green, continue with evidence-driven usability improvements: configuration ergonomics, startup diagnostics, operational status, safe local persistence, packaging/release hygiene, and end-to-end operator documentation.

No capability is added merely because it is technically possible. Each change must have a test or explicit operational verification and must preserve the phase gates.

## Release gates

1. Full pytest suite passes.
2. Coverage remains >= 94%.
3. mypy passes with zero errors.
4. ruff passes with zero errors.
5. Alembic head remains unchanged unless a migration is explicitly authorized.
6. Security review passes.
7. No prohibited/deferred capability is introduced.
8. Windows setup and local startup are reproducible from a clean checkout/archive.

## Failure policy

If any problem is discovered during implementation, fix the smallest correct root cause immediately, add or update a regression test where applicable, rerun the affected gate, and continue. Do not stop the phase merely to report a fixable defect.
