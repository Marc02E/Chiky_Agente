# PHASE 12 — OFFICIAL ROADMAP (FASE 12B–12G)

Status: **CLOSED — FASE 12 (12A–12G) IMPLEMENTED AND CLOSED VIA MASTER CLOSURE AUDIT (2026-08-16, PASS WITH ENVIRONMENT-LIMITED VERIFICATION)**
This document is the official definition of FASE 12 subphases B–G. It was
produced from a READ-ONLY inspection of the real tree (no code, migration,
dependency, CI, or infrastructure changes). It supersedes any earlier
assumption that items such as Redis, Kafka, Kubernetes, RAG persistence,
caching, or new providers belong to FASE 12.

Authoritative sources: `PHASE-2-9-AUDIT.md`, `docs/PHASE-2-9-IMPLEMENTATION.md`
(FASE 12A section), `docs/PHASE-GATES.md`, `src/personal_ai_secretary/**`
(real implementation), `.github/workflows/ci.yml`.

---

## 1. Current tree state (verified read-only)

- FASES 2–11: CLOSED. FASE 12A: CLOSED (`PASS WITH ENVIRONMENT-LIMITED VERIFICATION`).
- FASE 12A implemented OpenTelemetry **in-process** tracing: `observability/tracing.py`
  (`init_tracing`, `shutdown_tracing`, `start_span`, `set_span_correlation`,
  `mark_span_error`, `get_recorded_spans`, `clear_recorded_spans`); wiring in
  `api/app.py` lifespan; `workflow.run` + 8 stage spans in `workflow/engine.py`;
  `provider.run` / `tool.run` spans in `agents/builtin.py`; optional OTLP/HTTP
  export (lazy import, `/v1/traces` appended) with in-memory fallback.
- Baseline: **234 tests, 92% coverage, mypy PASS (44 files), ruff PASS**,
  Alembic head `0005_metric_records`. PG16, Docker, multi-worker/réplica,
  idempotencia, readiness, observability, audit/security VERIFIED. CI real and
  OTLP cloud UNVERIFIED (environmental).
- Documented FASE 12A limitations (the technical debt FASE 12 must resolve):
  1. no traces on the HTTP request layer;
  2. no span-to-metrics correlation beyond `correlation_id`;
  3. no auto-instrumentation;
  4. OTLP cloud/external export not exercised.

## 2. What FASE 12 is about

FASE 12 is the **tracing / observability phase** that started with 12A. Its
purpose is to complete the observable request→workflow path with the existing
manual-span, no-auto-instrumentation architecture, while preserving all FASE
2–11 contracts and gates. Everything outside that purpose is FASE 13+.

## 3. Subphase inventory (B–G) and dependency graph

```
12A (CLOSED) — in-process tracing + optional OTLP local
   |
   +-----> 12B  HTTP request-layer tracing            (MUST HAVE)
   |          |
   |          +-----> 12C  trace <-> metrics <-> audit correlation (SHOULD HAVE)
   |
   +-----> 12D  OTLP export hardening + cloud verify   (SHOULD HAVE)
   |          |
   |          +-----> 12E  sampling / resource / processor governance (SHOULD HAVE)
   |
   12F  multi-worker trace verification + propagation  (SHOULD HAVE)
   |
   12G  observability consolidation + phase deliverables (MUST HAVE — closes phase)
   |
   MASTER CLOSURE AUDIT (after ALL of B–G)
```

Branching is technically valid: 12B→12C is one chain; 12D→12E is a second,
independent chain. Both chains only meet again at 12F/12G. This permits
parallel implementation if the operator ever allows it; the default execution
below uses the requested sequential block grouping.

## 4. Recommended implementation blocks

### BLOQUE 1: 12B + 12C + 12D
Technically compatible: all are additive tracing work touching `api/app.py`,
`observability/*`, and `shared/config.py`. No schema, no migration, no
contract change, no new dependency.
- 12B must land first (it creates the request-layer span that 12C links to).
- 12C depends on 12B's span being present.
- 12D is independent of both and can ride along safely.

### BLOQUE 2: 12E + 12F + 12G
Technically compatible: 12E (config governance) is additive and independent;
12F (multi-worker verification + propagation) builds on 12B/12D; 12G is the
consolidation/closure subphase that consumes all previous work.

