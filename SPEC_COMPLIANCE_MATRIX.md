# Specification compliance matrix

PASS requires actual implementation and recorded test evidence. NOT_IMPLEMENTED is not acceptance. Section bundles include all normative fields, enumerations, prohibitions and acceptance examples.

Latest observed repair checkpoint (2026-09-15): 424 backend tests passed with
no failures, skips or xfails; JUnit SHA-256
`70c3959a461afd1dc5a218ceb733ab3ad09e82f96ce2d539a68c63962c18b9c8`.
Ruff, strict mypy over 116 source files, frontend lint/typecheck/17 Vitest
tests/Vite build, generated contracts and fresh migrations through
`0034_quarantine_legacy_context` passed. Rebuilt Compose API, Web, Worker,
PostgreSQL, Temporal, MinIO, SearXNG and the pinned BGE-M3 embedding service
reported healthy; API and Web returned HTTP 200 and the fresh application logs
contained no error or unhandled-exception match. The exact previously failed
PDF ingestion `59a5a700-12f6-55aa-9f7c-7282076399ad` is now `SUCCEEDED`
with four 1024-dimensional chunks, and a live Worker probe ranked the relevant
appeal-policy chunk first using `BAAI/bge-m3`. Production reranker validation
remains open; deterministic bounded RRF fallback is verified.

This checkpoint also verifies authoritative Turn ownership/context projection,
late-result isolation, stale-direct-Turn reconciliation, explicit primary and
backup model fallback with terminal audit, provider-reported usage preservation,
post-call factual usage accounting, zombie-attempt fencing, and immutable
durable model selection for both built-ins and Replan. Default environment
models freeze all public execution settings without secrets and fail closed
when those settings drift; explicitly selected historical revisions remain
executable after later edit or disable.

The follow-up semantic research fallback is separate from the primary/backup
provider mechanism. For AUTO-routed Turns with a configured CHAT model, complex
WEB/RAG/MIXED requests attempt a typed bounded model plan for queries, source
priorities, Evidence requirements, freshness and answer organization; eligible
planner failure safely retains the deterministic plan. Narrow FACT and
DOCUMENT_QA Turns retain a deterministic fast path and may invoke at most one
evidence-aware model recovery after an empty or weak first pass. Web is capped
at two search rounds and RAG at one recovery pass; the model cannot choose
Capabilities, grant permission, escape the selected document version or treat
parametric knowledge as current Evidence. Explicit document summaries use
ordered version-scoped coverage rather than similarity search, and empty
Web/RAG results do not create Evidence. Delta verification passed 294 unit, 3
contract, 15 focused real-dependency integration and 30 security tests plus
relevant Ruff and strict mypy checks. The rebuilt API, Worker and Web are
healthy with API/Web HTTP 200 and no fresh application-error log match. This
scoped evidence does not change the matrix status counts or the repository-wide
incomplete decision.

A deployed live drill forced both a primary `LLM_SERVER_ERROR` and an exhausted
three-reply `LLM_SCHEMA_ERROR`; both produced `STARTED -> USED` and selected the
configured real `Qwen/Qwen3.8-27B` backup. The non-empty/schema-valid results
and per-provider-reply usage are recorded in
`work/verification/model-fallback-live.json` (SHA-256
`d639093b45ce26915f8356d46fc3877d9fdbdb42b5f5d0144b7f2edf982863b0`).
Post-change delta validation passed 266 unit tests, 34 targeted PostgreSQL
integration tests and 19 frontend tests plus relevant static/build checks.

A production OpenAI-compatible Qwen Chat/structured probe and one complete
governed Agent Task, plus a cold-cache real web-research Agent Task through
SearXNG, remain retained as historical production-provider evidence. Anonymous
public search upstreams do not provide a commercial availability SLA.
The 2026-09-10 Qwen PASS is retained as historical evidence, and the current
tunnel/key configuration was revalidated by Task
`8708638c-5cf9-4ff3-8484-0a363bb12e53` on 2026-09-12.
Evaluation controlled acceptance executed all 72 case bindings over 63 unique
passing nodes and persisted 72 result rows plus Usage and Audit evidence. The
record window measures Evidence coverage 9/9, Claim provenance 9/9, Tool
executions 41/41, retry/replan/HITL distributions, Recovery 6/6, latency and
tokens. This is not itself production-provider validation, and capability-selection,
citation correctness, claim grounding, duplicate-side-effect and policy-violation
dataset metrics remain unmeasured. All six Section 60 pairs completed separately in controlled component
mode; the separate real-provider Task is recorded on the applicable rows. PASS rows below require direct evidence and
do not inherit status from this summary.

Current bundle totals: **53 PASS, 38 PARTIAL, 25 NOT_IMPLEMENTED** (116 rows).

## 2026-09-22 Completion and final local verification checkpoint

The latest local gate run supersedes the older test-count summaries above but
does not promote unrelated matrix rows. The full backend entry point recorded
**866 passed**, 0 failed, 0 skipped and 0 xfailed. Full Ruff passed, strict
mypy passed over **137 source files**, and the Web lint/typecheck/test/build
gate passed with **22 Vitest tests**. The current Playwright acceptance passed
1/1 against the Skills governance menu.

| Plan requirement area | Implementation mapping | Observed validation | Result / limitation |
|---|---|---|---|
| Completion contracts and deterministic policy boundary | src/enterprise_agent/contracts/domain.py; src/enterprise_agent/completion/; src/enterprise_agent/storage/completion.py; contracts/; scripts/export_contracts.py; scripts/export_openapi.py | Completion unit/integration tests; contract tests; export scripts | PASS at tested scope; clean git diff --exit-code -- contracts is not claimed because the worktree contains intended generated contract changes |
| Fenced Completion and outbox integration | src/enterprise_agent/capabilities/builtins.py; src/enterprise_agent/storage/outbox.py; src/enterprise_agent/runtime/outbox_dispatcher.py; apps/worker/main.py | 61 focused Completion tests; 16 Completion/fencing/reconciler integration tests; 5 outbox integration tests | PASS at tested scope; historical outbox identity-mismatch records remain retained |
| Question-set evaluation and traceability | scripts/evaluate_question_set.py; tests/evaluation/test_completion_question_set.py; work/verification/api-rebuilt-20260922-all-question-set.json | Seven rebuilt API observations with persisted Requirements, Assessments, Verification and Delivery results where available | PARTIAL: five COMPLETE/FULL_ANSWER/PASS; city_today and campus are fail-closed UNAVAILABLE with no delivery decision |

The rebuilt API/Worker correction is material: observations from the older
image that lacked Completion Contracts are not accepted as current Completion
evidence. These results demonstrate that execution state is not used as a
quality shortcut, but they do not close the remaining frozen release gates or
change the bundle totals above.

Quality regression checkpoint (2026-09-16, scoped; no bundle status promotion):
352 unit tests and the focused PostgreSQL policy-neighborhood integration test
passed. A real public weather Task `d61da1fc-eef6-558e-86c4-97fe05a685b1`
completed with an official dated forecast; a real three-role public question
Task `9ff14076-00a9-5892-bdfd-541244433f50` completed in 41.596 seconds
with all three requested role answers and four first-party citations. Search
and synthesis remained approximately 24.72 and 15.14 seconds respectively,
so latency is still not acceptable for a simple fact question. Procedural RAG
candidate widening, document-neighborhood Evidence, direct-answer prompting,
and a source-faithful timeout fallback passed synthetic tests. The exact
user-workspace leave/onboarding questions have **not** passed a live retest:
running them against the configured model would transmit existing HR document
content, and that escalation was rejected by the local auto-review. This
unexecuted acceptance remains PARTIAL, not PASS.

Quality regression checkpoint (2026-09-17, scoped; no bundle status promotion):
the user explicitly authorized transmitting the three scoped HR test files to
the configured model. Real model-backed Turns passed for a three-day leave
procedure (`b2d19c9e-dbc1-5b96-b99e-e5871172acc5`, 48.86 s), a general
leave procedure (`3ac96a22-47fc-5492-8ee3-ab042c8ff939`, 57.05 s), and
onboarding (`3fe88b62-b0dc-53b7-9bb4-934dd77f9048`, 52.93 s). The exact
screenshot prompt "请假流程" also routed RAG and completed in Task
`a26ec95b-627c-5811-b0c7-cb46d1c2994c` (45.687 s), returning the
application, approval, cancellation and emergency follow-up steps with
HR-02 page citations. The selected
four-page HR-02 PDF summary passed twice, including Task
`9d2511e7-0588-55d5-a86a-771c4a8ae8b6` (55.85 s), with section-level
coverage and concise grounded bullets instead of the previous raw-excerpt
fallback. Its next unrelated question in the same conversation routed WEB
without retaining the selected document. The exact two-city next-day weather
question passed in Task `690b3604-7f6a-5052-92b9-7cec46df19ee`
(38.64 s), with distinct Dongguan and Guangzhou forecasts from opened NMC
pages; the preceding real attempts exposed dated-search misses, HTTP/HTTPS
deduplication loss, 900-character evidence clipping, first-subquery-only
grounding, and duplicate-city output. The exact three-part current-leadership
question passed in Task `c7400127-c0b0-5416-865a-1bf6a64a8f3a`
(39.71 s), citing official Japanese and Australian pages after a bounded
retry of a transient upstream 503. A real DIRECT greeting probe returned
“晚上好！有什么我可以帮你的吗？” with route 0.01 s, first token 0.027 s and
total 0.037 s. A separate real "中午好，现在几点？" chat probe used the
authoritative application clock instead of a model or web guess: DIRECT route
0.01 s, first token 0.044 s, total 0.054 s, and its displayed timestamp was
within two minutes of the configured Asia/Shanghai clock. The WEB-only test
script's refusal to accept this DIRECT route is a harness assumption, not a
clock-answer failure. Current unit regression passed 364/364 with a workspace-local
pytest temporary directory, and focused real-PostgreSQL procedural retrieval
passed 1/1; frontend Vitest passed 22/22. The default system pytest temporary directory denied access and
produced 14 setup errors before the isolated rerun; these were environment
errors, not assertions. These are exemplar quality checks, not proof of broad
model/provider reliability or a production latency SLA. Complex Web/RAG
answers still take roughly 39–57 seconds, transient upstream failures remain
possible, and browser-level attachment/focus acceptance was not rerun here.

