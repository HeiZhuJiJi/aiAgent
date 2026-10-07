# Recovery context optimization - 2026-09-18

## Implemented
1. Semantic answer review receives full answer text, ordered claim text and cited source IDs without replaying each duplicate supporting quote. Original candidates, quote-integrity validation, source evidence and audit records are preserved.
2. Generation no longer serializes answer_contract and source_plan twice inside and outside research_plan.
3. Reviewer instructions check all core coverage gaps before choosing a wording revision; actual searchable gaps request collection immediately.
4. A persisted recovery search receives up to 32 URLs from existing task-scoped WEB evidence. Coverage can see them and proposed direct fetches of those URLs are skipped. Search results may still contain those URLs; this is not a global URL ban or permission change. New detail links and revised queries remain eligible.

## Verification
- 484 tests passed in 20.11 seconds: all unit tests plus model handler fencing, replans, terminal no-results, replan usage and budgets. work/verification/recovery-context-final.xml.
- Ruff and mypy passed for changed production code. Final type-only JsonValue cast does not change behavior.
- New regression verifies complete answer/claim order/source IDs and unchanged original quotes while shrinking the reviewer payload.
- New search-handler regression verifies a previously collected direct URL is not fetched again and a fresh recovery query still returns a new record.
- API and worker rebuilt/restarted; health checks passed. No existing test weakened, no budget caps increased, no screenshot-specific answer/source rule added.

## Actual selected-model probes (all retained)
- 4bca94f3-382e-5937-bc53-ad37f156438e: weather COMPLETED, 28.529 seconds, 44,477 tokens. Six supported days plus explicit seventh-day gap; answer-repair recovery occurred after verbatim-quote rejection. Not complete seven-day acceptance, not proof of successful gap search.
- f3e2d194-bf31-54b7-8984-3a236f7721ca: intermediate campus probe FAILED, 27.334 seconds, 50,505 tokens. Recovery repeated general homepages and citywide lists. This motivated prior-URL context propagation. Reviewer also proposed an over-specific missing roster, which remains a quality concern.
- 86917f4b-638b-59a0-9f28-2f89834e976a: final campus probe COMPLETED, 17.385 seconds, 16,517 tokens. Listed 12 schools and distinguished 10 on Xiaoguwei island from 2 on the south bank. Captured Panyu government source text supports this distinction. One page is dated 2023; the final wording could better attribute source currency. No runtime replan in this sample, so it does NOT establish causality for the new skip behavior or statistical stability.
- 00d344a2-45e3-5299-9ad9-932749c956f1: original authorized DOCX summary COMPLETED, 4.106 seconds; existing live acceptance checks passed, source citation present.

Artifacts: work/verification/review-context-live-{forecast,campus}.json; work/verification/recovery-context-final-campus.json; work/verification/live-regressions/*recovery-context-final*.

## Remaining acceptance
Full screenshot/stability acceptance remains INCOMPLETE. Forecast lacks one requested day; verbatim citation generation still sometimes needs repair; retrieval can drift from nested scope to parent scope; dynamic search results differ between probes. Keep failed cases visible. Next bounded work should target source relevance/currentness and gap-coverage recovery without fabricating assumed rosters or weakening integrity checks. No complete release-gate claim.