Stop rule: if any subphase reveals an architectural, security, migration, or
infrastructure risk that makes continuing unsafe, stop the block and run an
isolated closure audit for the completed subphases before resuming.

## 5. FASE 12B — HTTP request-layer tracing

1. **Official name:** FASE 12B — HTTP request-layer tracing.
2. **Objetivo:** give every API request a root span so the request→workflow
   trace tree is complete end-to-end (currently only the workflow is traced).
3. **Problema que resuelve:** documented 12A limitation "no traces on the HTTP
   request layer"; operators cannot correlate an incoming HTTP call with the
   workflow it triggered.
4. **Alcance exacto:** start a middleware-level span per request in the
   existing correlation middleware (`api/app.py` `correlation_middleware`),
   set `correlation_id`, `http.method`, `http.target`/route, `http.status_code`;
   end the span on response and mark ERROR on exception. The `workflow.run`
   span (12A) becomes a child automatically via the current-span context.
5. **Entregables:** `api/app.py` middleware span; optional `http_request`
   helper in `observability/tracing.py`; tests; docs.
6. **Archivos/componentes probablemente afectados:** `api/app.py`,
   `observability/tracing.py` (helper only), `tests/contract/test_openapi.py`,
   `tests/integration/test_requests_api.py`, `tests/integration/test_health.py`.
7. **Dependencias:** none new; reuses 12A `start_span`/`set_span_correlation`/
   `mark_span_error`.
8. **Dependencias respecto a subfases anteriores:** depends on 12A only.
9. **Qué NO pertenece:** auto-instrumentation, trace context propagation to
   downstream services (12F), cloud OTLP (12D).
10. **Riesgo técnico:** LOW — additive middleware; must not regress the 401/404/
    409/503 exception paths and must end spans on exceptions.
11. **Riesgo de seguridad:** LOW — attributes are method/path/status/IDs only;
    never headers, tokens, or payloads. Path may contain user data → use the
    route template, not the raw path.
12. **Impacto PostgreSQL/Alembic:** NONE.
13. **Impacto Docker:** NONE (no new deps); re-run build/smoke only as gate.
14. **Impacto multi-worker/multi-réplica:** spans are per-process per request;
    no shared state; unchanged from 12A.
15. **Tests requeridos:** middleware span present with correct status/route
    attributes; workflow span is a child; ERROR on failing endpoint; span ends
    on all branches; correlation_id echoed.
16. **Gates mínimos:** pytest, mypy, ruff, Docker smoke, local OTLP e2e.
17. **Criterios de aceptación:** every HTTP 2xx/4xx/5xx request produces a
    request-layer span exported to the local collector with `correlation_id`;
    suite green.
18. **Dependencias para la siguiente subfase:** provides the request-layer span
    that 12C links to metrics/audit.

## 6. FASE 12C — Trace ↔ metrics ↔ audit correlation

1. **Official name:** FASE 12C — trace↔metrics↔audit correlation.
2. **Objetivo:** link `trace_id`/`span_id` to audit events and metric
   snapshots so a single `correlation_id`/trace can be walked across logs,
   spans, metrics, and audit.
3. **Problema que resuelve:** documented 12A limitation "no span-to-metrics
   correlation beyond correlation_id"; today audit/metrics only carry
   `correlation_id`.
4. **Alcance exacto:** capture the current span/trace context in
   `Observability.emit` and persist `trace_id`/`span_id` inside audit details;
   record the current `trace_id` alongside duration gauges and expose it on
   the metrics snapshot/readiness where useful. Details column is JSONB/JSON so
   **no migration is required**.
5. **Entregables:** `observability/observer.py` changes; audit/metrics
   integration; tests; docs.
6. **Archivos/componentes probablemente afectados:** `observability/observer.py`,
   `observability/audit.py` (details mapping), `api/app.py` (metrics endpoint
   payload), `domain/contracts.py` (optional response field, contract-additive),
   `tests/unit/test_observability.py`.
7. **Dependencias:** none new.
8. **Dependencias respecto a subfases anteriores:** depends on 12B (request
   span) for a span to exist at emit time; still functional without 12B for
   workflow-level spans.
9. **Qué NO pertenece:** DB trace storage, trace sampling, exporter tuning.
10. **Riesgo técnico:** LOW — additive fields; keep response contract backward
    compatible (new optional fields only).
11. **Riesgo de seguridad:** LOW — `trace_id`/`span_id` are non-sensitive;
    must not add payloads to audit.
