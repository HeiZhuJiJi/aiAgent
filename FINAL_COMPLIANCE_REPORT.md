# Compliance status report — INCOMPLETE

Date: 2026-09-22. This is an interim, explicitly incomplete compliance report, not release acceptance. **PROJECT COMPLETE is not claimed.**

## Latest local verification checkpoint (2026-09-22)

The current source was rebuilt and verified after the Completion and outbox
repairs. The full repository verification entry point completed with **866
passed**, 0 failed, 0 skipped and 0 xfailed. Full Ruff passed, strict mypy
passed over **137 source files**, and the Web check passed ESLint, strict
TypeScript, **22 Vitest tests** and the Vite production build. The focused
Completion tests passed (61 unit, 16 Completion/fencing/reconciler
integration, 5 outbox integration and 3 Supplier Risk flagship tests). The
Chromium Playwright acceptance passed 1/1 against the current Skills
governance menu. These results supersede the older 424/116/17 checkpoint as
the latest local regression evidence; older records remain historical.

The Completion layer is active in the rebuilt API and Worker. A fresh
seven-question report is retained at
work/verification/api-rebuilt-20260922-all-question-set.json. Five cases
(district_today, miami_tomorrow, district_week, history_week and
public_science) produced persisted Requirement, VerificationResult,
RequirementAssessment and FULL_ANSWER/COMPLETE results. The city_today and
campus cases were safely reported as UNAVAILABLE without a delivery decision;
they must not be counted as quality passes. In particular, the university-list
case did not receive an apparently complete list from partial sources, and the
city-today case did not receive an unsupported current answer. This is
Completion-gate evidence, not full quality-set closure.

The API and Worker images were rebuilt before the fresh run. Retained results
created by the previous image lacked Completion Contracts and are excluded
from the current quality conclusion. The contract export scripts both
completed successfully. git diff --exit-code -- contracts remains non-zero
because the worktree already contains extensive intended generated contract
changes; the generated-contract gate is therefore recorded as not clean, not
silently promoted to PASS.

The release decision remains **INCOMPLETE**. The current question-set limits,
unexecuted frozen release gates and existing PARTIAL/NOT_IMPLEMENTED matrix
rows continue to block a project-complete claim.

## Latest verified checkpoint update

The 2026-09-15 repair checkpoint supersedes the older regression and RAG
statements below. The repository-defined verification entry point completed
with **424 passed**, 0 failed, 0 skipped and 0 xfailed; the final JUnit SHA-256
is `70c3959a461afd1dc5a218ceb733ab3ad09e82f96ce2d539a68c63962c18b9c8`.
Full Ruff, strict mypy over 116 source files, frontend
ESLint/TypeScript/**17 Vitest tests**/Vite build, generated-contract checks and
fresh migrations through `0034_quarantine_legacy_context` passed.

The repair establishes an authoritative Conversation Turn lifecycle with
attempt ownership, user-facing outcomes and a single completed-only Context
Projection. Failed or incomplete user messages, legacy backfill, late task
results and stale direct Turns can no longer silently contaminate later model
context. Direct chat, bounded evidence-backed QA and durable Tasks retain
separate execution boundaries; atomic research-Turn creation binds the user
message, immutable Task snapshot, plan, steps and Outbox request in one
transaction.

The semantic research fallback is distinct from provider failover. For
AUTO-routed Turns with a configured CHAT model, complex WEB/RAG/MIXED questions
attempt a bounded model plan that specifies materially distinct Web/retrieval
queries, source-class priorities, Evidence requirements, freshness and the
required answer shape; it does not answer the question or select executable
Capabilities. Eligible planner failure safely retains the deterministic plan.
Narrow FACT and DOCUMENT_QA Turns use the deterministic fast path and may invoke
at most one evidence-aware recovery plan when the first Evidence pass is empty
or weak. Web execution is capped at two search rounds and RAG at one recovery
pass, with no recursive planning loop. The Runtime, not model output, remains
authoritative for route, policy, budget, permission and the selected immutable
document version. Parametric model knowledge cannot stand in for current
Evidence.

Selected-document summaries now use ordered version-scoped chunk coverage
rather than similarity search. Complete coverage is retained for bounded small
documents; larger documents use bounded beginning-to-end sampling and the
generation prompt explicitly requires a representative-summary disclosure.
Empty Web/RAG results remain no Evidence instead of becoming high-confidence
provider-envelope Evidence.
The follow-up delta passed 294 unit, 3 contract, 15 focused real-dependency
integration and 30 security tests, plus relevant Ruff and strict mypy checks.
API, Worker and Web were rebuilt from this source and reported healthy; API
`/health` and Web returned HTTP 200 and their fresh application logs contained
no ERROR/CRITICAL/Traceback/Unhandled match. This is scoped repair evidence,
not a replacement for the historical full-suite result or release acceptance.

Explicit primary/backup fallback is now finite and typed. It is allowed for a
final server failure, and for structured output only after schema repair is
exhausted; timeout, rate limit, policy, budget, embedding and a stream after its
first emitted chunk do not silently cross providers. STARTED and terminal
USED/FAILED/CANCELLED outcomes are persisted without prompts or secrets.
Malformed provider envelopes preserve valid reported usage, post-call actual
usage remains recorded even after a budget crosses EXCEEDED, and stale
attempts cannot write Claims, links, fallback audit or usage. All durable
built-in and Replan model calls resolve the immutable Task snapshot: explicit
historical revisions remain usable after later edit/disable, while drift in a
default environment model's public settings fails closed. Secrets are never
embedded in that snapshot.

