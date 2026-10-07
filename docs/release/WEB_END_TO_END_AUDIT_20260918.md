# Web research end-to-end audit

## Scope and method
User requested an audit from request entry through final UI, including non-weather questions. Production behavior, configuration and services were not changed. This report combines code-path inspection, retained real tasks, the preceding exact-query live provider probe, deterministic non-weather diagnostic inputs, and existing tests. It is a quality/reliability audit, not a full security, deployment or master-spec release certification.

Evidence labels: REPRODUCED = observed live or deterministic execution; CODE = directly identified in implementation, not separately demonstrated as a live user failure; LIMIT = current capability/design limit; UNVERIFIED = needs additional observations. Do not conflate test success with end-user quality.

## Actual path and coverage
1. Chat UI submits original content and context to API. Active agent planning receives local date/timezone and completed eligible history. Planning/route validation freezes the decision; semantic correctness of reference resolution is still a model judgment. Inspected, no new pronoun-resolution live batch.
2. Agent planner proposes tool/source plan, queries and AnswerContract. Complementary queries are prompted, not guaranteed; a compound FACT requires query count to cover aspect count. A schema-valid plan can still add overly strict requirements or pick an incomplete query.
3. Runtime reads frozen context and applied Plan objectives. Recovery search uses its persisted objective. Search provider also generates variants and applies topic/source heuristics. These are multiple decision layers, not a simple single model search loop.
4. SearXNG returns results. Normalization truncates search text to 1200 characters and drops upstream diagnostic details from the public result contract. Known upstream rate limits exist, but the exact-query trace still had 10 useful hits.
5. Provider performs entity/topic filtering, URL deduplication and diversity ranking before opening a bounded subset of pages.
6. Fetch reads HTML/text/JSON with public-address checks, redirect limit and byte limit. It does not render browser-only content or support PDF in this path. Parsing and excerpts transform source content.
7. Runtime merges search/fetch results, evaluates coverage and runs bounded follow-up queries/URLs. Prior result URLs can suppress later search records for the same URL.
8. Evidence persistence retains source records and fencing. These records are not automatically factual acceptance. Generation performs another ranking, source-count selection, date filtering and excerpt stage; some paths allow semantic selection only after this narrowing.
9. Original/resolved question, plan, selected evidence and supported draft enter the selected model. Existing HTTP-boundary integration tests verify this. It is incorrect to claim the model is never given the question and sources.
10. Quality assessment determines repair/replan/acceptance. Cite/hash checks preserve integrity, but semantic assessment can still accept scope substitution. Shared research_progress projects a previous judgment, not an evidence-backed per-fact ledger.
11. Replan receives original goal/current plan/failure and up to 12 source URL/title observations. New evidence collection and synthesis are executed under existing budget/fencing. Web bounded QA has max_replans=1.
12. User output distinguishes some infrastructure failures but many validation failures share a generic terminal sentence. Raw stage provenance is not shown as an end-to-end evidence-loss explanation.

