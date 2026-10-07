# Incremental answer recovery and new screenshot audit

## Scope
The user requested improvements without regressing completed behavior, then proposed forming a supported draft and searching only for missing facts. This change pilots incremental drafts in web research; document/RAG generation remains on its existing path.

## Observed baseline
Saved actual screenshot tasks and Evidence in work/verification/new-screenshot-baseline.json. The news task collected several dated official articles but cross-publisher ordering displaced them with incomplete index pages. The weekly forecast required eight inclusive dates. The university-list task lacked a usable list. The product-ingredients reply relied on a third-party compilation, with official pages about other product categories; no new product verification fix is claimed.

## Implementation and safeguards
- Web context satisfies the required independent publisher minimum, then retains distinct relevant reports from the same publisher. It does not lower the configured minimum.
- Planner explicitly uses N inclusive dates for an N-day interval.
- model.validator.assessed records an internal supported_draft under the existing task lock and attempt fence. Only explicitly supported claim indexes, excluding unsupported indexes, enter it. It is not final acceptance or an authoritative Claim commit.
- Recovery loads drafts only for the same workspace/task/capability. Every reused source ID, content hash, actual source hash and verbatim quote must still match current permitted Evidence. Missing/changed sources prevent reuse.
- Generation receives the supported draft plus new Evidence, retains useful facts, resolves conflicts and returns one coherent answer. Supporting sources omitted from ordinary ranking are added to its context. The entire final answer still undergoes normal semantic and deterministic review.
- This is an internal draft, not a new live partial-answer UI. Source acquisition can still fail; an empty or wrong-entity source set cannot yield a valid draft.

## Verification
473 unit/integration checks PASS (19.15 seconds), work/verification/incremental-draft-all.xml. Tests verify source-change rejection, workspace separation, draft delivery at the real HTTP generation boundary, and absence of draft input from the unchanged document path. Final concise supported-index prompt clarification followed; Ruff and mypy PASS.
Services rebuilt and healthy. No dependencies or frozen authorization/commit invariants changed.

Actual configured DeepSeek runs:
- Original HR movie: f64d7c39-59b9-5243-89f0-87de3c6dfbf9, COMPLETED, 11.235 s. Conditional personal/annual leave advice and source citations.
- Original HR summary: 1a792744-5106-5017-a06e-5a04d83c0ff8, COMPLETED, 5.137 s; unrelated follow-up detached document scope. Both acceptance checks PASS.
- News: 480f943c-a521-5f7e-b140-4dc384a1b781, COMPLETED, 22.357 s. Several dated specific events replace most undated headline fragments. This is a single observed outcome, not a repeatability guarantee.
- University list: 9fb6576a-db28-582d-9ba2-6ab009935b1a, FAILED, 18.296 s; no usable Evidence returned.
- Weekly district weather: 05b363d0-6e4b-5ebb-98ed-1919c134707f, FAILED, 32.460 s. Date interval now seven dates, but sources still lack requested district records and recovery eventually lacks assessment budget.
- Initial custom probe had a Windows pipe encoding error before a meaningful task; rerun used ASCII Unicode escapes to send exact original Chinese questions. Do not count the encoding probe as a product quality result.

## Remaining work
Improve entity-specific discovery, open actual records rather than generic homepages, distinguish current office-holder evidence from historical appearances, verify variant-specific product labels rather than general third-party compilations, and make recovery context/budget efficient. Full screenshot acceptance remains INCOMPLETE. Existing successful chat, new DOCX and onboarding tasks were observed in the user baseline; their exact documents were not resent in this turn. Original authorized HR regression is measured separately.