A deployed live drill now supplies real backup-provider evidence rather than
only test-double coverage. A forced primary `LLM_SERVER_ERROR` and an exhausted
three-reply `LLM_SCHEMA_ERROR` both produced `STARTED -> USED`, selected the
configured real `Qwen/Qwen3.8-27B` backup, and returned a non-empty or
schema-valid result. Provider-reported usage was retained for every malformed
primary reply and the backup reply. The sanitized record is
`work/verification/model-fallback-live.json`, SHA-256
`d639093b45ce26915f8356d46fc3877d9fdbdb42b5f5d0144b7f2edf982863b0`.
Post-change delta validation passed 266 unit tests, 34 targeted PostgreSQL
integration tests and 19 frontend tests, plus the relevant Ruff, mypy,
lint/typecheck and production build checks. This is evidence for the repaired
paths, not a new repository-wide release-complete claim.

The rebuilt Compose runtime is healthy and API/Web both returned HTTP 200. The
previously failed PDF ingestion
`59a5a700-12f6-55aa-9f7c-7282076399ad` is `SUCCEEDED` at version 14 with
document version `d78a77a0-b844-5ebe-a4dc-a4f7fa139526`, four chunks and
1024-dimensional embeddings. A live Worker query used the pinned
`BAAI/bge-m3` service and ranked the relevant employee-appeal section first.
All 144 legacy-backfilled Turns have zero context eligibility. One pre-existing
direct Turn crossed the configured 900-second stale boundary during this live
validation and the rebuilt Worker changed it from RUNNING to
COMPLETED/UNAVAILABLE, proving the deployed reconciliation path.

A non-destructive post-check also found accumulated development/test history:
7,093 nonterminal Task rows and 84 failed, 9 pending and 21 running ingestion
jobs at the observation time. These records were not deleted or rewritten.
Dedicated verification-database isolation plus an explicitly approved retention
cleanup remains operational follow-up; it does not change the successful exact
PDF result above. Production reranker validation also remains open; the
deterministic bounded RRF fallback is covered by tests.

The handover checkpoint is materially ahead of the earlier body of this report.
On 2026-09-12 the repository-defined verification entry point completed with
**250 passed**, 0 failed, 0 skipped and 0 xfailed. Ruff, strict mypy, frontend
ESLint/TypeScript/Vitest/Vite build, generated contracts, fresh platform
migrations through `0030_data_lifecycle` and the independent Procurement
migration all passed.

The real Chromium Playwright acceptance also passed against the Compose Web and
API services. It registered and logged in through the rendered UI, created an
isolated Workspace, and verified both the Workspace list and active selector.
The global Outbox dispatcher now has a database-backed Workspace round-robin
ordering proof while preserving FIFO for Workspace-scoped dispatch.
PlanExecutionWorkflow now acquires real Redis global/Workspace Task leases
before loading executable Steps. A real Redis/PostgreSQL/Temporal/MCP test
proves that an over-limit Task remains READY with `schedule_block_reason`,
then automatically continues after capacity is released and clears its lease.
Pause, retry wait, HITL, terminal and Continue-As-New boundaries release and
reacquire the lease instead of occupying capacity while dormant.
The runtime reconciler now isolates malformed active tasks as immutable manual
review incidents while continuing safe repairs for other tasks. Explicit
source-Plan fencing permits deterministic consecutive Replans and rejects
stale or divergent replays.

Supplier A, B and C now complete the nine-step declarative Supplier Risk plan
through real PostgreSQL, Temporal, Redis, MinIO and Procurement MCP, using a
test-only OpenAI-compatible HTTP service for model, embedding, reranking and web
protocol boundaries. The flow verifies parallel research, Evidence, Claims,
conditional Reviewer, Supplier C Replan and Plan v2, immutable DOCX, Trace,
operation-bound HITL and exactly one Supplier A admission. Deterministic tests
also cover MCP disconnect/reconnect and all required FailureInjector points.

The practical backup/restore gate passed after a real Supplier A flagship run.
Four PostgreSQL dumps, 570 MinIO objects and five configuration files were
backed up; isolated restore verified Workspace, completed Task, Plan, 14
Evidence records, Artifact hash, 52 Audit records and exactly one Procurement
admission. No secret values were exported.

Release remains incomplete, but the production Chat/structured-provider blocker
is closed. After correcting the Docker host/provider configuration, authenticated
`/v1/models` checks passed from both API and Worker, the real synthetic probe
passed on `Qwen/Qwen3.8-27B`, and Agent Task
`0505362d-6cb2-4a03-94fb-abbb2d30dbab` completed through Procurement MCP and
the governed structured model Capability. It persisted 2,298 Task tokens,
48.277403 seconds create-to-complete latency and linked Task/Capability/Usage
Trace identifiers. The real Compose web-search path also completed a cold-cache
Qwen Agent Task (`8708638c-5cf9-4ff3-8484-0a363bb12e53`) in 27.502 seconds with
3,779 tokens, 5 Evidence records and 19 Trace nodes. The deployed pinned BGE-M3
embedding path is now verified by the 2026-09-15 exact-PDF ingestion and live
semantic probe; the production reranker remains unverified. Anonymous public
search upstream availability is not a production SLA.
The controlled Evaluation runner executed all 72 Golden Task bindings over 63
unique passing acceptance nodes and persisted 72 results, Usage and an Audit
evidence hash. Its database window now measures Evidence coverage 9/9, Claim
provenance 9/9, Tool executions 41/41, retry/Replan/HITL distributions,
Recovery 6/6, latency and tokens. This is not a production-provider benchmark;
five frozen Section 59 dataset dimensions remain unmeasured. A separate controlled component runner
completed all six frozen ablation pairs with explicit non-production limitations.
Workspace
deletion implements disable, cancel/drain and a durable purge request, but the
irreversible database/MinIO purge remains blocked pending explicit approval of
that specific destructive operation. The sections below retain detailed
historical evidence; statements saying flagship, Replan, built-ins,
Continue-As-New, MCP reconnect, observability deployment, CI/Kubernetes or
backup/restore are absent should be read as superseded by this update and the
current `SPEC_COMPLIANCE_MATRIX.md`.

## Implemented requirements and mappings

