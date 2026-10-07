# Fixed recovery acceptance — 2026-09-18

Questions frozen before implementation. Numerical live answers are not hardcoded. COMPLETED is not quality PASS. Inspect sources, scope, dates and missing requested facts.

- **city_today (web)**: 广州今天的天气怎么样 — 明确日期、城市范围、天气和温度，不能用发布日期缺失否定有效数据日期。
- **district_today (web)**: 广州黄埔今天的天气怎么样 — 保留区级对象；回归已经成功的问题。
- **miami_tomorrow (web)**: 迈阿密明天的天气怎么样 — 明确当地日期；从带表头和发布时间的证据读取目标日期。
- **district_week (web)**: 广州黄埔未来一周的天气怎么样 — 覆盖请求周期，保留来源差异，不能用一天代替一周。
- **history_week (web)**: 广州白云上周的天气怎么样 — 历史记录覆盖请求周期；不使用未来预报冒充实况。
- **campus (web)**: 广州大学城有哪些学校呢 — 完整名单，区分范围和时期；追溯原始来源。
- **public_science (web)**: 请联网查一下，NASA如何解释日食和月食的区别？用中文说明 — 非天气泛化；使用英文原始来源，中文解释。
- **chat (chat)**: 用简单的例子解释一下什么是RAG — 不无故联网，不编造产品身份。
- **leave (rag)**: 我想请假三天，流程是什么？ — 只依据已授权HR文件，保留条件、流程和引用。
- **docx (attachment)**: 帮我总结一下这个文件 — 原始已授权RAG项目说明.docx，总结目标、流程、条件与限制。

Repeat city_today, miami_tomorrow and one previously successful question after fixes. Record failed runs without replacing them. Automated regressions also cover source truncation, tables, metadata time distinctions, scoped reread, material repair vs discovery, and budget reservation.

## Implemented corrections

- `capabilities/builtins.py`: keep full retained fetch text (the fetch layer still bounds the remote response); preserve multilingual query focus during final generation packing and local reread. Previously the first 12,000 characters were retained and final repacking lost the translated queries.
- `runtime/evidence_records.py`: retain complete serialized tables with source context/units; let relevant explanatory prose compete with matching navigation/list labels.
- `capabilities/builtins.py`: allocate context to an indivisible table before redundant short previews, while preserving required source counts/publishers; total generation character cap remains bounded.
- Local reread now replaces Evidence inside the actual answer-repair request. Updated excerpts must pass a fresh semantic review; changed text never grants factual acceptance or permission.
- Optional source selection checks available token allowance; repeated generation still uses canonical task-budget accounting and fencing.
- `api/app.py`: explicit research explanations and forced Web mode now reach the existing query planner instead of being suppressed by direct-explanation routing. Existing single-fact/direct-chat fast paths remain covered.
- Generation/review now explicitly distinguishes child/parent measurement scope, observations/reanalysis/forecast, and conflicting lists versus a confirmed union. These are generic instructions, not named-place exceptions.
- User-authorized Web/MIXED defaults: 80,000 tokens and 120 seconds (previously 50,000/90). Other task defaults remain unchanged. `validate_web_research.py` uses the same values as the UI.

## Regression evidence

- Final unit suite: **568 passed** (`work/verification/recovery-retention-unit.xml`).
- Chat-page suite: **20 passed**, including actual Web task budget arguments.
- Added real-DB/model-transport test verifies that a reread-only fact appears in the second generation request and in a subsequent fresh review (four model exchanges, no acceptance shortcut).
- Mypy: three changed production files pass. Ruff: changed production/test files pass.
- Intermediate failures were retained and repaired in implementation: explicit requested-day and monthly-summary exclusion assertions were not weakened.
- No topic/city/provider-specific production branch was added in this iteration. Older specialized branches predate this iteration and are not claimed removed.

