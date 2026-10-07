# Complementary discovery and gap recovery

## Changes
- runtime/agent_graph.py requests 2-3 complementary queries where useful, including aliases/source-language terms, preserving requested scope in the answer contract. No question-specific source URLs were added.
- capabilities/builtins.py interleaves remaining planned queries with feedback-driven queries for multi-aspect recovery. Previously four remaining variants could consume the final batch and prevent observed-gap recovery. Existing single-query fast paths remain unchanged; successful evidence is merged and retained.
- The preceding discovery fix in apps/web_provider/main.py admits subject-matching leads only when strict filtering yields no candidates. Leads remain untrusted and require normal evidence review. Coverage assessment uses bounded total excerpts/links rather than multiplying the full allowance by every result.

## Verification
476 unit and selected integration tests PASS in 19.09 seconds: work/verification/query-diversity-all.xml. Includes replan execution, usage accounting, budget/fencing, no-reliable-result handling, and document generation isolation. New regression test_recovery_queries_share_final_batch_with_unsearched_aspects verifies that a missing-aspect query executes alongside remaining planned work and both old/new evidence survive. Existing assertions were not weakened. Ruff PASS; mypy PASS for all three changed production modules. Initial test setup omitted a required SourcePlan field and was corrected; another run hit an inaccessible system pytest temp directory, then passed with a fresh project-local temp directory.

API, worker and web-provider rebuilt and healthy.

## Actual selected DeepSeek model observations
Model: deepseek-v4-flash at the configured api.deepseek.com service. Public questions only; no additional document export.
- University list: 41868181-b9d6-57f4-ab45-cc00dd2406d9, COMPLETED in 21.329 seconds. Prompt explicitly requested Web lookup because the unmodified stable-fact question can route DIRECT. Three complementary Chinese/English queries were planned. Initial insufficient evidence triggered fresh collection. Final answer lists ten island universities, distinguishes the south-bank extension and cites the Panyu government detail page. This is an observed single-run improvement, not a repeatability guarantee or a test of the original DIRECT response.
- Huangpu weekly forecast: 0cdbe6d7-d6d6-5c24-8508-bbef732b4d07, mechanically COMPLETED in 26.383 seconds after evidence-gap recovery. QUALITY NOT ACCEPTED: output substitutes Guangzhou city data for district data and turns a retrieval gap into an unsupported assertion that no independent forecast exists. It also uses a tide-page weather field as supplemental evidence. Scope/absence validation remains unreliable despite existing instructions; do not count this as screenshot acceptance.
- Prior district run 3357615f-c9d5-5d4e-91b8-12dbbe889d9e FAILED after real recovery because only one district day was supported. That failure is preserved in discovery-recovery-forecast.json.

Raw current outputs: work/verification/query-diversity-campus.json and query-diversity-forecast.json. To reproduce use scripts/validate_web_research.py with WEB_VALIDATION_QUESTION and the selected_settings configuration from validate_screenshot_quality.py, sequentially. The runner's terminal state is not a semantic quality oracle.

## Remaining scope
District-specific record discovery, scope/absence validation consistency, historical observations, product variant accuracy and repeated full screenshot acceptance remain incomplete. No new live RAG/DOCX claims are made in this patch; existing regression coverage passed and preceding authorized HR observations remain recorded in INCREMENTAL_ANSWER_RECOVERY_20260917.md. Full release gates were not executed; no project completion claim.
