# Screenshot answer-quality repair — 2026-09-17

> Follow-up: new real screenshot turns exposed further failures. See [new screenshot recovery](NEW_SCREENSHOT_RECOVERY_20260917.md) for the subsequent fixes and current measured status. Earlier passes below apply only to those recorded runs.

Status: SCOPED SCREENSHOT AND ORIGINAL-HR ACCEPTANCE VERIFIED. Not a project/release completion claim.

## Scope and environment

User acceptance target: natural ordinary chat, correct assistant identity, Guangzhou/Huangpu tomorrow forecasts, evidence-grounded leave advice, current US officeholders and separation of powers, latest Inter Miami match results, prior-week Miami observations, and attachment summaries. Actual selected model configuration was read from the screenshot workspace: `deepseek-v4-flash`, `https://api.deepseek.com`. Public probes use isolated test workspaces against the deployed API, Temporal worker and real web provider. The configured environment default model is different and is not used for these probes. No credential values are included in this report.

The user subsequently explicitly authorized sending the original HR documents to the existing selected model endpoint. The original `HR-02_leave_policy_v2.pdf` and `TPL-08_employee_leave_application.md` were then ingested into isolated validation workspaces and processed through the real RAG/attachment pipeline. Their SHA-256 hashes match versions present in the original screenshot workspace; hashes are retained in the raw acceptance records. The earlier automatic-approval block is resolved by that user authorization. Public fictional material in `tests/fakes/public_quality_handbook.md` was used only for the earlier, separately labeled checks.

## Repairs and mappings

| Problem | Implementation | Verification |
|---|---|---|
| Empty evidence bypassed replanning and became a terminal canned answer | `runtime/activities.py`, `storage/replans.py`, `capabilities/builtins.py` | `test_no_reliable_results_terminal.py`, `test_replans.py`, `test_plan_execution_recovery.py` |
| Replanning reused old queries/completed changed steps, or produced a truncated/invalid DAG | Persist revised read objectives, rerun affected completed steps, include frozen goal/date context, validate DAG while preserving deterministic trigger, allow a bounded 2048-token decision | `test_replanner.py`, real PostgreSQL replan tests |
| Identity drift to a vendor name | Product identity with configured model label in bounded-chat prompt | Real greeting and identity probes |
| Search operators/months mistaken for entities, historical weather treated as current, undated queries penalized dated results | `apps/web_provider/main.py`, `runtime/agent_graph.py` | `test_web_provider.py`, live public questions |
| Date and temperature formats accepted during generation but rejected by final review | One shared fact normalization function; US and English date forms and Celsius ranges normalized without accepting unsupported values | `test_reviewer.py`, history and forecast probes |
| Core information lost to navigation, arbitrary item bounds or source selection | Focused verbatim excerpts, historical rows and units preserved, bounded 8-item summary/list defaults, preserve relevant same-host reports, title relevance ranking | `test_bounded_qa_fast_paths.py`, `test_agent_graph.py` |
| Fallback/repaired answers skipped usefulness checks | Exhausted generation repairs still receive quality assessment; missing core aspects request replanning; dates required to substantiate latest-match answers | Unit quality tests, live acceptance failures retained rather than reported as success |
| Official government role sources misclassified; multipart role coverage incomplete | Recognize actual institutional role sources and maintained first-party role pages | Primary-source regression and current-officeholder probes |
| Latest sports search used a fixed Chinese news market or stale fixture pages | Source-language news market, live ESPN team-directory discovery followed by completed game report links; no fixed team/game IDs | Source-language and dynamic-ID regressions; live latest-score probes |
| Undated history station pages returned today rather than the requested week | Derive requested month URLs from discovered Weather Underground station paths, open concrete leads before additional engine queries, stop only on full daily observation coverage with source minima preserved; final grounding/quality review remains required | `test_research_fallback_loop.py`, live history recovery and repeated probes |
| Search stopped at a schedule/index page | Bounded same-host discovery of report links, at most two direct-fetch waves of three pages; each fetched page gets its own URL/content/hash; links are metadata, never factual Evidence | Report-link, hash and wrong-host tests |
| Chinese district lookup returns only fuzzy/citywide weather | Resolve forecast locations through NMC's actual autocomplete directory and open matched station pages; reject fuzzy place suggestions and non-forecast paths | Official-directory and exact-place tests |
| Attachment summary read only four chunks and generated the wrong schema | Preserve up to 20 sampled chunks, explicit coverage indicator, exact summary schema | Public real ingestion/attachment probe, schema/coverage unit tests |