The source specification was copied byte-for-byte into `docs/spec/00-master-spec.md`; SHA-256 is recorded in `docs/spec/SOURCE.sha256`. All 86 numbered sections, 15 INV IDs and 15 ACC IDs are preserved. Nineteen documents establish source ownership, plus AGENTS.md, implementation plan and section-level compliance matrix. Further atomic requirement decomposition remains necessary.

| Component | Code | Validation | Scope limit |
|---|---|---|---|
| Versioned contracts and DAG validation | src/enterprise_agent/contracts/domain.py | tests/unit/test_contracts.py; tests/contract/test_schemas.py | Not all contracts have persistent services |
| Explicit state transitions | src/enterprise_agent/domain/states.py | tests/unit/test_contracts.py | Not integrated across complete runtime |
| Workspace-scoped persistence and fencing | src/enterprise_agent/storage/ | tests/integration/test_fencing.py; tests/integration/test_plan_step_lifecycle.py | Fenced Evidence commits, immutable TaskStep materialization, DAG dependency unlocking and Task terminal propagation pass; artifact authoritative-result fencing remains |
| Side-effect operation ledger | src/enterprise_agent/storage/operations.py | tests/failure/test_response_loss.py; tests/integration/test_capability_executor.py; tests/integration/test_reconciler.py | UNKNOWN/reconciliation, stale-attempt protection, policy-authorized internal dispatch, approval-bound external dispatch and general runtime consistency repair tested |
| Governed Capability Registry and Executor | src/enterprise_agent/capabilities/; migrations/versions/0009_capability_calls.py | tests/integration/test_capability_executor.py | Exact version and identity binding, JSON Schema input/output validation, persisted policy decisions, deny/no-call, approval gate, replay idempotency, fenced result commits, budget enforcement and repeated-call loop abort tested; production adapter catalog remains |
| Persistent task budget and loop guards | src/enterprise_agent/runtime/budgets.py; src/enterprise_agent/runtime/loop_guard.py; migrations/versions/0010_task_budgets.py; migrations/versions/0011_runtime_loop_guards.py | tests/integration/test_budgets.py; tests/integration/test_capability_executor.py; tests/integration/test_loop_guard.py; tests/integration/test_api_identity.py | Atomic pre-call limits and all three required loop patterns tested; fenced post-call provider usage is recorded even when it crosses or follows EXCEEDED, without authorizing another call |
| Redis admission control | src/enterprise_agent/runtime/admission.py; migrations/versions/0012_admission_control.py | tests/integration/test_admission_control.py | Atomic global/workspace/model-provider/capability/MCP limits, heartbeat/release/expiry and canonical READY block reason tested; explicit fair queue integration remains |
| OpenTelemetry and Prometheus foundation | src/enterprise_agent/observability/; src/enterprise_agent/api/app.py; src/enterprise_agent/capabilities/executor.py | tests/integration/test_observability.py | HTTP trace IDs, correlated response headers, all required metric families and core Task/Policy/Approval/Capability counters tested; full cross-layer export and dashboards remain |
| Transactional Outbox and Temporal approval delivery | src/enterprise_agent/storage/outbox.py; src/enterprise_agent/runtime/outbox_dispatcher.py; migrations/versions/0008_transactional_outbox.py | tests/integration/test_outbox.py; tests/integration/test_api_approvals.py; tests/integration/test_task_control_temporal.py; tests/integration/test_reconciler.py | Concurrent lease fencing, Workspace round-robin fairness, idempotent enqueue, expired lease takeover, approval/task-control Signal delivery and idempotent missing-execution repair tested |
| Durable Temporal recovery | src/enterprise_agent/runtime/; apps/worker/ | tests/failure/test_worker_crash.py; tests/integration/test_plan_temporal_mcp.py; tests/integration/test_replans.py; tests/integration/test_temporal_continue_as_new.py | Actual Worker kill/restart, canonical Outbox-started Plan execution, explicit-source consecutive Replan, Continue-As-New and approval-gated MCP write pass |
| Immutable execution snapshots and plan versions | src/enterprise_agent/storage/plans.py; src/enterprise_agent/storage/tasks.py; src/enterprise_agent/storage/model_configs.py; src/enterprise_agent/runtime/activities.py; src/enterprise_agent/capabilities/builtins.py; migrations/versions/0004_immutable_plans.py | tests/integration/test_immutable_plans.py; tests/integration/test_model_fallback_snapshot.py; tests/integration/test_replans.py | Hash-verified roundtrip, database immutability, explicit historical model revisions, default public model configuration drift rejection, built-in execution and Replan snapshot resolution tested |
| Deterministic permissions and egress | src/enterprise_agent/governance/policy.py; src/enterprise_agent/capabilities/executor.py | tests/security/test_policy.py; tests/integration/test_capability_executor.py | Policy is enforced before provider invocation and persisted per call; full resource matrix and service-account policy remain |
| Durable operation-bound HITL | src/enterprise_agent/contracts/domain.py; src/enterprise_agent/storage/approvals.py; src/enterprise_agent/storage/operations.py; src/enterprise_agent/runtime/activities.py; src/enterprise_agent/runtime/workflows.py; apps/web/src/; migrations/versions/0005_durable_approvals.py | tests/security/test_hitl.py; tests/failure/test_response_loss.py; tests/integration/test_hitl_temporal_signal.py; tests/integration/test_api_approvals.py; tests/integration/test_plan_temporal_mcp.py; frontend checks | Approval bypass, expiry, replay, handoff, immutable terminal decision, hash-bound review parameters, real Plan WAITING_HITL, Outbox/Temporal resume and exactly-one external write tested |
| Local identity, workspace, task-control and approval API | src/enterprise_agent/identity/; src/enterprise_agent/api/; src/enterprise_agent/storage/tasks.py; src/enterprise_agent/storage/task_controls.py; src/enterprise_agent/storage/approval_queries.py; migrations/versions/0006_local_identity.py; migrations/versions/0007_task_snapshot_binding.py | tests/integration/test_api_identity.py; tests/integration/test_api_approvals.py; tests/integration/test_task_control_temporal.py; tests/contract/test_openapi.py | Argon2 credentials, hashed opaque sessions, revocation, membership filtering, task-bound snapshots, optimistic Pause/Resume/Cancel with actual Temporal delivery, admin-only approval decisions, uniform errors and generated OpenAPI tested; remaining API groups and OIDC adapter remain |
| AES-GCM secret binding | src/enterprise_agent/governance/secrets.py | tests/security/test_model_secrets.py | Database secret lifecycle and all log/trace redaction remain |
| Compatible HTTP model gateway | src/enterprise_agent/models/gateway.py; src/enterprise_agent/runtime/activities.py; src/enterprise_agent/runtime/budgets.py; src/enterprise_agent/capabilities/builtins.py; deploy/docker/backend.Dockerfile | tests/security/test_model_secrets.py; tests/unit/test_model_settings.py; tests/unit/test_fallback_model_gateway.py; tests/integration/test_api_models.py; tests/integration/test_model_fallback_snapshot.py; tests/integration/test_model_handler_fencing.py; tests/integration/test_budgets.py; work/verification/model-live.json; work/verification/model-fallback-live.json; RELEASE_VALIDATION_REPORT.md | Controlled token, timeout, retry, schema repair, typed explicit fallback, terminal fallback audit, usage preservation/accounting and stale-attempt fencing pass; real Qwen primary probe and complete governed Agent Task pass; deployed real-backup drills pass for both primary server failure and exhausted structured-schema repair, with the current temporary backup endpoint retained as an operational limitation |
| Versioned conversations and project memory | src/enterprise_agent/storage/memory.py; src/enterprise_agent/storage/turns.py; src/enterprise_agent/runtime/conversation_results.py; src/enterprise_agent/runtime/reconciler.py; src/enterprise_agent/api/app.py; migrations/versions/0017_project_memory.py; migrations/versions/0018_conversation_messages.py; migrations/versions/0032_conversation_turn_lifecycle.py; migrations/versions/0033_conservative_legacy_turn_backfill.py; migrations/versions/0034_quarantine_legacy_turn_context.py | tests/integration/test_memory.py; tests/integration/test_api_conversations.py; tests/integration/test_conversation_turns.py; tests/unit/test_conversation_result_gate.py; tests/unit/test_legacy_turn_backfill.py; tests/contract/test_schemas.py | Authenticated messages and workspace memory remain versioned; authoritative Turn/Attempt/Outcome ownership, atomic research-Turn creation, completed-only Context Projection, late-result isolation, stale-direct reconciliation and legacy quarantine are integrated |
| React operator web | apps/web/src/; apps/web/package.json; apps/web/components.json | pnpm typecheck; Vitest; Vite production build; real Chromium Playwright | Auth, workspace, streaming Chat, conversation/message, task control, Plan/TaskStep state, Attempt/Evidence counts, knowledge upload/status, Skill lifecycle, MCP lifecycle/capability catalog, approval, artifact and audit API integrations are live; attachment and deep-trace browser acceptance remain |
| Immutable governed audit | src/enterprise_agent/storage/audit.py; src/enterprise_agent/storage/tables.py; migrations/versions/0019_audit_event_fields.py; migrations/versions/0020_audit_workspace_foreign_keys.py | tests/integration/test_fencing.py; tests/integration/test_api_audit.py; full integration suite | Frozen actor/workspace/task/step/action/resource/capability/policy/approval/result/trace/timestamp fields, all production writer actor types, RBAC reads, workspace FKs and mutation rejection verified |
| Governed procurement MCP | src/enterprise_agent/procurement/; src/enterprise_agent/mcp/manager.py; src/enterprise_agent/mcp/adapter.py; src/enterprise_agent/api/app.py; mcp_servers/procurement/server.py; migrations/versions/0023_mcp_manager.py | tests/integration/test_procurement.py; tests/integration/test_api_mcp.py; tests/integration/test_plan_temporal_mcp.py; tests/e2e/test_supplier_risk_flagship.py | Independent stdio tools, fixed-profile registration, exact discovery, schema hashing, immutable normalized capability snapshots, health/disable/reconnect, RBAC, audit, native idempotency, reconciliation mapping and multi-capability flagship scheduling tested |
| Governed declarative Skill lifecycle | src/enterprise_agent/skills/package.py; src/enterprise_agent/runtime/skill_planner.py; src/enterprise_agent/storage/skills.py; src/enterprise_agent/storage/tasks.py; src/enterprise_agent/storage/steps.py; skills/supplier-risk/; migrations/versions/0022_skill_lifecycle.py; migrations/versions/0024_plan_step_materialization.py | tests/security/test_skill_package.py; tests/integration/test_api_skills.py; tests/integration/test_skill_planner.py; tests/integration/test_demo_data.py; tests/integration/test_plan_step_lifecycle.py; tests/e2e/test_supplier_risk_flagship.py | Real MinIO quarantine, validation, admin lifecycle, immutable versions, dynamic active-MCP dependency resolution, generic Plan generation, exact ExecutionSnapshot binding, TaskStep materialization, DAG state propagation and complete Supplier A/B/C Plan execution pass |
| Development infrastructure | deploy/compose/infrastructure.yml; deploy/k8s/; observability/ | Real Docker startup; fresh platform and independent Procurement Alembic migrations; Temporal gRPC probe; Compose service health | Full local application Compose and deployable observability/Kubernetes configuration exist; production deployment proof remains |

