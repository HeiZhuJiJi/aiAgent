# Web evidence loss repair - 2026-09-18

## Changes
- Discovery retains bounded partial-entity leads alongside strict matches. These are retrieval candidates, not approved factual support. Final semantic review remains required.
- Resource identity preserves the full path and query. Named article aliases additionally require an identical description; numeric IDs alone no longer collapse distinct collections.
- Complete sources that fit the excerpt budget are preserved verbatim.
- Short nonempty table/list cells count toward source detail rather than being discarded by a 20-character prose threshold.
- Forecast packing no longer deletes other sources just because their date notation differs. Existing same-resource structured-representation preference and minimum-source requirements are preserved.
- Web generation and semantic review explicitly distinguish source-specific omissions from global absence and integrate complementary facts with attribution.

## Verification
Before production edits: five added regression tests gave four failures and one pass; the partial-name candidate fixture was then strengthened with a neutral hostname.
After shared logic fixes: 482 tests passed in 19.58 seconds (all unit tests plus model-handler fencing, replans, no-results terminal handling, replan usage accounting, budgets). Artifact: work/verification/evidence-loss-fix.xml.
After final prompt change: 56 relevant generation/recovery tests passed in 2.83 seconds. Ruff passed; mypy passed for both modified production modules before final prompt-only changes.
No existing required tests were removed or weakened. API, worker, and web-provider were rebuilt and restarted; health checks passed.

## Real model results - not blanket acceptance
Model: selected deepseek-v4-flash, actual configured service.
- c74e9219-4839-544e-adf5-4e8bbd679e3c: weather COMPLETED in 23.4 seconds; district forecast delivered, but contradictory missing-day wording. This triggered the final prompt clarification.
- 3af93f06-c31a-52ff-a759-2ebb38e59570: final weather probe FAILED in 28.464 seconds. Nine evidence records; reviewer supported Sep 18-23 and requested Sep 24 supplementation. Settled token total 51,487 against a nominal 50,000-token test budget; no successful subsequent search attempt is present. This is NOT a quality pass; inspect replan/budget scheduling next rather than relax grounding.
- 31ad637f-286d-56f5-9c20-66d05371c8d2: explicit web campus probe COMPLETED in 9.249 seconds. Answer supplied a phased 13-school list from secondary sources, with dated-name/context caveats. Current roster correctness is not independently established; completion is not semantic acceptance.
- c0a6f32c-3023-5673-9f05-7c41e86b8e51: previously authorized original DOCX summary COMPLETED in 5.138 seconds; existing live acceptance checks passed, useful summary and original source citation present.
Artifacts: work/verification/evidence-loss-live-*.json; work/verification/live-regressions/*evidence-loss-fix*.

## Remaining work
Prioritize preserving budget for targeted gap search before repeated answer repair; inspect actual retry reasons rather than increase caps blindly. Also audit current-versus-historical list scope. These changes fix reproduced information-loss defects, not all screenshot acceptance requirements. Full release gates and all original HR/chat live cases were not rerun in this change. PROJECT COMPLETE is not claimed.
