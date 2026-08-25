# Sequential phase gates

The implementation roadmap is executed sequentially. A phase is closed only after implementation, tests, static analysis, security checks, and evidence review pass.

| Phase | Focus | Gate |
|---|---|---|
| 2 | Platform foundation | API, persistence, provider, tests, mypy, ruff |
| 3 | Agent foundation | five governed roles + separation of duties |
| 4 | Knowledge and memory | governed evidence + retention/user isolation |
| 5 | Tools and MCP | registry + authorization + critical-risk deny-by-default |
| 6 | Evaluation | release quality gates + regression/security evidence |
| 7 | Observability | correlation, metrics, audit, traces baseline |
| 8 | Deployment | reproducible container/CI/release configuration |
| 9 | Optimization | measurable latency/reliability/cost instrumentation |
| 10 | Idempotency + stale recovery | guarded transitions + idempotency keys |
| 11 | Metrics aggregation + readiness | DB-backed metrics + infrastructure readiness probe |
| 12 | Observability (full) | HTTP tracing, trace↔metrics↔audit correlation, OTLP, sampling, multi-worker |
| 13 | Compliance + RAG + NVIDIA | F-04 enforcement, DB-backed evidence store, remote provider activation |
| 14 | Audit retention + CI/CD | evidence retention policies, stale index, GitHub Actions workflow |
| 15 | Restoration + validation | FASE 15 restoration, NVIDIA guard, compliance rules verification |
| 16 | Consolidation + hardening | artifact cleanup, coverage hardening, documentation |
| 17 | Code quality + operational readiness | Clock fix, DB pool wiring, coverage 96%, stale cleanup, env docs |
| 18 | Productization + real-world usability | Reproducible setup, verification automation, local startup, operational docs, release hygiene | **CLOSED** |
| 19 | Release engineering + maturity validation | CI coverage gate, factory edge-case test, documentation update, final real-world validation | **CLOSED** |
| 20 | Product functionality + user value | Conversation history, context injection, session/request APIs, functional validation & hardening | **CLOSED** |
| 21 | Release & deployment readiness | Reproducible install, production guards, migration integrity, live smoke, security audit, CI/CD audit, wheel build, functional validation | **CLOSED** |
| UI | User-friendly desktop application | Web UI (Chiky agente), dark theme, SPA, launcher, 420 tests, coverage 96%, mypy/ruff clean | **CLOSED** |

For each phase: SPEC → PLAN → TASKS → IMPLEMENT → TEST → EVALUATE → RELEASE.