`SPEC_COMPLIANCE_MATRIX.md` records the current per-bundle status. ACC-001
through ACC-015 now have direct passing component, failure or flagship evidence.
This does not satisfy INV-015 or Definition of Done while the remaining Section
59 dataset dimensions and other open matrix rows remain unverified.

## Test summary

Latest backend run: **866 passed**, 0 failed, 0 skipped, 0 xfailed. pytest XML: `work/verification/pytest.xml`. Full Ruff and strict mypy over 137 source files pass. Frontend: ESLint PASS, strict TypeScript PASS, Vitest **22 passed**, Vite production build PASS; the current real Chromium Playwright acceptance also passed 1/1 against the Skills governance menu.

- Unit/contract checks cover invalid DAGs, terminal-state resurrection, UNKNOWN retry rejection, review consistency, generated schemas and original specification checksum.
- Security component checks cover policy denial, mandatory approval decisions, restricted/confidential egress prevention before HTTP, secret tenant/identity binding, two-repair limit and malicious Skill archives.
- Real PostgreSQL integration checks cover stale attempt evidence rejection, replayed commit idempotency, cross-workspace acquisition and composite foreign keys, append-only audit, immutable snapshots and ordered append-only plan versions. Snapshot and plan payloads are reconstructed through Pydantic and verified against their canonical SHA-256 hashes.
- Plan creation materializes immutable TaskStep rows from the exact Plan version. Root nodes become READY, dependent nodes remain BLOCKED, completion of all prerequisites atomically unlocks a join, and the final Evidence commit transitions the canonical Task from RUNNING to COMPLETED. Authenticated APIs expose Steps, Attempts and Evidence after task/workspace authorization; cross-workspace reads are rejected.
- Attempt failures persist the frozen Failure code, safe detail and trace. A retryable failure moves the Step to RETRY_WAIT with a PostgreSQL-clock availability time; early acquisition fails, a due retry receives a new fencing token, and the immutable Plan Step `max_attempts` limit makes the last failed Attempt terminal. Migration `0025_attempt_retry_state` stores both the retry deadline and classified failure payload.
- Real independent MCP process discovers six tools and creates/looks up/replays a procurement admission. Eight concurrent native requests create one row; mismatched idempotency parameters are rejected.
- Durable HITL checks prove an external effect cannot dispatch without its exact operation-bound approval. Approval requests are immutable and replay-safe, displayed parameters are verified against the operation digest, expired requests fail before changing task state, approval relinquishes the old Attempt, and execution resumes under a newly fenced Attempt. A real PlanExecutionWorkflow creates the approval, persists Task/Step `WAITING_HITL`, waits in an ApprovalWaitWorkflow child, receives the decision through the transactional Outbox, reloads canonical PostgreSQL state, and performs exactly one real Procurement MCP write after approval; the procurement database remains unchanged before approval.
- FastAPI integration checks use real local users to verify registration, Argon2 password verification, opaque session issuance/revocation, workspace membership filtering, cross-workspace task and approval denial, trace-correlated uniform errors and generated OpenAPI contracts. Session tokens are returned once and only their SHA-256 hashes are persisted. Every task created through the API is transactionally bound to an immutable ExecutionSnapshot and that snapshot is hash-verified on reload. Pause/Resume/Cancel use the domain transition matrix and optimistic versions; approval decisions require an owner/admin membership and reject stale re-decisions.
- Transactional Outbox checks prove concurrent publishers claim different events, idempotency keys reject changed payloads, expired leases can be reclaimed, and a stale publisher cannot commit after lease takeover. The approval API test runs the real PostgreSQL/outbox/Temporal path and verifies the Workflow reads its final decision from canonical PostgreSQL state.
- Runtime reconciliation compares canonical PostgreSQL Task state with Temporal execution state. Missing safe executions enqueue one deterministic repair event; malformed tasks create immutable manual-review incidents without aborting repair of healthy candidates. Replan writes are fenced by their explicit source Plan, so a second legitimate Replan appends a new immutable Plan while stale and divergent replay remains fail-closed.
- Governed Capability Executor checks prove denied and unapproved calls never reach the provider, allowed read-only calls invoke once across replay, changed replays fail, internal effects require a persisted ALLOW decision, and external effects require the exact approved Operation. Input/output schemas, call identity, operation arguments, Attempt fencing and provider timeouts are enforced at the shared boundary. The centralized failure matrix drives bounded exponential retries for read calls; a side-effect timeout is invoked exactly once, persists `UNKNOWN`, and replays from the ledger for reconciliation without a second provider call.
- Task-control integration starts real Temporal Workflows and proves a pause Signal prevents step execution until resume, then reaches PostgreSQL COMPLETED. A cancel Signal reaches PostgreSQL CANCELLED and completes without executing the protected step.
- Task creation now binds the initiating user to the canonical Task and enqueues a deterministic `task.execution.requested` event in the same transaction as the immutable Plan and TaskSteps. A real Outbox publisher starts PlanExecutionWorkflow; the independent Worker reads PostgreSQL state through Activities, selects parallelizable READY nodes, inherits the creator's current workspace role, enforces the frozen snapshot allowlist and Policy, reserves Step and Capability budgets, invokes the real Procurement MCP adapter through CapabilityExecutor, persists Evidence, and reaches PostgreSQL COMPLETED. The same path now prepares an immutable Operation for an external admission, builds nested MCP arguments from the discovered JSON Schema, displays the exact hash-bound parameters for approval, then resumes and commits the external result under a new fencing token. The integration does not use a business provider fake.
- Budget checks use row locks and the PostgreSQL clock. Concurrent requests cannot overspend the last capability slot, expired deadlines fail before usage changes, and API-created tasks receive persisted token, cost, time and call limits. Persistent loop guards cover repeated capability/arguments, consecutive steps without new Evidence and consecutive Replans; each threshold abort terminally fences the applicable Task, Step and Attempt and writes an audit event.
- Runtime checkpoints are immutable, hash-verified, workspace-scoped records containing only reconstructable domain references and bounded summaries. Idempotent resume tokens prevent divergent replay; a fresh Alembic migration to `0013_task_checkpoints` was verified from an empty database.
- The deterministic complex-task Reviewer checks JSON Schema, required deliverables, constraints, Evidence coverage, citation identity and hashes, unsupported Claims and high-risk Claim grounding. Routing explicitly excludes simple chat; the Supplier A/B/C Temporal flagship integrates the conditional Review Step.
- PostgreSQL now persists immutable, hash-verified Claims and explicit Claim→Evidence links with composite workspace foreign keys and RLS. Replays are idempotent, changed content is rejected, cross-workspace Evidence cannot be linked, and migration `0014_claim_provenance` passes from an empty database.
- Artifact bytes now round-trip through the real MinIO service while immutable PostgreSQL manifests retain task, workspace, Claim references, media type, URI and SHA-256. Reads re-hash object bytes, replay verifies identity and content, database mutation is rejected, and migration `0015_artifacts` passes from an empty database.
- Context selection now executes the frozen candidate→priority→relevance→dedup→compression→token-budget pipeline. It records selected, compressed, duplicate, irrelevant and budget-excluded decisions in the resulting `ContextBundle` and accepts a model-specific tokenizer/compressor boundary instead of hiding approximate token accounting.
- Conversation summaries are append-only, hash-verified and strictly versioned per conversation. Project FACT, DECISION and PREFERENCE records retain immutable superseded history while active-memory reads exclude replaced records. Tests cover replay consistency, invalid version gaps, divergent supersedes branches, type-changing replacements, workspace isolation and database-level mutation rejection; migration `0017_project_memory` passes from an empty database.
- Authenticated conversation endpoints create and list conversations and append/list immutable user messages. Client-supplied identities make POST replay deterministic, and real HTTP tests prove cross-workspace reads and writes are denied. The generated OpenAPI contract includes these endpoints, and fresh migration verification passes through `0018_conversation_messages`.
- Artifact endpoints list immutable manifests, return metadata and stream downloads only after workspace membership validation. Downloads use the real MinIO object and re-check its SHA-256; the HTTP response includes the verified digest as an ETag. Cross-workspace download denial passes through the real API path.
- The React/Vite/Tailwind/shadcn-style Web shell provides responsive navigation, local registration/login, workspace creation and switching, durable conversation messages, live Task cards with optimistic Pause/Resume/Cancel, operation-bound approval actions and integrity-checked Artifact download links. It uses no mock business data. TypeScript, a DOM component test and the production bundle pass. A real Chromium Playwright run also verifies rendered registration/login and isolated Workspace creation/selection through Compose Web/API; the form labels are explicitly associated with their controls.
- Audit records are exposed through an authenticated API and rendered in the Web timeline table. Events include the frozen actor, workspace, task, step, action, resource, capability, policy decision, approval, safe result summary, trace and timestamp fields. All 30 production constructors explicitly type the actor; task, step and approval references use composite workspace foreign keys. The repository checks deterministic `audit.read` after membership validation, and real tests prove owner access, ordinary-member denial, non-member denial and database-level UPDATE/DELETE rejection. Fresh migration verification passes through `0026_task_creator`.
- MCP registration accepts only the built-in Procurement provider and persists a fixed stdio profile with an environment-variable reference instead of a database URL. A real official-SDK child process exposes the exact six-tool contract; the manager hashes normalized descriptors, stores immutable capability snapshots, rejects stale lifecycle commands, hides disabled capabilities and records registration, discovery and disable audit events. The production stdio Adapter revalidates the immutable active Schema before and after calls. Real direct, Temporal DAG and flagship calls pass through the shared Capability Executor, including Policy, budget, loop guard, fencing, Schema validation, audit and replay; disable is fail-closed and health detect/reconnect is verified.
- Skill archives are written to a real MinIO quarantine bucket before validation. The validator rejects executable or unexpected members, traversal, symlinks, expansion attacks, missing files, invalid manifests and schemas, remote Schema references, dependency/version mismatches, permission omissions and cyclic workflows. Valid packages become immutable workspace-scoped Skill versions, record a permission diff, require an explicit owner/admin confirmation, and then support audited enable/disable transitions. Production validation resolves current healthy MCP capabilities. The generic planner converts an enabled declarative workflow into an immutable Plan, binds exact Skill/Capability versions into the ExecutionSnapshot and materializes the DAG. The supplier-risk package's nine-step DAG completes for A/B/C through the real runtime; deployed provider verification remains pending.
- Demo generation creates hash-manifested policy DOCX/PDF, multi-sheet supplier XLSX, deterministic supplier-risk Skill ZIP and three workspace-scoped procurement fixtures. Supplier A clearly meets the rules, Supplier B clearly fails, and Supplier C has an UNKNOWN screening result plus insufficient history and therefore requires Replan. Tests parse the documents with the production parser, validate the ZIP with the production Skill validator and prove database seeding is idempotent.
- Hybrid retrieval applies reciprocal-rank fusion to distinct PostgreSQL FTS and vector candidate lists, guards stable Chunk identity, and uses a bounded reranker with deterministic RRF fallback on timeout, unavailable service or invalid scores. The pinned BGE-M3 embedding path is live; production reranker behavior remains pending.
- Knowledge ingestion stores versioned documents and locator-bearing Chunks in PostgreSQL with immutable history, SHA-256 identities, a fixed BGE-M3 `vector(1024)` contract, generated `tsvector`, GIN FTS and HNSW cosine indexes. The authenticated API hash-checks and stages one of seven supported formats in real MinIO, creates a deterministic PostgreSQL job and transactional Outbox event, exposes typed safe failures and retry to the React Web, and batches embeddings. The independent Worker registers a Temporal ingestion Workflow and Activity; acquisition/completion is fenced, workflow creation reconciles duplicate publish, and identical document replay avoids a second embedding call. In addition to protocol-bound integration tests, the exact user-reported failed PDF completed through the deployed `BAAI/bge-m3` service with four 1024-dimensional chunks and a successful scoped semantic retrieval probe.
- The parser accepts PDF, DOCX, PPTX, XLSX, CSV, Markdown and TXT and emits normalized text with format-specific page, paragraph/section, slide, sheet/row/cell, row or line locators. Tests create and parse real files in memory and reject unsupported media and unsafe expanding Office archives. Parser dependencies and type stubs are pinned.
- Admission checks use real Redis and one Lua transaction across all applicable scope keys. Tests cover global, workspace, model-provider, capability and MCP limits, heartbeat, explicit release and expired lease reclamation. The production Plan Workflow acquires global/Workspace Task leases; the unified Capability Executor acquires Capability plus MCP/model-provider leases, while direct chat, probe, embedding, reranking and Replan model calls use the same model-provider gate. Real tests verify zero provider calls while blocked, canonical `schedule_block_reason`, automatic continuation and complete lease cleanup.
- Observability checks verify valid 128-bit OpenTelemetry trace IDs on HTTP responses, Prometheus exposition of every required metric family, core runtime counters, and absence of authorization material from metric output.
- Strict mypy passed for `src` and MCP entrypoint. Final lint results are retained separately in the verification directory.

