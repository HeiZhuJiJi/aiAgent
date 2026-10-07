# Release Validation Report — PROVIDER GATE PASS / RELEASE INCOMPLETE

Date: 2026-09-22

## Decision

**The production-provider closure gate passed, but repository-wide Definition of Done is not yet reached.** No Capability, MCP, Skill, Scheduler, Provider or architecture module was added. The existing OpenAI-compatible Qwen service completed a real two-step Agent Task through PostgreSQL, Temporal, Redis, the Procurement MCP subprocess and the governed model Capability. Remaining release blockers are the open Definition-of-Done rows recorded below and in `SPEC_COMPLIANCE_MATRIX.md`.
Current endpoint note (2026-09-12): the replacement Cloudflare tunnel and
corrected key completed a real Qwen web-research Agent Task. This supersedes the
temporary 2026-09-11 HTTP 401 observation; the failed observation remains part
of the historical troubleshooting record.

## 2026-09-22 Completion and final local gate checkpoint

The rebuilt API and Worker were used for the current Completion observation.
The full backend verification entry point completed with **866 passed**, 0
failed, 0 skipped and 0 xfailed. Ruff passed; strict mypy passed over **137
source files**; and the Web check passed lint, strict TypeScript, **22 tests**
and the production build. The current Playwright acceptance passed 1/1 after
its assertion was aligned with the existing 管理 Skills governance menu.

| Question-set case | Completion | Delivery | Quality result |
|---|---|---|---|
| district_today, miami_tomorrow, district_week, history_week, public_science | COMPLETE | FULL_ANSWER | PASS |
| city_today | UNAVAILABLE | none | not a quality pass |
| campus | UNAVAILABLE | none | not a quality pass |

The machine-readable report is
work/verification/api-rebuilt-20260922-all-question-set.json. The two
negative cases are deliberate fail-closed observations: the runtime did not
turn incomplete or insufficiently supported evidence into a full answer.
Completion focused tests also passed 61 unit, 16 integration and related
outbox/flagship coverage. This checkpoint does not close the frozen release
gates, and **RELEASE INCOMPLETE** remains the decision.

## 2026-09-17 user-question quality checkpoint

This is scoped live acceptance, not a release decision. With the user's
authorization, the configured model processed three isolated HR files in a
test workspace. Real answers passed for three-day leave, general leave,
the exact screenshot prompt "请假流程" (Task
`a26ec95b-627c-5811-b0c7-cb46d1c2994c`, 45.687 seconds), onboarding, and a
selected four-page leave-policy summary. The summary was
repeated and the next unrelated question routed to WEB without inheriting
the attachment. A live two-city next-day forecast returned distinct dated
Dongguan and Guangzhou facts from opened NMC pages; a live three-part
Japan/Australia leadership question returned all roles from official sources.
The ordinary DIRECT greeting answered naturally with a 0.027-second first
token. A separate real "中午好，现在几点？" chat probe used the application
clock fast path (DIRECT) and produced a timestamp within two minutes of the
configured Asia/Shanghai clock, with a 0.044-second first token and
0.054-second total response. It did not require web search. Exact Task IDs,
timings, failed intermediate attempts and code/test
mappings are recorded in `SPEC_COMPLIANCE_MATRIX.md`. Final regression: 364
unit tests, 22 frontend Vitest tests, and focused real-PostgreSQL procedural
retrieval passed.
Complex Web/RAG answers still took roughly 39–57 seconds; wider provider
reliability, browser-level attachment/focus behavior and the repository-wide
Definition of Done remain unverified. **RELEASE INCOMPLETE** is unchanged.

## 2026-09-15 repair checkpoint

This checkpoint does not change the repository-wide **RELEASE INCOMPLETE**
decision. It closes the reported conversation-quality, model-fallback and
knowledge-ingestion defects with the following observed evidence:

- the full backend suite completed **424 passed**, 0 failed, 0 skipped and
  0 xfailed; `work/verification/pytest.xml` SHA-256 is
  `70c3959a461afd1dc5a218ceb733ab3ad09e82f96ce2d539a68c63962c18b9c8`;
- Ruff, strict mypy over 116 source files, frontend lint/typecheck/**17 tests**/
  production build, generated contracts and fresh migrations through
  `0034_quarantine_legacy_context` passed;