12. **Impacto PostgreSQL/Alembic:** NONE (JSONB details; no new table/column).
13. **Impacto Docker:** NONE.
14. **Impacto multi-worker/multi-réplica:** NONE; correlation is per-event.
15. **Tests requeridos:** audit events carry trace_id/span_id when a span is
    active; metrics snapshot optionally carries trace context; 12A in-memory
    tests still green.
16. **Gates mínimos:** pytest, mypy, ruff, local OTLP e2e.
17. **Criterios de aceptación:** a single request's span, audit event, and
    metric observation share the same trace/correlation identifiers.
18. **Dependencias para la siguiente subfase:** none (12C does not gate 12D/12E).

## 7. FASE 12D — OTLP export hardening and cloud/external verification

1. **Official name:** FASE 12D — OTLP export hardening + external verification.
2. **Objetivo:** make OTLP export production-ready (configurable timeout,
   optional headers, compression, batching) and re-verify export against the
   local collector, documenting external/cloud export.
3. **Problema que resuelve:** 12A left OTLP cloud UNVERIFIED and used default
   `BatchSpanProcessor`/exporter settings; no way to set sampling/headers/TLS
   or to bound export cost.
4. **Alcance exacto:** add config surface (sampling ratio is 12E; here: exporter
   timeout, optional static headers, compression toggle, batch schedule) in
   `shared/config.py`; wire into `observability/tracing.py`; re-run the local
   collector e2e; attempt external/cloud verification where the environment
   permits and document the result (UNVERIFIED if it cannot be exercised).
5. **Entregables:** config fields, exporter wiring, local e2e evidence, docs.
6. **Archivos/componentes probablemente afectados:** `shared/config.py`,
   `observability/tracing.py`, `.env.example`, `docs/PHASE-2-9-IMPLEMENTATION.md`.
7. **Dependencias:** none new (requests/httpx already present via exporter).
8. **Dependencias respecto a subfases anteriores:** depends on 12A only.
9. **Qué NO pertenece:** sampling ratio governance (12E), trace propagation (12F).
10. **Riesgo técnico:** MEDIUM — exporter config must remain backward
    compatible with the default no-endpoint in-memory path.
11. **Riesgo de seguridad:** MEDIUM — headers may carry credentials; they must
    be sourced from env, never logged, never placed on spans; default empty.
12. **Impacto PostgreSQL/Alembic:** NONE.
13. **Impacto Docker:** LOW — env passthrough for OTLP settings; image unchanged.
14. **Impacto multi-worker/multi-réplica:** per-worker export unchanged.
15. **Tests requeridos:** exporter uses configured timeout/headers/compression
    (monkeypatched), no-endpoint path unchanged, local collector e2e re-run.
16. **Gates mínimos:** pytest, mypy, ruff, local OTLP e2e.
17. **Criterios de aceptación:** OTLP settings honored; local export re-verified;
    cloud result documented as VERIFIED or UNVERIFIED with evidence.
18. **Dependencias para la siguiente subfase:** provides the config surface
    that 12E extends with sampling/resource governance.

## 8. FASE 12E — Sampling, resource, and processor governance

1. **Official name:** FASE 12E — tracing configuration governance.
2. **Objetivo:** bound trace cost and standardize span identity via
   configurable sampling ratio, service resource attributes, and batch
   processor parameters.
3. **Problema que resuelve:** no production control over trace volume; spans
   all share the same `service.name`; no sampling/retention knobs.
4. **Alcance exacto:** add `otel_sampling_ratio` (parent-based/default),
   resource attributes (deployment env, app version, worker id), and optional
   batch schedule/queue knobs; wire into `init_tracing`; keep in-memory and
   no-op paths identical in behavior.
5. **Entregables:** config + wiring, sampling tests (ratio 0/1/in-between),
   docs.
6. **Archivos/componentes probablemente afectados:** `shared/config.py`,
   `observability/tracing.py`, `.env.example`, `tests/unit/test_tracing.py`.
7. **Dependencias:** none new.
8. **Dependencias respecto a subfases anteriores:** builds on 12D's config
   surface; independent of 12B/12C/12F.
9. **Qué NO pertenece:** request-layer spans (12B), correlation (12C), cloud
   verification (12D), propagation (12F).
10. **Riesgo técnico:** LOW — sampling must not break `get_recorded_spans`
    tests when ratio is 1 or disabled mode.