## Failure test summary

The suite now kills an actual independent Temporal Worker process after its first PostgreSQL domain command commits, starts a new Worker, and verifies durable workflow completion plus canonical PostgreSQL `TaskRun=COMPLETED`, with one authoritative record per protected step. A separate real PostgreSQL/procurement test persists `UNKNOWN` after a lost admission response, requires reconciliation, and verifies one procurement admission. A zombie attempt cannot submit the side-effect result. This satisfies ACC-002, ACC-003 and ACC-004 in their explicit test scenarios. The deterministic FailureInjector covers LLM timeout, LLM 429, schema corruption, MCP timeout/disconnect, response loss, Worker crash and context overflow. MCP health transition and reconnect are verified with a real stdio server. Runtime-wide stale Artifact commit injection remains a narrower follow-up beyond the existing fenced Capability/Attempt result boundary.

## Security summary

Component tests pass; full production Security Gate is **NOT PASSED**. Created `runtime_rls_test` with NOLOGIN, NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOINHERIT, NOREPLICATION and NOBYPASSRLS; granted only SELECT on public.task_runs. Four real PostgreSQL tests in tests/integration/test_rls.py passed: unfiltered queries see only the selected workspace in both directions; missing workspace is fail-closed; the role has no write privileges or evidence SELECT privilege; and every public platform table containing `workspace_id` has forced RLS with exactly one fail-closed Workspace policy. Operation dispatch requires a matching, persisted APPROVED decision, and the actual procurement response-loss scenario passes through that boundary.

