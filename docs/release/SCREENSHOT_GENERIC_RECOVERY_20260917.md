# Generic grounded-answer recovery — 2026-09-17

## Scope and acceptance

The user requires useful Chinese answers to the actual screenshot questions, using the selected deepseek-v4-flash model, live search, real PostgreSQL and Temporal, and authorized original attachments. A successful task status or mechanical string check alone is not quality acceptance. Source languages are unrestricted. No screenshot answers, dates, cities, scores or source-specific endpoints were hardcoded in this follow-up.

## Implemented

- Question, resolved request and collected Evidence are transmitted together; a persisted synthesis recovery objective now reaches the model rather than being hidden by the original instruction.
- Deterministic grounding checks citation identity and actual source quotations. A separately assessed semantic judgment replaces lexical number matching only for the exact accepted Claim/source hashes, workspace and task. Runtime review still enforces data integrity, source boundaries, schema, fencing and existing budgets. ADR-003 records the decision.
- Answer repair reuses collected Evidence. Missing facts trigger collection; persisted recovery queries no longer replay the original query list. Complete earlier Evidence remains available.
- Provider discovery has a 10.5-second overall deadline inside the caller's 12-second wait, preserves already completed source results, runs independent hints concurrently, and cancels unfinished hint tasks. This prevents serial fallback/enrichment latency from discarding earlier successful reads. Timeout recovery does not fabricate opened-page status or cache incomplete coverage.
- Provider discovery links retain original URLs. Ranking uses descriptive anchor/path relevance rather than hostname matches. Coverage assessment receives relevant passages instead of just leading navigation.
- Web synthesis has a bounded 6,500-character source allocation; search has a 20-second local allowance inside the existing task budget. Original Evidence is retained. Optional-call deadlines cannot become negative.

## Measured checks

- 464 checks passed (27.78 s): full unit suite plus model-handler/fencing, replans, no-reliable-results terminal handling, replan accounting and budget integrations. Artifact: work/verification/quality-acceptance.xml. Follow-up link ranking: 9 passed. The follow-up combined run passed 465 checks (28.25 s), work/verification/quality-bounded.xml. After aligning the compact JSON prompt to its unchanged citation schema, 99 affected unit checks passed. Provider deadline/cleanup checks: 98 PASS, including completed-result preservation with hash integrity and cancellation. Scoped Ruff and mypy passed for six production files.
- Question/Evidence HTTP-boundary checks use real PostgreSQL and production handler/gateway with a controlled test model. They do not establish live answer quality. Checks also verify accepted semantic proof reaches final runtime review and cannot be reused for changed content/source/workspace.
- All live attempts, including failures, remain under work/verification/screenshot-quality and work/verification/live-regressions. These isolated workspaces use the screenshot-selected actual model/service.

## Important observed failures (not hidden as passes)

The earlier 14,000-character synthesis allowance could exhaust the 24,000-token task budget through repeated generation/review. Earlier collection could replay completed queries. Some answers marked completed substituted broad/adjacent facts; these are not accepted merely because the runner reported true. The acceptance-* batch still failed Huangpu, next-week Miami, historical Dongguan and latest-match tasks; it predates the final context/time/ranking changes. The specific matchup also omitted its event date. Those failures remain evidence of incomplete acceptance until final verification.

An automatic-review rejection prevented narrowing the existing branches oracle: it considers any occurrence of “证据未” a missing institutional role, including publication-date caveats. The original assertion is retained. Manual source review is reported separately; no automatic pass is fabricated.

Automatic approval also blocked the latest original-HR re-run pending explicit payload/service authorization, and blocked clarifying useful partial-answer semantics for sources whose publication horizon ends before the requested range. Both questions were presented to the user; neither blocked action was executed. Earlier measured HR/DOCX results remain available and are not silently relabeled as latest-build results.

## Reproduction

- .venv312/Scripts/python.exe scripts/validate_screenshot_quality.py --phase <unique-phase>
- .venv312/Scripts/python.exe scripts/validate_live_regressions.py --phase <unique-phase> --cases miami_future dongguan_history docx
- Original HR only with explicit authorization: scripts/validate_original_hr_quality.py C:/Users/10190/Desktop/Database/knowledge_bases --phase <unique-phase>

## Additional measured result

