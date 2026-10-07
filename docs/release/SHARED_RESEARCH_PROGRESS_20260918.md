# Shared research progress and review diagnostics

## Scope and implementation
Generic web research change; no new dependencies, source allowlists, question-specific answers, or extra model calls. The existing frozen user request and answer contract remain the scope authority.

capabilities/builtins.py now loads the latest completed typed model.validator.assessed record for the same workspace/task and model.structured_output capability. Search coverage assessment, generation and final quality review receive recovery hints containing previous decision, open gaps, limitations to recheck and critique. Hints explicitly require current evidence and do not establish facts, permissions or a changed scope. Existing supported drafts still require matching source IDs/hashes/quotes. Document/RAG generation does not receive the new web progress packet.

Quality audit records include the IDs, character lengths and SHA-256 hashes of the actual reviewed excerpts. Writes retain the task lock and attempt fence. Different assessments within one operation now have distinct content-derived audit IDs; identical replay stays idempotent. Previously operation-only deduplication could discard the repaired second assessment and retain stale gaps.

This is a bounded projection of the latest assessment, not a complete per-fact evidence ledger. It does not yet provide a full search/fetch/filter event timeline or an explicit evidence-ID mapping for every closed gap. Semantic gap closure still depends on model judgment.

## Tests and deployment
477 unit and selected integration checks PASS in 19.25 seconds (work/verification/shared-progress-all.xml). Ruff and mypy PASS. Tests use inventory and release-date examples rather than screenshot-specific rules. They verify identical generation/review progress payloads, workspace/task separation, no new state in document generation, exact reviewed excerpt fingerprints, latest revision visibility and idempotent replay. API/worker rebuilt and healthy; provider behavior not changed in this patch.

## Live selected-model observations
Actual configured deepseek-v4-flash, public questions only:
- d691d64d-e2dc-5277-8486-74adfd5b8853: weekly district forecast, COMPLETED 14.327 seconds without outer replan. Answer contains six days and a disclosed seventh-day gap, citing district/old-district and city sources. This run does not exercise shared recovery and is NOT counted as a full semantic PASS: exact locality attribution and publication-horizon assertions remain to be independently checked. A challenge page also remained in the collected source set.
- da126921-bdb4-5616-8d91-59f3c37692f8: explicit Web university-list question, FAILED 24.392 seconds after replan. Sources still lacked the full list; two quality assessments consistently report the gap. Read-only DB verification shows both records persisted with reviewed manifests of 2 then 4 excerpts. Prior successful run of the same explicit-Web question remains recorded, so retrieval is demonstrably not stable. Do not claim this patch preserves every prior live success.

Raw outputs: work/verification/shared-progress-forecast.json and shared-progress-campus.json. Reproduce with validate_web_research.py, WEB_VALIDATION_QUESTION and selected_settings from validate_screenshot_quality.py. Mechanical COMPLETED is not a semantic-quality oracle. A read-only diagnostic initially used a nonexistent audit table name; rerun through AuditRow succeeded without database mutation.

## Remaining work
Full acquisition diagnostics, source availability and candidate selection reliability, precise scope attribution, evidence-backed per-gap closure, and repeated screenshot quality evaluation remain incomplete. No new live document export; existing authorized document observations remain in earlier reports. Full release gates were not run. No project-completion claim.