## Findings
| ID | Priority | Evidence | Finding and impact | Implementation |
|---|---|---|---|---|
| A01 | High | REPRODUCED | Pre-fetch lexical hard filtering deletes useful results whose snippet omits one name; opening the page would resolve identity. Chinese simplified/traditional and cross-language matching remain incomplete. Exact district weather result was deleted; generic multi-entity requests share this path. | apps/web_provider/main.py viable_results, discovery_candidates, _entity_matches |
| A02 | High | REPRODUCED | Numeric leaf IDs and discarded query strings collapse different resources: /spec/123456 vs /manual/123456; report?year=2025 vs report?year=2026. Actual 7-day/40-day forecast collision also observed. | _source_identity; pipeline merge_batch |
| A03 | High | REPRODUCED | Excerpt anchor/window cap loses useful data even when the whole source fits the budget. A 536-character 40-institution list becomes 237 characters / 17 institutions with limit=5000. Actual 4837-character page became a 272-character excerpt. | relevant_excerpt |
| A04 | High | REPRODUCED | FORECAST pre-generation full-ISO-date filter removes relevant MM/DD district records while retaining city/tide sources with ISO strings. This is a forecast branch defect, not proven identical behavior for all news. | capabilities/builtins.py _analysis_evidence_payload |
| A05 | High | CODE + retained live evidence | Source ranking/truncation precedes semantic selection. Non-compound paths cap sources at five; selector sees only bounded candidate excerpts. A model cannot recover sources already removed. Ranking/format heuristics can dominate actual scope coverage. | _analysis_evidence_payload; _model_handler selector construction |
| A06 | High | CODE | Supported-draft loading unions claims from the latest eight audits. If a later assessment revokes an earlier supported claim while source hash remains unchanged, the earlier draft can still be restored. Final review still runs, so this is reintroduction risk, not automatic final acceptance. | _load_answer_draft, _restore_answer_draft |
| A07 | Medium | REPRODUCED key/cache behavior; CODE endpoint consequence | Cache key excludes requested result count. Small-limit result cached first can satisfy a later larger-limit request with the same smaller set, without searching again. Existing test covers large-to-small only. TTL/stale caching itself is intentional. | _search_cache_key, _cached_results, pipeline early cache return |
| A08 | Medium | CODE + date calculation | Planner uses local date/timezone; coverage/replanner/provider use UTC calendar date. At 01:00 Shanghai the components can disagree on today. Absolute bounds mitigate but do not unify the context. Live wrong-date causation not asserted. | api/app.py AgentGraphContext; _assess_web_search; runtime/replanner.py; provider date functions |
| A09 | High | CODE + real captured challenge page | Fetch success is treated as readable content based on status/type/length. A CAPTCHA/challenge page can become collected WEB evidence. There is no robust typed distinction among substantive body, navigation, challenge and truncated response. | fetch_public_page; captured shared-progress-forecast evidence |
| A10 | Medium | REPRODUCED parsing; LIMIT | HTML table row/cell structure becomes newline text; colspan/rowspan and image labels are not preserved. Script-based data is omitted; PDF is rejected by content type. Plain text can work for simple tables, but complex record alignment is not guaranteed. | _VisibleTextParser, fetch_public_page |
| A11 | High | CODE | Evidence progress uses len(evidence) on commit, not new facts or closed gaps. Repeated sources with new record IDs count as progress. Timestamp-in-snippet changes also change content hashes, weakening content-based deduplication. | storage/attempts.py commit_evidence; runtime/loop_guard.py check_evidence_progress; provider normalize_results |
| A12 | Medium | CODE + retained live repeat | Replanner history is only first 12 URL/title pairs, taken from evidence ordered by UUID, not most relevant/recent failures. Empty queries and failed fetches do not produce evidence and are absent from this list. It cannot reliably avoid every already-failed strategy. | runtime/activities.py observed_sources; storage/attempts.py list_task_evidence |
| A13 | Medium | LIMIT | Two internal query rounds, 20-second collection deadline, 10.5-second provider deadline, 90-second UI Web goal and one outer replan interact. Internal wording promises of recovery do not imply another executable round. Increasing counters alone would repeat lossy processing. | builtins._web_search_handler; provider SEARCH_PIPELINE_TIMEOUT_SECONDS; runtime/budgets.py; chat-page.tsx |
| A14 | High | retained live REPRODUCED | Final semantic review accepted city substitution and inferred absence in task 0cdbe6d7-d6d6-5c24-8508-bbef732b4d07. Shared progress is a hint; no deterministic evidence-backed resolution exists for each gap. No blanket relaxation is justified. | _assess_answer_quality, _require_quality_replan, _load_research_progress |
| A15 | Medium | CODE | New manifests record reviewed excerpt ID/length/hash, but not complete historical model inputs or per-candidate rejection reasons. Search/fetch/selection losses still require separate instrumentation. State projection is incomplete diagnostics, not full tracing. | _record_answer_quality_assessment and result contracts |
| A16 | Medium | CODE + screenshot/live outputs | Multiple distinct failure stages collapse into generic user-facing validation/safe-failure text. Task COMPLETED is not a semantic-quality score; users cannot infer root cause from the bubble alone. | runtime/conversation_results.py; chat-page.tsx webTaskFailureMessage |

## Things confirmed present, not missing
- Original/resolved question and selected evidence reach model generation; HTTP integration assertions exist.
- Actual replan runs occurred in campus and weather tasks; calling this an entirely linear no-replan workflow would be wrong.
- Frozen task/plan validation, workspace isolation, task locks, attempt fencing and evidence integrity remain in the inspected paths. No proposal to remove these protections.
- Draft content can survive recovery with source ID/hash/quote checks. The limitations concern semantic revocation and gap closure.
- Public fetch boundaries enforce scheme, address and redirects. Limited parsing capability should not be confused with missing network controls.

## Verification and artifacts
Non-weather deterministic probes: work/verification/audit-generic-functions.py, run from project root with Python runpy.run_path (direct script execution initially lacked the apps import root); output full-chain-generic-probes.json. They operate only on temporary in-process data, not service state. Results: two distinct URL collisions; 40-to-17 item loss; flattened table; equal cache keys across 1/10 limits and a one-item cached result; different UTC/Shanghai dates for the same instant.

Current existing provider + answer unit suites: 153 PASS in 3.85 seconds, audit-existing-tests.xml. Passing while the diagnostic defects reproduce identifies coverage gaps. No failing required test was removed, weakened, skipped or changed. Earlier 477 selected checks remain historical measurements, not a new full-suite claim.

Prior exact-query capture and historical packing replay: WEB_PIPELINE_DIAGNOSIS_20260918.md. Actual campus repeat failure and weather mixed-quality completion: SHARED_RESEARCH_PROGRESS_20260918.md. Their limits remain applicable; no fresh end-to-end live model batch was run in this audit.

## Recommended repair order (not implemented in this audit)
1. Stop irreversible information loss: candidate admission vs final acceptance, conservative document identity, preserve short full bodies and structured sections, remove date-format-only exclusion.
2. Establish source representation and status: capture page/section/row relations, challenge/truncation states, normalized date/location scope and available detail links. Keep original text for review.
3. Complete recovery state: task-scoped per-gap supporting evidence, revocation/conflict handling, attempted-query/fetch outcomes and content novelty. Unify time context.
4. Align generation/review/UI on the same scope, then evaluate budgets using measured useful progress. Maintain integrity/authorization invariants.
5. Add cross-domain regressions for these cases and repeated live questions across lists, products, news, history and weather before claiming quality stability.

## Remaining audit limits
Pronoun resolution across long conversations, every search engine's parser, every external format, exact historical prompt bytes, claim-by-claim accuracy across all screenshots, RAG ingestion and attachment internals, full release/security/recovery gates were not exhaustively retested. This report completes the present diagnostic pass, not the repairs or PROJECT COMPLETE.