| Requirement | Frozen source | Owner | Code | Test | Result |
|---|---|---|---|---|---|
| RUN-S000 | Section 0 | 01-product-scope.md | — | — | NOT_IMPLEMENTED |
| RUN-S001 | Section 1 | 01-product-scope.md | — | — | NOT_IMPLEMENTED |
| RUN-S002 | Section 2 | 01-product-scope.md | — | — | NOT_IMPLEMENTED |
| RUN-S077 | Section 77 | 01-product-scope.md | — | — | NOT_IMPLEMENTED |
| RUN-S084 | Section 84 | 01-product-scope.md | — | — | NOT_IMPLEMENTED |
| RUN-S085 | Section 85 | 01-product-scope.md | — | — | NOT_IMPLEMENTED |
| OPS-S003 | Section 3 | 02-system-architecture.md | — | — | NOT_IMPLEMENTED |
| OPS-S004 | Section 4 | 02-system-architecture.md | — | — | NOT_IMPLEMENTED |
| OPS-S006 | Section 6 | 02-system-architecture.md | — | — | NOT_IMPLEMENTED |
| OPS-S072 | Section 72 | 02-system-architecture.md | — | — | NOT_IMPLEMENTED |
| INV-S005 | Section 5 | 03-runtime-invariants.md | src/enterprise_agent/contracts/domain.py; src/enterprise_agent/storage/; src/enterprise_agent/runtime/; src/enterprise_agent/capabilities/executor.py | tests/integration/; tests/security/; tests/failure/ | PARTIAL; INDIVIDUAL_INVARIANT_STATUS_RECORDED_BELOW |
| INV-001 | Section 5: INV-001 | 03-runtime-invariants.md | src/enterprise_agent/storage/; src/enterprise_agent/runtime/activities.py; src/enterprise_agent/runtime/workflows.py | tests/integration/test_plan_temporal_mcp.py; tests/integration/test_knowledge_temporal.py; tests/integration/test_task_control_temporal.py | PASS; POSTGRESQL_CANONICAL_TASK_PLAN_STEP_ATTEMPT_APPROVAL_OPERATION_EVIDENCE_STATE_VERIFIED |
| INV-002 | Section 5: INV-002 | 03-runtime-invariants.md | src/enterprise_agent/runtime/workflows.py; src/enterprise_agent/runtime/activities.py | tests/failure/test_worker_crash.py; tests/integration/test_plan_temporal_mcp.py; tests/integration/test_hitl_temporal_signal.py | PASS; WORKFLOWS_ORCHESTRATE_AND_ACTIVITIES_READ_OR_COMMIT_CANONICAL_POSTGRESQL_STATE |
| INV-003 | Section 5: INV-003 | 03-runtime-invariants.md | src/enterprise_agent/storage/operations.py; src/enterprise_agent/storage/outbox.py; src/enterprise_agent/capabilities/executor.py; src/enterprise_agent/runtime/reconciler.py | tests/failure/test_response_loss.py; tests/integration/test_outbox.py; tests/integration/test_capability_executor.py; tests/integration/test_reconciler.py | PASS; REPLAYABLE_OPERATION_OUTBOX_AND_RUNTIME_CONSISTENCY_RECONCILIATION_VERIFIED |
| INV-004 | Section 5: INV-004 | 03-runtime-invariants.md | src/enterprise_agent/capabilities/executor.py; src/enterprise_agent/storage/operations.py | tests/failure/test_response_loss.py; tests/integration/test_capability_executor.py | PASS; EXTERNAL_TIMEOUT_PERSISTS_UNKNOWN |
| INV-005 | Section 5: INV-005 | 03-runtime-invariants.md | src/enterprise_agent/storage/operations.py; src/enterprise_agent/procurement/service.py | tests/failure/test_response_loss.py | PASS; UNKNOWN_RECONCILES_BEFORE_ANY_RETRY_AND_NO_DUPLICATE_ADMISSION |
| INV-006 | Section 5: INV-006 | 03-runtime-invariants.md | src/enterprise_agent/storage/task_controls.py; src/enterprise_agent/runtime/workflows.py | tests/integration/test_task_control_temporal.py | PASS; CANCEL_TRANSITIONS_TASK_WITHOUT_IMPLICIT_OPERATION_ROLLBACK |
| INV-007 | Section 5: INV-007 | 03-runtime-invariants.md | src/enterprise_agent/contracts/domain.py; src/enterprise_agent/mcp/manager.py | tests/unit/test_contracts.py; tests/integration/test_api_mcp.py | PASS; EVERY_SIDE_EFFECT_DESCRIPTOR_REQUIRES_EXPLICIT_COMPENSATION_MODE |
| INV-008 | Section 5: INV-008 | 03-runtime-invariants.md | src/enterprise_agent/runtime/context.py; src/enterprise_agent/governance/policy.py; src/enterprise_agent/skills/package.py; src/enterprise_agent/capabilities/executor.py | tests/unit/test_context_manager.py; tests/security/test_policy.py; tests/security/test_skill_package.py; tests/security/test_prompt_injection.py | PASS; MALICIOUS_PDF_AND_WEB_REMAIN_UNTRUSTED_DATA_AND_CANNOT_AUTHORIZE_EXTERNAL_EFFECT |
| INV-009 | Section 5: INV-009 | 03-runtime-invariants.md | src/enterprise_agent/capabilities/executor.py; src/enterprise_agent/governance/policy.py; src/enterprise_agent/runtime/activities.py | tests/security/test_policy.py; tests/integration/test_capability_executor.py; tests/integration/test_plan_temporal_mcp.py | PASS; REAL_PLAN_MCP_READ_AND_EXTERNAL_WRITE_BOTH_CROSS_PERSISTED_DETERMINISTIC_POLICY_DECISION |
| INV-010 | Section 5: INV-010 | 03-runtime-invariants.md | src/enterprise_agent/contracts/domain.py; src/enterprise_agent/storage/tables.py; contracts/ | tests/contract/test_schemas.py; scripts/verify_migrations.py | PASS; PYDANTIC_AND_PERSISTED_CONTRACTS_CARRY_SCHEMA_VERSION_AND_GENERATED_JSON_SCHEMA |
| INV-011 | Section 5: INV-011 | 03-runtime-invariants.md | src/enterprise_agent/storage/provenance.py; src/enterprise_agent/storage/artifacts.py; src/enterprise_agent/storage/attempts.py; src/enterprise_agent/capabilities/builtins.py | tests/integration/test_provenance.py; tests/integration/test_artifacts.py; tests/e2e/test_supplier_risk_flagship.py | PASS; FLAGSHIP_DOCX_CLAIMS_TRACE_TO_HASH_VERIFIED_EVIDENCE_AND_SOURCES |
| INV-012 | Section 5: INV-012 | 03-runtime-invariants.md | src/enterprise_agent/runtime/workflows.py; src/enterprise_agent/runtime/activities.py; src/enterprise_agent/storage/checkpoints.py | tests/failure/test_worker_crash.py; tests/integration/test_plan_temporal_mcp.py | PARTIAL; ACTUAL_WORKER_KILL_RECOVERY_VERIFIED_ON_DURABLE_TASK; COMPLETE_FLAGSHIP_CRASH_POINTS_PENDING |
| INV-013 | Section 5: INV-013 | 03-runtime-invariants.md | src/enterprise_agent/storage/plans.py; src/enterprise_agent/storage/tasks.py; src/enterprise_agent/storage/model_configs.py; src/enterprise_agent/runtime/activities.py; src/enterprise_agent/capabilities/builtins.py; migrations/versions/0004_immutable_plans.py; migrations/versions/0007_task_snapshot_binding.py | tests/integration/test_immutable_plans.py; tests/integration/test_api_identity.py; tests/integration/test_plan_temporal_mcp.py; tests/integration/test_model_fallback_snapshot.py; tests/integration/test_replans.py | PASS; EVERY_API_TASK_BINDS_AND_RUNTIME_RESOLVES_AN_IMMUTABLE_HASH_VERIFIED_SNAPSHOT; EXPLICIT_HISTORICAL_PRIMARY_FALLBACK_AND_REPLAN_REMAIN_PINNED_AFTER_EDIT_OR_DISABLE; DEFAULT_ENV_PUBLIC_CONFIG_DRIFT_FAILS_CLOSED_WITHOUT_SNAPSHOTTING_SECRETS |
| INV-014 | Section 5: INV-014 | 03-runtime-invariants.md | src/enterprise_agent/storage/attempts.py; src/enterprise_agent/storage/operations.py; src/enterprise_agent/storage/outbox.py; src/enterprise_agent/capabilities/builtins.py; src/enterprise_agent/models/gateway.py | tests/integration/test_fencing.py; tests/failure/test_response_loss.py; tests/integration/test_outbox.py; tests/integration/test_model_handler_fencing.py | PASS; ZOMBIE_ATTEMPT_STALE_OUTBOX_CLAIM_LINK_AND_FALLBACK_AUDIT_AUTHORITATIVE_COMMITS_REJECTED |
| INV-015 | Section 5: INV-015 | 03-runtime-invariants.md | — | — | NOT_IMPLEMENTED |
| DOM-S007 | Section 7 | 04-domain-model.md | src/enterprise_agent/contracts/domain.py | tests/contract/test_schemas.py | PARTIAL |
| DOM-S008 | Section 8 | 04-domain-model.md | src/enterprise_agent/contracts/domain.py | tests/unit/test_contracts.py | PARTIAL |
| DOM-S013 | Section 13 | 04-domain-model.md | src/enterprise_agent/storage/attempts.py; src/enterprise_agent/storage/steps.py; src/enterprise_agent/runtime/activities.py; src/enterprise_agent/runtime/workflows.py; migrations/versions/0024_plan_step_materialization.py; migrations/versions/0025_attempt_retry_state.py | tests/integration/test_fencing.py; tests/integration/test_plan_step_lifecycle.py; tests/integration/test_skill_planner.py; tests/integration/test_plan_temporal_mcp.py | PARTIAL; MATERIALIZED_ATTEMPT_FENCING_EVIDENCE_COMMIT_CLASSIFIED_FAILURE_DATABASE_TIMED_RETRY_MAX_ATTEMPTS_TEMPORAL_SCHEDULING_DAG_UNLOCK_AND_TASK_TERMINAL_PROPAGATION_VERIFIED; REPLAN_PENDING |
| DOM-S015 | Section 15 | 04-domain-model.md | src/enterprise_agent/contracts/domain.py; src/enterprise_agent/storage/plans.py; migrations/versions/0004_immutable_plans.py | tests/contract/test_schemas.py; tests/integration/test_immutable_plans.py | PARTIAL; ROUNDTRIP_AND_DATABASE_IMMUTABILITY_VERIFIED |
| DOM-S019 | Section 19 | 04-domain-model.md | — | — | NOT_IMPLEMENTED |
| STM-S011 | Section 11 | 05-state-machines.md | src/enterprise_agent/domain/states.py; src/enterprise_agent/storage/task_controls.py | tests/unit/test_contracts.py; tests/integration/test_api_identity.py | PARTIAL; API_PAUSE_RESUME_CANCEL_AND_OPTIMISTIC_VERSION_VERIFIED |
| STM-S012 | Section 12 | 05-state-machines.md | src/enterprise_agent/domain/states.py; src/enterprise_agent/storage/steps.py; src/enterprise_agent/storage/attempts.py | tests/unit/test_contracts.py; tests/integration/test_plan_step_lifecycle.py | PARTIAL; ROOT_READY_DEPENDENT_BLOCKED_RUNNING_COMPLETED_UNLOCK_AND_TASK_COMPLETION_INTEGRATED; RETRY_REPLAN_CANCELLATION_PATHS_PENDING |
| RUN-S009 | Section 9 | 06-agent-runtime.md | src/enterprise_agent/contracts/domain.py | tests/unit/test_contracts.py | PARTIAL |
| RUN-S010 | Section 10 | 06-agent-runtime.md | src/enterprise_agent/storage/plans.py; src/enterprise_agent/storage/replans.py; src/enterprise_agent/runtime/replanner.py; src/enterprise_agent/runtime/activities.py; migrations/versions/0027_replan_decisions.py | tests/integration/test_immutable_plans.py; tests/unit/test_replanner.py; tests/integration/test_replans.py; tests/e2e/test_supplier_risk_flagship.py | PASS; ALL_REPLAN_ACTIONS_AND_EXPLICIT_SOURCE_FENCED_CONSECUTIVE_REPLANS_APPEND_IMMUTABLE_PLAN_VERSIONS |
| RUN-S038 | Section 38 | 06-agent-runtime.md | src/enterprise_agent/models/gateway.py; src/enterprise_agent/models/settings.py; src/enterprise_agent/api/app.py; src/enterprise_agent/runtime/activities.py; deploy/docker/backend.Dockerfile | tests/security/test_model_secrets.py; tests/unit/test_model_settings.py; tests/integration/test_api_models.py; scripts/probe_model.py; work/verification/model-live.json; RELEASE_VALIDATION_REPORT.md | PARTIAL; OPENAI_COMPATIBLE_CHAT_AND_STRUCTURED_PATHS_TOKEN_USAGE_TIMEOUT_RETRY_SCHEMA_REPAIR_TESTED; 2026_09_10_QWEN_PROBE_AND_COMPLETE_AGENT_TASK_PASSED_WITH_2298_TASK_TOKENS_AND_TRACE; EMBED_AND_ALL_STRUCTURED_ROLES_NOT_PRODUCTION_VALIDATED |
| RUN-S039 | Section 39 | 06-agent-runtime.md | src/enterprise_agent/models/gateway.py; src/enterprise_agent/runtime/activities.py; src/enterprise_agent/runtime/budgets.py; src/enterprise_agent/capabilities/builtins.py | tests/security/test_model_secrets.py; tests/unit/test_fallback_model_gateway.py; tests/integration/test_model_fallback_snapshot.py; tests/integration/test_model_handler_fencing.py; tests/integration/test_budgets.py; tests/failure/test_deterministic_injection.py; tests/integration/test_replans.py; work/verification/model-fallback-live.json; RELEASE_VALIDATION_REPORT.md | PARTIAL; STRUCTURED_SCHEMA_FAILURE_TWO_REPAIR_LIMIT_TIMEOUT_429_USAGE_AND_RUNTIME_BUDGET_RECORDING_VERIFIED; EXPLICIT_PRIMARY_BACKUP_FALLBACK_ONLY_ON_ELIGIBLE_SERVER_OR_EXHAUSTED_SCHEMA_FAILURE_WITH USED_FAILED_CANCELLED_AUDIT_AND NO_STREAM_EMBED_TIMEOUT_RATE_LIMIT_OR_DETERMINISTIC_HTTP_REJECTION_BYPASS; MALFORMED_ENVELOPE_AND_REPAIR_USAGE_PRESERVED_AND_FACTUALLY_RECORDED_AFTER_CALL; DEPLOYED REAL_QWEN_BACKUP FAILOVER PASSED FOR PRIMARY_SERVER_FAILURE_AND_EXHAUSTED_STRUCTURED_SCHEMA_REPAIR; TEMPORARY_BACKUP_ENDPOINT_REMAINS_AN_OPERATIONAL_LIMIT |
| RUN-S046 | Section 46 | 06-agent-runtime.md | src/enterprise_agent/runtime/reviewer.py; src/enterprise_agent/capabilities/builtins.py; src/enterprise_agent/contracts/domain.py | tests/unit/test_reviewer.py; tests/unit/test_contracts.py; tests/unit/test_web_task_result.py; tests/integration/test_builtin_capabilities.py; tests/e2e/test_supplier_risk_flagship.py | PASS; CONDITIONAL_REVIEWER_EXECUTES_IN_FLAGSHIP_AND_REJECTS_UNGROUNDED_OR_INVALID_CLAIMS; ANSWER_CONTRACT_GROUNDING_AND_CONDITIONAL_QUALITY_REPAIR_VERIFIED |
| RUN-S047 | Section 47 | 06-agent-runtime.md | — | — | NOT_IMPLEMENTED |
| RUN-S048 | Section 48 | 06-agent-runtime.md | src/enterprise_agent/runtime/budgets.py; src/enterprise_agent/capabilities/executor.py; src/enterprise_agent/storage/tasks.py; migrations/versions/0010_task_budgets.py | tests/integration/test_budgets.py; tests/integration/test_capability_executor.py; tests/integration/test_api_identity.py; tests/integration/test_replan_usage_accounting.py | PARTIAL; CAPABILITY_COUNT_COST_DEADLINE_ATOMIC_PRECHECK_VERIFIED; EVERY_REAL_REPLAN_PROVIDER_REPLY_IS_IDEMPOTENTLY_RECORDED_BEFORE_REPAIR_OR_BUDGET_REJECTION_WITH_CONCURRENT_REPLAY_AND_LATE_CALLBACK_FENCING |
| RUN-S049 | Section 49 | 06-agent-runtime.md | src/enterprise_agent/runtime/loop_guard.py; src/enterprise_agent/capabilities/executor.py; src/enterprise_agent/storage/attempts.py; src/enterprise_agent/storage/tasks.py; migrations/versions/0011_runtime_loop_guards.py | tests/unit/test_research_fallback_loop.py; tests/unit/test_bounded_qa_fast_paths.py; tests/unit/test_knowledge_semantic_recovery.py; tests/integration/test_capability_executor.py; tests/integration/test_loop_guard.py; tests/integration/test_api_identity.py | PARTIAL; REPEATED_CAPABILITY_ARGUMENT_NO_EVIDENCE_AND_REPLAN_ABORTS_VERIFIED; LOCAL_WEB_AND_RAG_RECOVERY_BOUNDS_VERIFIED; PLANNER_INTEGRATION_PENDING |
| RUN-S050 | Section 50 | 06-agent-runtime.md | src/enterprise_agent/contracts/domain.py; src/enterprise_agent/runtime/retry.py; src/enterprise_agent/capabilities/executor.py | tests/unit/test_retry_matrix.py; tests/integration/test_capability_executor.py | PARTIAL; FROZEN_CODES_CENTRAL_CLASSIFICATION_AND_CAPABILITY_FAILURES_VERIFIED; ALL_RUNTIME_LAYERS_PENDING |
| RUN-S051 | Section 51 | 06-agent-runtime.md | src/enterprise_agent/runtime/retry.py; src/enterprise_agent/capabilities/executor.py; src/enterprise_agent/models/gateway.py; src/enterprise_agent/storage/operations.py; src/enterprise_agent/storage/model_configs.py | tests/unit/test_retry_matrix.py; tests/integration/test_capability_executor.py; tests/security/test_model_secrets.py; tests/unit/test_fallback_model_gateway.py; tests/integration/test_model_fallback_snapshot.py; tests/failure/test_response_loss.py; work/verification/model-fallback-live.json | PARTIAL; BOUNDED_READ_RETRY_SCHEMA_REPAIR_SIDE_EFFECT_UNKNOWN_RECONCILE_AND_NO_BLIND_RETRY_VERIFIED; EXPLICIT_FINITE_MODEL_FALLBACK_STATE_MACHINE_AND_IMMUTABLE_MODEL_CHAIN_VERIFIED; DEPLOYED REAL_BACKUP_PROVIDER FAILOVER PASSED FOR ELIGIBLE_AVAILABILITY_FAILURE_AND_EXHAUSTED_STRUCTURED_SCHEMA_FAILURE; CURRENT_TEMPORARY_TUNNEL_PREVENTS_A_PRODUCTION_SLA_CLAIM |
| RUN-S056 | Section 56 | 06-agent-runtime.md | src/enterprise_agent/runtime/admission.py; src/enterprise_agent/runtime/activities.py; src/enterprise_agent/runtime/workflows.py; src/enterprise_agent/capabilities/executor.py; src/enterprise_agent/models/gateway.py; src/enterprise_agent/storage/outbox.py; src/enterprise_agent/runtime/outbox_dispatcher.py; apps/worker/main.py; migrations/versions/0012_admission_control.py | tests/integration/test_admission_control.py; tests/integration/test_capability_executor.py; tests/integration/test_outbox.py; tests/integration/test_plan_temporal_mcp.py; tests/integration/test_temporal_continue_as_new.py | PASS; REAL_REDIS_GLOBAL_WORKSPACE_MODEL_PROVIDER_CAPABILITY_AND_MCP_GATES_READY_BLOCK_REASON_WORKSPACE_FAIRNESS_DORMANT_BOUNDARY_RELEASE_AND_ZERO_PROVIDER_CALLS_WHILE_BLOCKED_VERIFIED |
| RUN-S065 | Section 65 | 06-agent-runtime.md | src/enterprise_agent/storage/tables.py; src/enterprise_agent/storage/usage.py; src/enterprise_agent/storage/evaluation.py; src/enterprise_agent/runtime/budgets.py; migrations/versions/0028_evaluation_usage_incidents.py | tests/integration/test_api_evaluation.py; tests/integration/test_budgets.py; work/verification/evaluation/report.json; RELEASE_VALIDATION_REPORT.md | PARTIAL; WORKSPACE_TASK_EVALUATION_USAGE_TOKEN_COST_AND_BUDGET_LINKS_PERSISTED; REAL_QWEN_TASK_RECORDED_2298_TOTAL_TOKENS_AND_LINKED_TRACE_DETAILS; MODEL_INPUT_OUTPUT_SPLIT_EMBEDDING_DURATION_AND_DIRECT_TRACE_ID_COLUMNS_PENDING |
| TMP-S054 | Section 54 | 07-temporal-execution.md | src/enterprise_agent/runtime/; apps/worker/ | tests/failure/test_worker_crash.py; tests/integration/test_hitl_temporal_signal.py; tests/integration/test_task_control_temporal.py; tests/integration/test_plan_temporal_mcp.py; tests/integration/test_replans.py; tests/integration/test_temporal_continue_as_new.py; tests/e2e/test_supplier_risk_flagship.py | PASS; DETERMINISTIC_WORKFLOW_ACTIVITY_BOUNDARY_HEARTBEAT_SIGNAL_CANCELLATION_RECOVERY_VERSION_BINDING_REPLAN_AND_CONTINUE_AS_NEW_VERIFIED |
| TMP-S055 | Section 55 | 07-temporal-execution.md | src/enterprise_agent/runtime/workflows.py; src/enterprise_agent/runtime/activities.py; src/enterprise_agent/storage/checkpoints.py | tests/integration/test_temporal_continue_as_new.py; tests/integration/test_checkpoints.py | PASS; HISTORY_THRESHOLDS_PERSIST_CANONICAL_CHECKPOINT_AND_CONTINUE_WITH_MINIMAL_FROZEN_PAYLOAD |
| CAP-S016 | Section 16 | 08-capability-skill-mcp.md | src/enterprise_agent/contracts/domain.py; src/enterprise_agent/capabilities/registry.py; src/enterprise_agent/capabilities/executor.py; src/enterprise_agent/storage/operations.py; src/enterprise_agent/runtime/budgets.py; src/enterprise_agent/runtime/loop_guard.py; src/enterprise_agent/runtime/retry.py; migrations/versions/0009_capability_calls.py | tests/security/test_policy.py; tests/integration/test_capability_executor.py; tests/integration/test_budgets.py; tests/failure/test_response_loss.py | PARTIAL; VERSION_SCHEMA_POLICY_IDEMPOTENCY_HITL_SIDE_EFFECT_FENCING_BUDGET_LOOP_AND_READ_RETRY_PATHS_VERIFIED |
| SKL-S018 | Section 18 | 08-capability-skill-mcp.md | src/enterprise_agent/skills/package.py; src/enterprise_agent/storage/skills.py; src/enterprise_agent/runtime/skill_planner.py; src/enterprise_agent/runtime/activities.py; src/enterprise_agent/runtime/workflows.py; src/enterprise_agent/capabilities/builtins.py; skills/supplier-risk/; apps/web/src/ | tests/security/test_skill_package.py; tests/integration/test_api_skills.py; tests/integration/test_skill_planner.py; tests/integration/test_builtin_capabilities.py; tests/e2e/test_supplier_risk_flagship.py | PASS; DECLARATIVE_NO_CODE_SKILL_INSTALLS_RESOLVES_AND_EXECUTES_COMPLETE_FLAGSHIP_WITHOUT_RUNTIME_SKILL_NAME_BRANCH |
| MCP-S020 | Section 20 | 08-capability-skill-mcp.md | src/enterprise_agent/mcp/manager.py; src/enterprise_agent/mcp/adapter.py; src/enterprise_agent/capabilities/executor.py; src/enterprise_agent/runtime/activities.py; src/enterprise_agent/runtime/workflows.py; src/enterprise_agent/api/app.py; src/enterprise_agent/storage/tables.py; apps/web/src/; migrations/versions/0023_mcp_manager.py | tests/integration/test_api_mcp.py; tests/integration/test_plan_temporal_mcp.py; tests/failure/test_deterministic_injection.py; tests/e2e/test_supplier_risk_flagship.py | PASS; REGISTER_CONNECT_DISCOVER_SCHEMA_SYNC_NORMALIZE_HEALTH_RECONNECT_DISABLE_AUDIT_AND_RUNTIME_INVOCATION_VERIFIED |
| MCP-S021 | Section 21 | 08-capability-skill-mcp.md | src/enterprise_agent/procurement/; src/enterprise_agent/mcp/manager.py; src/enterprise_agent/mcp/adapter.py; src/enterprise_agent/runtime/activities.py; mcp_servers/procurement/server.py; mcp_servers/procurement/migrations/ | tests/integration/test_procurement.py; tests/integration/test_api_mcp.py; tests/integration/test_plan_temporal_mcp.py; tests/e2e/test_supplier_risk_flagship.py; scripts/verify_procurement_migrations.py | PASS; SIX_TOOL_INDEPENDENT_DATABASE_MCP_READS_AND_HITL_GATED_IDEMPOTENT_ADMISSION_EXECUTE_IN_COMPLETE_FLAGSHIP |
| CAP-S044 | Section 44 | 08-capability-skill-mcp.md | — | — | NOT_IMPLEMENTED |
| SEC-S017 | Section 17 | 09-governance-security.md | — | — | NOT_IMPLEMENTED |
| SEC-S028 | Section 28 | 09-governance-security.md | src/enterprise_agent/governance/policy.py | tests/security/test_policy.py | PARTIAL |
| SEC-S029 | Section 29 | 09-governance-security.md | src/enterprise_agent/governance/policy.py | tests/security/test_policy.py | PARTIAL |
| SEC-S030 | Section 30 | 09-governance-security.md | src/enterprise_agent/contracts/domain.py; src/enterprise_agent/storage/approvals.py; src/enterprise_agent/storage/approval_queries.py; src/enterprise_agent/storage/operations.py; src/enterprise_agent/storage/outbox.py; src/enterprise_agent/runtime/activities.py; src/enterprise_agent/runtime/workflows.py; src/enterprise_agent/runtime/outbox_dispatcher.py; src/enterprise_agent/api/app.py; apps/web/src/; migrations/versions/0005_durable_approvals.py; migrations/versions/0008_transactional_outbox.py | tests/security/test_hitl.py; tests/failure/test_response_loss.py; tests/integration/test_hitl_temporal_signal.py; tests/integration/test_api_approvals.py; tests/integration/test_plan_temporal_mcp.py; tests/integration/test_outbox.py; pnpm check | PASS; POLICY_REQUIRE_APPROVAL_IMMUTABLE_HASH_BOUND_PARAMETERS_TASK_WAITING_HITL_APPROVE_REJECT_FRONTEND_CARD_OUTBOX_SIGNAL_PARENT_RESUME_AND_REAL_EXTERNAL_WRITE_VERIFIED |
| SEC-S031 | Section 31 | 09-governance-security.md | src/enterprise_agent/storage/audit.py; src/enterprise_agent/storage/tables.py; src/enterprise_agent/api/app.py; migrations/versions/0001_runtime.py; migrations/versions/0019_audit_event_fields.py; migrations/versions/0020_audit_workspace_foreign_keys.py | tests/integration/test_fencing.py; tests/integration/test_api_audit.py; tests/integration/test_capability_executor.py; tests/integration/test_api_approvals.py; scripts/verify_migrations.py | PASS; APPEND_ONLY_FROZEN_ACTOR_WORKSPACE_TASK_STEP_ACTION_RESOURCE_CAPABILITY_POLICY_APPROVAL_RESULT_TRACE_TIMESTAMP_FIELDS_RBAC_AND_WORKSPACE_FKS_VERIFIED |
| SEC-S032 | Section 32 | 09-governance-security.md | src/enterprise_agent/identity/; migrations/versions/0006_local_identity.py; migrations/versions/0029_core_registries.py; src/enterprise_agent/api/app.py | tests/integration/test_api_identity.py; tests/unit/test_oidc_contract.py; tests/integration/test_api_audit.py | PASS; LOCAL_AUTH_SESSION_REVOCATION_ALL_ACTOR_TYPES_AND_OIDC_ADAPTER_BOUNDARY_VERIFIED |
| SEC-S033 | Section 33 | 09-governance-security.md | src/enterprise_agent/storage/tables.py; src/enterprise_agent/identity/service.py; src/enterprise_agent/api/app.py | tests/integration/test_fencing.py; tests/integration/test_rls.py; tests/integration/test_api_identity.py | PARTIAL; TASK_RUNS_RLS_AND_WORKSPACE_API_ISOLATION_VERIFIED |
| SEC-S034 | Section 34 | 09-governance-security.md | src/enterprise_agent/governance/secrets.py | tests/security/test_model_secrets.py | PARTIAL |
| SEC-S035 | Section 35 | 09-governance-security.md | — | — | NOT_IMPLEMENTED |
| SEC-S036 | Section 36 | 09-governance-security.md | src/enterprise_agent/runtime/context.py; src/enterprise_agent/governance/policy.py; src/enterprise_agent/capabilities/executor.py | tests/security/test_prompt_injection.py; tests/security/test_policy.py | PASS; MALICIOUS_PDF_AND_WEB_CANNOT_BYPASS_ALLOWLIST_SCHEMA_POLICY_EGRESS_OR_HITL |
| SEC-S037 | Section 37 | 09-governance-security.md | src/enterprise_agent/models/gateway.py | tests/security/test_model_secrets.py | PARTIAL |
| CTX-S040 | Section 40 | 10-context-rag-evidence.md | src/enterprise_agent/contracts/domain.py; src/enterprise_agent/runtime/context.py | tests/unit/test_context_manager.py; tests/unit/test_contracts.py | PARTIAL; CANDIDATE_PRIORITY_RELEVANCE_DEDUP_COMPRESSION_TOKEN_BUDGET_AND_PER_ITEM_DECISIONS_VERIFIED; TRACE_PERSISTENCE_PENDING |
| CTX-S041 | Section 41 | 10-context-rag-evidence.md | src/enterprise_agent/contracts/domain.py; src/enterprise_agent/storage/memory.py; src/enterprise_agent/storage/turns.py; src/enterprise_agent/storage/tables.py; src/enterprise_agent/runtime/conversation_results.py; src/enterprise_agent/runtime/reconciler.py; src/enterprise_agent/api/app.py; migrations/versions/0017_project_memory.py; migrations/versions/0018_conversation_messages.py; migrations/versions/0032_conversation_turn_lifecycle.py; migrations/versions/0033_conservative_legacy_turn_backfill.py; migrations/versions/0034_quarantine_legacy_turn_context.py | tests/integration/test_memory.py; tests/integration/test_api_conversations.py; tests/integration/test_conversation_turns.py; tests/unit/test_conversation_result_gate.py; tests/unit/test_legacy_turn_backfill.py; tests/contract/test_schemas.py; apps/web/src/components/chat-page.test.tsx; scripts/verify_migrations.py | PASS; VERSIONED_MEMORY_AND AUTHORITATIVE_TURN_ATTEMPT_OUTCOME_LIFECYCLE_WITH_SINGLE_CONTEXT_PROJECTION_COMPLETED_ONLY_ELIGIBILITY_LATE_RESULT_ISOLATION_STALE_DIRECT_RECONCILIATION_AND_ZERO_LEGACY_CONTEXT_ELIGIBILITY_VERIFIED |
| CTX-S042 | Section 42 | 10-context-rag-evidence.md | src/enterprise_agent/models/gateway.py; src/enterprise_agent/models/settings.py; src/enterprise_agent/api/app.py; src/enterprise_agent/runtime/document_parser.py; src/enterprise_agent/runtime/knowledge_ingestion.py; src/enterprise_agent/runtime/retrieval.py; src/enterprise_agent/runtime/activities.py; src/enterprise_agent/runtime/workflows.py; src/enterprise_agent/runtime/outbox_dispatcher.py; src/enterprise_agent/storage/knowledge.py; src/enterprise_agent/storage/knowledge_jobs.py; src/enterprise_agent/storage/tables.py; deploy/compose/infrastructure.yml; migrations/versions/0016_knowledge_index.py; migrations/versions/0021_knowledge_ingestion_jobs.py | tests/security/test_model_secrets.py; tests/unit/test_model_settings.py; tests/unit/test_document_parser.py; tests/unit/test_retrieval.py; tests/unit/test_compose_embedding.py; tests/unit/test_knowledge_ingestion_stability.py; tests/unit/test_knowledge_rerank_fallback.py; tests/unit/test_knowledge_semantic_recovery.py; tests/unit/test_result_evidence.py; tests/unit/test_web_task_result.py; tests/integration/test_knowledge.py; tests/integration/test_api_knowledge.py; tests/integration/test_knowledge_job_retry.py; tests/integration/test_knowledge_temporal.py; tests/integration/test_outbox.py; scripts/verify_migrations.py; RELEASE_VALIDATION_REPORT.md | PARTIAL; SEVEN_FORMAT_PARSE_NORMALIZE_LOCATORS_MINIO_STAGING_AUTHENTICATED_UPLOAD_DURABLE_JOB_TYPED_FAILURE_RETRY_OUTBOX_TEMPORAL_ACTIVITY_FENCING_REPLAY_BATCH_EMBED_POSTGRES_FTS_PGVECTOR_AND_BOUNDED_RERANK_WITH_DETERMINISTIC_RRF_FALLBACK_VERIFIED; BOUNDED_SEMANTIC_QUERY_RECOVERY_ORDERED_SELECTED_VERSION_SUMMARY_COVERAGE_AND_EMPTY_RESULT_NO_EVIDENCE_VERIFIED; DEPLOYED_PINNED_BGE_M3 INGESTED_THE_EXACT_FAILED_PDF_INTO_FOUR_1024D_CHUNKS_AND_LIVE_SEMANTIC_RETRIEVAL_RANKED_THE_RELEVANT_CHUNK_FIRST; PRODUCTION_RERANKER_VALIDATION_PENDING |
| CTX-S043 | Section 43 | 10-context-rag-evidence.md | src/enterprise_agent/contracts/domain.py; src/enterprise_agent/storage/attempts.py; src/enterprise_agent/storage/provenance.py; src/enterprise_agent/storage/artifacts.py; src/enterprise_agent/storage/tables.py; migrations/versions/0014_claim_provenance.py; migrations/versions/0015_artifacts.py | tests/integration/test_fencing.py; tests/integration/test_provenance.py; tests/integration/test_artifacts.py; tests/unit/test_reviewer.py; scripts/verify_migrations.py | PARTIAL; IMMUTABLE_WORKSPACE_SCOPED_EVIDENCE_CLAIM_ARTIFACT_PROVENANCE_HASH_VERIFIED_REPLAY_AND_REVIEW_GROUNDING_VERIFIED; SOURCE_DOCUMENT_MODEL_PENDING |
| CTX-S045 | Section 45 | 10-context-rag-evidence.md | src/enterprise_agent/contracts/domain.py; src/enterprise_agent/storage/artifacts.py; src/enterprise_agent/capabilities/builtins.py; src/enterprise_agent/storage/tables.py; migrations/versions/0015_artifacts.py | tests/integration/test_artifacts.py; tests/integration/test_builtin_capabilities.py; tests/e2e/test_supplier_risk_flagship.py | PASS; REAL_MINIO_HASH_VERIFIED_IMMUTABLE_DOCX_WITH_REQUIRED_SECTIONS_AND_CLAIM_CITATIONS_VERIFIED |
| REC-S014 | Section 14 | 11-consistency-recovery.md | src/enterprise_agent/storage/attempts.py; src/enterprise_agent/storage/operations.py | tests/integration/test_fencing.py; tests/failure/test_response_loss.py | PARTIAL; EVIDENCE_AND_SIDE_EFFECT_RESULTS_VERIFIED |
| REC-S022 | Section 22 | 11-consistency-recovery.md | src/enterprise_agent/procurement/service.py; src/enterprise_agent/storage/operations.py | tests/integration/test_procurement.py; tests/failure/test_response_loss.py | PARTIAL |
| REC-S023 | Section 23 | 11-consistency-recovery.md | src/enterprise_agent/storage/operations.py; src/enterprise_agent/procurement/service.py; src/enterprise_agent/capabilities/executor.py | tests/failure/test_response_loss.py; tests/integration/test_capability_executor.py | PARTIAL; UNKNOWN_RECONCILIATION_AND_SIDE_EFFECT_TIMEOUT_NO_BLIND_RETRY_VERIFIED |
| REC-S024 | Section 24 | 11-consistency-recovery.md | — | — | NOT_IMPLEMENTED |
| REC-S025 | Section 25 | 11-consistency-recovery.md | src/enterprise_agent/storage/attempts.py; src/enterprise_agent/storage/operations.py; src/enterprise_agent/storage/outbox.py; src/enterprise_agent/runtime/activities.py | tests/integration/test_fencing.py; tests/integration/test_outbox.py; tests/failure/test_response_loss.py; tests/failure/test_worker_crash.py | PARTIAL; ATTEMPT_AND_TRANSACTIONAL_OUTBOX_LEASE_FENCING_VERIFIED |
| REC-S026 | Section 26 | 11-consistency-recovery.md | src/enterprise_agent/storage/outbox.py; src/enterprise_agent/storage/task_controls.py; src/enterprise_agent/storage/approval_queries.py; src/enterprise_agent/runtime/outbox_dispatcher.py; src/enterprise_agent/runtime/reconciler.py | tests/integration/test_outbox.py; tests/integration/test_api_approvals.py; tests/integration/test_task_control_temporal.py; tests/integration/test_plan_temporal_mcp.py; tests/integration/test_reconciler.py | PASS; OUTBOX_SIGNAL_DELIVERY_AND_GENERAL_POSTGRES_TEMPORAL_RECONCILER_VERIFIED |
| REC-S027 | Section 27 | 11-consistency-recovery.md | src/enterprise_agent/runtime/reconciler.py; src/enterprise_agent/storage/tables.py; migrations/versions/0028_evaluation_usage_incidents.py | tests/integration/test_reconciler.py; scripts/verify_migrations.py | PASS; INVALID_COMBINATIONS_CREATE_IMMUTABLE_INCIDENTS_SAFE_REPAIRS_ARE_IDEMPOTENT_AND_MALFORMED_TASKS_ARE_ISOLATED_FOR_MANUAL_REVIEW |
| REC-S052 | Section 52 | 11-consistency-recovery.md | src/enterprise_agent/contracts/domain.py; src/enterprise_agent/storage/checkpoints.py; src/enterprise_agent/storage/tables.py; migrations/versions/0013_task_checkpoints.py | tests/integration/test_checkpoints.py; scripts/verify_migrations.py | PASS; IMMUTABLE_HASH_VERIFIED_REFERENCE_ONLY_CHECKPOINT_WITH_IDEMPOTENT_RESUME_TOKEN_AND_FRESH_MIGRATION |
| REC-S053 | Section 53 | 11-consistency-recovery.md | src/enterprise_agent/runtime/; apps/worker/ | tests/failure/test_worker_crash.py | PARTIAL; ACTUAL_PROCESS_KILL_AND_RESUME_VERIFIED |
| OBS-S057 | Section 57 | 12-observability-evaluation.md | src/enterprise_agent/observability/tracing.py; src/enterprise_agent/api/app.py | tests/integration/test_observability.py; tests/integration/test_api_identity.py | PARTIAL; HTTP_OTEL_TRACE_ID_AND_CORRELATED_RESPONSE_VERIFIED; FULL_CROSS_LAYER_EXPORT_PENDING |
| OBS-S058 | Section 58 | 12-observability-evaluation.md | src/enterprise_agent/observability/metrics.py; src/enterprise_agent/api/app.py; src/enterprise_agent/capabilities/executor.py | tests/integration/test_observability.py; tests/integration/test_capability_executor.py | PARTIAL; ALL_REQUIRED_METRIC_FAMILIES_EXPOSED_AND_CORE_PATHS_INSTRUMENTED; DASHBOARDS_PENDING |
| EVAL-S059 | Section 59 | 12-observability-evaluation.md | src/enterprise_agent/evaluation/; src/enterprise_agent/storage/evaluation.py; evaluation/execution_manifest.json; scripts/run_evaluation.py; src/enterprise_agent/api/app.py | tests/unit/test_evaluation.py; tests/integration/test_api_evaluation.py; tests/evaluation/test_evaluation_gate.py; work/verification/evaluation/report.json | PARTIAL; CONTROLLED_ACCEPTANCE_EXECUTED_72_OF_72_BINDINGS_OVER_63_PASSING_UNIQUE_NODES_AND_PERSISTED_72_RESULTS_USAGE_AUDIT; TASK_SUCCESS_72_OF_72_EVIDENCE_COVERAGE_9_OF_9_CLAIM_PROVENANCE_9_OF_9_TOOL_EXECUTIONS_41_OF_41_RECOVERY_6_OF_6_PLUS_RETRY_REPLAN_HITL_LATENCY_TOKEN_STATS_FROM_DATABASE_WINDOW; CAPABILITY_SELECTION_CITATION_CORRECTNESS_CLAIM_GROUNDING_DUPLICATE_SIDE_EFFECT_AND_POLICY_VIOLATION_DATASET_METRICS_PENDING |
| EVAL-S060 | Section 60 | 12-observability-evaluation.md | evaluation/ablations.json; src/enterprise_agent/evaluation/runner.py; src/enterprise_agent/evaluation/ablation.py; scripts/run_ablations.py | tests/unit/test_evaluation.py; tests/unit/test_ablations.py; tests/evaluation/test_evaluation_gate.py; work/verification/evaluation/ablation-report.json | PASS; SIX_REQUIRED_PAIRS_EXECUTED_IN_CONTROLLED_COMPONENT_MODE_WITH_RESULTS_HASH_AND_EXPLICIT_NON_PRODUCTION_LIMITATION |
| OBS-S061 | Section 61 | 12-observability-evaluation.md | src/enterprise_agent/runtime/failure_injector.py; src/enterprise_agent/models/gateway.py; src/enterprise_agent/mcp/manager.py; src/enterprise_agent/capabilities/executor.py | tests/failure/test_deterministic_injection.py; tests/integration/test_capability_executor.py; tests/security/test_prompt_injection.py | PASS; ALL_REQUIRED_FAILURE_POINTS_HAVE_DETERMINISTIC_SCHEDULES_AND_CORE_TRANSPORT_INJECTIONS |
| API-S070 | Section 70 | 13-database-api-contract.md | src/enterprise_agent/api/app.py; src/enterprise_agent/contracts/domain.py; src/enterprise_agent/storage/tasks.py; src/enterprise_agent/storage/steps.py; src/enterprise_agent/storage/attempts.py; src/enterprise_agent/storage/task_controls.py; src/enterprise_agent/storage/approval_queries.py; src/enterprise_agent/storage/memory.py; src/enterprise_agent/storage/artifacts.py; src/enterprise_agent/storage/audit.py; src/enterprise_agent/runtime/outbox_dispatcher.py; apps/api/main.py; contracts/openapi.json | tests/unit/test_chat_routing.py; tests/contract/test_openapi.py; tests/integration/test_api_identity.py; tests/integration/test_api_approvals.py; tests/integration/test_api_conversations.py; tests/integration/test_api_artifacts.py; tests/integration/test_api_audit.py; tests/integration/test_skill_planner.py | PARTIAL; AUTH_WORKSPACE_CONVERSATION_MESSAGE_TASK_PLAN_STEP_ATTEMPT_EVIDENCE_QUERY_TASK_CONTROL_APPROVAL_ARTIFACT_DOWNLOAD_AUDIT_RBAC_RELIABLE_SIGNAL_AND_UNIFORM_ERRORS_VERIFIED; AUTO_TYPED_BOUNDED_RESEARCH_PLAN_FAST_PATH_AND_SELECTED_DOCUMENT_LOCK_VERIFIED |
| DB-S071 | Section 71 | 13-database-api-contract.md | src/enterprise_agent/storage/tables.py; migrations/versions/0001_runtime.py through migrations/versions/0030_data_lifecycle.py; mcp_servers/procurement/migrations/versions/0001_procurement.py | tests/integration/; tests/security/; tests/failure/; scripts/verify_migrations.py; scripts/verify_procurement_migrations.py | PASS; ALL_FROZEN_CORE_TABLES_WORKSPACE_KEYS_CONSTRAINTS_INDEXES_RLS_AND_FRESH_MIGRATION_HEADS_VERIFIED |
| UI-S068 | Section 68 | 14-frontend-ux.md | apps/web/src/App.tsx; apps/web/src/api.ts; apps/web/src/components/ui/; apps/web/package.json; apps/web/components.json | apps/web/src/App.test.tsx; apps/web/e2e/platform.spec.ts; pnpm check; pnpm test:e2e | PARTIAL; REACT_TYPESCRIPT_VITE_TAILWIND_SHADCN_SHELL_AND_REAL_AUTH_WORKSPACE_CHAT_TASK_PLAN_STEP_ATTEMPT_EVIDENCE_KNOWLEDGE_SKILL_MCP_APPROVAL_ARTIFACT_AUDIT_API_INTEGRATION_VERIFIED; REAL_CHROMIUM_REGISTER_LOGIN_WORKSPACE_CREATE_SELECT_PASS; ATTACHMENT_AND_DEEP_TRACE_BROWSER_ACCEPTANCE_PENDING |
| UI-S069 | Section 69 | 14-frontend-ux.md | apps/web/src/App.tsx; apps/web/src/api.ts | apps/web/src/App.test.tsx; pnpm typecheck; pnpm build | PARTIAL; LIVE_TASK_CARDS_STATE_SNAPSHOT_PLAN_TASKSTEP_STATE_ATTEMPT_EVIDENCE_COUNTS_AND_OPTIMISTIC_PAUSE_RESUME_CANCEL_CONTROLS_IMPLEMENTED; CLAIM_REPLAN_TRACE_DETAILS_PENDING |
| ACC-S066 | Section 66 | 15-supplier-risk-scenario.md | src/enterprise_agent/runtime/; src/enterprise_agent/capabilities/builtins.py; skills/supplier-risk/; mcp_servers/procurement/ | tests/e2e/test_supplier_risk_flagship.py | PASS; FULL_SUPPLIER_RISK_AND_CONDITIONAL_ADMISSION_FLOW_VERIFIED_FOR_A_B_AND_C |
| ACC-S067 | Section 67 | 15-supplier-risk-scenario.md | src/enterprise_agent/procurement/demo.py; scripts/init_procurement.py; scripts/generate_demo_data.py; skills/supplier-risk/; demo/supplier-risk/generated/ | tests/integration/test_demo_data.py; tests/integration/test_procurement.py | PASS; A_ADMIT_B_REJECT_C_REPLAN_FIXTURES_POLICY_DOCX_PDF_SUPPLIER_XLSX_CONTRACT_PURCHASE_RISK_PROCUREMENT_DATABASE_SEED_AND_DETERMINISTIC_SKILL_ZIP_HASH_VERIFIED |
| ACC-S062 | Section 62 | 16-testing-acceptance.md | src/enterprise_agent/runtime/failure_injector.py; src/enterprise_agent/runtime/; src/enterprise_agent/capabilities/executor.py; src/enterprise_agent/mcp/manager.py | tests/failure/; tests/security/test_prompt_injection.py; tests/integration/test_replans.py; tests/integration/test_rls.py | PASS; ALL_TEN_FROZEN_HARD_FAILURE_SCENARIOS_HAVE_DIRECT_PASSING_TESTS |
| ACC-S078 | Section 78 | 16-testing-acceptance.md | src/; apps/; mcp_servers/ | tests/fakes/; tests/unit/; tests/integration/; tests/security/; tests/failure/; tests/e2e/ | PASS; PRODUCTION_PATHS_USE_REAL_DEPENDENCIES_AND_TEST_DOUBLES_ARE_CONFINED_TO_TESTS_FAKES_OR_TEST_HTTP_SERVERS |
| ACC-S079 | Section 79 | 16-testing-acceptance.md | — | — | NOT_IMPLEMENTED |
| ACC-S080 | Section 80 | 16-testing-acceptance.md | — | — | NOT_IMPLEMENTED |
| ACC-001 | Section 80: ACC-001 | 16-testing-acceptance.md | src/enterprise_agent/runtime/; src/enterprise_agent/capabilities/builtins.py; skills/supplier-risk/; mcp_servers/procurement/ | tests/e2e/test_supplier_risk_flagship.py | PASS; SUPPLIER_A_B_C_COMPLETE_TEMPORAL_E2E |
| ACC-002 | Section 80: ACC-002 | 16-testing-acceptance.md | src/enterprise_agent/runtime/; apps/worker/ | tests/failure/test_worker_crash.py | PASS |
| ACC-003 | Section 80: ACC-003 | 16-testing-acceptance.md | src/enterprise_agent/storage/operations.py; src/enterprise_agent/procurement/service.py | tests/failure/test_response_loss.py | PASS |
| ACC-004 | Section 80: ACC-004 | 16-testing-acceptance.md | src/enterprise_agent/storage/attempts.py; src/enterprise_agent/storage/operations.py | tests/integration/test_fencing.py; tests/failure/test_response_loss.py | PASS |
| ACC-005 | Section 80: ACC-005 | 16-testing-acceptance.md | src/enterprise_agent/identity/service.py; src/enterprise_agent/api/app.py; migrations/versions/0001_runtime.py | tests/integration/test_api_identity.py; tests/integration/test_rls.py | PASS |
| ACC-006 | Section 80: ACC-006 | 16-testing-acceptance.md | src/enterprise_agent/governance/policy.py; src/enterprise_agent/capabilities/executor.py | tests/security/test_policy.py; tests/integration/test_capability_executor.py | PASS; DENIED_BEFORE_PROVIDER_INVOCATION |
| ACC-007 | Section 80: ACC-007 | 16-testing-acceptance.md | src/enterprise_agent/storage/approvals.py; src/enterprise_agent/storage/operations.py; src/enterprise_agent/runtime/activities.py; src/enterprise_agent/runtime/workflows.py | tests/security/test_hitl.py; tests/failure/test_response_loss.py; tests/integration/test_hitl_temporal_signal.py; tests/integration/test_plan_temporal_mcp.py | PASS; REAL_PLAN_EXTERNAL_WRITE_ZERO_BEFORE_APPROVAL_EXACTLY_ONE_AFTER_APPROVAL |
| ACC-008 | Section 80: ACC-008 | 16-testing-acceptance.md | src/enterprise_agent/runtime/context.py; src/enterprise_agent/capabilities/executor.py; src/enterprise_agent/governance/policy.py | tests/security/test_prompt_injection.py | PASS; MALICIOUS_PDF_AND_WEB_CANNOT_CREATE_OPERATION_OR_CALL_PROVIDER |
| ACC-009 | Section 80: ACC-009 | 16-testing-acceptance.md | src/enterprise_agent/runtime/skill_planner.py; src/enterprise_agent/storage/tasks.py; src/enterprise_agent/storage/skills.py; skills/supplier-risk/ | tests/integration/test_skill_planner.py; tests/e2e/test_supplier_risk_flagship.py | PASS; ENABLED_DECLARATIVE_SKILL_EXECUTES_WITHOUT_RUNTIME_CORE_BUSINESS_BRANCH |
| ACC-010 | Section 80: ACC-010 | 16-testing-acceptance.md | src/enterprise_agent/mcp/manager.py; src/enterprise_agent/runtime/failure_injector.py | tests/failure/test_deterministic_injection.py | PASS; MCP_DISCONNECT_TRANSITIONS_UNHEALTHY_THEN_RECONNECTS_AND_REDISCOVERS_IDENTICAL_SCHEMA |
| ACC-011 | Section 80: ACC-011 | 16-testing-acceptance.md | src/enterprise_agent/runtime/replanner.py; src/enterprise_agent/runtime/activities.py; src/enterprise_agent/storage/replans.py | tests/integration/test_replans.py; tests/e2e/test_supplier_risk_flagship.py | PASS; SUPPLIER_C_EVIDENCE_GAP_APPENDS_PLAN_V2_AND_COMPLETES |
| ACC-012 | Section 80: ACC-012 | 16-testing-acceptance.md | src/enterprise_agent/storage/provenance.py; src/enterprise_agent/capabilities/builtins.py; src/enterprise_agent/storage/artifacts.py | tests/integration/test_provenance.py; tests/e2e/test_supplier_risk_flagship.py | PASS; CRITICAL_DOCX_CLAIMS_RETAIN_EVIDENCE_IDENTITIES_AND_HASHES |
| ACC-013 | Section 80: ACC-013 | 16-testing-acceptance.md | src/enterprise_agent/storage/plans.py; migrations/versions/0004_immutable_plans.py | tests/integration/test_immutable_plans.py | PASS |
| ACC-014 | Section 80: ACC-014 | 16-testing-acceptance.md | src/enterprise_agent/storage/traces.py; src/enterprise_agent/api/app.py; src/enterprise_agent/observability/ | tests/integration/test_api_trace.py; tests/e2e/test_supplier_risk_flagship.py | PASS; TRACE_EXPOSES_TASK_PLAN_STEP_ATTEMPT_LLM_CAPABILITY_MCP_REPLAN_HITL_ARTIFACT_CHAIN |
| ACC-015 | Section 80: ACC-015 | 16-testing-acceptance.md | src/enterprise_agent/storage/audit.py; src/enterprise_agent/storage/approvals.py; src/enterprise_agent/capabilities/executor.py | tests/integration/test_api_audit.py; tests/integration/test_plan_temporal_mcp.py; tests/e2e/test_supplier_risk_flagship.py | PASS; HIGH_RISK_ADMISSION_POLICY_APPROVAL_OPERATION_RESULT_AND_TRACE_ARE_AUDITABLE |
| ACC-S081 | Section 81 | 16-testing-acceptance.md | — | — | NOT_IMPLEMENTED |
| ACC-S082 | Section 82 | 16-testing-acceptance.md | — | — | NOT_IMPLEMENTED |
| OPS-S063 | Section 63 | 17-deployment-operations.md | src/enterprise_agent/operations/lifecycle.py; src/enterprise_agent/identity/contracts.py; migrations/versions/0030_data_lifecycle.py; docs/adr/ADR-001-workspace-deletion-and-immutable-history.md | tests/integration/test_data_lifecycle.py; tests/integration/test_rls.py; scripts/verify_migrations.py | PARTIAL; RETENTION_POLICY_VERSIONING_AUDIT_ALL_WORKSPACE_TABLE_FORCED_RLS_AND_READY_TO_PURGE_SAFETY_BOUNDARY_VERIFIED; RETENTION_DELETION_AND_WORKSPACE_PURGE_NOT_EXECUTED_BECAUSE_IMMUTABLE_HISTORY_BYPASS_REMAINS_UNAPPROVED |
| OPS-S064 | Section 64 | 17-deployment-operations.md | src/enterprise_agent/operations/backups.py; scripts/backup.py; scripts/restore.py; scripts/verify_backup_restore.py | scripts/verify_backup_restore.py; work/verification/backup-restore.json | PASS; POSTGRES_PROCUREMENT_MINIO_CONFIGURATION_BACKUP_AND_ISOLATED_RESTORE_VERIFIED_WITH_FLAGSHIP_RECORDS |
| OPS-S083 | Section 83 | 17-deployment-operations.md | README.md | manual read | PARTIAL |
| DOM-S073 | Section 73 | 18-requirement-traceability.md | — | — | NOT_IMPLEMENTED |
| DOM-S074 | Section 74 | 18-requirement-traceability.md | — | — | NOT_IMPLEMENTED |
| DOM-S075 | Section 75 | 18-requirement-traceability.md | — | — | NOT_IMPLEMENTED |
| DOM-S076 | Section 76 | 18-requirement-traceability.md | — | — | NOT_IMPLEMENTED |


