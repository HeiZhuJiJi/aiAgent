# Web and RAG generation chain audit — 2026-09-17

## Finding

The normal generation path DOES send the question and retrieved evidence together to the selected model. It does NOT simply return search results. However, evidence is filtered and excerpted before generation, the model is asked for structured JSON, and multiple validation/review/output gates run afterwards. Several branches can bypass generation entirely.

## Verified implementation

- `apps/web/src/components/chat-page.tsx`: persists original_request, resolved_request, instruction and search query separately. The generation instruction is the context-resolved request, not necessarily the original text verbatim.
- `runtime/activities.py:_capability_arguments`: passes the stored instruction into model.structured_output; only defaults to the step objective when instruction is absent.
- `capabilities/builtins.py:_model_handler`: loads task Evidence, applies source plan, selects/excerpts evidence, then places instruction and evidence in the same user message of analysis_prompt.
- `models/gateway.py:ModelGateway.structured/chat`: appends a JSON schema instruction, serializes messages, and posts to chat/completions. Structured generation disables thinking and requests JSON for matching DeepSeek configurations.
- `capabilities/builtins.py:_analysis_evidence_payload`: web context starts with a five-source / 4,500-character allocation, divided among sources; historical and specialized paths can override excerpt selection/size. Provider enrichment already excerpts pages before this second stage. This is not full-page delivery.
- No Evidence, no allowed Evidence, REQUIRE_COMPLETE source shortfall, or unavailable generation time can produce a terminal/fallback result without a fresh generation call.
- After generation, schema, grounding, coverage, optional semantic quality, runtime review and final result publication gates can reject or replace the answer.

## Observed checks

- Read source from the RUNNING worker container: confirmed task Evidence load, question/evidence prompt fields, structured model call, HTTP messages serialization, excerpting and early no-evidence branch. No deployment was performed for this audit.
- Added `test_generation_http_request_contains_question_and_collected_evidence` for WEB and DOCUMENT: real PostgreSQL Evidence is loaded through the production model handler and production gateway; the HTTP boundary captures question, Evidence ID, locator and source text in the same request. Model responses are controlled through tests/fakes/ScriptedModel. This verifies transport, not live DeepSeek answer quality.
- 36 integration checks PASS (14.81 s): `work/verification/model-request-capture.xml`.
- 423 unit checks PASS (7.56 s): `work/verification/generic-recovery-unit.xml`.
- Ruff and mypy pass for builtins.py, replanner.py and provider main.py.

## Unreleased recovery changes in this follow-up

Answer revision now reports ANSWER_REPAIR_REQUIRED rather than EVIDENCE_INSUFFICIENT. Replanner must preserve collection dependencies and refine only failed synthesis steps for that cause. Failed structured generation no longer silently marks a generic refusal as valid. Numeric discrepancies confined to answer prose require semantic review rather than immediate rejection; atomic claims and citation checks remain strict. Provider-specific NWS endpoint rewriting and invalid-station URL rewriting introduced in the prior follow-up were removed. Valid JSON source structure is preserved.

The user explicitly approved replacing four tests for those withdrawn adapters with generic integrity assertions; other tests were preserved and new recovery/transport checks added. These changes are not yet deployed or accepted against live screenshot questions. Other domain-specific rules remain in the existing code; this is NOT a completed generic redesign or a whole-project/release completion claim.
