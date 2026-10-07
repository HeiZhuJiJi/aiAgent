# Generic research scope and recovery repair - 2026-09-18

## Scope and observed causes

The requested outcome is high-quality answers across public search, RAG and attachments without city, website or question-specific patches. This report covers the public-search recovery tranche. It does NOT declare screenshot acceptance or release completion.

Observed causes this turn:
- Historical intent could be changed to CURRENT when external freshness lookup was requested.
- Query expansion existed in both runtime and provider, including topic-specific source hints and URL templates.
- Search coverage prechecks could run repeatedly before generation/review, consuming much of the 50,000-token task allowance.
- A decisive entity ambiguity survived review: task 4e91d7b1-00eb-5ea0-9edf-dd4dbcba9c63 answered Guangzhou Baiyun using a page whose original breadcrumb says Guiyang > Baiyun District. The compressed passages omitted that parent context. This was a failed quality result despite COMPLETED state.
- Raw sources can contain anomalous values; copying them with a caveat is not always a useful answer.
- Backend dependencies had exited before integration verification. Existing containers were restarted without deleting volumes; this environmental failure was not treated as a code pass.

## Changes and mapping

- src/enterprise_agent/runtime/research_scope.py: preserve full named scope in query variants; generic date broadening after an empty temporal search, protecting entity/version dates; retain bounded breadcrumb context independently of relevance/date excerpts.
- src/enterprise_agent/runtime/agent_graph.py and contracts/domain.py: additive entity_scope in bounded research plans; preserve HISTORICAL intent when external lookup is needed; request complementary short/precise/source-language queries.
- src/enterprise_agent/api/app.py: carry named scope from the tool plan into the persisted bounded research plan.
- src/enterprise_agent/runtime/web_provider.py and apps/web_provider/main.py: runtime-managed queries bypass provider-side hidden topic expansions, source hints and additional query templates. Cache keys distinguish managed requests. Standalone legacy provider clients retain compatibility mode; this is not a claim that every legacy helper has been deleted.
- src/enterprise_agent/capabilities/builtins.py: removed main-handler calls to fixed official-role pages, historical-weather URL rewriting and NMC-specific gap queries. Related-link following now ranks observed same-host HTTPS links without sports/weather path templates. Uniform short-excerpt refetch and HTTPS transport recovery preserve the existing safety boundary. Existing topic helper definitions remain for compatibility/tests but those recovery helpers have no main-handler call sites.
- Coverage prechecks are capped at two per web-search capability and identical evidence/candidate inputs are not checked again. New evidence is still collected and preserved. This is not a full task-wide generation-budget reservation.
- Both generation and final review receive source_identity_context. Parent/child conflict is wrong-entity evidence, not a disagreement about the requested target. Menu mentions do not establish identity. Source hashes and citation integrity checks remain unchanged.
- Generation/review distinguish optional anomalous measurements from missing essential facts; list membership, names and effective dates must remain scoped. These are semantic instructions, not deterministic guarantees of correctness.

## Regression evidence

Final suite: 540 passed in 25.01s. Artifact: work/verification/research-scope-tests.xml.
Includes all unit/contract tests and selected candidate-archive, model fencing, replanning, no-reliable-results, replan-usage and budget integration tests. Ruff and mypy passed after import formatting. No required failing assertion was deleted or weakened.

New/expanded coverage:
- tests/unit/test_research_scope.py: hierarchical targets, cross-language queries, historical planning, managed provider queries, generic temporal broadening and version-date preservation.
- tests/unit/test_web_candidate_recovery.py: bounded prechecks still collect new evidence.
- tests/unit/test_generic_discovery_recovery.py: school/product/manual/observation link discovery; HTTPS recovery across unrelated domains; main handler does not invoke legacy topic templates; breadcrumb retention across campus/product/department contexts while preserving hashes and content limits.

Automatic review rejected changing the original HTTPS refetch test. The original test was retained; the safe alternative uses the same HTTPS policy across all publishers and adds separate multi-domain tests. No permission remains pending for this resolved alternative.

API, worker and web-provider were rebuilt/deployed. Final health snapshot: API, worker, provider, PostgreSQL, Temporal and web healthy. Data volumes were preserved. Import formatting is semantically identical to the deployed build.

## Actual live results, including failures

All public probes used the selected deepseek-v4-flash configuration, original question and actual provider. No answers were hard-coded.

| Stage | Task | Observed result |
| --- | --- | --- |
| Initial scope change, Baiyun | ae6cf35b-cd42-5c0c-91da-db8340e3032a | COMPLETED, 23.448s, 47,883 tokens; included anomalous source measurement, quality not accepted |
| Intermediate Baiyun | 7d910e82-1c04-5f43-be7d-490c70618c11 | FAILED, 26.494s, 57,267 tokens; insufficient useful records then budget exhaustion |
| Precheck cap, Baiyun | 84282961-86b8-5382-90d3-303dbdca3648 | FAILED, 40.591s, 52,880 tokens; insufficient evidence then insufficient review time |
| Generic recovery, Baiyun | 4e91d7b1-00eb-5ea0-9edf-dd4dbcba9c63 | COMPLETED but wrong parent location; quality FAIL, triggered breadcrumb repair |
| Generic recovery, campus | 7c59b57d-d775-5154-9e74-6ef135a0c062 | COMPLETED, 16.323s; conflicting/incomplete rosters and weak sources, quality NOT accepted |
| Generic recovery, Huangpu | 30d47956-67d9-5dae-a5d2-dfea6a401135 | COMPLETED, 17.314s; only one day plus broad trend, not the requested complete week |
| Breadcrumb repair, Baiyun | 71027b5c-3fa9-539d-9558-12ffeeda292e | COMPLETED, 19.382s, 26,244 tokens; one-day answer only, six days missing, screenshot acceptance still FAIL |
| Original authorized DOCX | 13a82634-bb9f-55e9-a77a-a7f4acd982b7 | COMPLETED, 5.115s; existing document acceptance quality_ok=true |

Artifacts: work/verification/research-scope-*.json, generic-recovery-final-*.json, generic-recovery-identity-baiyun.json, live-regressions/research-scope-docx.json. The final breadcrumb probe no longer reproduced the previous wrong-parent answer, but a single sample does not establish general prevention.

## Remaining work and limits

- Range/list completeness remains unstable: a one-day answer can be marked acceptable for a week, and a conflicting partial roster can still pass. COMPLETED is not high-quality acceptance.
- Keyword anchoring alone cannot establish entity identity. Breadcrumb preservation makes that identity visible, but semantic selection/review can still err and some pages lack breadcrumbs.
- External engines, navigation-only pages, conflicting sources and evidence ranking still affect coverage. No provider availability guarantee is implied.
- Per-capability precheck caps do not reserve sufficient task-wide tokens/time for every replan and final review.
- Legacy provider compatibility mode and other source-format/ranking helpers remain; only the enumerated main recovery branches were replaced here. Whole-codebase genericity is not claimed.
- Full release gates, kill/recovery, reconciliation and backup/restore were not rerun. PROJECT COMPLETE is not claimed.

Reproduction: run the unit/contract and listed integration suite with the existing local test database; run scripts/validate_web_research.py with selected_settings from scripts/validate_screenshot_quality.py and WEB_VALIDATION_QUESTION set to each original screenshot question. Do not print model secrets. DOCX reproduction: scripts/validate_live_regressions.py --phase research-scope --cases docx.
