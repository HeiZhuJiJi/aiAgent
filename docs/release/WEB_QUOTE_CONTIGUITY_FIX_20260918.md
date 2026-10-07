# Quote continuity fix - 2026-09-18

## Actual screenshot task diagnosis
Task 782838b2-e799-578f-8eaa-89f5da9c2a66 asked for Dongguan weather last week, resolved to 2026-09-07 through 2026-09-13. Collection succeeded. Both synthesis attempts failed after two generation calls each with a non-verbatim support quote. The one allowed replan replaced synthesis with unchanged evidence; a subsequent replan hit the replan budget. This was NOT a token-budget failure. No semantic answer assessment was reached. Captured evidence included wrong-period and wrong-place leads as well as thin historical snippets.
Artifact: work/verification/latest-user-history-failure.json. Old logs do not retain rejected quote text, so the concrete code defect below cannot be asserted as the sole cause of that specific failure.

## Reproduced shared defect and fix
_best_verbatim_quote filtered out source lines shorter than four characters and stitched the remaining lines. This produced a purported verbatim quote absent from the original, especially for short table cells. New regression failed before the fix and passed afterward. Short cells are now retained. Strict quote matching is unchanged. Integrity errors now identify claim/support indexes and evidence ID, with instructions to copy a continuous span rather than concatenate or translate it.
Real probes exposed unsupported negative-event claims. Generation/review instructions now distinguish no mention from no occurrence and remove unrequested unsupported negative conclusions instead of inventing extra retrieval requirements.

## Verification
485 regression tests passed in 19.65 seconds: all unit tests and selected fencing, replan, terminal handling, usage and budget integrations. work/verification/quote-contiguity.xml.
After final prompt clarification: 59 targeted tests passed in 3.13 seconds. Ruff passed; mypy passed before the final prompt-only edit. No required tests weakened; no budget caps increased. API and worker rebuilt and healthy.

## Actual original-question runs
- ff7bf661-227e-5778-b890-537ebb53dd9b: COMPLETED, 34.505 seconds, 31,274 tokens. Initial sources lacked history; actual collection replan found a dated seven-day record. Numeric rows match captured 2345 source. Additional negative-event wording was overconfident and prompted final clarification.
- ac0d26b0-6ffc-58c3-a021-7ba309e1a980: COMPLETED, 28.437 seconds, 45,695 tokens. Actual collection replan found seven-day history. Same negative-event limitation observed.
- 6a778efd-43ea-5675-b9c2-62022ba1cf8f: final prompt version COMPLETED, 18.357 seconds, 22,445 tokens. Seven dated rows with source attribution; no unsupported no-extreme-weather claim. Different provider source (IP.cn) than earlier runs; this is source-grounded output, not an independent meteorological truth guarantee. Final sample did not require replan.
Artifacts: work/verification/quote-contiguity-history-{1,2,final}.json.

## Limits
Three original-question runs completed; the first two contain wording flaws recorded above. This is evidence of progress, not proof that all web searches are stable. All screenshot topics, partial-result delivery and release gates remain outside this completed change. No PROJECT COMPLETE claim.
