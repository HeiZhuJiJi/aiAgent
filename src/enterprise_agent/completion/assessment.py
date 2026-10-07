"""Requirement assessment over immutable verification facts."""

from collections.abc import Iterable
from uuid import UUID

from enterprise_agent.completion.gaps import recovery_action_for_gap
from enterprise_agent.contracts.domain import (
    AssessmentStatus,
    GapType,
    RecoveryAction,
    Requirement,
    RequirementAssessment,
    VerificationResult,
)


def _freshness_threshold(requirement: Requirement) -> float:
    value = requirement.freshness_policy.get("minimum")
    return float(value) if isinstance(value, (int, float)) else 0.0


def assess_requirement(
    requirement: Requirement,
    verification: VerificationResult,
    *,
    assessment_id: UUID,
    evaluator_version: str,
    supersedes_id: UUID | None = None,
) -> RequirementAssessment:
    """Apply Requirement Policy to facts; VerificationResult itself never assesses."""
    gates = verification.gates
    metrics = verification.metrics
    gap_type: GapType | None = None
    if not gates.authorization:
        status, gap_type = AssessmentStatus.BLOCKED, GapType.AUTHORIZATION
    elif gates.operation_certainty == "UNKNOWN":
        status, gap_type = AssessmentStatus.UNKNOWN, GapType.UNKNOWN
    elif gates.operation_certainty == "CONFLICTING":
        status, gap_type = AssessmentStatus.CONFLICTING, GapType.CONFLICT
    elif not gates.schema_validity:
        status, gap_type = AssessmentStatus.UNVERIFIABLE, GapType.INVALID
    elif metrics.freshness < _freshness_threshold(requirement):
        status, gap_type = AssessmentStatus.STALE, GapType.STALE
    elif not gates.entity_match or not gates.temporal_match:
        status, gap_type = AssessmentStatus.UNSUPPORTED, GapType.UNSUPPORTED
    elif not gates.evidence_support:
        status, gap_type = AssessmentStatus.UNSUPPORTED, GapType.MISSING
    elif metrics.completeness < 1:
        status, gap_type = AssessmentStatus.PARTIALLY_SUPPORTED, GapType.MISSING
    else:
        status = AssessmentStatus.SUPPORTED

    supporting_claim_ids = verification.claim_ids if status in {
        AssessmentStatus.SUPPORTED,
        AssessmentStatus.PARTIALLY_SUPPORTED,
    } else ()
    supporting_evidence_ids = verification.evidence_ids if supporting_claim_ids else ()
    return RequirementAssessment(
        id=assessment_id,
        workspace_id=requirement.workspace_id,
        task_id=requirement.task_id,
        requirement_id=requirement.id,
        status=status,
        gap_type=gap_type,
        supporting_claim_ids=supporting_claim_ids,
        supporting_evidence_ids=supporting_evidence_ids,
        verification_ref=verification.id,
        evaluator_version=evaluator_version,
        policy_version=verification.policy_version,
        input_snapshot_hash=verification.input_snapshot_hash,
        assessed_at=verification.created_at,
        supersedes_id=supersedes_id,
        attempt_id=verification.attempt_id,
        fencing_token=verification.fencing_token,
    )


def effective_assessments(
    assessments: Iterable[RequirementAssessment],
) -> tuple[RequirementAssessment, ...]:
    """Select the latest non-superseded assessment for each Requirement."""
    values = tuple(assessments)
    superseded = {item.supersedes_id for item in values if item.supersedes_id is not None}
    latest: dict[UUID, RequirementAssessment] = {}
    for item in sorted(values, key=lambda value: (value.assessed_at, value.id)):
        if item.id in superseded:
            continue
        prior = latest.get(item.requirement_id)
        if prior is None or (item.assessed_at, item.id) > (prior.assessed_at, prior.id):
            latest[item.requirement_id] = item
    return tuple(sorted(latest.values(), key=lambda value: (value.requirement_id, value.id)))


def gap_action(assessment: RequirementAssessment) -> RecoveryAction | None:
    """Expose the recovery mapping without conflating it with Assessment status."""
    return None if assessment.gap_type is None else recovery_action_for_gap(assessment.gap_type)