Current development service database users are bootstrap users. The RLS tests explicitly SET LOCAL ROLE to the authorized non-superuser role. This validates data policies and full table coverage, while production least-privilege service-role deployment remains open.

## Production Provider validation

**PASS FOR CHAT/STRUCTURED AGENT PATH.** The existing OpenAI-compatible provider was restored by synchronizing the approved Cherry Studio URL/key into ignored local configuration and using the container-reachable HTTPS address. The key was never printed or written to committed evidence. Authenticated `/v1/models` checks returned HTTP 200 from both API and Worker and listed `Qwen/Qwen3.8-27B`. `work/verification/model-live.json` records a real synthetic structured probe with 157 input and 73 output tokens; its SHA-256 is `7fb371548b4c07a56dda66bc7d93302a30ab6d45d1c122c84e320a6260fd055e`.
The 2026-09-10 PASS remains historical evidence. After a tunnel URL change and a temporary HTTP 401 caused by the local key, the corrected 2026-09-12 configuration completed real Task `8708638c-5cf9-4ff3-8484-0a363bb12e53`; the replacement is therefore revalidated. Versioned Workspace model configurations retain their saved Base URL until edited, so changing `.env` still does not mutate those records.


With explicit user authorization for Beacon Industrial Trading Ltd. Supplier Profile Evidence egress, Task `0505362d-6cb2-4a03-94fb-abbb2d30dbab` and Plan `3b9f7229-8509-42bd-a6d6-01bc60779bb9` then completed through real PostgreSQL, Temporal, Redis, Procurement MCP and Qwen. Both Steps have one SUCCEEDED Attempt. The MCP Capability took 1.137 seconds, the model Capability 45.961 seconds, and Task create-to-complete latency was 48.277 seconds. Canonical Usage/Budget records contain two Capability calls, one LLM call, 2,298 total tokens, zero Replans, zero final-task retries and cost 0 because no provider price schedule is configured. Two Evidence rows and two Evidence-cited Claims were persisted. `RELEASE_VALIDATION_REPORT.md` records Task, Capability and Usage Trace IDs and the pre-fix Error/Retry history.