- rebuilt Compose API, Web, Worker, PostgreSQL, Temporal, MinIO, SearXNG and
  pinned BGE-M3 embedding services are healthy; API and Web return HTTP 200 and
  the new application logs have no ERROR/CRITICAL/Traceback/Unhandled match;
- exact ingestion `59a5a700-12f6-55aa-9f7c-7282076399ad` is `SUCCEEDED`
  at version 14 with document version
  `d78a77a0-b844-5ebe-a4dc-a4f7fa139526`, four chunks and 1024 dimensions;
  a live Worker `BAAI/bge-m3` query returned four scoped matches and ranked the
  relevant appeal-policy chunk first;
- authoritative Turn ownership/context projection, atomic research-Turn
  creation, late-result isolation and stale direct-Turn reconciliation are
  verified; all 144 legacy-backfilled Turns remain context-ineligible, and one
  live stale direct Turn was reconciled from RUNNING to
  COMPLETED/UNAVAILABLE after its 900-second boundary;
- explicit primary/backup fallback, terminal audit, malformed-response usage
  preservation, post-call factual accounting and zombie fencing are verified;
  durable built-ins and Replan use the immutable model snapshot;
- a deployed live failover drill forced both an `LLM_SERVER_ERROR` and an
  exhausted three-reply `LLM_SCHEMA_ERROR` on the primary path. Both emitted
  `STARTED -> USED`, selected the configured real `Qwen/Qwen3.8-27B` backup,
  and returned a non-empty or schema-valid response respectively. The
  sanitized evidence is `work/verification/model-fallback-live.json`, SHA-256
  `d639093b45ce26915f8356d46fc3877d9fdbdb42b5f5d0144b7f2edf982863b0`;
- post-change delta validation completed **266 unit tests**, **34 targeted
  PostgreSQL integration tests** and **19 frontend tests**, plus relevant Ruff,
  mypy, frontend lint/typecheck and production build checks.

### Semantic research fallback follow-up

The term *fallback* now names two deliberately separate mechanisms. The
primary/backup provider failover described below handles eligible transport or
structured-output failures. It is not the user-facing research fallback. The
research fallback is the bounded semantic planner expressed by
`BoundedResearchPlan`, `SourcePlan` and `AnswerContract`.

- For AUTO-routed Turns with a configured CHAT model, complex WEB/RAG/MIXED
  requests (including news roundups, forecasts, comparisons and
  selected-document summaries) attempt an up-front typed model plan for
  search/retrieval queries, source-class priorities, Evidence minima, freshness
  and answer organization. The model does not answer at this stage; an eligible
  planner failure safely retains the deterministic plan.
- Narrow FACT and selected-document DOCUMENT_QA requests start on the
  deterministic fast path. If the first Evidence pass is empty or weak, WEB
  and RAG may each invoke at most one evidence-aware model recovery plan. WEB
  can consume that plan in its only remaining search round and RAG in one
  recovery pass. Novel-query checks prevent repeating the same search and there
  is no third plan/retrieve loop.
- The application remains authoritative for the execution route, enabled
  Capabilities, policy, budget, permission and immutable selected-document
  boundary. Planner output cannot grant permission, introduce a write, switch
  documents or use parametric model knowledge as current Evidence.
- DOCUMENT_SUMMARY no longer treats “summarize this attachment” as a similarity
  query. It reads ordered chunks from the selected immutable document version;
  small documents use complete coverage and larger documents use bounded
  beginning-to-end sampling; the generation prompt explicitly requires the
  result to be labelled representative.
- Empty Web or RAG results remain an Evidence gap. Provider/result envelopes
  are no longer promoted into confidence-1 WEB or DOCUMENT Evidence.

This follow-up delta passed **294 unit tests**, **3 contract tests**, **15
focused real-dependency PostgreSQL integration tests** and **30 security
tests**. Relevant Ruff and strict mypy checks passed. The current API, Worker
and Web images were rebuilt from this source; all three containers are healthy,
API `/health` and Web returned HTTP 200, and recent API/Worker/Web-provider logs
had no ERROR/CRITICAL/Traceback/Unhandled match. These delta results do not
replace the historical 424-test repository checkpoint and do not constitute a
new repository-wide release-complete claim or a production-quality benchmark
of every external search/model combination.

