# Nonexclusive source selection audit and repair

## Confirmed shared defects repaired
1. Historical packing: sources matching ISO-formatted bounds displaced other sources before generation, irrespective of entity. Removed this destructive filter while keeping date-aware ranking and source-count constraints.
2. Coverage shortcuts: historical table/date counting and authoritative-host shortcuts could stop discovery without confirming entity/scope. Production search now uses the bounded semantic coverage checkpoint instead. Legacy helper functions remain but do not control search termination.
3. Primary-source query narrowing: when any primary candidate existed, all other candidates disappeared. Removed the hard narrowing; ranking and explicit source permissions still apply.
4. Generation shortlist: most answer types only exposed five rank-selected candidates. All web answer types now offer up to 16 bounded previews to the existing semantic selector. Authority-based bypass removed. Selected originals are repacked with the full generation text budget.
5. Engine failures: HTTP 200 with empty results and unresponsive_engines was treated as normal absence. It now indicates backend failure. Empty auxiliary hint lists no longer count as successful engine searches; valid partial results remain usable.

## Observed replay
Original Baiyun task 59d1ef56-3461-546e-9ff0-ac876f9368c7 contained Baiyun sources but packing returned only two Huadu Weather Underground records. After the change, Baiyun source candidates survive. Artifacts: work/verification/baiyun-scope-audit.json and baiyun-packing-after.json. This verifies selection loss, not completeness of the underlying Baiyun pages.

## Tests and deployment
488 tests passed in 19.23 seconds (all unit tests plus selected fencing, replan, terminal handling, usage and budgets); work/verification/nonexclusive-final.xml. Ruff and mypy passed. API/worker and web-provider rebuilt and healthy.
Existing tests asserting automatic stopping or exclusive dated selection were updated to require semantic coverage and retention of complementary records. Date-first ordering, one-source budget ordering, independent-source minima, missing-day fetch cases and quote integrity remain tested. New generic Alpha/Beta record test detects date-format-driven wrong-entity exclusion. Added unavailable-engine and partial-success preservation tests.

## Live acceptance - incomplete, failures retained
- 3dcde80e-7b7b-5b24-9bfe-48dbe0632cb0: Baiyun FAILED, 31.537 seconds, 52,128 tokens. Model now saw Baiyun facts, but mixed monthly statistics with daily weather and contradictory advice. Revision exhausted budget. NOT a quality pass.
- 72c2c58a-041a-5e29-81f7-a57049ac9275: campus FAILED, 10.284 seconds, zero evidence. Separate instrumented query returned 4 raw results and 2 final candidates, while Brave reported rate limits, Google CAPTCHA, Sogou crash; subsequent variants returned zero. This proves upstream degradation exists, but is not an exact replay of every query from that task. Artifact: nonexclusive-campus-pipeline.json.
- c090d3c5-f41a-5e05-b564-9e52b9efaf65: original authorized DOCX summary COMPLETED, 5.128 seconds, live acceptance checks passed.
- Final engine failure classification is covered by tests; no additional billable live acceptance run after that last provider-only change.

## Still open
The system still has source-specific URL adapters and optional hints (weather, officeholders, sports); they were not all removed. Their existence is not proof of correct scope and the removed shortcuts no longer permit them to decide completeness. Forecast same-resource representation selection, cache key result-limit omission, parser/table layout fidelity, source currency, repair budget allocation, and truthful partial/failure delivery remain audit/repair work. Do not equate a record containing required dates or a known hostname with a source answering the requested entity. Overall screenshot acceptance and release gates remain INCOMPLETE.