The live run exposed and retained evidence for two defects: the MCP stdio subprocess could not import a source-only package under its minimal environment, and 45–46 second Qwen calls exceeded the 15-second Activity heartbeat interval. The Docker image now installs the existing package into site-packages, and the existing governed-step Activity emits a five-second heartbeat while a Capability runs. No DAG, state-machine, fencing, Capability, MCP, Skill, Provider or Scheduler architecture was added. Post-fix Ruff, strict mypy and the full 250-test backend suite pass. The real Compose Web provider path is validated by the 2026-09-12 cold-cache task; BGE, Reranker and production streaming Chat UI browser acceptance remain unverified. Public anonymous search upstreams remain subject to external throttling and do not provide a commercial SLA.

## Data lifecycle

**PARTIAL / RELEASE FAIL.** Retention policies are created per Workspace, version-checked, bounded and audited. Real tests verify updates, stale-write rejection, every Workspace-scoped platform table's forced fail-closed RLS, and that an empty Workspace deletion disables the Workspace and stops at durable `READY_TO_PURGE`. No irreversible purge was run. Retention deletion and Workspace database/vector/MinIO/secret purge conflict with immutable-history triggers and remain behind ADR-001's privileged, explicit-approval boundary.

## E2E and flagship

**PASS in the controlled local acceptance environment.** Supplier A/B/C each run through Login → Workspace → configured protocol endpoints → Knowledge → Skill → MCP → Plan DAG → parallel Research → Evidence → conditional Replan → Reviewer → Claims → DOCX → Policy → optional HITL → conditional Admission. Supplier A creates one approved admission, Supplier B creates none, and Supplier C appends Plan v2 after an Evidence gap. Separately, the narrower two-step production Qwen Agent Task passes; it does not turn the controlled A/B/C suite into a production benchmark.

