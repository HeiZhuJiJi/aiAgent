# Generic source structure, review recovery and search health — 2026-09-18

Status: implementation and automated regressions PASS; screenshot quality/stability acceptance INCOMPLETE. No PROJECT COMPLETE claim.

## Verified causes and implementation

1. Source extraction flattened non-table lists and sibling numeric spans. `apps/web_provider/main.py` now retains nested ordered/unordered list boundaries, empty items and safe sibling numeric separators; table behavior remains. `runtime/evidence_records.py` packs complete adjacent list blocks within the existing budget, preserving source order without inferring column pairings. `capabilities/builtins.py` reuses spare excerpt capacity for the retained original. An excerpt manifest distinguishes omitted context from absent source data. This does not reconstruct arbitrary div grids or prove relationships from list order alone.
2. Review replies failed Pydantic validation with `coverage.0.evidence_ids too_long max_length=4`, while answer generation allowed five. New field-specific safe schema feedback in `models/gateway.py` exposed the defect. `runtime/answer_coverage.py` now owns MAX_ANSWER_EVIDENCE=16, matching the existing bounded candidate context pool; answer, semantic selection, coverage and generation instructions share it. This also accommodates cumulative recovery and document source sets. Selection asks for the smallest sufficient complementary subset; the original preferred result count no longer discards distinct required records. The total task token/time budget is unchanged. Wrong entity, partial period, unsupported facts, actual source references and fencing remain checked.
3. An unavailable reviewer discarded a generated answer and forced regeneration. An UNREVIEWED candidate is now persisted, bound to the exact task/workspace, question/contract hash, complete evidence IDs/hashes and verified source content. A matching recovery skips selection and regeneration but still needs fresh semantic acceptance. UNREVIEWED is never a supported draft or a persisted factual Claim. Generation leaves review time. Repeated audit-only source IDs/hashes and verbose manifest instructions are omitted from model transport; original evidence and audit payload remain intact.
4. Real SearXNG logs show Google CAPTCHA with 3600-second suspension, Brave rate limits with 180-second suspension, and Sogou adapter AttributeError. Later searches returned materially different, broad pages. This is an observed infrastructure/provider problem in addition to application logic, not proof that all bad queries have one cause. Provider now reports bounded per-request backend_diagnostics even alongside nonempty results. Runtime retains these in web.search output, under the output schema. Partial evidence remains usable and degraded responses are not stored as healthy search cache entries. Diagnostics are not factual Evidence or permissions. Existing SearXNG configuration was not changed, and CAPTCHA was not bypassed.

## Validation and preservation

- Full unit suite: **556 passed in 10.25s**, `work/verification/structural-unit.xml`.
- Contracts and selected actual-DB integrations: **44 passed in 18.19s**, `work/verification/structural-db.xml`. Includes model fencing, task/workspace isolation, candidate archive, replans, terminal no-result behavior, usage accounting and budgets.
- Ruff passed changed production/test files; mypy passed six changed production files.
- Tests added in `tests/unit/test_evidence_records.py`: parallel/nested/empty/malformed lists, adjacent inline numbers, unchanged inline formatting, bounded complete block packing, omitted-context manifests, unused-context reread.
- `tests/unit/test_model_gateway.py`: precise schema repair constraints without raw sensitive response values or unknown keys.
- `tests/unit/test_answer_coverage.py`: all allowed source references fit selection/answer/coverage/schema prompt; broader entities and partial periods still rejected; transport does not mutate audit evidence.
- `tests/unit/test_web_provider.py`: partial results retained while engine faults remain visible and are not cached healthy.
- `tests/integration/test_model_handler_fencing.py`: UNREVIEWED isolation, changed evidence rejection, no premature Claims, recovery executes exactly one fresh semantic model call before success.
- No required assertions were deleted or weakened. No new production dependency, per-city lookup, website URL rewrite, or question-specific answer was added by this iteration. Preexisting topic-specific paths remain elsewhere; this is not an assertion that the whole repository has been purged of them.

## Real end-to-end observations

Actual API, Temporal worker, web provider and configured DeepSeek; original public questions unchanged. Artifacts are retained under `work/verification`.