11. **Riesgo de seguridad:** LOW — resource attributes are non-sensitive.
12. **Impacto PostgreSQL/Alembic:** NONE.
13. **Impacto Docker:** NONE.
14. **Impacto multi-worker/multi-réplica:** each worker applies the same
    config independently; worker id in resource helps attribute spans.
15. **Tests requeridos:** ratio=0 emits nothing in-memory; ratio=1 emits all;
    default = 1 (current behavior); no-op mode unaffected.
16. **Gates mínimos:** pytest, mypy, ruff.
17. **Criterios de aceptación:** sampling and resource attributes configurable
    and verified; default behavior identical to 12A.
18. **Dependencias para la siguiente subfase:** none beyond 12D.

## 9. FASE 12F — Multi-worker trace verification and trace propagation

1. **Official name:** FASE 12F — multi-worker trace verification + propagation.
2. **Objetivo:** prove each worker exports its own traces to a shared local
   collector and propagate a W3C-style trace header across the request
   boundary for distributed correlation.
3. **Problema que resuelve:** multi-worker/multi-réplica behavior was verified
   for metrics/audit but not for traces; no inbound/outbound trace context.
4. **Alcance exacto:** verify two workers / two replicas each exporting their
   request+workflow spans to the same collector (distinct worker ids in
   resource); add optional inbound `traceparent` parsing in the correlation
   middleware and outbound propagation on provider/tool HTTP calls if the
   architecture permits it (small, additive).
5. **Entregables:** multi-worker collector e2e evidence; optional propagation;
   docs.
6. **Archivos/componentes probablemente afectados:** `api/app.py` (header
   parse), `observability/tracing.py` (propagation helpers), `providers/ollama.py`
   /`providers/remote.py` (outbound header, optional), Docker e2e scripts.
7. **Dependencias:** none new (W3C traceparent is a string header; OpenTelemetry
   `tracecontext` propagator is bundled with the OTel SDK already present).
8. **Dependencias respecto a subfases anteriores:** depends on 12B (request
   span) and 12D (export verification).
9. **Qué NO pertenece:** cloud verification (12D), sampling (12E), new
   providers (FASE 13+).
10. **Riesgo técnico:** MEDIUM — propagation must be opt-in and must not alter
    API contracts or break when the header is absent.
11. **Riesgo de seguridad:** LOW — only a trace context string crosses
    boundaries; must be validated/ignored when malformed.
12. **Impacto PostgreSQL/Alembic:** NONE.
13. **Impacto Docker:** LOW — e2e topology only (collector + 2 app replicas).
14. **Impacto multi-worker/multi-réplica:** the core verification target.
15. **Tests requeridos:** multi-worker collector receives spans from both
    workers with distinct worker-id resources; malformed `traceparent`
    ignored; absence of header = normal behavior.
16. **Gates mínimos:** pytest, mypy, ruff, multi-worker collector e2e.
17. **Criterios de aceptación:** traces from 2 workers/replicas land in one
    collector and are distinguishable; propagation (if added) is opt-in and
    safe.
18. **Dependencias para la siguiente subfase:** feeds 12G consolidation.

## 10. FASE 12G — Observability consolidation and phase deliverables

1. **Official name:** FASE 12G — observability consolidation.
2. **Objetivo:** standardize and document the complete trace/metric/audit
   picture and produce the artifacts that let the MASTER CLOSURE AUDIT of
   FASE 12 run.
3. **Problema que resuelve:** after B–F the observability surface is complete
   but not documented as a single coherent contract.
4. **Alcance exacto:** unify span attribute naming, resource attributes, and
   correlation across request/workflow/provider/tool spans; finalize docs
   (`docs/PHASE-2-9-IMPLEMENTATION.md`, `PHASE-2-9-AUDIT.md`); final full
   regression and Docker/PG16/OTLP evidence capture.
5. **Entregables:** consolidated span attribute guide, updated docs, final gate
   evidence pack.
6. **Archivos/componentes probablemente afectados:** `observability/tracing.py`
   (attribute constants), `workflow/engine.py`, `agents/builtin.py` (attribute
   names), docs, tests.
7. **Dependencias:** none new.
8. **Dependencias respecto a subfases anteriores:** consumes B–F.
9. **Qué NO pertenece:** new features; only consolidation and documentation.
10. **Riesgo técnico:** LOW — attribute renaming must be covered by tests to
    avoid silent breakage.