No permission, frozen snapshot, fencing or UNKNOWN retry rules were relaxed. No model/API keys, answer text or current officeholder names were hardcoded as answers. Website addresses identify source pages only; their live content is fetched and checked. No new dependency or architectural service was added. Public fetch adds optional `source_links` metadata with a bounded size; older responses remain accepted. All links still pass the existing safe-fetch boundary before their content can become Evidence.

## Measured results

Automated regression: **398 unit tests passed in 6.89 s**, JUnit `work/verification/screenshot-quality-unit.xml`; **32 real-PostgreSQL targeted integration tests passed in 11.33 s**, JUnit `work/verification/screenshot-quality-integration.xml`. Scoped Ruff, strict mypy and diff-whitespace checks passed. API/worker/provider images were rebuilt and their containers passed health checks; this is local deployment, not a release gate result.

| Case | Result | Seconds | Task | Raw record |
|---|---|---:|---|---|
| Guangzhou tomorrow forecast | PASS (observed answer and cited source checked) | 17.334 | `fc3197db-099e-5f8b-95b1-c290a3da1108` | `acceptance_final-guangzhou.json` |
| Huangpu district tomorrow forecast | PASS (observed answer and cited source checked) | 18.267 | `9438c1e8-9a34-5de7-a072-457fe2ab6790` | `acceptance_final-huangpu.json` |
| Current US president | PASS (observed answer and cited source checked) | 25.375 | `2d0847dc-7aa0-5b9e-940d-462f4c407759` | `acceptance_final-president.json` |
| Three branches and current institutional leaders | PASS (observed answer and cited source checked) | 24.368 | `bbce8b6c-6dbb-5538-9979-a2764a8cac5b` | `acceptance_final-branches.json` |
| Inter Miami vs Cruz Azul latest result | PASS (observed answer and cited source checked) | 11.263 | `c253966d-da8f-537f-bfd7-20c5f08c204b` | `acceptance_final-match.json` |
| Inter Miami latest completed match | PASS (observed answer and cited source checked) | 6.202 | `b5c1e273-fe90-5594-97bf-45ba47bc73fc` | `acceptance_final-latest_match.json` |
| Miami previous-week daily observations | PASS (observed answer and cited source checked) | 16.34 | `ab1ed86b-f6f6-57d8-b88a-a93102ba9cee` | `station_final-history_weather.json` |
| Morning greeting | PASS; DIRECT, natural greeting | 0.042 | direct chat | `fixed2-greeting.json` |
| Assistant identity | PASS; Agent Work, no Claude/vendor impersonation | 2.604 | direct chat | `fixed2-identity.json` |

After the final historical station/month repair, **two consecutive history probes passed**: `ab1ed86b-f6f6-57d8-b88a-a93102ba9cee` in 16.340 s (`station_final-history_weather.json`) and `ae94c1bb-d9d5-55d0-b01d-9c790417d062` in 12.304 s (`station_repeat-history_weather.json`). Both contained all seven dates, matching daily maximum/average/minimum temperatures and precipitation, explicit units and a specific monthly source URL.

All raw records are under `work/verification/screenshot-quality/`. The six non-history web cases are the final full-suite run; the history-only fix was then tested separately. These observed passes do not establish an uptime or repeated-run success-rate guarantee. Earlier failed runs remain under `work/verification/screenshot-quality/`; a task marked COMPLETED is not itself a quality pass. The public web runner exits nonzero when required observable answer checks fail.

A live recovery was observed in task `7be5cdbd-d064-5fa2-9746-5ec566909f28` (`history_order-history_weather.json`): the first synthesis failed `EVIDENCE_INSUFFICIENT`, then another search and synthesis succeeded, and the final answer contained all seven dated daily measurements (31.527 s). This is actual model/provider execution, separate from controlled replan integration tests.

The subsequent `acceptance_final` run passed all six other web questions, but history task `38a90fcb-8473-5fc7-ba24-978d662e7a12` failed because it only opened undated daily station pages (29.432 s). That failure motivated the discovered-station/month repair. Earlier failures, stale sports answers and incorrect event dates are NOT counted as passes; no raw failure records were overwritten.

Public fictional RAG: `public_rag.json`, task `135e8701-7ba9-5712-82eb-ce78654abce5`, completed in 10.22 s. It selected the applicable leave type and supported procedure from the uploaded public fixture.

Public fictional attachment: `public_attachment-fixed2.json`, task `a1b11abd-9ae7-509a-b62f-89323af70e81`, completed in 5.156 s. Covered leave types, application, approval thresholds and return/cancellation; read all 12 fixture chunks. A subsequent unrelated public question detached from attachment scope.

## Original-document follow-up after explicit user authorization