## 2026-09-17 evidence-driven Replan repair (scoped)

Master section 10 / evidence-insufficient and terminal-failure triggers map to `runtime/activities.py`, `runtime/replanner.py`, `capabilities/builtins.py`, and `storage/replans.py`. Removed no-result and explicit quality-Replan bypasses; persisted revised read queries now execute; changed completed steps are not reused as completed. Observed: 367 unit tests and 32 real-PostgreSQL targeted integration tests passed, plus Ruff and strict mypy. Integration tool/model responses are scripted; production-provider quality, deployment, and full release gates are NOT VERIFIED in this run. See `docs/release/REPLAN_RECOVERY_REPAIR_20260917.md` for precise mappings, limitations and reproduction; `work/verification/replan-unit.xml` and `work/verification/replan-integration.xml` contain measured results. Existing historical no-Replan no-result acceptance is superseded for active execution; safe rendering of already-terminal historical results remains. RELEASE INCOMPLETE.


## 2026-09-17 screenshot answer-quality repair (scoped, subsequent live verification)

This supplements the earlier evidence-driven Replan repair; it does not upgrade any whole-spec/release status. Master section 10 / ACC-011 map to `runtime/activities.py`, `runtime/replanner.py`, `storage/replans.py`, and `capabilities/builtins.py`: failed synthesis with missing evidence re-enters bounded replanning, revised queries execute, affected steps rerun, original permissions and execution snapshots remain frozen. A real selected-model task (`7be5cdbd-d064-5fa2-9746-5ec566909f28`) failed initial evidence validation, performed another search/synthesis, and completed with the requested dated observations. Controlled PostgreSQL tests separately cover failure accounting and frozen-plan semantics.

