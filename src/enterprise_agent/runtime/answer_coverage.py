"""Requested coverage and source-established limitations, independent of topic."""
import re
from typing import Literal
from uuid import UUID

from pydantic import Field

from enterprise_agent.contracts.domain import Contract

# One requirement may depend on every source admitted to an answer.
MAX_ANSWER_EVIDENCE = 16


class AnswerCoverageItem(Contract):
    requirement: str = Field(min_length=1, max_length=240)
    status: Literal['ANSWERED', 'MISSING_EVIDENCE', 'OMITTED_FROM_ANSWER', 'UNAVAILABLE']
    entity_alignment: Literal['MATCH', 'BROADER', 'DIFFERENT', 'UNVERIFIED'] = 'UNVERIFIED'
    period_alignment: Literal['MATCH', 'PARTIAL', 'DIFFERENT', 'UNVERIFIED'] = 'UNVERIFIED'
    evidence_ids: tuple[UUID, ...] = Field(default=(), max_length=MAX_ANSWER_EVIDENCE)
    limitation_quote: str = Field(default='', max_length=400)


def coverage_gaps(
    coverage: tuple[AnswerCoverageItem, ...], sources: list[dict[str, object]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Only explicit source text can support an unavailable-scope exception."""
    by_id = {str(item.get('evidence_id')): str(item.get('content', '')) for item in sources}
    def compact(text: str) -> str:
        return re.sub(r'\s+', '', text)
    missing = []
    omitted = []
    for item in coverage:
        if item.status == 'ANSWERED' and (item.entity_alignment in {'BROADER', 'DIFFERENT'}
                                          or item.period_alignment in {'PARTIAL', 'DIFFERENT'}) or item.status == 'MISSING_EVIDENCE':
            missing.append(item.requirement)
        elif item.status == 'OMITTED_FROM_ANSWER':
            omitted.append(item.requirement)
        elif item.status == 'UNAVAILABLE':
            quote = compact(item.limitation_quote)
            if not quote or not any(
                quote in compact(by_id.get(str(source_id), '')) for source_id in item.evidence_ids
            ):
                missing.append(item.requirement)
    return tuple(dict.fromkeys(missing)), tuple(dict.fromkeys(omitted))


def discovery_check_fits(max_tokens: int, used_tokens: int, estimated_tokens: int) -> bool:
    """Keep most of the shared allowance for answering, review and recovery."""
    remaining = max_tokens - used_tokens
    answer_reserve = (max_tokens * 3 + 3) // 4
    return remaining >= answer_reserve + max(0, estimated_tokens)


def alignment_review_error(coverage: tuple[AnswerCoverageItem, ...]) -> str | None:
    if any(item.status == 'ANSWERED' and (item.entity_alignment == 'UNVERIFIED'
           or item.period_alignment == 'UNVERIFIED') for item in coverage):
        return 'Explicitly assess entity_alignment and period_alignment for every ANSWERED requirement. Use MATCH for an unrestricted dimension; broader entities or partial periods do not satisfy the original requested scope.'
    return None
