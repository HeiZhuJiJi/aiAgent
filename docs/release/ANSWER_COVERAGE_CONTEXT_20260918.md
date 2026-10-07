# Generic answer coverage and evidence-context repair — 2026-09-18

Status: screenshot quality acceptance remains INCOMPLETE until the final live outcomes below demonstrate adequate coverage. This is not a release-completion report.

## Verified causes and implemented changes

1. A semantic reviewer could accept a useful fragment despite missing essential requested scope. `runtime/answer_coverage.py` and `capabilities/builtins.py` now distinguish ANSWERED, MISSING_EVIDENCE, OMITTED_FROM_ANSWER and source-established UNAVAILABLE for 1–6 core requirements. Missing evidence replans collection; existing evidence omitted from the answer requests answer repair. An unavailable exception needs a visible verbatim source quote. Original user wording is sent separately alongside resolved wording, candidate and evidence; planning cannot invent extra obligations. Optional limitation notes do not become required work.
2. Optional discovery checks competed with synthesis/review for the same budget. Precheck estimation now preserves most of the shared allowance; skipped checks still allow bounded reads of already observed candidate URLs. This heuristic does NOT guarantee that every retry fits. Budget gates were not raised or bypassed.
3. Weak dated results did not trigger the same general discovery broadening as empty results. Bounded second-round scheduling now includes simplified queries; shortened Chinese range endings are removed along with their full start date while entity/version identifiers remain protected.
4. Citation repair only considered six source lines. Tables with one cell per line therefore caused otherwise recoverable quote failures. Repair now examines continuous original passages up to the existing 600-character support limit. It neither drops intervening cells nor changes the answer's numbers. Existing reference integrity and semantic gates remain.
5. Follow-up page order could monopolize three fetch slots, and visited links consumed those slots. Eligible same-host HTTPS leads are now pooled and ranked before applying the shared cap, with visited URLs excluded first. Ranking uses the resolved question, including its time context, rather than only a simplified search string.
6. A decisive real failure was found in task `2fc6f6ea-ba58-5b48-a708-dfd476cbedce`: raw archived source `https://www.tianqi24.com/baiyun/history09.html` contained every requested September 7–13 record, but keyword-based context packing omitted them. The dated-row extractor recognized full dates but not short month/day records beneath a year heading. `dated_text_records` now recognizes both while deriving a short record's year only from source context, never the question or current clock. Ambiguous year headings and invalid dates are not assigned a valid date. Packing retains original headings and whole relevant records. Replaying this exact raw source retains all seven rows in 632 characters; original values and hashes remain unchanged.

No city, publisher, domain, weather station, or expected answer was added to production selection logic. Tests use inventory, schedules, incident logs, products and departments. Existing legacy topic-specific helpers elsewhere in this dirty repository remain; this report does not claim whole-codebase genericity.

## Regression mapping and measured results

- `tests/unit/test_answer_coverage.py`: essential versus optional requirements, missing versus omitted evidence, exact limitation quotes, discovery budget reserve, continuous table quotes, quotation length limit, contextual year/short-date records and ambiguous/invalid dates.
- `tests/unit/test_generic_discovery_recovery.py`: cross-domain generic follow-up selection, later-page high-relevance candidates, visited-link exclusion, existing HTTPS boundaries.
- `tests/integration/test_model_handler_fencing.py`: actual HTTP test fixture now supplies the new structured coverage result; original assertions remain intact, including question/evidence delivery and fencing.
- Final full unit suite: **526 passed in 8.77s**, `work/verification/answer-coverage-unit.xml`.
- Contract and selected DB integration suite: **42 passed in 17.34s**, `work/verification/answer-coverage-integration.xml`; rerun after the final short-date extractor. Includes candidate archive, model handler/fencing, replans, no-reliable-results terminal handling, usage accounting and budgets.
- Ruff and mypy passed for changed production modules and checked tests. No necessary tests deleted, skipped or weakened.
- One unit invocation had 14 setup errors from the Windows default temporary-directory ACL; using a new workspace test directory passed. One integration invocation had four failures because the test process lacked REDIS_URL; configuring the actual local Redis resolved them without changing code/assertions.

