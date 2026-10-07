# Review materiality repair - 2026-09-18

## Diagnosis

Actual task 516d5919-db36-56b3-be43-8a317daf7279 had two review judgments (saved in work/verification/wording-review-audit.json):
- First revision had a material scope concern: the answer compared district 35C with city 34C as basically consistent, potentially confusing which location the numbers describe.
- The next judgment acknowledged supported core facts and said the answer was overall acceptable, yet returned REVISE and acceptable=false for a clearer limitation sentence. Runtime honored the rejection and exhausted recovery budget.
This was not proof that every wording correction is unnecessary. The missing distinction was required correction versus optional polish.

## Implementation

src/enterprise_agent/capabilities/builtins.py:
- AnswerQualityAssessment adds backward-compatible revision_severity (NONE, STYLE_ONLY, MATERIAL) and blocking_issue.
- Reviewer prompt reserves MATERIAL for factual/scope/unsupported/essential-gap/material-clarity problems. Optional wording preferences must use PASS/PARTIAL_ACCEPT with positive acceptance checks; optional advice can remain in critique.
- _review_consistency_error rejects contradictory nonblocking assessments. Unsupported claims, missing facts, wrong language/depth, unacceptable answers or blocking issues cannot be accepted under STYLE_ONLY. No string matching of critique or automatic override of a rejection.
- A contradictory structured assessment retries the review itself through the existing gateway (up to two attempts), not the answer generator. Review output allowance increases from 384 to 768 tokens to fit the explicit judgment. The existing task budget/time limits and usage accounting still apply.
- Legacy assessments without severity retain their previous semantics. Grounding/citation checks and material revision/replan gates remain enabled.

## Verification

New tests: tests/unit/test_review_materiality.py (optional advice passes; each material failure blocks a style label; actual scope correction still revises; legacy rejection is not auto-accepted). Existing assertions unchanged. All unit tests and selected archive/fencing/replanning/terminal/usage/budget integrations: 512 passed in 20.32s; work/verification/review-materiality-tests.xml. Ruff and mypy passed. API and worker rebuilt/restarted.

Live result will be retained in work/verification/review-materiality-live-baiyun.json. Overall screenshot reliability and release gates are not declared complete by this scoped repair. Upstream retrieval, partial historical coverage and other review interpretation errors remain possible.
## Actual live result after this repair

Task 1e0253ba-6de2-52da-9f5f-85d6900e2375 still FAILED, 31.444s, 53925 tokens. This time the review explicitly returned MATERIAL and a concrete blocking_issue: monthly forecast/statistics were compared with daily historical observations. That is a substantive objection, not optional polishing. The task spent 32057 tokens across four search-step LLM calls before generation/review/revision and ran out of budget before final assessment. The review also incorrectly suggested a weekday correction (2026-09-07 is Monday); this is a remaining reviewer accuracy issue, not resolved by materiality classification. No acceptance bypass was added. Artifact: work/verification/review-materiality-live-baiyun.json. Overall live stability remains INCOMPLETE.
