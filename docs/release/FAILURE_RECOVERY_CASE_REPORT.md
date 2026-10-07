# Failure Recovery Case Report

Date: 2026-09-10  
Result: **PASS for the three controlled engineering scenarios described below**

## Technical summary

The cases use actual process termination, real Temporal, real PostgreSQL, real Redis and the independent Procurement PostgreSQL database. They are controlled failure tests, not production-provider benchmarks. The final Golden Evaluation JUnit SHA-256 is `b0ff64922869e151e650d3d66b261aeaa769a0d749ff1492da17c8f5488fdc1e`; the separate Admission Control JUnit SHA-256 is `577e03b52f617fa9b02a180d9fe66a39fffdd980d63f5e8cf98bd5d0874f5fa2`.

## Case 1 — Worker crash recovery

**Problem / fault injection.** `tests/failure/test_worker_crash.py::test_actual_worker_process_crash_resumes_without_recommitting_completed_step` starts an independent Temporal Worker, waits until `protected-step` is durably committed to PostgreSQL, then kills that OS process before starting a replacement Worker on the same Task Queue.

**Detection.** Temporal retains the Workflow history while the first Worker disappears. The test does not treat process exit as Task completion; it waits on the durable Workflow result and canonical PostgreSQL records.

**State and recovery sequence.**

```text
Worker A starts
→ protected-step commits to PostgreSQL
→ Worker A is killed
→ Temporal makes remaining work available
→ Worker B starts on the same queue
→ after-recovery commits
→ Workflow and canonical Task complete
```

**Recorded result.**

| Field | Value |
|---|---|
| Task ID | `4f4f4071-b76e-4ee7-a848-c00d23cdca77` |
| Workspace ID | `9e113f0b-6d33-4079-a3cc-6d1157d8199e` |
| Canonical Task state | `COMPLETED` |
| First durable command | `protected-step` at `2026-09-10T04:41:57.776702Z` |
| Second durable command | `after-recovery` at `2026-09-10T04:42:11.858952Z` |
| Authoritative command count | 2, with 2 distinct IDs |
| JUnit result | `PASSED`, `16.784 s` |

**Verification conclusion.** Recovery completed without recommitting the already completed protected Step. This directly exercises ACC-002. It does not prove every possible flagship crash point; that broader coverage remains PARTIAL under INV-012/REC-S053.

## Case 2 — Tool `UNKNOWN` → Reconcile

**Problem / fault injection.** `tests/failure/test_response_loss.py::test_lost_response_reconciles_without_duplicate_side_effect` creates a real supplier Admission in the independent Procurement database and deliberately discards the response. The platform therefore cannot safely label the external effect succeeded or failed.

**Detection.** The platform records the authoritative Operation as `UNKNOWN`. A replay of the same logical Operation returns `RECONCILE`; it does not dispatch a second blind write.

**State and recovery sequence.**

```text
PREPARED + approved
→ DISPATCHED
→ remote Admission commits
→ response deliberately lost
→ platform records UNKNOWN
→ stable Operation replay selects RECONCILE
→ lookup by Operation ID returns FOUND
→ platform commits SUCCEEDED
→ repeated external request returns the same Admission ID
```

**Recorded result.**

| Field | Value |
|---|---|
| Task ID | `1a8f4046-cb71-4b1d-b573-e86a1aad805e` |
| Operation ID | `1963c8d1-1403-5878-bde7-e8ad1a803c20` |
| Approval ID / state | `4aef799c-74e3-4efa-b2cb-960b630401b8` / `APPROVED` |
| Final Operation state / version | `SUCCEEDED` / `4` |
| Admission ID | `1a65141a-a4d2-4676-96dc-1367a2d6b633` |
| Procurement Admission rows for Operation | exactly 1 |
| JUnit result | `PASSED`, `0.206 s` |

**Verification conclusion.** Reconciliation recovered the lost response and preserved exactly-once business effect semantics, satisfying ACC-003. The test intentionally leaves its successor Attempt non-terminal because it targets Operation ownership/reconciliation rather than whole-Task cleanup; this record is not hidden.

## Case 3 — Admission Control and basic Workspace fairness

**Problem / fault injection.** `tests/integration/test_admission_control.py` configures real Redis leases with limits `global=2`, `workspace=1`, `model-provider=1`, `capability=1`, `MCP=1`. It fills individual scopes and attempts competing acquisitions from three Workspaces.

**Detection.** The single Lua admission transaction returns canonical reasons: `workspace_task_concurrency`, `model_provider_concurrency`, `capability_concurrency`, `global_task_concurrency` or `mcp_concurrency`. A blocked durable Task remains `READY` rather than being marked failed.

**State and recovery sequence.**

```text
Workspace A acquires a lease
→ a second A request is blocked by workspace_task_concurrency
→ Workspace B can still acquire a different provider/capability lease
→ global capacity blocks Workspace C
→ leases are released or expire
→ expired MCP lease is reclaimed
→ waiting model call proceeds once the blocker is released
→ Redis lease counts return to zero
```

**Recorded result.**

| Field | Value |
|---|---|
| Blocked Task ID | `8b2e4891-fe16-465a-8404-7c6f63cc7506` |
| Workspace ID | `c8366580-d6d0-461f-b7a6-4d0199d9bd23` |
| Canonical Task state | `READY` |
| Persisted block reason | `workspace_task_concurrency` |
| Redis recovery checks | explicit release, heartbeat extension, 100 ms expiry reclamation, final zero lease counts |
| Provider-call boundary check | 0 calls while blocked; exactly 1 after lease release |
| JUnit result | 3/3 passed in `0.74 s` |

The provider-call boundary check uses a test HTTP transport only to count whether the existing Model Gateway invoked its downstream client; it is not presented as production-provider validation. Fairness evidence is the basic per-Workspace concurrency boundary required by Section 56: one Workspace cannot consume another Workspace's reserved opportunity under the tested limits. Strict FIFO/weighted-fair queue ordering is neither implemented nor claimed, consistent with the prohibition on inventing a complex distributed Scheduler.

## Limitations

- These scenarios prove the named failure paths and durable records, not exhaustive fault coverage at every flagship Step.
- Historical pre-fix Production Provider validation Tasks remain preserved with `RETRY_WAIT` or stale `RUNNING` Attempts; they are not rewritten as successful recoveries.
- Admission tests validate real Redis/PostgreSQL behavior. Their call-count transport is an observation aid, not a mock production Provider claim.