## Evaluation

**PARTIAL / RELEASE FAIL.** `scripts/run_evaluation.py` automatically executed all 72 versioned Golden Task bindings across all 12 categories over 63 deduplicated acceptance nodes; 63/63 passed in 101.62 seconds. Final run `b05d324f-8413-4dc7-8c11-448ba0dac7c7` persisted one `evaluation_runs` row, 72 `evaluation_results`, a Usage row and an Audit evidence hash. Its UTC record window directly queried eight canonical tables and measured: Task success 72/72, Plan validity 72/72, Evidence coverage 9/9, Claim provenance correctness 9/9, Tool executions 41/41, one separately retained Policy DENIED call, 63 Step Attempts across 56 Steps, 6 retried Steps and 7 retry Attempts, 4 APPLIED plus 2 ABORTED Replan decisions, 7 APPROVED HITL records, 6/6 Recovery cases, mean/p50/p95/max latency 1635.56/145/13652/16784 ms, 1325 tokens and cost 1.25. Evidence is in `work/verification/evaluation/report.json` (SHA-256 `e06161cefd75842c6e0c889a7644699ae4cb10a4ac261a39ff14088dcbad4d7c`) and its JUnit hash is `b0ff64922869e151e650d3d66b261aeaa769a0d749ff1492da17c8f5488fdc1e`.

The Golden result is explicitly a controlled acceptance baseline using test-only OpenAI-compatible endpoints, not production-provider validation. Evidence coverage and Claim provenance are now calculated from Claim/Evidence rows and their immutable hashes rather than manually filled observations; Tool, retry, Replan, HITL, Recovery, latency and token statistics likewise come from the execution window. Capability-selection accuracy, citation correctness, claim grounding, duplicate-side-effect rate and policy-violation rate still lack complete Golden dataset measurements; EVAL-S059 therefore remains PARTIAL.

The separate `scripts/run_ablations.py` execution completed all six frozen Section 60 comparisons: Planner/No Planner, Replan/No Replan, Context Manager/Full Context, Conditional Reviewer/No Reviewer, Dynamic Capability Retrieval/All Capabilities and Single Agent/Parallel Research Sub-Agent. It invokes existing production component functions with deterministic synthetic inputs and records treatment, control, delta, timing and SHA-256 evidence in `work/verification/evaluation/ablation-report.json` and `.md`. The JSON file SHA-256 is `17e1f86f8265a3029c456efab0e24d95d3de437896c89f3c717d7cfd78866096`. EVAL-S060 is PASS at controlled component scope; the report explicitly states that it does not prove a full Agent Task on a production provider.

## Missing requirements

Major remaining work is now concentrated in the five still-unmeasured Golden quality dimensions, stale canonical cleanup after exhausted Temporal Activity retries, full cancellation compensation, production least-privilege service-role deployment, production BGE/Reranker/Web and streaming Chat UI validation, retention deletion and the Workspace purge that still requires explicit destructive-operation approval. Atomic requirement decomposition and current documentation traceability also remain incomplete.

The platform Alembic chain contains required core tables through `0030_data_lifecycle`. Procurement has an independent Alembic chain at `0001_procurement`; fresh disposable-database upgrades verify both exact heads.

## Backup / restore

**PASS.** `scripts/verify_backup_restore.py` ran a real Supplier A task, generated an immutable DOCX and admission, backed up both business databases, MinIO and non-secret configuration, restored to isolated databases and buckets, and revalidated Task/Plan/Evidence/Artifact/Audit plus the Artifact SHA-256. The recorded result is `work/verification/backup-restore.json`.

## ADRs

No architecture invariant was intentionally changed. Python package uses `enterprise_agent` instead of `platform` to avoid shadowing the Python standard-library module; master section 72 permits reasonable packaging adjustments. ADR-001 records the unresolved conflict between irreversible Workspace deletion and immutable history; its privileged purge proposal remains unimplemented.

## How to run

See README.md for reproducible local setup and existing tests. The infrastructure command does not start a finished platform. Real model/web evaluation needs user-supplied provider configuration and secrets stored locally, never pasted into report output.

## Release decision

**FAIL / INCOMPLETE.** The production Qwen Chat/structured Agent gate and requested Evaluation quality closure now pass. Five frozen Evaluation dataset dimensions, stale failure cleanup, production least-privilege deployment and lifecycle deletion still prevent repository-wide release, regardless of the 207 passing backend tests, six completed controlled component ablations and passing frontend component/build gates.

## Current workspace

The active repository is `D:/Data/PyCharmData/Project/EnterpriseAgentWorkPlatform`; verification uses `.venv312` with Python 3.12 and `sys.executable`-pinned scripts.

## Model configuration integration update

Implemented `src/enterprise_agent/models/settings.py` and `scripts/probe_model.py`: reads MODEL_MODE / MODEL_BASE_URL / MODEL_NAME / MODEL_API_KEY, keeps keys in SecretStr, allows plain HTTP only for loopback local mode, and prevents a public endpoint being labeled internal. The earlier remote tunnel/loopback configurations were superseded by the approved, container-reachable OpenAI-compatible HTTPS configuration. The real probe and full Agent Task now pass; secrets remain only in ignored local configuration.

The test runner now uses a fresh unique temporary directory under project work/ to avoid the inaccessible system pytest temp directory; no tests or assertions were skipped or weakened.