Context/evidence and answer review (master sections 39-43) map to `runtime/agent_graph.py`, `runtime/reviewer.py`, `runtime/web_provider.py`, `capabilities/builtins.py`, `apps/web_provider/main.py`, and the bounded-chat prompt in `api/app.py`. Repairs include source-language/dated-result ranking, exact official forecast-place discovery, real sports report discovery, opened-page evidence, observed-station historical month selection, shared strict fact normalization, mandatory core-answer quality checks, and attachment coverage/schema correction. Source addresses/identities are discovery inputs only; answers and measurements come from fetched, hash-validated evidence. No answer oracle is used in production.

Measured: **398 unit tests PASS** (`work/verification/screenshot-quality-unit.xml`), **32 targeted real-PostgreSQL integration tests PASS** (`work/verification/screenshot-quality-integration.xml`), scoped Ruff/mypy/diff checks PASS. Rebuilt local API/worker/provider services passed health checks. Seven public screenshot web questions and two ordinary-chat questions have observed real-model passes; public fictional RAG and attachment cases also passed real ingestion and model execution. At this stage original HR RAG/attachment acceptance was **NOT VERIFIED** because external-model permission was pending; the subsequent authorized follow-up below supersedes that limitation. Historical-weather failures during earlier repeated runs are retained and described, not counted as passes. Public checks do not establish a production availability guarantee.

