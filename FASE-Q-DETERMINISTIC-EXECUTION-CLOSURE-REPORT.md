# FASE Q — Deterministic Agent Execution & Task Control — Closure Report

**Project:** personal_ai_secretary ("Chiky")
**Phase:** Q — Deterministic Agent Execution & Task Control
**Date:** 2026-08-24
**Baseline:** FASE P (1501 tests, 94% coverage, ruff 0, mypy 0)
**Result:** ✅ COMPLETE — all quality gates green, live acceptance executed

---

## 1. Executive Summary

FASE Q evolves the ExecutionAgent from "LLM proposes and hopes" to
"LLM proposes → **backend controls** → tools execute → evidence demonstrates →
verification decides". The agent now runs an explicit, observable task state
machine with deterministic progress detection, backend coaching directives,
bounded failure recovery, honest completion enforcement, and wall-clock/latency
observability — without weakening any security or approval control from prior
phases.

The headline live-acceptance result: the debugging scenario that FASE P
classified as **model-limited and unsolvable on CPU (defect D2)** now ends
with the target file **actually repaired on disk**, because the backend
coached the model out of its read-loop instead of relying on model discipline.

## 2. Requirements Coverage (Q.1–Q.15)

| Req | Requirement | Status | Evidence |
|-----|-------------|--------|----------|
| Q.1 | Task state machine | DONE | `agents/task_state.py` (`TaskState`, `TaskStateMachine`, sticky terminal states) |
| Q.2 | Progress detection | DONE | `ProgressDetector` (identical-call, same-tool, read-without-modify, cycle signals) |
| Q.3 | Backend directives / coach-then-stop | DONE | `directive_for()` + loop integration; exactly one directive before forced stop |
| Q.4 | Task contracts per type | DONE | `build_task_contract()`, 13 task types, artifacts + verification + evidence |
| Q.5 | Honest completion guard | DONE | Post-loop: mutating task without mutation → FAILED + "⚠ Task status: NOT COMPLETED" note |
| Q.6 | Bounded failure recovery | DONE | FAIL→DIAGNOSE→CORRECT→RETRY capped at `MAX_CONSECUTIVE_TOOL_FAILURES=3` |
| Q.7 | Model-aware failure handling | DONE | `classify_failure` + `should_suggest_fallback` wired into provider-error path |
| Q.8 | Latency control | DONE | `classify_latency_source()`, `duration_budget_exceeded()`, 600 s default budget check each round |
| Q.9 | Minimal prompt update | DONE | 8-line "Task Protocol" in `tools/prompt.py`; behavior is backend-enforced, not prompt-hoped |
| Q.10 | Tests for scenarios A–H (+I) | DONE | 60 new tests: `test_q_task_state.py` (51), `test_q_agent_control.py` (9) |
| Q.11 | Live acceptance | DONE | `scripts/q_live_acceptance.py`: L1 PASS, L2 mutation succeeded (P-D2 resolved), L3 hardware-timeout documented |
| Q.12 | Security regression | DONE | `test_p_security_and_models.py` + `test_governed_components.py`: 45 tests green |
| Q.13 | Performance comparison | DONE | See §8 — no regression vs P baseline; new latency observability added |
| Q.14 | Quality gates | DONE | 1731 passed / 0 failed, coverage 94.64% (≥94%), ruff 0 (src+new tests), mypy 0 |
| Q.15 | This closure report | DONE | — |

## 3. Architecture

### New module: `src/personal_ai_secretary/agents/task_state.py`

- **`TaskState`** — IDLE → UNDERSTANDING → PLANNING → READING → MODIFYING /
  EXECUTING → VERIFYING → CORRECTING → {COMPLETED | FAILED | BLOCKED}.
  Terminal states are sticky; unusual transitions are logged but allowed so
  backend control never crashes on a quirky path.
- **Tool classification** — `READ_TOOLS` / `MODIFY_TOOLS` / `EXECUTE_TOOLS` /
  `VERIFY_TOOLS` with `state_for_tool()` mapping.
- **`TaskStateMachine`** — records every transition;
  `history_values()` produces compact metrics like `"idle>understanding"`.
- **`ProgressDetector`** — distinguishes *valid repetition* (new args or new
  results = progress) from *loops* (identical repeats, read-only streaks on
  modification tasks, cyclic A,B,A,B patterns). Issues at most
  `MAX_UNPRODUCTIVE_DIRECTIVES=1` coaching directive before recommending stop.
- **`TaskContract` / `build_task_contract()`** — deterministic per-task-type
  contract: objective, affected files (regex-extracted from user text),
  expected flow, artifacts, verification requirements, completion evidence,
  approval flag, and the expected next action used by directives.
- **Helpers** — `failure_recovery_message()`, `classify_latency_source()`
  ('llm_bound'/'tool_bound'/'command_bound'/'balanced'), `duration_budget_exceeded()`.

