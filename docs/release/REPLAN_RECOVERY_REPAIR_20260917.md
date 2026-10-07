# Evidence-driven Replan repair — 2026-09-17

Scope: source-code repair and local regression, not deployment or release acceptance.
Authority: master specification section 10 (deterministic triggers, immutable Plan versions), existing retry/reconciliation, budget, policy and fencing rules.

## Findings and changes

- `runtime/activities.py`: removed the canonical NO_RELIABLE_RESULTS exception from evidence-insufficiency detection. Such a successful Capability result now fails the owning Step through the existing fenced attempt path and yields EVIDENCE_INSUFFICIENT / REPLAN_REQUIRED. Failure observations and missing-information details now reach the planner.
- `capabilities/builtins.py`: empty/disallowed document evidence uses the same evidence trigger. An explicit answer-quality REPLAN decision raises an evidence failure before partial-answer extraction can swallow it. CLARIFY, REVISE and PARTIAL_ACCEPT remain distinct.
- `runtime/replanner.py`: guidance requires evidence collection before repeated synthesis and specifies that revised read objectives are executable queries. Failure details are explicitly untrusted data. This guidance is not a guarantee that a real model will choose a successful recovery.
- `runtime/activities.py`, `capabilities/builtins.py`: new/changed persisted read steps can change Web/RAG queries; legacy primary queries still cannot override the validated initial research plan. Document identity, source/answer constraints, task goal, workspace, policy and write arguments retain their existing authority.
- `storage/replans.py`: changed or explicitly affected completed steps no longer inherit COMPLETED in the next Plan. Unchanged completed steps retain the existing preservation behavior.

## Recovery distinctions

| Observation | Runtime handling |
|---|---|
| No usable evidence / structured evidence_sufficient=false | EVIDENCE_INSUFFICIENT -> Replan |
| Quality assessment explicitly requests REPLAN | Preserve missing aspects and critique -> Replan |
| Transient transport/provider failure | Existing bounded retry; terminal step failure -> Replan |
| Capability unavailable / exhausted validation repair | Existing typed Replan trigger |
| Missing user input | Clarification remains distinct from evidence recovery |
| UNKNOWN side-effect outcome | Existing reconciliation before retry; unchanged |
| Denied permission / rejected approval | Cannot acquire permission through Replan; unchanged governance |
| Budget/deadline/replan limit reached | Existing bounded abort; no infinite retry |

Bounded Web/RAG tasks still have the existing one-Replan allowance; ordinary durable tasks retain their existing larger allowance. No timeout or recovery budget was increased.

## Observed validation

- Full unit suite: **367 passed**, 5.60 s. Result: `work/verification/replan-unit.xml`.
- Real local PostgreSQL targeted integration: **32 passed**, 11.32 s. Result: `work/verification/replan-integration.xml`.
- Integration modules: `test_no_reliable_results_terminal.py`, `test_replans.py`, `test_replan_usage_accounting.py`, `test_budgets.py`.
- Scenarios include zero and irrelevant evidence, both legacy and frozen research-plan context; blocked premature materialization; immutable recovery Plan creation; actual dispatch of a different search query; explicit aborted Replan and exactly-once final message; no unsupported Claims; revised completed-step execution; existing model-HTTP Replan test, usage and budget limits.
- Ruff on all seven modified Python files: PASS. Strict mypy on the four modified production modules: PASS.
- Intermediate failures were a test expecting None where the API correctly rejects premature materialization, and query precedence regression caught by the existing unit test. Both corrected; final suites above pass.

## Limitations and reproduction

The integration suite uses real PostgreSQL with isolated workspace IDs, but scripted tool/model responses for deterministic recovery testing. The terminal test explicitly supplies the recovery and abort decisions; it does not prove a production model will autonomously recover the screenshot questions. The separate existing model HTTP test verifies Replan invocation, not live-provider answer quality.

Run `.venv312/Scripts/python.exe -m pytest tests/unit -q`. For integration, configure TEST_DATABASE_URL for the local migrated PostgreSQL database and REDIS_URL, then run the four integration modules above. Do not print database credentials. Existing `scripts/verify_local.py` can provide local environment setup, but also initializes procurement data and writes the general verification report; this run used a scoped runner instead.

Not performed: deployment/restart, screenshot-question live-provider replay, browser acceptance, whole-project release gates, or a unified redesign of every intent/quality category. Partial answers not marked REPLAN may still pass, and optional quality assessment may still time out. Provider failure or exhausted budget may legitimately end a Replan with ABORT_TASK. These remain separate from the confirmed bypasses repaired here. RELEASE INCOMPLETE remains unchanged.