Exact task IDs, results, timing, implementation/test mappings, failures, limitations and reproduction instructions: `docs/release/SCREENSHOT_ANSWER_QUALITY_20260917.md`; raw records: `work/verification/screenshot-quality/`. Actual screenshot-selected model configuration was used, not the different environment-default model. Full master sections 79-82 release gates, process-kill/recovery and backup/restore were not rerun for this repair. **RELEASE INCOMPLETE.** Original-document acceptance is tracked in the subsequent authorized follow-up below.


## 2026-09-17 authorized original-HR acceptance follow-up

The user explicitly authorized the pending original-HR transmission to the existing screenshot-selected model (`deepseek-v4-flash`, api.deepseek.com). Source files `HR-02_leave_policy_v2.pdf` and `TPL-08_employee_leave_application.md` were ingested into isolated live validation workspaces; their hashes match versions already present in the screenshot workspace. This resolves the earlier permission-related acceptance limitation.

Context/evidence requirements (sections 39-43) and final answer validation map to `storage/knowledge.py` (retain document name on summary Evidence locators), `capabilities/builtins.py` (conditional policy options, DOCUMENT_QA usefulness review, preserve readable fully validated repairs and continue filtering unsupported claims). Regression mapping: `tests/integration/test_knowledge.py` checks filename/page provenance; `tests/unit/test_bounded_qa_fast_paths.py` checks concise document QA review; `tests/unit/test_web_task_result.py` checks readable repair preservation and rejection of unsupported added text. Latest full unit result: **401 PASS**, 7.08 s, `work/verification/original-hr-unit.xml`; knowledge integration: **9 PASS**, included in the earlier 407-test combined run, `work/verification/original-hr-tests.xml`. Scoped Ruff/mypy and whitespace checks passed. Local API/worker were rebuilt and passed health checks.

