"""Deterministic delivery policy over verified facts."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID

from enterprise_agent.completion.assessment import effective_assessments
from enterprise_agent.contracts.domain import (
    AssessmentStatus,
    ContractVersion,
    DeliveryDecision,
    DeliveryDecisionType,
    ExecutionSnapshot,
    Requirement,
    RequirementAssessment,
    VerificationResult,
)


def _fact_is_authorized(
    requirement_id: UUID,
    authorization_facts: Mapping[str, bool],
) -> bool:
    return authorization_facts.get(str(requirement_id), authorization_facts.get("delivery", True))


def build_delivery_decision(
    contract: ContractVersion,
    requirements: Sequence[Requirement],
    assessments: Sequence[RequirementAssessment],
    verifications: Sequence[VerificationResult],
    execution_snapshot: ExecutionSnapshot,
    *,
    decision_id: UUID,
    created_at: datetime,
    authorization_facts: Mapping[str, bool] | None = None,
    supersedes_id: UUID | None = None,
) -> DeliveryDecision:
    """Select deliverable Claims without inspecting raw tool output or model text."""
    auth = {} if authorization_facts is None else authorization_facts
    by_verification = {item.id: item for item in verifications}
    current = effective_assessments(assessments)
    requirement_by_id = {item.id: item for item in requirements}
    assessment_by_requirement = {item.requirement_id: item for item in current}
    included: set[UUID] = set()
    all_verified_claims: set[UUID] = set()
    blocked = False
    for item in current:
        verification = (
            by_verification.get(item.verification_ref)
            if item.verification_ref is not None
            else None
        )
        authorized = _fact_is_authorized(item.requirement_id, auth)
        if verification is not None:
            all_verified_claims.update(verification.claim_ids)
        requirement = requirement_by_id.get(item.requirement_id)
        if (
            requirement is not None
            and requirement.mandatory
            and (item.status is AssessmentStatus.BLOCKED or not authorized)
        ):
            blocked = True
        if (
            verification is not None
            and authorized
            and item.status in {AssessmentStatus.SUPPORTED, AssessmentStatus.PARTIALLY_SUPPORTED}
            and verification.gates.authorization
            and verification.gates.entity_match
            and verification.gates.temporal_match
            and verification.gates.evidence_support
            and verification.gates.schema_validity
            and verification.gates.operation_certainty == "CERTAIN"
        ):
            included.update(set(item.supporting_claim_ids) & set(verification.claim_ids))

    unresolved = tuple(
        item.id
        for item in requirements
        if item.mandatory
        and not (
            (assessment := assessment_by_requirement.get(item.id)) is not None
            and assessment.status is AssessmentStatus.SUPPORTED
            and assessment.verification_ref in by_verification
            and bool(
                set(assessment.supporting_claim_ids)
                & set(by_verification[assessment.verification_ref].claim_ids)
            )
            and _fact_is_authorized(item.id, auth)
            and by_verification[assessment.verification_ref].gates.authorization
            and by_verification[assessment.verification_ref].gates.entity_match
            and by_verification[assessment.verification_ref].gates.temporal_match
            and by_verification[assessment.verification_ref].gates.evidence_support
            and by_verification[assessment.verification_ref].gates.schema_validity
            and by_verification[assessment.verification_ref].gates.operation_certainty
            == "CERTAIN"
        )
    )
    ambiguous = any(
        item.gap_type is not None
        and item.gap_type.value == "AMBIGUOUS"
        for item in current
        if item.requirement_id in requirement_by_id
    )
    allow_partial = contract.completion_policy.get("allow_partial") is True
    if blocked:
        decision = DeliveryDecisionType.BLOCKED
        disclosure = "Delivery is blocked by authorization or policy facts."
    elif requirements and not unresolved and included:
        decision = DeliveryDecisionType.FULL_ANSWER
        disclosure = ""
    elif ambiguous:
        decision = DeliveryDecisionType.CLARIFICATION_REQUIRED
        disclosure = "The requested scope is ambiguous and requires clarification."
    elif included and allow_partial:
        decision = DeliveryDecisionType.PARTIAL_ANSWER
        disclosure = "Only Claims supported by the current verification snapshot are included."
    else:
        decision = DeliveryDecisionType.UNAVAILABLE
        disclosure = "No safely deliverable verified Claim satisfies the current policy."

    excluded = tuple(sorted(all_verified_claims - included, key=str))
    verification_ref = next(
        (
            assessment.verification_ref
            for assessment in reversed(current)
            if assessment.verification_ref in by_verification
        ),
        None,
    )
    reason_codes = tuple(
        sorted(
            {
                assessment.gap_type.value
                for assessment in current
                if assessment.gap_type is not None
            }
        )
    )
    return DeliveryDecision(
        id=decision_id,
        workspace_id=contract.workspace_id,
        task_id=contract.task_id,
        completion_snapshot_id=execution_snapshot.snapshot_id,
        decision=decision,
        included_claim_ids=tuple(sorted(included, key=str)),
        excluded_claim_ids=excluded,
        unresolved_requirement_ids=unresolved,
        reason_codes=reason_codes,
        disclosure=disclosure,
        policy_version=str(contract.completion_policy.get("version", "1")),
        verification_ref=verification_ref,
        created_at=created_at,
        supersedes_id=supersedes_id,
    )
