# Multi-engine candidate pooling — 2026-09-18

Scope: generic multi-engine discovery, source/query provenance, complementary model selection and preservation of recovery leads. Screenshot quality acceptance remains INCOMPLETE; not PROJECT COMPLETE.

## Existing behavior and defects

SearXNG already queries multiple configured enabled engines; adding a second fan-out layer would multiply requests without making suspended engines available. Its existing per-engine CAPTCHA/rate-limit suspension is retained. No engine is restarted or re-enabled to bypass suspension.

The provider normalized only the first bounded aggregate results and then sliced them to roughly twice the result count before selection. Engine provenance was lost. Repeated URLs lost query/engine associations. Runtime's first-come candidate archive and later page-link ranking could crowd out results from another engine or query before the model reviewed them.

## Implementation mapping

- `runtime/discovery_pool.py`: bounded round-robin pooling across discovery origins. Deduplication happens by URL before pool allocation. Engine agreement is not independent source corroboration.
- `apps/web_provider/main.py`: inspect at most 200 aggregate raw entries; normalize safe public URLs, merge duplicate engine labels, and retain a diverse pool of 40. Removed the secondary pre-review slice. SearchResult carries bounded engines/queries as discovery metadata; unselected candidate archive merges provenance and keeps the richer snippet. Existing relevance ranking, public-destination checks and fetch protections remain.
- `runtime/web_provider.py`: consumes candidate provenance and merges repeated URLs across calls. Bounded 80-candidate archive distributes space across actual query origins instead of first-response wins. Result-level metadata is excluded from factual output serialization. Candidate metadata is serialized under its Pydantic schema and retained for recovery.
- `capabilities/builtins.py`: archived candidates merge origins; engine candidates and untagged page links receive space before the 80-item cap, then a diverse 16-item candidate preview reaches the existing coverage/next-page model. The model is instructed to select complementary pages, read original bodies, treat engine count as discovery metadata only, and use available sources despite partial backend failures. Search backend diagnostics are included in this decision context. Existing successful evidence and missing-aspect recovery remain.
- Provider still opens a deterministic initial set for latency. The model additionally reviews the wider unselected pool and chooses pages to fetch through the existing candidate_urls path. This is not a claim that every initial page is selected by a model or that all returned pages are fetched.
- No topic/city/site-specific production rule, URL template, dependency, task budget increase, validator bypass or engine configuration change was added.

## Tests and measured results

- Full units: **560 passed in 10.17s**, `work/verification/multi-engine-unit.xml`.
- Contracts and selected actual database integrations: **44 passed in 17.97s**, `work/verification/multi-engine-db.xml` (candidate archival, model fencing/isolation, replans, terminal no-results, usage accounting and budget enforcement).
- Ruff passed modified production/tests; mypy passed four production files.
- New `test_web_provider.py` cases verify engine diversity survives a dominant first engine, duplicate URLs merge engines/queries while retaining content hashes, and repeated discoveries do not become multiple sources.
- New `test_web_candidate_recovery.py` cases verify origin metadata reaches model previews and one page's 100 navigation links cannot hide another engine's candidate.
- Existing required assertions retained. No tests skipped, removed or weakened.

## Actual live evidence

Artifacts under `work/verification/` use the original public questions and real local API/Temporal/provider/DeepSeek.

| Artifact | Task/result | Quality interpretation |
| --- | --- | --- |
| multi-engine-campus.json | 0eb45fdf-2008-56a9-bcf3-533c23307402, COMPLETED 16.341s | NOT PASS: partial roster, mixed historical/current scopes and AI search-summary citation. |
| multi-engine-huangpu.json | 4d8daa29-2b5a-503c-bf85-02fb212d9e8e, COMPLETED 18.327s | Seven forecast days with source attribution and differences. Single-run improvement, not stability proof. |
| multi-engine-baiyun.json | 1cc6369d-65bb-5252-a0c6-cd53d7e47198, FAILED 38.701s | Missing historical dates; recovery source context still insufficient. |
| live-regressions/multi-engine-docx.json | 311bc6ad-4461-552d-ac99-565dae82c490, COMPLETED 5.163s, quality_ok=true | Original authorized DOCX regression passed. |

`multi-engine-campus-discovery.json` and `multi-engine-huangpu-discovery.json` are actual web.search database outputs. They contain candidates attributed to 360search and sogou, three attempted queries, and 80 retained candidates. They preserve Google CAPTCHA/Brave rate-limit diagnostics; the forecast run also reports Sogou unexpected crash. These directly verify multi-engine provenance persisted through the runtime. Source access can vary between requests; a contributing engine may fail on a later query.

The three public probes and DOCX preceded the final navigation-flood allocation adjustment. The final unit and DB runs include it. Final-version roster probe is recorded below.

## Remaining limits

Upstream limits still reduce source coverage. Multi-engine aggregation alone does not fix synthesis treating an incomplete roster as complete, AI-generated summaries used as sources, or extraction/packing dropping some historical records. These remain explicit quality failures, not accepted because the task state is COMPLETED. Generic bounded pools can still omit useful candidates under high volume. The existing model coverage check has a finite budget and at most two checks; it cannot inspect every possible page. Prior topic-aware fallback code elsewhere was not removed in this bounded change.

API, worker and provider rebuilt/recreated; no data volumes removed. Final health snapshot is recorded separately. Full release gates, kill/recovery, response loss/reconciliation and backup/restore not executed. No full release PASS claimed.

Reproduce with `scripts/validate_web_research.py` using selected_settings from `scripts/validate_screenshot_quality.py`; preserve complete JSON outputs and audit/capability records. DB regression uses actual local TEST_DATABASE_URL and REDIS_URL, never printing credentials; use fresh --basetemp directories.


## Final-version observations

Final roster task `ad6ad674-87a6-5d24-95d3-cae937096fa4`, artifact `multi-engine-final-campus.json`, COMPLETED in 26.512s. It now lists ten first-phase and two second-phase schools separately. This improves completeness/scope over the previous partial list, but still cites AI search-summary pages and secondary aggregators; strict source-quality acceptance remains NOT PASS. No stability claim from one run. API, worker, web-provider, SearXNG, PostgreSQL and Temporal reported healthy after deployment; SearXNG process health does not establish every upstream engine is working.