Production reranker validation remains open. The bounded deterministic RRF
fallback is test-verified. The backup state machine now also has deployed
real-provider proof for both availability and structured-schema exhaustion;
this does not claim that every configured provider pair or outage mode has been
production-certified.
The shared development database also contains accumulated historical/test
state: 7,093 nonterminal Task rows and, at the observation time, 84 failed,
9 pending and 21 running ingestion jobs. No destructive cleanup was performed.
Dedicated verification-database isolation and an approved retention cleanup
remain operational follow-up.

## Gate results

| Gate | Result | Auditable evidence |
|---|---|---|
| Production OpenAI-compatible Provider | **PASS** | Authenticated `GET /v1/models` returned HTTP 200 from both API and Worker environments with `Qwen/Qwen3.8-27B`; `work/verification/model-live.json` is PASS, SHA-256 `7fb371548b4c07a56dda66bc7d93302a30ab6d45d1c122c84e320a6260fd055e` |
| Real primary-to-backup model failover | **PASS WITH CONFIGURATION LIMIT** | Deployed gateway drill forced primary `LLM_SERVER_ERROR` and exhausted `LLM_SCHEMA_ERROR`; both reached `STARTED -> USED` on configured real `Qwen/Qwen3.8-27B`, with schema validation and per-reply usage preserved. `work/verification/model-fallback-live.json`, SHA-256 `d639093b45ce26915f8356d46fc3877d9fdbdb42b5f5d0144b7f2edf982863b0`. The current backup endpoint is a temporary tunnel and must be replaced before production SLA claims |
| Full Agent Task on production model | **PASS** | Task `0505362d-6cb2-4a03-94fb-abbb2d30dbab`, Plan `3b9f7229-8509-42bd-a6d6-01bc60779bb9`: MCP Evidence step and `model.structured_output` step both SUCCEEDED; canonical Task state COMPLETED |
| Real web-research Agent Task | **PASS WITH EXTERNAL-SLA LIMIT** | Cold-cache Task `8708638c-5cf9-4ff3-8484-0a363bb12e53`: 3/3 Steps SUCCEEDED through SearXNG, `web.search`, Qwen synthesis and review; 27.502 s end to end, 3,779 tokens, 5 Evidence records and 19 Trace nodes |
| Production Token / Latency / Error / Retry / Trace linkage | **PASS WITH RECORDED LIMITS** | 2,298 total tokens; 48.277 s create-to-complete; two Capability calls; zero final-task errors, Step retries, Replans and HITL; Trace, Capability and Usage identifiers are linked below. Provider billing price is not configured, so recorded cost is 0 rather than a claimed commercial charge |
| Golden Evaluation quality closure | **PASS FOR REQUESTED P0 METRICS / SECTION 59 PARTIAL** | Final run `b05d324f-8413-4dc7-8c11-448ba0dac7c7`: 72/72 bindings, Evidence coverage 9/9, Claim provenance 9/9, Tool executions 41/41, Recovery 6/6, plus record-backed retry/Replan/HITL/latency/token statistics; five frozen Section 59 dataset dimensions remain unmeasured |
| Six frozen ablations | **PASS — CONTROLLED COMPONENT SCOPE** | `scripts/run_ablations.py`; `work/verification/evaluation/ablation-report.json`; 6/6 `COMPLETED` |
| Backend regression | **PASS** | Current work/verification/pytest.xml: 866 passed, 0 failed, 0 skipped, 0 xfailed |
| Static and frontend quality | **PASS** | Full Ruff; strict mypy over 137 source files; frontend lint, strict TypeScript, 22 Vitest tests and Vite production build |
| Runtime service recovery after verification | **PASS** | Rebuilt API, Web, Worker, PostgreSQL, Procurement DB, Temporal, MinIO, SearXNG and pinned BGE-M3 service reported running/healthy; API/Web HTTP 200 |
| Documentation synchronization | **PASS** | `README.md`, `SPEC_COMPLIANCE_MATRIX.md`, `FINAL_COMPLIANCE_REPORT.md`, this report |

The synchronized requirement matrix contains 116 section/INV/ACC bundles: **53 PASS, 38 PARTIAL and 25 NOT_IMPLEMENTED**. These counts are evidence statuses, not a percentage-complete release claim.

## Release Evidence Package

