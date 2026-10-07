# ADR-003: Semantic grounding and deterministic citation integrity

## Context
Real screenshot tests show lexical date/number overlap rejects supported translations, source-relative dates and natural paraphrases. Repeated JSON repair exhausts the frozen budget. Conversely, numbers from unrelated rows can pass overlap checks.

## Conflict
The previous implementation treated literal fact-token presence as the final meaning-level authority. It cannot establish semantic support. The user explicitly requested a generic retrieval-plus-generation solution rather than per-example rules.

## Existing Decision
Task-scoped Evidence and Claims, schema enforcement, deterministic runtime review, snapshot/permission controls, fencing and budget accounting remain authoritative. No source can grant permission.

## Proposed Decision
For grounded chat, validate citation IDs and verbatim supporting spans deterministically; judge semantic support and question coverage using a separate model assessment. A successful assessment is recorded atomically with Claims under the current fence. The record binds the complete Claim digest and source-content hashes. Runtime review uses that exact task-scoped record instead of the lexical numeric heuristic only; it still enforces schema, deliverables, boundaries, hashes, citation locators and source integrity. Without a matching record, existing conservative review remains. Revised answers require a fresh assessment. Failed or unavailable assessments cannot grant semantic acceptance.

## Alternatives
More domain/website regexes: rejected as overfitting. Disable review: rejected. Treat an output as correct because generation completed: rejected. Always run search again: rejected because answer repair and missing facts are distinct failures.

## Consequences
One semantic assessment is required for grounded answers; it consumes the existing budget. It can still make mistakes, so live quality evaluation and exact recorded judgments remain necessary. No new production dependency, service or external permission is introduced.

## Migration
Existing audit storage supports the new immutable model.grounding.accepted action; no database or public schema migration. Existing Claims without attestations retain prior review. Replanned synthesis instructions preserve the original question and add the actual recovery objective.

## Required Tests
Transport includes question and Evidence; translated numeric expressions can pass semantic review; changed Claim/source/workspace or rejected assessment cannot reuse approval; tampered hashes and unknown citations still fail; usage and fencing remain enforced; live screenshot and cross-domain quality acceptance required. Release status remains incomplete until measured.


## Authorized partial scope clarification (2026-09-17)
The user explicitly authorized useful supported partial answers when the remainder is not yet published. Internal quality assessment now separates searchable `missing_aspects` from source-established `inherent_limitations`. Core coverage requires actual facts within the requested entity/time scope; an outside-period fact or a publication-horizon statement alone is not partial coverage. Existing citation/hash/unsupported-claim checks and searchable-gap replanning remain mandatory. Added a regression asserting accepted inherent limits and the exact error contract for searchable gaps.


## Incremental drafts (user-requested follow-up)
Supported in-scope claim fragments may be retained in the fenced validator audit as internal recovery drafts. Reuse requires task/workspace scope and unchanged source IDs/hashes/quotes. Drafts grant no final acceptance, permissions or authoritative Claim commits; the final merged answer is fully reviewed. Web is the initial application scope so document behavior remains stable. Tests cover rejected/changed-source claims, cross-workspace reuse, HTTP draft delivery and unchanged document input. This adds no schema migration or dependency.