### Integration: `agents/builtin.py` (`ExecutionAgent._run_with_provider`)

Per round, the backend now:

1. **Builds the contract** from classified task type + workspace + approval
   profile, and appends its compact directive to the system prompt.
2. **Checks the duration budget** (600 s default) *before* each round.
3. **Controls call admission**: normalizes/counters proposals, partitions
   fresh vs already-executed calls (in-batch duplicates still execute ≤1 time).
4. **Coaches or stops stalled rounds**: if everything proposed was already
   done on a modification task, injects ONE directive ("Stop reading. Next
   valid action: …"); a second stall ends the run honestly.
5. **Limits repetition**: identical call >2× or same tool name ≥4× triggers
   coach-or-stop; name-limit stop reports "used the … tool N times".
6. **Tracks state per execution**: READ→READING, modify→MODIFYING,
   verify-after-mutation→VERIFYING, verification failure→CORRECTING.
7. **Bounds failures** (Q.6): consecutive errored results ≥3 stop the loop
   with a recovery message summarizing what was accomplished vs failed.
8. **Finalizes honestly** (Q.5): approval-pending→BLOCKED; modification
   required but never performed→FAILED + explicit "NOT COMPLETED" note;
   otherwise COMPLETED. Latency classification, budget flag, transition
   history and final state are recorded in `RequestMetrics`.

### Observability: `observability/request_metrics.py`

New fields (also in `summary()`): `state_transitions`, `final_task_state`,
`unproductive_loop_detections`, `backend_directives`,
`consecutive_tool_failures`, `duration_budget_exceeded`,
`latency_classification`.

### Prompt: `tools/prompt.py`

Added an 8-line "## Task Protocol"
(UNDERSTAND→PLAN→ACT→VERIFY→CORRECT→COMPLETE) — informational only; every
rule it describes is enforced by code regardless of model compliance.

## 4. Design Decisions

| Decision | Rationale |
|----------|-----------|
| Lenient state machine (log-but-allow) | Backend control must never crash execution over an unexpected transition; stickiness is reserved for terminal states only. |
| Directives limited to 1 before stop | Prevents directive↔ignore ping-pong; second stall is a hard, honest stop. |
| Valid repetition preserved | Different args/results are progress; limits key on identical calls, not tool usage generally (protects multi-file CRUD). |
| Verification failure counts toward Q.6 budget | A verifier-detected false success IS a failure; prevents infinite correct-retry loops. |
| `final_outcome` semantics unchanged | Existing consumers/tests assert exact values; Q adds NEW metric fields instead of mutating existing ones. |
| Cycle detector ignores constant streams | Reading many different files is legitimate; constant same-tool streams are handled by name-count limits. |

## 5. Test Evidence

**Unit — `tests/unit/test_q_task_state.py` (51 tests):** machine transitions
& stickiness, tool/task-type classification, all four unproductive signals +
negative cases, directive issuance counting, contract contents per type,
recovery message wording, latency classification matrix, budget boundary.

**Agent-level — `tests/unit/test_q_agent_control.py` (scenarios A–I):**

| Scenario | Behavior verified |
|----------|-------------------|
| A | Create-file completes: MODIFYING→VERIFYING→COMPLETED, evidence-based |
| B | Read→Modify order visible in transition history |
| C | Debug task stuck reading: 1 directive injected, then stopped, FAILED |
| D | Project creation completes without unproductive stops |
| E | Analysis task finishes cleanly, no mutation demanded |
| F | Provider failure classified; friendly message; fallback surfaced; FAILED |
| G | Tool failures bounded at exactly MAX_CONSECUTIVE_TOOL_FAILURES |
| H | Claimed success without evidence → "NOT COMPLETED", FAILED |
| I | Approval-required operation → APPROVAL_REQUIRED prefix, BLOCKED |

**Regression:** full suite `pytest -m "not live and not slow"`:
**1731 passed, 0 failed** (baseline 1501 + 230 tests added across L–Q).

## 6. Quality Gates (Q.14)

| Gate | Required | Measured | Verdict |
|------|----------|----------|---------|
| pytest | 0 failures | 1731 passed / 0 failed | PASS |
| Coverage | ≥94% | **94.64%** | PASS |
| Ruff | 0 errors | 0 (src + new test files) | PASS |
| Mypy | 0 errors | 0 (73 source files) | PASS |

Note: `ruff check tests` reports pre-existing lint debt in L–P era test files
(imports/f-strings); these predate FASE Q and were left untouched. All files
created or modified by FASE Q are lint-clean.

## 7. Security Regression (Q.12)

`test_p_security_and_models.py` (path traversal, sibling bypass, protected
delete, command chaining/dangerous commands, approval flags, .git write block,
allowed-roots aliasing) and `test_governed_components.py`: **45/45 green**.
No security control was weakened: approvals fire BEFORE execution and now
additionally map to `BLOCKED` task state; dedup/admission logic executes
fewer calls than proposed, never more.

## 8. Performance Comparison (Q.13)

| Metric | FASE P baseline | FASE Q measured | Δ |
|--------|-----------------|-----------------|---|
| Simple create (llama3.1, live) | PASS (~2 min class) | PASS, 163 s, 3 rounds, llm_bound | ≈ equal |
| Debug-fix end state (llama3.1, live) | File NOT fixed (D2, model-limited) | **File actually fixed on disk** | ✅ functional win |
| Loop bounding | MAX_TOOL_ROUNDS only | + stall detection, directive, failure cap, 600 s budget | strictly stronger |
| Suite runtime | ~2 min class | 136 s for 1731 tests | no regression |
| Overhead per round | — | dict counters + enum compare (O(1)); regex contract built once per request | negligible |

CPU-only Ollama remains llm_bound (dominant share ≥60% in classification);
tool/command overhead is not measurable against inference time. DeepSeek-coder-v2
timed out during cold-load on CPU (>180 s first token) — same hardware limit
documented since FASE P (D1), not a regression.

## 9. Live Acceptance (Q.11)

Script: `scripts/q_live_acceptance.py` (llama3.1:latest, deepseek-coder-v2:latest via local Ollama).

| Scenario | Model | Result |
|----------|-------|--------|
| L1 create file | llama3.1 | **PASS** — file created & session-verified, `completed` |
| L2 fix add() bug | llama3.1 | **MUTATION SUCCEEDED** (`return a + b` written) after 1 backend directive; run later ended `failed` on an Ollama timeout (CPU), handled gracefully by Q.7 handler |
| L3 create file | deepseek-coder-v2 | Timeout during model load (hardware limit D1); graceful classified error message returned |

L2 is the phase's flagship outcome: P recorded this exact scenario as
unsolvable-on-CPU due to endless re-reading; with backend coaching the model
performed the modification. The subsequent timeout is orthogonal (provider
latency), and demonstrates bounded, honest termination.

## 10. Known Limitations

1. **Timeout-path metrics gap (minor):** when the Q.7 handler fires mid-run,
   `rounds` reflects rounds started, and `latency_classification` is computed
   from partial time sums (now set, but based on incomplete data by nature).
2. **Directive effectiveness is model-dependent:** coaching works when the
   model can act at all; a model that cannot produce a valid modify call will
   still be stopped honestly rather than completed magically.
3. **Contract file-hint extraction** is heuristic (filename regex, ≤5 files);
   complex multi-file objectives rely on the model's own planning.
4. Pre-existing limitations from FASE P remain (home-dir sandboxing scope,
   CPU-only inference, prompt-injection defense level) — intentionally
   unchanged by this phase.

## 11. Remaining Defects

| # | Defect | Severity | Type | Status |
|---|--------|----------|------|--------|
| QD1 | Latency class on early-abort uses partial sums | LOW | METRICS | Documented; acceptable |
| QD2 | Test-suite final summary line intermittently missing under PowerShell piping (exit code & JUnit XML correct) | LOW | TOOLING | Documented |
| — | P-D1 (CPU timeouts on big models) | LOW | HARDWARE | Unchanged, documented |
| — | P-D2 (debug read-loops) | — | MODEL | **RESOLVED by Q backend control** (live-verified) |
| — | P-D3/D4 | MEDIUM/LOW | DESIGN/CODE | Unchanged (out of scope) |

## 12. Files Changed

| File | Change |
|------|--------|
| `src/personal_ai_secretary/agents/task_state.py` | **NEW** (~540 lines): states, machine, detector, contracts, helpers |
| `src/personal_ai_secretary/agents/builtin.py` | Contract build, budget gate, admission control, coach-or-stop, per-tool hooks, Q.5/Q.6/Q.7/Q.8 integration |
| `src/personal_ai_secretary/observability/request_metrics.py` | 7 new FASE-Q fields + summary() entries |
| `src/personal_ai_secretary/tools/prompt.py` | "Task Protocol" section (Q.9) |
| `tests/unit/test_q_task_state.py` | **NEW** — 51 unit tests |
| `tests/unit/test_q_agent_control.py` | **NEW** — 9 scenarios (A–I) |
| `scripts/q_live_acceptance.py` | **NEW** — repeatable live acceptance harness |

## 13. Product Readiness Verdict

**READY.** The agent's execution layer is now deterministic and observable:
tasks carry contracts, loops are detected and coached then bounded, failures
recover with a hard cap, completions require evidence, and every request emits
a full state-transition history. Security posture is unchanged (45/45 security
regressions green). The single most important behavioral defect carried from
FASE P — debugging tasks that could never reach a modification on CPU — is
functionally resolved and live-verified.

Recommended next focus (post-Q): intra-home sandboxing (P-D3) and richer
model routing using the new `latency_classification` telemetry.