Real original-document acceptance: cinema RAG task `1cf726a0-1662-5b00-ac49-e1ba68fb0478` **PASS**, 13.273 s; attachment summary task `ff779850-6168-57e4-a1b6-259344e2e81a` **PASS**, 13.186 s; unrelated follow-up detached from attachment scope. Answers were manually checked against original pages for conditions, deadlines, citations and readable organization. Raw records: `work/verification/screenshot-quality/original_hr_verified-movie.json` and `original_hr_verified-attachment.json`; executable acceptance: `scripts/validate_original_hr_quality.py`. Earlier failed/insufficient answers remain recorded and are not reported as final passes. No original documents were edited and no leave application was submitted.

Scoped screenshot acceptance, including original HR RAG/attachment, is verified by the measured cases. This does not assert general production reliability or whole-project completion. Full release gates remain unrerun: **RELEASE INCOMPLETE**. Detailed reproduction, mappings and limitations: `docs/release/SCREENSHOT_ANSWER_QUALITY_20260917.md`.


## 2026-09-17 new screenshot failures and source-language follow-up

Sections 39–43 evidence/context and governed recovery map to `capabilities/builtins.py`, `runtime/reviewer.py`, `storage/knowledge.py`, `apps/web_provider/main.py` and `apps/web/src/components/chat-page.tsx`. New cases cover the user’s DOCX, next-week Miami forecast and historical Dongguan weather; source language is unrestricted and final output remains Chinese. Frozen snapshots, permissions, fencing and existing task budgets are preserved.