The full provider follow-up regression passed 466 checks (23.71 s), work/verification/quality-provider.xml. Later CJK link/passage relevance and full-question/multi-aspect context changes passed 62 affected unit checks and four HTTP-boundary/final-runtime-review integration checks (final-context-proof.xml). Ruff/mypy passed. Services rebuilt and healthy; no user checkout reset or original document modification.

The provider_bounded latest-match task d475b5bd-bfc9-5b4e-8217-0a9fd201d622 completed in 26.430 s with the dated 2-0 result from the September 16 ESPN report. The compact historical-Dongguan task f7cedbea-3de4-55da-96da-de5a08e7accb completed in 18.506 s with the actual September 11 daily row (88/75 degrees F, 67 percent humidity, 7 mph, 0 inches); disclosed Celsius approximations were manually checked arithmetically. These are individual observed results, not proof of repeatability. Earlier latest-match replies using the September 12 league result remain FAIL records. Huangpu and multi-part source coverage remain unstable; no full screenshot PASS is asserted.

## Search-engine health findings

Direct engine probes on the existing SearXNG service returned: Baidu CAPTCHA (zero results), DuckDuckGo CAPTCHA (zero), 360search zero for the district query, and Brave 21 relevant results. Existing Sogou logs show parser crashes and Bing logs show connection/parser failures. Brave was already integrated but disabled; deploy/compose/searxng-settings.yml now enables it. No dependency/account or CAPTCHA bypass was added. A later default-engine probe reported Brave temporarily suspended for too many requests and Google returning ten results. Search availability remains an external reliability limitation; suspension was not reset or bypassed.

The engine_recovery batch uses the deployed generic code and enabled engine. Mechanical success does not establish semantic completeness: Huangpu returned an explicitly labeled Guangzhou-city approximation, and Dongguan used a same-date morning forecast rather than a full-day observation. These are limited answers, not exact full-scope acceptance. The branches answer supplies the president, House speaker and chief justice but omits a current Senate officeholder name. Miami next-week still fails; the requested partial-answer policy adjustment remains pending automatic-approval confirmation. DOCX task 1abb1d73-17b4-58ab-943f-5b9e2258200d completed in 5.356 seconds and produced a useful sourced summary. No aggregate screenshot PASS is claimed.

## Release status

SCREENSHOT ACCEPTANCE PENDING. RELEASE INCOMPLETE. No whole-project completion claim: worker kill/recovery, response-loss reconciliation, fencing release gates, flagship flow and backup/restore were not comprehensively rerun. Existing older domain-specific heuristics remain and are not represented as a finished generic architecture.

Final batch index: work/verification/engine-recovery-index.json. All seven original web prompts mechanically completed; DOCX and Dongguan probes also completed. Miami next-week did not complete. This is 9/10 mechanical checks, NOT 9/10 fully audited high-quality answers; the manual scope limitations above remain. Regular chat greeting/identity and original-HR RAG/attachment had earlier successful observed runs, but the newest original-HR rerun is pending explicit authorization.


## Explicit authorization and follow-up (latest, 2026-09-17)
The user authorized the two previously blocked actions: useful partial scope semantics and sending the original HR PDF/template to the configured api.deepseek.com model for acceptance. Both are now executed. Earlier pending statements above describe historical status.

- `authorized_partial` HR movie task 2b203e3b-1a64-5862-91b1-c07c97ccb602: COMPLETED, 11.257 seconds; original PDF/template, conditional personal/annual leave advice and source citations. HR summary task 2f10f3d3-9493-58fd-99c3-2b5891c8e45f: COMPLETED, 5.115 seconds; useful original PDF summary and unrelated follow-up detached attachment scope.
- First partial-weather task 51e98e3f-1865-56fa-a142-53fe48f23b1f mechanically passed but answered outside the requested week. Manual FAIL retained. Tightened generic semantic instructions to require actual facts within the requested interval.
- Subsequent Miami task 8452fab5-6855-51fd-be1d-9a30961504a7: COMPLETED, 22.462 seconds. Gives September 21-23 highs 88/89/89 F, night lows 77 F, nighttime/Tuesday/Wednesday 80 percent precipitation; explicitly limits September 24-27. Compared against stored NWS page, including detailed forecast and valid period. This is an observed useful partial answer, not proof of repeated reliability.
- Huangpu task 5414db39-1929-5fe7-b8ed-d7e9669ef270 completed with a district/date-specific tianqi.com source. Branches task 35d0e0f9-609b-5a59-b756-0220df10ff62 FAILED after missing Senate roles and repeated generation exhausted budget. Preserved failure.
- Further generic fix: multi-aspect FACT/LIST/COMPARISON plans dispatch their initial independent queries together, preserving the second round for observed gaps. Same-question fallback queries retain lazy execution. Planner explicitly checks that each necessary entity appears in its queries without inventing optional requirements. All original assertions retained.
- Final unit suite: 431 PASS (7.89 seconds), authorized-final-unit.xml. Database/HTTP/fencing/replan/budget integrations: 37 PASS (12.97 seconds), authorized-final-integration.xml. Total 468. Scoped Ruff passed. Services rebuilt and healthy. Full screenshot/release acceptance remains pending.