- `docs/release/PRODUCTION_AGENT_TASK_REPORT.md` records the database-backed Production Task goal, Plan, Steps, Capability/MCP calls, Usage, latency, retry state, Trace, Evidence and Claims.
- `docs/release/FAILURE_RECOVERY_CASE_REPORT.md` records Worker process crash recovery, `UNKNOWN → Reconcile` exactly-once protection, and real Redis Admission Control/basic Workspace fairness.
- `work/verification/evaluation/report.json` is the machine-readable Golden Evaluation report; SHA-256 `e06161cefd75842c6e0c889a7644699ae4cb10a4ac261a39ff14088dcbad4d7c`.
- `work/verification/evaluation/junit.xml` is the final 63-node Evaluation JUnit; SHA-256 `b0ff64922869e151e650d3d66b261aeaa769a0d749ff1492da17c8f5488fdc1e`.
- `work/verification/admission-control-junit.xml` contains the separate 3/3 Admission Control verification; SHA-256 `577e03b52f617fa9b02a180d9fe66a39fffdd980d63f5e8cf98bd5d0874f5fa2`.
- `work/verification/model-fallback-live.json` records the sanitized deployed
  availability and structured-output failover drill; SHA-256
  `d639093b45ce26915f8356d46fc3877d9fdbdb42b5f5d0144b7f2edf982863b0`.

## Production Agent Task evidence

The user explicitly authorized egress of Beacon Industrial Trading Ltd. Supplier Profile Evidence for this run. The key remained masked and was not written to this report. No mock, fake result or test-only model endpoint participated.

| Metric | Observed value |
|---|---|
| Task / Plan | `0505362d-6cb2-4a03-94fb-abbb2d30dbab` / `3b9f7229-8509-42bd-a6d6-01bc60779bb9` |
| Provider model | `Qwen/Qwen3.8-27B`, OpenAI-compatible external endpoint |
| Canonical result | Task COMPLETED; 2/2 Steps SUCCEEDED; 1 Attempt per Step |
| End-to-end latency | 48.277 s from Task creation (`03:51:40.018868Z`) to `task.completed` (`03:52:28.296271Z`) |
| MCP latency | Capability 1.137 s; Step Attempt 1.199 s |
| Model latency | Capability 45.961 s; Step Attempt 46.139 s |
| Token / cost | 2,298 total tokens persisted; cost 0 because no provider price schedule is configured |
| Calls / repairs / retries | 2 Capability calls; 1 LLM call; 0 schema repairs; 0 final-task Step retries; 0 final-task errors |
| Evidence / Claims | 2 Evidence records: one MCP source and one derived model result; 2 Claims, both with Evidence citations |
| Task Trace | `task-0505362d-6cb2-4a03-94fb-abbb2d30dbab` |
| MCP Capability Trace | `capability:4af7be39-8a2a-5b50-8e3b-14386ddf339a` |
| Model Capability Trace | `capability:b8d8db3d-aa46-5ca0-bb94-e7be77d0b771` |
| Model Usage Trace | `builtin:b8d8db3d-aa46-5ca0-bb94-e7be77d0b771` |

Token usage is currently persisted as a total on the Task budget/Usage record; the real Agent Task does not persist separate input/output token columns. The separate real-provider probe recorded 157 input and 73 output tokens. HTTP transport retry attempts are not stored as a separate counter, so this report does not infer one from the successful response; the auditable Runtime-level figures are one model Step Attempt and one LLM call reservation.

## Real web-research validation

The built-in declarative `web-research` Skill uses the existing governed
Capability, model and Evidence path; it does not introduce a new Runtime,
Provider, Tool or MCP module. Only ordinary public search text was sent to the
approved upstreams. No attachment, Workspace Evidence or private document was
sent to a search engine.

| Metric | Observed value |
|---|---|
| Task | `8708638c-5cf9-4ff3-8484-0a363bb12e53` |
| Canonical result | COMPLETED; 3/3 Steps SUCCEEDED; 0 Step retries |
| End-to-end latency | 27.502 s |
| Step latency | Search 4.583 s; Qwen synthesis 21.502 s; review 0.065 s |
| Usage / provenance | 3,779 persisted tokens; 5 Evidence records; 19 Trace nodes |
| Model | `Qwen/Qwen3.8-27B` through the production OpenAI-compatible configuration |

