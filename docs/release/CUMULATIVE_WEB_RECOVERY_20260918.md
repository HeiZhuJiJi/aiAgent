# Cumulative web recovery and query refinement — 2026-09-18

## Scope and implementation

This repair follows the existing bounded workflow: alternate queries, open observed detail links, retain original Evidence, assess combined coverage and collect remaining gaps. No city, publisher URL template, expected answer or new dependency was introduced. Existing quote/hash validation, permissions, attempt fencing and budgets remain.

Implementation: `src/enterprise_agent/capabilities/builtins.py`.

- Recovery now loads persisted WEB Evidence for the same workspace/task and passes hash-checked source bodies into collection coverage checks. Current and retained sources are interleaved within the six-source checkpoint cap, preventing a full new batch from evicting every old source. Old evidence is not reissued as new collection output.
- Replanning preserves original query alternatives. The initial recovery checkpoint's additional queries now enter the executable queue, ahead of remaining variants and the persisted recovery objective. Successful search outputs record actual attempted queries; later recovery avoids replaying those queries. The strict capability output schema includes this optional metadata.
- Candidate pooling removes read URLs before truncation and ranks before applying limits. Observed detail links and engine candidates share bounded slots, so a large initial search batch cannot evict every later detail link. Link dates inside a bounded requested range participate in ranking. These are discovery hints, not evidence of values or proof of entity identity.
- Automatic traversal no longer treats a lone date or broad CJK bigram as sufficient reason to fetch an unrelated section. Same-branch dated links remain eligible; other soft candidates remain available for model inspection. Same-host/HTTPS constraints remain.
- Generation's source ranking now includes Chinese title overlap; previously title keyword scoring only counted English words. Publisher authority and article length no longer receive an uncontested advantage for Chinese questions.

## Verification

- Full unit suite: **544 passed in 10.06s**, `work/verification/cumulative-unit.xml`.
- Contracts and selected database integrations: **42 passed in 18.09s**, `work/verification/cumulative-integration.xml`. Covers candidate archive/task isolation, model fencing, replans, no-reliable-result terminal handling, usage accounting and budgets.
- Ruff and mypy passed for changed production code; checked tests passed Ruff.
- Regression additions in `tests/unit/test_web_candidate_recovery.py`: cumulative source preservation/hash rejection, gap-query execution across inventory/product/department topics, old-query deduplication, detail-pool starvation, interior-range dates, unrelated date navigation, Chinese title ranking.
- Existing strict full-output assertion in `test_research_fallback_loop.py` retained and extended with expected actual queries. Archive integration additionally checks successful same-task query history. No required assertion removed or weakened.
- An intermediate integration run found four failures because the newly emitted query metadata was absent from the output schema. The schema was repaired; final integrations all passed. This failure is not hidden.
- API and worker rebuilt and replaced with final code. No data volumes removed. Web-provider was not modified or restarted in this iteration.

## Real end-to-end probes: screenshot acceptance NOT passed

All probes use the actual local API, worker, web provider and configured DeepSeek, with unchanged public questions. Artifacts below are under `work/verification/`.

| Artifact | Task | Result |
| --- | --- | --- |
| cumulative-baiyun.json | 74702dac-9283-5293-9b26-f21f4ef2edaa | FAILED, 35.507s. Missing middle historical dates. |
| cumulative-campus.json | 2a701f8e-ca46-53b4-abe2-3ccc18646978 | COMPLETED, 12.268s, but mixed list scopes and AI search summaries: quality FAIL. |
| cumulative-final-baiyun.json | 749f553d-d52a-58da-9b74-3425ee79d6cc | FAILED, 46.714s. Detail navigation and historical coverage still incomplete. |
| cumulative-final-huangpu.json | cb148e91-9607-5f12-a44b-33c2d4cfb6e5 | FAILED, 30.459s. Unrelated authoritative navigation pages displaced target content; prompted Chinese-title/navigation repair. |
| cumulative-ranked-huangpu.json | b95a7c9b-db82-5317-ac79-fb2491a156e4 | FAILED, 29.523s. Four dates supported; three missing; first semantic assessment unavailable. |
| cumulative-ranked-baiyun.json | 4cd7ce81-b348-5169-abd2-45efa08a4b5b | FAILED, 32.440s. Semantic assessment unavailable, then insufficient assessment time. |
| cumulative-ranked-campus.json | 6c3f13aa-648e-5a89-8746-4cb2d10d56e8 | COMPLETED, 24.392s after quote repair, but incomplete/core-versus-expansion list and AI summary dependence: quality FAIL. |
| live-regressions/cumulative-final-docx.json | 9ed3cd6a-4e5e-5baf-8c19-7377f4821ad4 | COMPLETED, 5.172s, quality_ok=true. Original authorized DOCX. Run preceded last title/navigation repair, which only affects WEB handling. |

## Remaining defects and limitations

1. Real target-page text contains seven forecast dates followed by separate lists of descriptions/numbers, including concatenated adjacent numeric values. Table row preservation does not cover all non-table column/list layouts. Extraction and excerpt selection cannot yet guarantee date/value association. Do not infer missing separators or match adjacent-region figures to the target.
2. Review can be unavailable and exhaust recovery time. This is separate from missing Evidence; no validator bypass or budget increase was used to manufacture success.
3. Campus results still depend on search-generated summaries and inconsistent scope. Source discovery/quality and final completeness remain insufficient despite COMPLETED.
4. The six-source coverage cap and candidate/title heuristics are bounded approximations, not proof of comprehensive coverage. Legacy topic-aware paths elsewhere still exist; this report does not claim the whole repository is generic.
5. A direct HTML diagnostic request to an observed public target URL returned HTTP 403; no attempt to bypass that restriction was made. The source-structure finding above is from previously persisted provider Evidence, not a successful fresh HTML inspection.

Reproduce with `scripts/validate_web_research.py` using selected_settings from `scripts/validate_screenshot_quality.py`, and the unchanged questions. Run units with a fresh workspace --basetemp; DB integrations require local TEST_DATABASE_URL and REDIS_URL. Never print credentials.

This is a tested implementation repair, not full screenshot quality acceptance or PROJECT COMPLETE. Full release gates, worker kill/recovery, reconciliation and backup/restore were not executed in this iteration.