## Evidence packing and budget follow-up
Replaying task 91828af6-03ba-5850-9d92-24bc8aa30660 showed that a collected Senate leadership page contained the missing names, but preselection excluded it. Compound questions now expose up to 16 bounded candidates to semantic source selection, then re-expand selected immutable sources into the 6,500-character answer context. Short record labels are retained. HTML nav/footer content is excluded from factual body extraction while navigation links remain available for discovery. No website-specific selector was introduced.

A subsequent attempt (970b08b5-20f0-55cd-8852-a2dfb0c9bd9f) exhausted the 24,000-token task budget before final assessment. Product web/document defaults now use the existing ordinary-task 50,000-token ceiling; web acceptance runner matches that product configuration. All budget enforcement and quality assertions remain. This increases the maximum possible model consumption per grounded task; it is not a claim that every request uses that allowance.

- Branches task bb5d60eb-f8d8-56ba-918e-8e0ec9c48dca COMPLETED in 23.366 seconds, consumed 18,328 tokens. Names and institutional distinction present; Senate and Supreme Court stored source text manually checked.
- Final DOCX task 88bfba08-0a37-5267-8620-761b8737bd6d COMPLETED in 5.120 seconds with useful five-part original-document summary.
- Miami task 557abf4e-ebc5-5f79-aa66-4c596d00b669 COMPLETED in 17.309 seconds, gives September 21-23 and explicitly limits unpublished September 24-27. Mechanical oracle fails because spaced Chinese date is not an exact substring; original oracle retained.
- Dongguan task 8a18a6e1-c1b7-5c52-94c8-9ee901400aca is manual FAIL: undated day-of-month homepage forecast cannot establish the requested historical date. Its HTTP source also fails the script's HTTPS-only check. Added generic semantic instruction requiring historical records, no inferred source month/year, and separation of prior forecasts from observed facts.
- Broad evidence-pack regression: 470 PASS, 18.43 seconds, authorized-evidence-all.xml. Final historical prompt change: 40 affected checks PASS; Ruff and mypy passed. Frontend TypeScript/production build passed and web/API/worker/provider services are healthy. No release completion claim.


## Latest gap-priority verification and remaining blocker
Explicit searchable missing_aspects now take precedence over contradictory PASS/REVISE labels; true CLARIFY remains a user-input branch. This avoids spending another generation on absent data. The existing recovery test was strengthened so PASS/REVISE with a missing record must raise EVIDENCE_INSUFFICIENT; no required assertion was removed. Final unit suite 434 PASS (7.29 s), gap-final-unit.xml. The preceding combined run had 37 integration PASS and one old unit-contract expectation failure, corrected and rerun; do not label that original combined XML all-pass.

Historical task cb906b00-7123-56a9-912d-c6f157a45839 remains FAIL (33.510 seconds, 35,215 tokens). Trace proves a fresh successful collection step occurred between two synthesis failures. First rejection: undated prior forecast. Second rejection: source held September 10 rather than September 11; the candidate copied adjacent-day values and was correctly rejected. This establishes functioning replan, not successful retrieval of the requested historical record. The latest deployed code includes this gap-priority fix.

SCREENSHOT ACCEPTANCE STILL INCOMPLETE. No blanket PASS or final repeatability claim. Remaining historical-source retrieval requires further work; older successful individual history examples do not supersede this newest failure. Some runner checks also produce formatting-only false negatives (Chinese date spacing, HTTP versus HTTPS); unchanged assertions and manual findings are kept separately. The other six original web prompts were not all rerun after the final gap-priority change. All live attempts remain saved.