API and worker were rebuilt and replaced after each final production change; the final short-date extractor is deployed. Final API, worker, provider, web, PostgreSQL and Temporal health checks were healthy; Redis was running. Data volumes and configured providers were preserved.

## Actual live outcomes

Real API/Temporal/search-provider/selected DeepSeek calls; no mocked answers. Each probe used the normal 90-second / 50,000-token task budget. Different searches can return different pages; success is not attributed exclusively to one changed line.

| Probe / artifact under work/verification | Task | Result |
| --- | --- | --- |
| answer-coverage-delivery-huangpu.json | d5c24b75-b094-5ba2-b482-4df2aa702a66 | FAILED, 26.415s, 51,354 tokens. Quote repair required; repaired synthesis succeeded, but final runtime.review was blocked by budget. DB audit confirms ONE web.search call, not duplicate searching. |
| answer-coverage-quote-huangpu.json | 2ff14b67-c58d-575b-8ae6-7bca30a90b87 | COMPLETED, 19.341s, 27,274 tokens. Seven forecast dates, temperatures, conditions and source differences. |
| answer-coverage-quote-huangpu-repeat.json | a6555108-5cfd-56ba-8f2f-643a81b49f2d | COMPLETED, 10.205s, 21,667 tokens. Seven-date answer, not a one-day substitute. |
| answer-coverage-final-campus.json | 44cfe20a-bc21-51a8-91c3-496a8c90a501 | COMPLETED, 8.185s, 16,657 tokens. Twelve-school answer distinguishes ten core and two expanded campuses, dates historical evidence. Not independently re-audited against every school's current website. |
| live-regressions/answer-coverage-docx.json | c59968f2-3d33-50ec-96be-a337836d1fa2 | COMPLETED, 5.177s; existing original authorized DOCX acceptance quality_ok=true. |
| answer-coverage-quote-baiyun.json | bfa79ac2-d78e-5e72-a8bf-ae6870f6d201 | FAILED, 38.628s, 51,710 tokens. Missing requested historical coverage. |
| answer-coverage-ranked-baiyun.json | 2fc6f6ea-ba58-5b48-a708-dfd476cbedce | FAILED, 30.492s, 45,430 tokens. Revealed raw-source versus packed-context mismatch fixed afterward. |

Intermediate failures are retained in answer-coverage-*.json, including an experimental guard that wrongly made optional rainfall quantities mandatory; that guard was removed and nonblocking-limitation tests added. Final live short-date outcome is recorded below.

## Remaining risks / reproduction

Source discovery, ambiguous entities, unreliable source measurements and sparse archives still require semantic judgment. Whole-record packing can still omit records if the entire requested interval exceeds context allowance; omitted rows are unknown, not proof of nonexistence. Short dates without clear source-year context are intentionally not assigned a guessed year. This is a text-record improvement, not a full HTML table/schema parser.

Mandatory review and true budget caps remain. Successful samples do not establish a statistically stable success rate. The original screenshot suite spans more questions than this iteration's live probes. Full release gates, worker kill/recovery, reconciliation and backup/restore were not rerun; PROJECT COMPLETE is not claimed.

Reproduce public probes with `scripts/validate_web_research.py`, selected settings from `scripts/validate_screenshot_quality.py`, and the unchanged screenshot question in WEB_VALIDATION_QUESTION. Run the unit suite with a fresh workspace --basetemp. Integration tests require TEST_DATABASE_URL and REDIS_URL for local PostgreSQL/Redis; never print credentials. DOCX reproduction: `scripts/validate_live_regressions.py --phase answer-coverage --cases docx`.


## Final short-date live probe (not accepted)

`answer-coverage-dated-baiyun.json`, task `88600df9-5503-5316-a8ea-091825ba76c5`: FAILED, 35.554s, 51,842 tokens. This run selected a different evidence set. The reviewer rejected an over-interpretation of one day's high temperature and a monthly climate summary used for a weekly question. Answer repair then had insufficient review allowance. The archived-source regression proves the specific short-date loss is corrected; it does NOT prove that every live search selects that source or that every answer is now complete. Historical screenshot acceptance remains FAILED. Remaining work is materially useful source selection, preventing monthly/adjacent-scope substitution, and ensuring recovery fits the shared allowance. Two prior forecast successes and the campus/DOCX results remain as recorded, not a claim of full-suite stability.