- Contracts and selected real-DB integrations: **45 passed** (`work/verification/recovery-final-db.xml`).
- Provider enrichment now retains fetched body rather than only a 5k/6k excerpt; the new regression retains a fact beyond 12k characters with consistent content hash. Remote fetch still caps response bytes at 750,000. Generation context remains separately bounded; retaining raw text does not send all raw text to the model.

## Real observations (not inferred from test passes)

All raw public runs are preserved as `work/verification/recovery-r{1,2,3,4,5}-<case>.json`; absent combinations mean not run. `scripts/validate_fixed_recovery.py --phase <new-name>` refuses to overwrite previous observations.

Round 1 (50k/90s): city/district today completed; Miami, district week, historical, campus and NASA failed. Round 2 (80k/120s plus fixes): city/district today, Miami and district week completed; historical and NASA failed. Campus completed with contradictory, poorly sourced membership and is quality **FAIL**. Round 3: historical completed using a community-level reanalysis record as district-wide history, so quality **FAIL** despite completion. Campus returned a plausible 10+2 roster but lacks the fixed acceptance requirement of a primary institutional source. NASA failed; trace revealed its explicit explanation request had bypassed query planning. These observations motivated further changes; they are not erased by later runs.

Ordinary chat: DIRECT, 2.46s, substantive RAG explanation and no unsolicited Web request (`recovery-r1-chat.json`).
Authorized HR question: COMPLETED in 14.385s; three-day approval boundary, leave-type notice, application details and return/cancellation procedure with HR-02 citations (`recovery-r1-rag.jsonl`).
Original DOCX: current rerun blocked by automatic approval; awaiting the specific external-transfer confirmation. Earlier rounds' successful DOCX observations are not reused as this round's PASS.

## Status

Acceptance remains **INCOMPLETE** until the final real observations below meet the frozen criteria. Automated regression success is not screenshot quality or full-release acceptance. No PROJECT COMPLETE claim.

## Fourth-round observations

- Miami repeated: COMPLETED in 17.377s (previous 22.369s), correct requested date with forecast values and cited pages.
- NASA after route fix: COMPLETED in 30.462s, three English query variants, official NASA evidence, Chinese comparison. Earlier runs had one Chinese query and failed.
- District today: COMPLETED 12.311s; core date/district/temperature present, additional hourly detail needs care.
- City today: COMPLETED but quality FAIL: response admits missing date/value binding and gives unrelated multiday values. This exposed provider-side irreversible excerpting; not counted as a pass.
- District week and historical: FAILED. Campus: CAPABILITY_UNAVAILABLE (`Web search rejected`), not an evidence-verification error. No precise upstream cause inferred from this generic error alone.

Final API, worker, web and web-provider health checks passed after deployment. New UI tasks use the new budget; previously created task budgets are immutable snapshots and are not retroactively changed.

## Fifth-round observations and remaining work

After deploying provider-side original retention, city_today FAILED (29.542s, 43,266 tokens), district_week FAILED (33.595s, 81,476 tokens), history_week FAILED (44.866s), campus COMPLETED (14.454s) with a 10+2 roster but only secondary/aggregation citations. Campus therefore does not meet the frozen primary-source criterion. Original evidence and all failed attempts are retained.

City's available sources/reviews still focused on surrounding-location records; the failure is not merely a low token cap. The week answer exhausted the increased budget while revising missing weather-detail wording. Scope correctness and review efficiency remain unresolved. Higher allowance improves some cases but is not sufficient for reliable end-to-end quality. There is no demonstrated all-case stable pass.

Practical reproduction: refresh the web UI, start a new task (existing budget snapshots stay unchanged); use the frozen questions or run `scripts/validate_fixed_recovery.py --phase <unused-round-name>`. Selected model configuration is resolved from the real app, never replaced with a fake provider. Inspect each result's answer, source bodies and failed attempts, not only state.

Additional source-retention regression: `test_enrichment_retains_original_beyond_preview_for_later_reread`; final units now 568/568. DB tests remain 45/45, UI 20/20. Main app services healthy. Original-DOCX current-run acceptance is still pending its separate transfer approval; HR transfer approval was received and its real run passed.
