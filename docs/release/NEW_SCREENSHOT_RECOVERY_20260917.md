# Follow-up screenshot failures — 2026-09-17

**Subsequent direction change:** The user rejected example-specific fixes. See [generation chain audit](WEB_GENERATION_CHAIN_AUDIT_20260917.md). The NWS endpoint and invalid-station rewrites described below were subsequently removed from the working tree; earlier live results describe the earlier deployed implementation, not acceptance of the current generic changes.

This report supersedes any implication that the earlier screenshot passes establish general reliability. New real user turns exposed additional failures. Full release gates remain unexecuted: RELEASE INCOMPLETE.

## Scope and evidence

User cases: 下周迈阿密的天气怎么样; 上周五的东莞的天气怎么样; RAG项目说明.docx + 帮我总结一下这个文件. Validation uses the actual screenshot-selected deepseek-v4-flash endpoint, real search/fetch, Temporal and PostgreSQL. The user explicitly authorized this DOCX transmission. User additionally clarified that sources may be in any language; final answers should be Chinese.

Original failures are captured in work/verification/live-conversation-failures.json. Every later failed live run is retained in work/verification/live-regressions; they are not passes. The fixtures and source URLs used for verification do not supply production answers.

## Implementation and tests

- capabilities/builtins.py: selected-document coverage uses the original user request instead of injected document-version identifiers. storage/knowledge.py: bounded short-document summaries read all chunks when within the 32,000-character/200-chunk ceiling; long documents retain bounded coverage metadata. Tests: test_knowledge.py and real DOCX run.
- apps/web_provider/main.py: all search requests use automatic source language; official NWS point pages are fetched as their corresponding JSON metric endpoint using discovered coordinates, preserving timestamp/value alignment and source hashes/URLs. No fixed city coordinates, dates or answers are supplied by production code. Tests: test_web_provider.py.
- capabilities/builtins.py: requested date rows take precedence over navigation and current observations; single-day historical records are selected over unrelated weather widgets. Invalid Weather Underground station URLs may resolve through the same source city directory. User-facing answer item bounds do not incorrectly count each atomic fact as a separate answer. Tests: test_bounded_qa_fast_paths.py.
- capabilities/builtins.py and runtime/reviewer.py: normalize equivalent measurement whitespace; missing-coverage date notes are distinguished from factual measurements, while claim dates, values, units, verbatim quotes and Evidence authority remain checked. Tests retain negative cases for changed values/units and out-of-scope dates.
- capabilities/builtins.py: discovery has a 26-second internal deadline, a 12-second query allowance and bounded page reads, preserving completed retrieval results when further discovery expires. No runtime fencing or frozen budget is bypassed.
- apps/web/src/components/chat-page.tsx: new WEB requests receive a 90-second wall-clock allowance to permit one full recovery cycle; unchanged cost/token/call/replan caps. Existing task budgets are not mutated. Validation runner uses the same value. Generic validation notices no longer misidentify attachment failures as web failures. Frontend test checks the 90-second submitted goal.

## Measured validation

- 454 backend unit/integration checks passed in 18.45 seconds before the final provider-only forecast change: work/verification/new-screenshot-final-regression.xml. This includes real PostgreSQL knowledge, replan, budget and terminal-outcome tests.
- After the final provider change: full unit suite 414 PASS in 6.96 s, work/verification/structured-first-unit.xml; scoped Ruff/mypy passed.
- Frontend: 22 tests passed; after adding the budget assertion, 20 chat-page tests passed, plus TypeScript checking. Web image built successfully.
- API, worker, web-provider and web rebuilt and health-checked.
- DOCX: task 4552b60a-b4e1-557f-9d6a-cf731a5e336b COMPLETED, 5.136 s; first_fixed-docx.json. Answer covers the local PDF RAG goal, retrieval/reranking, Ollama, prerequisites and limitations, with original filename citation. Follow-up scope detachment also verified.
- Historical weather: task 7ffbd465-2d8a-59fd-b091-d73ace22fed4 COMPLETED, 20.336 s; recovery_window-dongguan_history.json. Checked against the actual 9/11/2026 row: high/low 88/75 °F, precipitation 0 in, wind 7 mph. Answer clearly states that the record does not label a weather condition; it does not invent sunny/rainy conditions.
- Miami forecast: task 98d7f395-c6f2-54a4-92a9-faaef317fde1 COMPLETED, 34.489 s; structured_first-miami_future.json. Answer manually compared with the official metric period records: September 21 high/low 31/26°C, September 22 high/low 32/26°C, September 23 high 32°C, precipitation probability 80%. The answer explicitly limits coverage to September 21–23 and does not invent the remaining days. Earlier failed runs remain failures.

## Reproduction and limits

Run `.venv312/Scripts/python.exe scripts/validate_live_regressions.py --phase reproduce --cases miami_future dongguan_history docx` with local services healthy and the authorized DOCX at its original local path. Runner uses selected model configuration without printing credentials and saves answers plus source evidence. Manually inspect date/value/source alignment in addition to mechanical checks. Weather date oracles reflect 2026-09-17 and must be refreshed for later days.

Search engines exhibited CAPTCHA, connection failures and an upstream Sogou parsing crash during these runs. Direct public sources mitigate some failures but do not establish a general uptime guarantee. Forecast horizons may cover only part of the requested future week; useful supported partial answers must explicitly mark uncovered days. Browser interaction, all master release gates, worker kill/recovery and backup/restore were not rerun. No whole-project completion claim is made.

## Repeated-run failures found during manual acceptance

`confirmed-miami_future.json` failed because compact coverage wording still triggered numeric validation. `confirmed-dongguan_history.json` mechanically passed but manual review REJECTED it: monthly maximum/minimum wind statistics were presented alongside the September 11 daily observation. Neither repeat counts as a quality pass. Subsequent changes normalize compact missing-date notes and isolate daily table rows even when the entire source fits the context budget. The historical runner now additionally rejects numbers absent from the requested daily row. Final repeated acceptance remains pending below.