Measured: 454 backend unit/real-PostgreSQL integration checks PASS (`work/verification/new-screenshot-final-regression.xml`), followed by 414 full unit checks for the final forecast-fetch change; scoped Ruff/mypy PASS. Frontend 22 tests PASS, then 20 chat-page tests with the new 90-second goal assertion and TypeScript checking PASS. Services built and healthy. Authorized DOCX, Dongguan historical weather and Miami next-week supported partial forecast have real-model PASS records, with task IDs, source comparisons and reproduction instructions in `docs/release/NEW_SCREENSHOT_RECOVERY_20260917.md`. Miami explicitly reports only the source-covered dates (September 21–23), not a fabricated seven-day forecast. Earlier failed attempts are retained and not counted as passes. General production reliability and master release gates remain unverified: RELEASE INCOMPLETE.


### 2026-09-17 generation-chain audit and generic recovery follow-up

Implementation: capabilities/builtins.py, runtime/replanner.py, apps/web_provider/main.py. Question and Evidence transmission confirmed in production code and running worker source; added WEB/DOCUMENT HTTP-boundary integration assertions in test_model_handler_fencing.py. Evidence: work/verification/model-request-capture.xml (36 PASS); generic-recovery-unit.xml (423 PASS); scoped Ruff/mypy PASS. Report: docs/release/WEB_GENERATION_CHAIN_AUDIT_20260917.md. Provider responses in transport tests are controlled test fixtures, not live quality acceptance. Working-tree generic recovery changes are NOT deployed; screenshot answer-quality acceptance remains pending. No release-gate or whole-project PASS is implied.


### 2026-09-17 semantic grounding and generic recovery (acceptance pending)

Sections 39–43 / grounded final-answer validation and recovery map to capabilities/builtins.py, runtime/reviewer.py, runtime/replanner.py, runtime/activities.py and apps/web_provider/main.py. ADR-003 preserves integrity, task/source boundaries, permission, snapshot, fencing and existing budgets while replacing literal fact-token matching with an immutable, hash-bound semantic assessment for accepted Claims. Full unit plus scoped real-PostgreSQL integration result: 464 PASS, work/verification/quality-acceptance.xml; later link ranking: 9 PASS. Live screenshot acceptance is NOT complete: final results and retained failures are documented in docs/release/SCREENSHOT_GENERIC_RECOVERY_20260917.md. Existing earlier PASS records do not establish current-build quality. No master release-gate or whole-project completion claim.

Follow-up observed checks: 466 PASS (23.71 s), work/verification/quality-provider.xml; final full-question context/CJK relevance: 62 unit and 4 transport/final-review integration PASS. Provider deadline preserves completed real source records and cancels unfinished reads; source hashes and existing budgets remain enforced. Live source retrieval/answer acceptance remains incomplete, including unstable district and multi-part coverage; report records all failed attempts and pending automatic-approval restrictions.

Existing SearXNG Brave engine enabled after read-only health probes found other backends failing. Final engine_recovery batch: 9/10 mechanical completion checks; not a full semantic-quality PASS. District approximation, Senate coverage, historical observation scope and future-period policy remain limitations. See engine-recovery-index.json and SCREENSHOT_GENERIC_RECOVERY_20260917.md.


### 2026-09-17 authorized partial scope and query scheduling
Internal assessment separates inherent unavailable scope from searchable missing facts; required time/entity scope remains mandatory. Multi-aspect independent queries execute before gap recovery; fallback queries remain lazy. Mapping: capabilities/builtins.py, runtime/agent_graph.py, test_bounded_qa_fast_paths.py, test_research_fallback_loop.py; 431 unit + 37 integration checks PASS. Original HR live cases PASS; Miami useful partial answer manually checked, prior outside-period false pass retained as FAIL. See docs/release/SCREENSHOT_GENERIC_RECOVERY_20260917.md for tasks and limitations. Screenshot acceptance and full release remain incomplete.

Evidence packing follow-up: compound source selection sees bounded candidates before truncation; nav/footer excluded from body while links retained, short field labels preserved. Tests: 470 PASS in authorized-evidence-all.xml, plus 40 affected final prompt checks. Product grounded-task token ceiling aligned to existing 50000 ordinary-task ceiling; budget enforcement unchanged, web live runner aligned. Branches and DOCX observed PASS; historical-date evidence weakness remains under validation. Refer to latest release report for task IDs and preserved failures.

Latest gap-priority state: 434 unit PASS (gap-final-unit.xml), preceding relevant 37 integrations PASS. Contradictory PASS/REVISE with searchable gaps now requires replan, CLARIFY retained. Historical task cb906b00-7123-56a9-912d-c6f157a45839 demonstrates an executed fresh collection but remains FAILED for adjacent-date evidence. Screenshot acceptance remains INCOMPLETE; no project/release completion assertion.


### User-requested incremental answer recovery
Mapping: capabilities/builtins.py draft extraction/restoration/fenced audit/generation context; runtime/agent_graph.py inclusive date prompt; test_bounded_qa_fast_paths.py and test_model_handler_fencing.py. Measured 473 checks PASS, incremental-draft-all.xml. Original HR RAG and summary live regressions PASS; news improved in one actual run. University-list and district-week forecast remain FAIL. See docs/release/INCREMENTAL_ANSWER_RECOVERY_20260917.md. Screenshot and release acceptance remain INCOMPLETE.


### 2026-09-18 complementary queries and bounded gap recovery
Mapping: runtime/agent_graph.py query prompt; capabilities/builtins.py recovery interleaving and coverage context; apps/web_provider/main.py discovery lead fallback; test_bounded_qa_fast_paths.py and test_web_provider.py. 476 selected checks PASS (query-diversity-all.xml), Ruff/mypy PASS. University-list Web probe improved after real replan. Weekly district probe mechanically COMPLETED but semantic acceptance FAIL due to city substitution and unsupported absence claim. Full screenshot/release acceptance remains INCOMPLETE. See docs/release/QUERY_DIVERSITY_RECOVERY_20260918.md for task IDs, reproduction and limitations.


### 2026-09-18 shared research progress and review diagnostics
Mapping: capabilities/builtins.py _load_research_progress, search/generation/review payloads, fenced assessment audit; test_model_handler_fencing.py. 477 selected checks PASS (shared-progress-all.xml); Ruff/mypy PASS. Latest assessment and actual reviewed excerpt fingerprints verified; distinct revisions retained with idempotent replay. Live university-list recovery remains FAILED for missing sources, forecast mechanically completed but semantic acceptance pending. Full retrieval timeline, per-fact gap ledger and full screenshot/release acceptance remain INCOMPLETE. See docs/release/SHARED_RESEARCH_PROGRESS_20260918.md.


### 2026-09-18 exact-query diagnosis (no production changes)
Observed 10 relevant raw search hits for screenshot query; valid first result rejected by lexical entity filter; distinct forecast URLs conflated by numeric identity; 4837-character fetched page reduced to 272-character excerpt despite 5000 budget. Historical evidence-packing replay reproduces district-source exclusion by ISO-date filter. Evidence and limitations: docs/release/WEB_PIPELINE_DIAGNOSIS_20260918.md. Findings are diagnosed defects, not PASS or completed fixes.


### 2026-09-18 end-to-end web quality audit (diagnosis only)
Audited request/planning/provider/fetch/evidence/generation/review/recovery/UI paths. 16 findings classified as reproduced defects, code findings or design limits in docs/release/WEB_END_TO_END_AUDIT_20260918.md. Generic probes reproduce document-identity collisions, list truncation and cache-limit behavior; 153 existing provider/answer tests PASS despite those uncovered cases (audit-existing-tests.xml). No production or required-test changes. Repairs, repeated full screenshot semantic acceptance and release gates remain INCOMPLETE.


### 2026-09-18 boundary audit supplement (read-only production)
Short structured-fact scoring defect reproduced (table=0, welcome prose=90); server rendering replays exactly match two retained answers. Inspected snippet-only Evidence persistence, missing structured-data classification and differing normal/fallback quality paths. Findings and narrowed hypotheses: docs/release/WEB_BOUNDARY_AUDIT_SUPPLEMENT_20260918.md. No repairs or new full-quality PASS claimed.


### 2026-09-18 web evidence loss repair
Implementation: apps/web_provider/main.py; src/enterprise_agent/capabilities/builtins.py. Regression: tests/unit/test_web_provider.py and tests/unit/test_bounded_qa_fast_paths.py. Observed 482 regression passes, then 56 prompt-related passes. See docs/release/WEB_EVIDENCE_LOSS_FIX_20260918.md for live task IDs and limitations. Screenshot acceptance remains INCOMPLETE: final weather probe failed and campus current roster semantics remain unverified. No release-level PASS claimed.


### 2026-09-18 recovery context optimization
Implementation and observed results: docs/release/WEB_RECOVERY_CONTEXT_OPTIMIZATION_20260918.md. 484 regression tests passed; final campus and original DOCX live probes completed with source-supported output. Forecast remains partial and an intermediate campus probe failed. Screenshot stability acceptance remains INCOMPLETE; no release PASS claimed.


### 2026-09-18 quote continuity repair
Reproduced non-verbatim quote introduced by dropping short source lines; fixed without weakening integrity checks. 485 regression passes, 59 final prompt-related passes. Three original historical-weather probes completed; two intermediate answers had negative-event wording flaws, final answer omitted them. See docs/release/WEB_QUOTE_CONTIGUITY_FIX_20260918.md. Overall screenshot stability and release acceptance remain INCOMPLETE.


### 2026-09-18 nonexclusive selection and engine error classification
See docs/release/WEB_NONEXCLUSIVE_SELECTION_20260918.md for mappings, changed legacy shortcut tests, 488 regression passes and deployment. Baiyun packing no longer excludes all target sources. Live Baiyun and campus acceptance FAILED; original DOCX passed. All failures retained. Overall quality/release acceptance remains INCOMPLETE.

### 2026-09-18 candidate reinspection and recovery
Implementation: apps/web_provider/main.py, src/enterprise_agent/runtime/web_provider.py, src/enterprise_agent/capabilities/builtins.py. New coverage: tests/unit/test_web_candidate_recovery.py and tests/integration/test_web_candidate_archive.py. Observed 502 regression passes (19.13s); existing assertions retained. Same-task candidate persistence, separate non-evidence leads, bounded HTTPS refetch, detail-link retention, result-limit-safe cache reuse and context budget enforced. See docs/release/WEB_CANDIDATE_RECOVERY_20260918.md for real task IDs and failures. Overall screenshot stability/release acceptance remains INCOMPLETE.

### 2026-09-18 review materiality
Implementation: src/enterprise_agent/capabilities/builtins.py. Tests: tests/unit/test_review_materiality.py plus existing regression suite, 512 passed in 20.32s. Explicit optional-polish/material-error distinction; contradictory nonblocking judgments retry only review; no automatic acceptance of legacy rejection or unsupported facts. See docs/release/REVIEW_MATERIALITY_20260918.md for actual prior judgments and live outcome. Screenshot stability/release gates remain INCOMPLETE.


### 2026-09-18 generic research scope and recovery
Implementation and measured evidence: docs/release/GENERIC_RESEARCH_SCOPE_20260918.md. Final regression: 540 passed in 25.01s; original HTTPS assertions retained, new cross-domain and breadcrumb-retention tests added. Historical direction, query scope, centralized managed search, bounded prechecks and generic candidate recovery changed; API/worker/provider deployed. Original DOCX passed. Public-search live tasks can complete with incomplete or wrong-scope answers; latest week query still covered only one day. Screenshot quality and release acceptance remain INCOMPLETE.


### 2026-09-18 generic answer coverage and context repair
Implementation, test mappings, raw-source replay and actual live failures: docs/release/ANSWER_COVERAGE_CONTEXT_20260918.md. Final units: 526 passed; selected contracts/DB integration: 42 passed after the final date extractor. Whole-range versus fragment coverage, materiality, bounded discovery checks, continuous table quotes, pooled follow-up candidates and short-date table context improved. Forecast probes completed twice with seven dates; campus and original DOCX passed their described checks. Historical probe exposed requested rows dropped during packing; source replay now retains them. API/worker updated. Screenshot stability and full release acceptance remain INCOMPLETE pending measured full acceptance.