Initial original-document runs exposed two additional issues: personal leave advice made an unnecessarily exclusive category choice, and selected-document summary citations dropped the filename. The former was not a missing-source problem: the same retrieved page already contained both annual and unpaid personal leave rules. `capabilities/builtins.py` now asks for conditional policy choices and runs the semantic usefulness checkpoint for DOCUMENT_QA as well as summaries. `storage/knowledge.py` now retains the selected document name in summary candidate metadata and source locators, preserving filename/page citations without fabricating model-generated metadata.

Regression evidence: 401 unit tests passed in 7.08 s (`work/verification/original-hr-unit.xml`); the preceding run included 398 unit plus 9 real-PostgreSQL knowledge tests, all 407 passing in 7.85 s (`work/verification/original-hr-tests.xml`). The new integration assertion checks that summary retrieval retains the selected filename and renders it with page references. The new unit case ensures even a concise single-aspect DOCUMENT_QA cannot bypass usefulness review. Additional cases verify that an already valid repaired answer keeps its paragraph/bullet structure while unsupported extra text is still removed. Scoped Ruff/mypy passed. API and worker were rebuilt and health-checked.

The original-HR runner checks applicable leave conditions, exact original notice periods, original filename/page citations, summary topics and detachment from attachments on unrelated follow-up. A prior runner false-negative required the literal word 请假 even when a correct answer used 事假; this was corrected to require a leave category together with approval and handover requirements. The new original-document checks are stricter and explicitly reject an exclusive unpaid-leave answer or the public fixture's different notice period. Initial records remain available under `original_hr_authorized-*` and `original_hr_fixed-*`.

Manual review of `original_hr_final-attachment.json` rejected a mechanically passing answer because repaired atomic claims were flattened into a long paragraph. `_grounded_partial_answer` now preserves an entirely validated answer rather than unnecessarily rebuilding it; repairs with invalid claims still undergo filtering, with readable list formatting. The live runner now rejects long single-paragraph policy dumps. That earlier mechanical pass is NOT the final attachment-quality acceptance. 

| Original-document case | Result | Seconds | Task | Raw record |
|---|---|---:|---|---|
| Screenshot: personal leave for cinema | PASS, answer manually checked against original pages | 13.273 | `1cf726a0-1662-5b00-ac49-e1ba68fb0478` | `original_hr_verified-movie.json` |
| Selected original HR PDF summary | PASS, answer manually checked against original pages | 13.186 | `ff779850-6168-57e4-a1b6-259344e2e81a` | `original_hr_verified-attachment.json` |

The final cinema reply explains conditional annual leave with available balance and the 3-workday notice period alongside unpaid personal leave and its 1-workday notice, followed by application and approval requirements; citations identify original pages 1 and 2. The final attachment answer is five readable bullets covering categories, notice, proof, approval and emergency/return rules, with `HR-02_leave_policy_v2.pdf 第1—4页` citation. Both outputs were checked against the original PDF, not only keyword assertions. The unrelated follow-up detached from the selected attachment and routed to WEB. The original-document external-transmission approval is no longer pending. No leave application was submitted.

## Reproduction and limits

- Authorized original HR reproduction: `.venv312/Scripts/python.exe scripts/validate_original_hr_quality.py C:/Users/10190/Desktop/Database/knowledge_bases --phase reproduce_original_hr`. This sends the two named original documents to the screenshot-selected model endpoint; raw records include source hashes.
- Run `.venv312/Scripts/python.exe scripts/validate_screenshot_quality.py --phase reproduce --cases guangzhou huangpu president branches match latest_match history_weather` with the local services available. The runner reads the existing selected model configuration from PostgreSQL without printing secrets and creates isolated test workspaces. Review the saved answer AND its Evidence, not only the mechanical checks.
- Full unit tests: `.venv312/Scripts/python.exe -m pytest tests/unit -q --basetemp=work/verification/pytest-screenshot-reproduce --junitxml=work/verification/screenshot-quality-unit.xml`.
- Targeted integration modules: `test_no_reliable_results_terminal.py`, `test_replans.py`, `test_replan_usage_accounting.py`, `test_budgets.py`, against the real local PostgreSQL/Redis environment. JUnit: `work/verification/screenshot-quality-integration.xml`. Their model/tool decisions are controlled test fixtures, not live answer-quality evidence.
- The sports acceptance oracle is explicitly dated 2026-09-17 (latest completed match/date/score); refresh that test oracle for later reproduction. Production answers are never supplied by that oracle.
- Public live checks consume the configured model API. Search engines may return empty results, CAPTCHA or throttling; an earlier one-off success does not establish reliability.
- Original HR-file external-model transmission was explicitly authorized by the user before the subsequent original-document tests. Browser interaction, full release gates, worker-kill/recovery, backup/restore and flagship end-to-end release checks were not rerun as part of this answer-quality repair. RELEASE INCOMPLETE.