11. **Riesgo de seguridad:** LOW — no new data recorded.
12. **Impacto PostgreSQL/Alembic:** NONE.
13. **Impacto Docker:** NONE.
14. **Impacto multi-worker/multi-réplica:** NONE beyond 12F evidence.
15. **Tests requeridos:** full suite; span attribute consistency tests.
16. **Gates mínimos:** pytest, mypy, ruff, alembic, PG16 smoke, Docker smoke,
    OTLP e2e.
17. **Criterios de aceptación:** the whole FASE 12 gate matrix is green and
    documented; phase is ready for the MASTER CLOSURE AUDIT.
18. **Dependencias para la siguiente subfase:** none within FASE 12; enables
    the Master Closure Audit only.

## 11. Prioritization and change classification

| Subphase | Priority | Justification |
|---|---|---|
| 12B | MUST HAVE | Closes the core 12A limitation (request-layer span); everything else links to it. |
| 12C | SHOULD HAVE | High value correlation; additive; can be sequenced without blocking others. |
| 12D | SHOULD HAVE | Resolves the UNVERIFIED OTLP cloud item; production readiness. |
| 12E | SHOULD HAVE | Cost/identity control; additive and low risk. |
| 12F | SHOULD HAVE | Multi-worker trace proof; matches the project's verification culture. |
| 12G | MUST HAVE | Required to close FASE 12 properly. |

Change classification (all B–G):
- Pure additive (A): 12B, 12C, 12E, 12G.
- Additive config with minimal behavior surface (A/B): 12D.
- Additive + verification/infra evidence (A/F): 12F.
- No contract (C), schema/migration (D), security (E) changes are planned in
  FASE 12 beyond optional additive response fields in 12C.

## 12. Reserved for FASE 13+ (explicitly NOT part of FASE 12)

The following are documented as reserved/deferred in the real tree and are
**NOT assigned to FASE 12** because architectural analysis shows they belong to
later phases (new infrastructure, new providers, or new enforcement):

- Redis / distributed caching — RESERVED FOR FASE 13+
- Kafka / event streaming — RESERVED FOR FASE 13+
- Kubernetes / orchestration — RESERVED FOR FASE 13+
- RAG persistence (DB-backed evidence store) — RESERVED FOR FASE 13+
- New providers (remote NVIDIA activation) — RESERVED FOR FASE 13+
- F-04 compliance enforcement — RESERVED FOR FASE 13+
- External CI execution — RESERVED FOR FASE 13+ (environmental)
- Auto-instrumentation (opentelemetry-instrumentation packages) — RESERVED FOR
  FASE 13+ (FASE 12 continues the manual-span 12A architecture)

## 13. Gates per block

### BLOQUE 1 (12B+12C+12D) — Smoke Gate
- pytest `--cov=src` (no regression, ≥92%)
- mypy src PASS; ruff check . PASS
- Alembic head unchanged (`0005_metric_records`)
- Docker build + run + HTTP smoke
- Local OTLP collector e2e (request-layer + workflow spans received)
- No security/schema/contract regressions (Spot-check)

### BLOQUE 2 (12E+12F+12G) — Smoke Gate
- pytest `--cov=src` (no regression)
- mypy src PASS; ruff check . PASS
- Alembic head unchanged
- Docker build + run + HTTP smoke
- Local OTLP collector e2e + multi-worker/replica collector e2e
- Full security review + docs review

## 14. Master closure audit (after ALL of 12B–12G)

Full independent audit: gates, alembic chain, PG16 real, Docker, multi-worker,
idempotencia, readiness, observability (traces+metrics+audit), security, docs,
cleanup, and the mandatory FASE 12 closure report. CI real and OTLP cloud stay
environment-limited UNVERIFIED unless the environment changes.

## 15. Confirmation

- FASE 12B–12G = IMPLEMENTED (BLOQUE 1 + BLOQUE 2 CLOSED, see `PHASE-2-9-AUDIT.md` closure audits)
- FASE 12 = NOT CLOSED (pending the FASE 12 — MASTER CLOSURE AUDIT / FINAL VERIFICATION)
- FASE 13+ = NOT INITIATED

BLOQUE 1 (12B/12C/12D) and BLOQUE 2 (12E/12F/12G) introduced additive code/tests/config only: no migrations, no dependencies, no CI changes, no infra changes.