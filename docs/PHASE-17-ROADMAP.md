# PHASE 17 — OFFICIAL ROADMAP

Status: **FASE 17 — CLOSED (17A+17B+17C+17D IMPLEMENTED; MASTER CLOSURE AUDIT COMPLETE)**
This document is the official definition of FASE 17 subphases and their
implementation blocks. It was produced from a READ-ONLY inspection of the real
tree and all existing roadmaps.

Authoritative sources: `docs/PHASE-16-ROADMAP.md`, `docs/PHASE-14-ROADMAP.md`,
`docs/PHASE-2-9-IMPLEMENTATION.md`, `docs/PHASE-GATES.md`,
`src/personal_ai_secretary/**` (real implementation).

---

## 1. Executive Summary

FASE 16 = **CLOSED** (consolidation + hardening).
FASE 15+ reserved work = **NOT INITIATED** (all items trigger-based).

FASE 17 is the **code quality, coverage hardening, and operational readiness
phase** that addresses evidence-based gaps identified during the FASE 16-17
inspection. It does NOT implement any deferred capability (Redis, Kafka,
Kubernetes, pgvector, multi-region, auto-instrumentation) because no triggers
for those items are met.

The real functional gaps identified:
1. **Dead code** — `advanced_select_rules` and `advanced_evaluate_policy` in
   `compliance/policy.py` are never called (exported but unused).
2. **Coverage gaps** — `api/app.py` (80%), `application/service.py` (79%),
   `infrastructure/stores.py` (85%) have significant uncovered paths.
3. **DB pool settings not wired** — `db_pool_size` and `db_max_overflow` from
   FASE 14B config are defined but not passed to `create_async_engine`.
4. **Stale root-level artifacts** — 12 `.txt` files from previous sessions.
5. **Missing env vars in `.env.example`** — operators need documentation.
6. **Fragile Clock duck-typing** — `PostgresMetricSink` uses `hasattr(now, 'now')`
   which is incorrect for datetime objects.

FASE 17 has **four subphases** (A–D), distributed as two blocks:
- BLOQUE 1: 17A + 17B (dead code cleanup + config wiring)
- BLOQUE 2: 17C + 17D (coverage hardening + consolidation)

---

## 2. Baseline (FASE 17 BASELINE — verified)

| Gate | Result | Notes |
|---|---|---|
| pytest | **339 passed**, coverage **94%** (2190 stmts / 140 missing) | FASE 16 baseline |
| mypy | **PASS (46 source files)** | Unchanged |
| ruff | **All checks passed!** | Unchanged |
| Alembic | head `0007_evidence_retention_and_stale_index` | Unchanged |

---

## 3. Evidence-Based Gap Analysis

