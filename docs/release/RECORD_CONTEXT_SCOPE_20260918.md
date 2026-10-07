# Record context and scope integrity repair — 2026-09-18

## Implemented path

This iteration changes evidence preparation before generation, plus the semantic acceptance contract. No publisher, city, station, expected answer, or website path was added to production logic. It preserves the frozen architecture, existing quote/hash checks, attempt fencing and budget limits.

- `apps/web_provider/main.py`: the existing HTMLParser now serializes table rows with cell boundaries rather than separating every cell into unrelated lines. Empty cell positions and original units are retained. Explicit rowspan/colspan attributes are rendered as layout annotations; no missing values are inferred or multiplied. Ordinary prose/navigation exclusion and link discovery remain.
- `runtime/evidence_records.py`: groups requested dated records with their own source section and headings. A later year/entity section is not appended to the preceding record. Groups are ranked as units and only whole context-plus-row bundles that fit the cap are emitted. Different groups containing the same date keep separate contexts. This is generic source text processing, not a full DOM or table-layout reconstruction engine.
- `runtime/research_scope.py`: a four-digit numeric cell after records begin can no longer change the inherited table year. Source year ambiguity and invalid dates remain unresolved, not guessed from the user question or clock.
- `capabilities/builtins.py`: both generic web excerpts and dated forecast records use the record packer before legacy fallbacks. Generation, selection and semantic review receive record_coverage; the audit stores the same manifest with excerpt hashes. It distinguishes records in the source from complete records actually retained. Counts only establish textual date matches, not entity relevance. The prompt binds each row to its section/date/units and prohibits treating aggregates or adjacent entities as target records. Existing non-record fallbacks remain.
- `runtime/answer_coverage.py`: review items now explicitly assess entity_alignment (MATCH/BROADER/DIFFERENT/UNVERIFIED) and period_alignment (MATCH/PARTIAL/DIFFERENT/UNVERIFIED). Live ANSWERED assessments must specify alignment; explicit broader/different/partial findings become missing requested coverage and use the existing recovery path. A disclaimer does not make an adjacent-scope record a match. This remains a model judgment, not a guarantee against misclassification.

## Tests and execution

- New `tests/unit/test_evidence_records.py` tests original cell values/units/empty cells/spans, same-date records for different branches, context-aware inclusion manifests, numeric cells resembling years, bounded whole-record packing and entity/period mismatch acceptance.
- Existing monthly-summary exclusion and four-style date-range tests initially found regressions. Production context packing was corrected; all original assertions retained.
- The real model HTTP fixture in `tests/integration/test_model_handler_fencing.py` now supplies explicit correct alignment fields. No required assertions were removed or relaxed.
- Final complete unit suite: **536 passed in 9.74s**, `work/verification/record-unit.xml`.
- Final contracts plus selected DB integrations: **42 passed in 17.88s**, `work/verification/record-integration.xml`. Includes model handler/fencing, candidate archive, replans, no-reliable-results terminal handling, usage accounting and budgets.
- Mypy passed for all five changed production modules; Ruff passed for checked production/test files.
- API, worker and web-provider rebuilt/replaced for the final version. Search cache is process-local and replaced with the provider process. No data volume removal or credential change.

## Live probes (actual provider and selected DeepSeek)

Before the explicit alignment contract, the following tasks technically COMPLETED but did not meet screenshot quality acceptance:

| Artifact under work/verification | Task | Observation |
| --- | --- | --- |
| structured-record-baiyun.json | ffe1e80c-41e2-570f-b0a8-87fd819c1e7e | 18.493s, 21,892 tokens. Partial dates and monthly data; internally contradictory missing-date wording. Quality FAIL despite COMPLETED. |
| structured-record-huangpu.json | bff6c9f6-18b2-5011-b9df-5976fa211504 | 13.273s, 21,806 tokens. Guangzhou city weekly temperatures substituted for district-specific coverage. Quality FAIL despite disclaimer and COMPLETED. |
| live-regressions/structured-record-docx.json | ddeb4061-1e1d-597b-96d4-9c113286991d | 5.194s, COMPLETED, original authorized DOCX quality_ok=true. |

These failed quality samples motivated the explicit alignment contract; they are not omitted from acceptance reporting. Final live outcomes are appended below.

## Limits and reproduction

HTML table row/cell boundaries are retained, but nested tables, malformed HTML, complex merged layouts and JavaScript-only cards are not comprehensively reconstructed. Legacy topic-aware fallbacks elsewhere are not removed by this iteration. Plain-text section recognition is heuristic; source identity and unit/date alignment still need semantic validation. A record manifest exposes truncation but is not itself evidence or an authorization source. Recovery still uses existing bounded retrieval/replanning; not every missing record is guaranteed to be fetched before budget exhaustion.

Run the full unit suite with a new workspace --basetemp. DB integrations require TEST_DATABASE_URL and REDIS_URL for the actual local PostgreSQL/Redis. Public probes use scripts/validate_web_research.py with selected_settings from scripts/validate_screenshot_quality.py; questions are unchanged, no source/answer injection. DOCX uses scripts/validate_live_regressions.py --phase structured-record-final --cases docx. Credentials must remain unprinted.

Full screenshot stability, release gates, kill/recovery, reconciliation and backup/restore are NOT established by these tests. PROJECT COMPLETE is not claimed.


## Follow-up: browse within the found page before another search

The runtime previously traversed only separately fetched recovery pages. Search-provider results already had opened content and source_links, but those links were not passed through the same traversal. After the first engine batch, the runtime now pools safe observed same-host links from engine-opened pages, ranks them using the resolved question, reads up to three within the existing deadline, and only then assesses coverage or runs another engine query. Read/failed URLs are tracked to prevent duplicate traversal. No guessed URL templates or extra site rules were added.

Three new cross-topic tests in tests/unit/test_generic_discovery_recovery.py verify detail reads occur before assessment, successful reads are included as Evidence, and a second engine query is unnecessary when the page provides the answer. Full units after this follow-up: 536 passed; the 42-contract/integration run precedes this final page-traversal change. Ruff and mypy passed. API/worker rebuilt and deployed.

Alignment-contract live outcomes before page traversal:
- structured-record-final-baiyun.json: task 4f13cb67-bc85-5749-9ce7-5761007156a7 FAILED, 38.658s, 51,342 tokens. Real missing period data triggered replanning; it was not accepted as a complete week.
- structured-record-final-huangpu.json: task e03301fb-3f34-51c8-8987-e4492efab2fd COMPLETED, 9.217s, 15,127 tokens. Full seven dates for the district from a 2345 source. The answer notes a separator interpretation on the last date; numerical interpretation/source quality still require caution, not a blanket high-quality PASS.
- live-regressions/structured-record-final-docx.json: task bee76027-bd56-5a01-9985-32ad3890564c COMPLETED, 5.146s, quality_ok=true.

Final page-first probe: structured-record-page-first-baiyun.json, task fc4e01b8-e619-5714-b656-87f0575e9179 FAILED in 35.555s, 52,249 tokens. Page links were available, but requested historical coverage and final review still did not complete within allowance. The reviewer also conflated visible date links with available record values in its critique; this remains a semantic distinction needing improvement. Page-traversal unit PASS does not establish end-to-end screenshot acceptance.