| Artifact | Result | Interpretation |
| --- | --- | --- |
| structural-huangpu.json, structural-baiyun.json, structural-campus.json | FAILED | Early schema failure/budget exhaustion, not hidden. |
| structural-schema-huangpu.json | COMPLETED 23.382s | Seven dated district forecast entries, attributed differences. |
| structural-schema-huangpu-repeat.json | COMPLETED 18.266s | Seven dated entries. 2345 huangpu-72027 evidence was inspected: its retained text references Guangzhou Huangpu; do not infer Shanghai solely from a similar name or numeric URL. |
| structural-schema-baiyun.json | FAILED 26.429s | Five dates supported; missing two; recovery budget exhausted. |
| structural-schema-campus.json | COMPLETED 21.334s | Quality NOT PASS: roster boundaries/campuses mixed despite task completion. |
| structural-final-huangpu.json | COMPLETED 16.883s | Seven dates with attributed numeric/source differences after first capacity fix; this is not a final-version stability guarantee. |
| structural-final-baiyun.json | FAILED 30.548s | Missing two historical dates; supplement executed but review budget exhausted. |
| structural-pool-baiyun.json | FAILED 33.431s | Predominantly forecast/out-of-period pages, no supported historical week. |
| structural-pool-campus.json | FAILED 24.377s | Homepages and individual-school evidence, incomplete roster. |
| structural-pool-huangpu.json | FAILED 31.412s | City-level and wrong-location pages, rejected on requested scope. |

The pool probes preceded the final schema-prompt synchronization and backend-diagnostic transport. Later missing evidence coincides with logged engine failures; no assertion that engine failures explain every selection defect. Log excerpts: `structural-search-backend.log`. Search outputs for failed forecast: `pool-search-0.json` and other pool-search files. Unreviewed/completed status is not a quality acceptance oracle.

## Operational events and remaining work

- During an intermediate DB run, PostgreSQL/Redis/MinIO/Temporal were stopped (Exited 255); 38 tests failed with connection refusal, five passed. Existing containers were restored without removing data volumes; subsequent final 44-test run passed. The cause of the infrastructure stop was not established.
- One automatic approval check timed out before executing a test command; the permitted single retry ran successfully. No approval remains pending.
- API, worker and web-provider rebuilt and updated. SearXNG was not restarted to circumvent upstream suspension.
- Remaining: healthy reliable upstream discovery; more selective child-entity/detail traversal and full roster boundaries; budget-efficient recovery under repeated genuine missing-evidence outcomes. Backend diagnostics currently reach task traces; they do not yet provide a dedicated end-user degradation message or guarantee automatic healthy-provider failover. External API integration cannot be claimed without a configured actual provider.
- Div-based layouts and ambiguous source labels remain limitations. Historical week and roster screenshot acceptance remain NOT PASS. Ordinary/RAG/DOCX preservation must be measured separately, not inferred from forecast success.
- Full release gates (worker kill/recovery, response-loss reconciliation, backup/restore) were not executed this iteration.

Reproduction: use `scripts/validate_web_research.py` with selected_settings from `scripts/validate_screenshot_quality.py` and the original questions; retain all outputs. DB tests require the actual local TEST_DATABASE_URL and REDIS_URL; never print credentials. Use fresh workspace --basetemp paths.


## Final deployment checks

Final API, worker and web-provider were rebuilt/recreated and reported healthy; PostgreSQL and Temporal healthy. Original authorized DOCX: task `c84c66a4-7d6e-595f-9394-c2d02f07bbaa`, COMPLETED in 5.204s, quality_ok=true, artifact `work/verification/live-regressions/structural-health-final-docx.json`. This measures that document, not every RAG/chat scenario.

Actual HTTP-provider/runtime diagnostic probe `structural-live-health.json` used the simple original university-roster keywords. It returned a CERNET roster article and Panyu government pages while runtime.backend_diagnostics correctly reported Brave too many requests and Google Suspended: CAPTCHA. Thus a precise unchanged query can return relevant pages despite partial backend failure; neither keywords nor backend errors alone explain every run. This probe validates diagnosis and source discovery, not final answer quality.

Final-version campus end-to-end: `structural-health-campus.json`, task `d77b58d8-9d31-5903-8ab4-7b1e0c60811f`, COMPLETED 14.28s. It returned an 11-school combined list and disclosed differing 10/12-school source scopes; full-roster quality acceptance remains NOT PASS. This demonstrates why task completion alone is insufficient: identity/membership scope and historical-versus-current source reconciliation still require improvement.