| Gap | Evidence | Risk | Priority | FASE |
|---|---|---|---|---|
| Dead code in compliance/policy.py | `advanced_select_rules` and `advanced_evaluate_policy` never called | LOW | HIGH | 17A |
| DB pool settings not wired | `db_pool_size`/`db_max_overflow` in config but not in `create_async_engine` | MEDIUM | HIGH | 17A |
| Coverage gap: api/app.py (80%) | 42 uncovered lines: 404 handlers, exception handlers, sweep loop | LOW | HIGH | 17C |
| Coverage gap: application/service.py (79%) | 43 uncovered lines: idempotency races, send_message, history | LOW | HIGH | 17C |
| Coverage gap: infrastructure/stores.py (85%) | 30 uncovered lines: prune functions, Clock paths | LOW | HIGH | 17C |
| Stale root-level .txt artifacts | 12 files (892KB) from previous sessions | LOW | MEDIUM | 17B |
| Missing env vars in .env.example | `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `AUDIT_RETENTION_SECONDS`, etc. | LOW | MEDIUM | 17B |
| Fragile Clock duck-typing | `hasattr(now, 'now')` is True for datetime objects | MEDIUM | HIGH | 17A |

---

## 4. Reserved Capabilities (FASE 15+ — ALL TRIGGER-BASED)

All items from `docs/PHASE-14-ROADMAP.md` §24 remain **NOT INITIATED** and
**NOT TRIGGERED**:

| Capability | Trigger | Status |
|---|---|---|
| Redis | Proven DB contention, multi-region, measured cache pressure | NOT TRIGGERED |
| Kafka | Real async/decoupling/webhook/background requirement | NOT TRIGGERED |
| Kubernetes | Replica/rollout/platform need | NOT TRIGGERED |
| Distributed caching | Measured read amplification/latency baseline | NOT TRIGGERED |
| Auto-instrumentation | NOT RECOMMENDED unless manual FASE 12 layer removed | NOT TRIGGERED |
| Advanced RAG (pgvector/embeddings) | Measured retrieval amplification | NOT TRIGGERED |
| Multi-region/cloud deployment | Real replicación/orquestación need | NOT TRIGGERED |

**Decision:** No FASE 17 subphase implements any of these.

---

## 5. Proposed Subphases

### 5.1 FASE 17A — Dead Code Cleanup + Clock Fix

1. **Nombre:** FASE 17A — Dead code cleanup and Clock fix.
2. **Objetivo:** remove unused advanced compliance functions and fix the fragile
   Clock duck-typing in PostgresMetricSink.
3. **Problema que resuelve:** `advanced_select_rules` and `advanced_evaluate_policy`
   are exported but never called; `PostgresMetricSink` uses `hasattr(now, 'now')`
   which is incorrect for datetime objects.
4. **Cambios esperados:** remove dead code; fix Clock handling; verify no
   regressions.
5. **Archivos afectados:** `compliance/policy.py` (remove dead code),
   `infrastructure/stores.py` (fix Clock), tests.
6. **Dependencias:** none new.
7. **Migraciones requeridas:** NONE.
8. **Tests requeridos:** full suite green after changes.

### 5.2 FASE 17B — Config Wiring + Artifact Cleanup

1. **Nombre:** FASE 17B — Config wiring and artifact cleanup.
2. **Objetivo:** wire DB pool settings into `create_async_engine`, document
   missing env vars, clean stale artifacts.
3. **Problema que resuelve:** `db_pool_size` and `db_max_overflow` from FASE 14B
   are defined but not used; `.env.example` is incomplete; 12 stale `.txt` files
   pollute the project root.
4. **Cambios esperados:** wire pool settings; update `.env.example`; delete stale
   `.txt` files; update `.gitignore`.
5. **Archivos afectados:** `infrastructure/database.py`, `.env.example`,
   `.gitignore`, root `.txt` files (DELETE).
6. **Dependencias:** none new.
7. **Migraciones requeridas:** NONE.
8. **Tests requeridos:** full suite green after changes.

### 5.3 FASE 17C — Coverage Hardening

1. **Nombre:** FASE 17C — Coverage hardening.
2. **Objetivo:** close coverage gaps in `api/app.py`, `application/service.py`,
   and `infrastructure/stores.py` by adding targeted tests.
3. **Problema que resuelve:** these three files have 80%, 79%, and 85% coverage
   respectively; many uncovered paths are genuinely testable (404 handlers,
   exception handlers, prune functions).
4. **Cambios esperados:** targeted test additions; coverage improvement to ≥95%.
5. **Archivos afectados:** test files (additions only).
6. **Dependencias:** none new.
7. **Migraciones requeridas:** NONE.
8. **Tests requeridos:** new tests for uncovered paths; full suite green.

### 5.4 FASE 17D — Consolidation and Phase Deliverables

1. **Nombre:** FASE 17D — Consolidation.
2. **Objetivo:** standardize and document the complete FASE 17 surface and
   produce the evidence pack that lets the FASE 17 closure run.
3. **Cambios esperados:** update docs; final regression; produce gate matrix.
4. **Archivos afectados:** docs only.
5. **Dependencias:** consumes 17A, 17B, 17C.
6. **Tests requeridos:** full suite; cross-subphase consistency.

---

## 6. Dependency Graph

```
17A (dead code + Clock) ──┐
                          ├── 17D (consolidation)
17B (config + cleanup) ────┤
                          │
17C (coverage) ────────────┘
```

---

## 7. Implementation Blocks

### BLOQUE 1: 17A + 17B
Dead code cleanup + config wiring + artifact cleanup. Both are additive
(or subtractive) and have no cross-block feature leakage.

### BLOQUE 2: 17C + 17D
Coverage hardening + consolidation. 17C adds tests; 17D consolidates.

---

## 8. Gates per Block

### BLOQUE 1 (17A+17B) — Smoke Gate
- pytest (no regression)
- mypy src PASS; ruff check . PASS
- Alembic head unchanged

### BLOQUE 2 (17C+17D) — Smoke Gate
- Full regression (FASES 2–17)
- mypy src PASS; ruff check . PASS
- Coverage ≥95%
- Full security review + docs review

---

## 9. Explicitly Out of Scope (FASE 17)

- Redis, Kafka, Kubernetes, distributed caching, auto-instrumentation (15+).
- New providers, vector/embeddings, advanced RAG (15+).
- Multi-region/cloud deployment, billing, frontend, autonomous loops (15+).
- Any HTTP contract break, any removal of existing guards.
- Any new feature implementation.

---

## 10. Confirmation / Status

- FASE 14 = **CLOSED**
- FASE 15 = **CLOSED**
- FASE 16 = **CLOSED**
- FASE 17 = **CLOSED** (this document)
- FASE 15+ reserved work = **NOT INITIATED** (all trigger-based)

CODE CHANGES = **YES** (17A: dead code removal + Clock fix; 17B: config wiring;
17C: test additions)
MIGRATIONS = **NONE**
NEW DEPENDENCIES = **NONE**
CI CHANGES = **NONE**
INFRASTRUCTURE CHANGES = **NONE**
DOCUMENTATION CHANGES = **YES** (this document; PHASE-GATES.md update;
.env.example update)