Four separate cold search checks also returned relevant results for a Guangdong
annual-rainfall comparison, China new-energy-vehicle exports, recent OpenAI
product announcements and Guangzhou AI policy in 3.79 s, 1.15 s, 6.33 s and
2.68 s respectively. DuckDuckGo returned real results in isolated engine
checks. Baidu returned CAPTCHA for both isolated checks and was therefore
removed from the active engine set rather than counted as working. Bing and
360search remain enabled, but all anonymous public engines can be rate-limited;
this PASS proves the current real path, not a commercial upstream SLA.

## Production failure and recovery evidence

The first real tasks exposed two small deployment/runtime defects; neither was hidden or replaced with a fake result.

- Tasks `45175d44-529c-4da8-8429-72fe7dbf4abc` and `c51f9588-97df-4ba0-9278-6315c7b95cf5` failed the MCP Attempt with `CAPABILITY_UNAVAILABLE` because the stdio child received a minimal environment and the container had only a `PYTHONPATH` source import. `deploy/docker/backend.Dockerfile` now installs the existing package into standard site-packages; a cleared-environment import and the final MCP call passed.
- Tasks `6f9eb59a-8383-4381-bfe4-ea88de76987a` and `dbdb4116-c6b6-4b73-a107-eb39480b2f8e` reached Qwen but their workflow histories failed at the configured maximum fifth Activity attempt because a 45–46 second model generation exceeded the 15-second heartbeat interval. `execute_governed_plan_step` now emits a five-second heartbeat while the existing Capability runs. The final workflow had zero Activity timeouts and completed.
- The four pre-fix validation Task rows remain preserved as failure evidence; two failed workflow histories left canonical Attempts in RUNNING and two MCP failures remain in RETRY_WAIT. This stale-validation cleanup behavior is a real residual limitation and is not counted as a successful recovery or silently rewritten.

## Ablation results

Execution mode: `CONTROLLED_COMPONENT_ABLATION`. All pairs call existing production component functions with deterministic synthetic inputs. No new Runtime feature-flag system or alternate architecture was introduced.

| Pair | Treatment result | Control result | Observed comparison |
|---|---|---|---|
| Planner / No Planner | success 1.0; valid plan 1.0 | success 0.0; valid plan 0.0 | Planner produced a valid frozen-contract plan |
| Replan / No Replan | success 1.0; replan success 1.0 | both 0.0 | Existing trigger selection and decision validation succeeded |
| Context Manager / Full Context | success 1.0; 6 tokens | success 0.0; 13 tokens | Managed context stayed within the six-token budget |
| Conditional Reviewer / No Reviewer | grounding/citation 1.0/1.0 | 0.5/0.5 | Reviewer rejected the ungrounded fixture |
| Dynamic Retrieval / All Capabilities | selection accuracy 1.0 | selection accuracy 0.0 | Existing resolver selected the one declared dependency |
| Single / Parallel Research | both success 1.0 | both success 1.0 | Identical deterministic CPU-bound work was measured; the recorded run observed 26.38 ms vs 18.51 ms |

Important limitations:

- No Planner and No Replan controls intentionally produce no output and are scored unsuccessful.
- These are component-level comparisons, not complete Agent Tasks and not production-provider benchmarks.
- The research pair compares identical CPU-bound work; it does not claim production network/model speedup.

Evidence integrity:

- Ablation JSON SHA-256: `17e1f86f8265a3029c456efab0e24d95d3de437896c89f3c717d7cfd78866096`
- Ablation Markdown SHA-256: `7e26591eb2209716c757932c347b1db9e4be21a76b0420a9d2d49cf4b0157ec4`

## Remaining release blockers

1. Complete the five still-unmeasured Golden dataset dimensions—capability selection, citation correctness, claim grounding, duplicate side effects and policy violations—and the other open Definition-of-Done rows recorded in `SPEC_COMPLIANCE_MATRIX.md`.
2. Resolve or formally accept the stale canonical validation rows left by exhausted Temporal Activity retries; do not rewrite them as successful history.
3. Deploy and validate production least-privilege service roles; BGE and Reranker production endpoints remain unverified. The real Compose web-search path is validated, but its anonymous public upstreams do not provide a commercial availability SLA.
4. Keep irreversible Workspace deletion and retention purge behind the existing immutable-data and ADR-001 approval boundary.

Until the remaining Definition-of-Done rows have direct evidence, **PROJECT COMPLETE must not be claimed**.