Final live short-date task 88600df9-5503-5316-a8ea-091825ba76c5 failed on answer-scope repair and exhausted allowance; historical screenshot acceptance remains FAILED. Source replay retention and regression PASS do not imply end-to-end quality PASS. Final API/worker health verified; see the linked report.


### 2026-09-18 record context and explicit scope integrity
Implementation, test mappings and observed failures: docs/release/RECORD_CONTEXT_SCOPE_20260918.md. Final unit suite 533 passed; contracts/selected DB integrations 42 passed. Generic HTML table row/cell retention, scoped dated-record packing, truncation manifests and explicit entity/period alignment implemented. Original assertions preserved. Two intermediate web tasks completed with inadequate/wrong-scope answers and are quality FAIL; original DOCX passed. Final real results recorded in the linked report; screenshot stability and full release acceptance remain INCOMPLETE.

Page-first follow-up: engine-opened page links now traversed before external query recovery; 536 final unit passes. Final DOCX passed; district forecast returned seven dates; historical period still failed before this follow-up. Final page-first probe recorded in RECORD_CONTEXT_SCOPE_20260918.md. No full screenshot/release PASS claimed.


### 2026-09-18 cumulative web recovery verification

Implementation/test/observed mappings: [CUMULATIVE_WEB_RECOVERY_20260918](docs/release/CUMULATIVE_WEB_RECOVERY_20260918.md). Existing bounded web-research recovery now includes retained Evidence in coverage, executes gap-query suggestions, retains original alternatives, records actual query history, prevents detail-link candidate starvation, and scores Chinese source titles. Full units 544 PASS; contracts/selected DB integrations 42 PASS. Actual screenshot acceptance remains NOT PASS: historical and forecast probes failed; campus COMPLETED output still insufficient quality. Authorized DOCX regression quality_ok=true. No full-release or PROJECT COMPLETE claim.


### 2026-09-18 generic structured evidence and review recovery

Implementation/test/observed mapping: [STRUCTURED_EVIDENCE_REVIEW_20260918](docs/release/STRUCTURED_EVIDENCE_REVIEW_20260918.md). 556 unit tests and 44 contract/actual-DB integrations passed. Generic list preservation, complete-block context, capacity-aligned answer/review schemas, safe field-level repair feedback, isolated pending-review recovery and partial-search engine diagnostics implemented. Existing factual/scope/fencing checks retained. Real forecast succeeded in intermediate runs; final pool historical/roster/forecast probes failed on incomplete/wrong-scope discovery amid upstream engine faults. Screenshot stability and full release acceptance remain INCOMPLETE.


### 2026-09-18 multi-engine candidate pooling

[Implementation and observed validation](docs/release/MULTI_ENGINE_CANDIDATE_POOL_20260918.md): generic cross-engine/query candidate pooling and provenance, URL deduplication, complementary model review and navigation-flood protection. 560 unit and 44 contract/actual-DB tests passed. Actual discovery records include multiple engines and backend failure diagnostics. Forecast returned seven days; original DOCX passed; historical and roster quality remain NOT PASS. No full-release acceptance claim.


### 2026-09-18 fixed acceptance and generic recovery iteration

[Implementation, measured runs and remaining limitations](docs/release/FIXED_RECOVERY_ACCEPTANCE_20260918.md). Fixed questions were recorded before changes. Generic original-text retention, complete table packing, prose/list competition, actual repair-message evidence replacement, multilingual focus preservation and explicit-Web explanation planning implemented. User authorized Web/MIXED allowance increase to 80,000 tokens/120 seconds; UI and real probe use the same values. 568 units, 45 contract/real-DB integrations and 20 chat-page tests passed. Actual Miami repeat, NASA and HR examples succeeded; historical/roster/forecast reliability and original-DOCX current-run verification remain incomplete as detailed in the report. No screenshot-suite or full-release PASS claimed.


## 2026-09-18 通用流程重新对照

状态 INCOMPLETE。见 [逐项核查与本轮修复](docs/release/GENERIC_PIPELINE_RECONCILIATION_20260918.md)。覆盖原始复核一致性、候选缺口摘要、规划超时恢复；保留尚未实现项与失败实测，不能据此声明截图质量或完整发布通过。

2026-09-18 追加：定向原文重读、导航/面包屑分离、主张编号一致性、候选与已有来源保留已实现，588单元及最终10相关数据库测试通过。真实验收仍INCOMPLETE，r9 RAG虽自动标记通过但人工发现审批边界错误，已记录质量FAIL。详见上述通用流程核查报告。


### 2026-09-18 统一链路续修（INCOMPLETE）

详见 `docs/release/GENERIC_PIPELINE_RECONCILIATION_20260918.md` 的 r11–r13 续轮：局部修答、引文绑定、明确重读、抓取完整性、公开JSON、用户需求边界及预算收尾。607单元、130网页/合同/数据库、22前端测试通过；真实r11/r12仍有失败，不能视为验收PASS。更正此前HR“三天仅部门审批”的不充分判断，必须按原文解释基础规则与附加复核。未修改冻结不变量，未声明PROJECT COMPLETE。


### 2026-09-18 r14/r15 通用研究恢复复核 — INCOMPLETE

新增映射：独立复核矛盾保守恢复、失败局部事实隔离、图标可访问文本提取、搜索后端错误诊断、跨语言完整对象别名、复核与历史缺口隔离。详见 docs/release/GENERIC_PIPELINE_RECONCILIATION_20260918.md 的 r14/r15 节。618 单元、25 数据库/API/合同通过；后续独立复核变更58项相关回归通过。真实七题中4题任务失败，NASA与HR虽完成仍有人工事实质量失败，不能标PASS。外部引擎限流/CAPTCHA、动态页面、条件阈值及证据遗漏尚未解决。未完成全发布门禁，未宣告项目完成。


r15真实补充：Miami、黄埔一周、大学城三题任务均COMPLETED，但Miami日历矛盾、大学城仅首批名单仍属质量缺口；周预报单次改善尚无重复稳定验收。详细task_id与结果见同一报告r15节。总状态保持INCOMPLETE。

### 2026-09-29 Grafana local startup recovery (scoped verification only)

- Observed cause: Windows process `clash-core-service` (PID 7676) listened on `127.0.0.1:53000`, preventing the stopped Grafana container from binding its configured port. The previous exit was code 0 and was not recorded as OOM-killed.
- Implementation: `deploy/compose/infrastructure.yml` accepts `GRAFANA_HOST_PORT` (default 53000) and uses the already declared `grafana_data` volume; local `.env` selects 53001. `start.cmd` reads the actual published address. `.env.example` and `docs/STARTUP_GUIDE.md` document the override.
- Data safety: old container data was archived before recreation; the restored `grafana.db` SHA-256 matched the source before startup. The proxy process was left running on 53000.
- Observed checks: Compose configuration validation passed; Grafana on 127.0.0.1:53001 returned HTTP 200 from `/api/health` with `database=ok`; the startup address command printed the correct address; `py -3.12 -m pytest tests/unit/test_compose_embedding.py -q` passed all 3 tests. These existing tests cover shared Compose configuration, not all Grafana behavior.
- Local evidence and recovery archives: `work/verification/grafana-startup-20260929T101740Z/manifest.json`, `data.tar`, `restore.tar`, and original configuration backups. This ignored directory contains local data and secrets; do not publish it.
- Scope: only Grafana was recreated and started. API, worker, web, migrations, Temporal, full release gates, and question-set quality were not validated by this repair. Overall project acceptance remains INCOMPLETE.

### 2026-09-29 measured retrieval, recovery and real-model benchmark (scoped evidence)

Protocol and measured results: docs/release/RESUME_BENCHMARK_PROTOCOL_20260929.md and docs/release/RESUME_BENCHMARK_REPORT_20260929.md. No production logic or existing assertion changed. All failures remain in raw samples and denominators.

| Requirement / scope | Implementation and executable check | Observed result / limitation |
|---|---|---|
| Section 42, retrieval component | scripts/run_resume_benchmark.py; runtime/retrieval.py and storage/knowledge.py; real CPU BGE-M3 and PostgreSQL | Fixed 100-document synthetic corpus, 60 labeled queries: vector Top-1 58/60, FTS+vector+RRF 60/60; both Recall@5 60/60. No reranker or full RAG acceptance claim. |
| Section 79, component performance evidence | Same script, retained load.jsonl | 360 requests across concurrency 1/4/8, no request errors; at concurrency 8, P95 671.5 ms and 16.14 successful requests/s. Each level lasts only 7–14 seconds; not sustained capacity or Agent response latency. |
| INV-001/002/003/012, durable recovery subset | scripts/benchmark_resume_recovery.py; runtime/workflows.py; real Temporal, PostgreSQL and benchmark-owned worker processes | Five actual kills/restarts, five completed two-step workflows, no duplicate authoritative commands; mean recovery 14.039 s. Not the full flagship workflow or all crash positions. |
| INV-003/004/005, unknown-result reconciliation subset | Same script; storage/operations.py and procurement/service.py; separate real procurement database | 20/20 committed-write/lost-result trials reconcile before retry, exactly one business row per operation after replay. Direct production-function calls; not network-level MCP packet loss. |
| INV-011 / Section 42, real-model answer delivery | scripts/benchmark_resume_agent.py; actual auth/route/task/result/evidence/usage APIs; configured Qwen model | Live answer delivery failed; all planned outcomes and retained source/attempt records are in the linked report. Retrieval hits are not answer quality PASS. No matrix status promotion. |
| Sections 79/82, measured-result integrity | scripts/resume_benchmark_metrics.py, tests/unit/test_resume_benchmark_metrics.py, scripts/summarize_resume_benchmark.py | Four new metric/data regressions plus two existing retrieval regressions passed (6 total). Raw samples, timing definitions, hashes, setup failures and reproduction instructions retained. |

Evidence: work/verification/resume-benchmark/20260929-retrieval-v2/, 20260929-recovery-v1/, 20260929-agent-v1/ and harness-tests.xml. Section 60 ablations, full security/fencing gates, backup/restore, flagship acceptance and complete Sections 79–82 release gates were not rerun or promoted. Overall project acceptance remains INCOMPLETE.

### 2026-09-29 Temporal namespace bootstrap recovery (startup verified)

- Observed failure: `temporal-namespace` exited 1 after attempting to register an existing `default` namespace, leaving Worker unstarted. The old script suppressed the preceding describe error, so its exact cause cannot be established from those logs; the Temporal healthcheck checks only TCP reachability.
- Implementation: `deploy/compose/temporal-namespace.sh`, mounted by `deploy/compose/infrastructure.yml`, bounds CLI calls and retries, reconciles after registration errors including AlreadyExists, and requires a successful describe before exit 0. Initialization failure continues to block Worker. No namespace or database was deleted, and no runtime consistency checks were relaxed. `.gitattributes` preserves LF for the new shell files.
- Regression coverage: `tests/integration/test_temporal_namespace_bootstrap.py` executes the production shell in the pinned admin-tools image with a network-isolated CLI fake defined only in `tests/fakes/temporal_namespace.sh`. Seven scenarios cover existing, newly created, concurrent registration, response loss, transient recovery, persistent failure, and unconfirmed registration. These seven plus three Compose and two Worker queue tests passed: 12 passed in 7.01s. Targeted Ruff check and formatting passed.
- Live observations: repeated initialization against the real existing namespace exited 0; an intentionally unreachable endpoint exited 1 without reporting success. Full `docker compose --env-file .env up -d --no-build --wait --wait-timeout 120` exited 0. API, Web and Grafana returned HTTP 200; Temporal and Worker were healthy, with workflow and activity pollers observed on `enterprise-agent-runtime`.
- Local evidence: `work/verification/temporal-namespace-startup-20260929/report.json` and its live-check logs.
- Outstanding finding: Worker logs repeatedly report `CONSISTENCY_VIOLATION: outbox event identity mismatch` for existing tasks. This task did not modify those records or weaken `src/enterprise_agent/storage/outbox.py` checks. Task recovery and question-set quality remain unverified; successful startup is not full release acceptance. Overall project acceptance remains INCOMPLETE.
