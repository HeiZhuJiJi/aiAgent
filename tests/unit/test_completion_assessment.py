from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from enterprise_agent.completion.assessment import (
    assess_requirement,
    effective_assessments,
    gap_action,
)
from enterprise_agent.contracts.domain import (
    AssessmentStatus,
    GapType,
    RecoveryAction,
    Requirement,
    RequirementType,
    VerificationGates,
    VerificationMetrics,
    VerificationResult,
)


def requirement(*, freshness: float = 0.0) -> Requirement:
    return Requirement(
        id=uuid4(),
        workspace_id=uuid4(),
        task_id=uuid4(),
        contract_version_id=uuid4(),
        key="required-record",
        type=RequirementType.VERIFICATION,
        mandatory=True,
        description="The record must be verified.",
        completion_rule="The verification gates pass.",
        freshness_policy={"minimum": freshness},
    )


def verification(
    value: Requirement,
    *,
    authorization: bool = True,
    certainty: str = "CERTAIN",
    schema_validity: bool = True,
    entity_match: bool = True,
    temporal_match: bool = True,
    evidence_support: bool = True,
    completeness: float = 1.0,
    freshness: float = 1.0,
) -> VerificationResult:
    return VerificationResult(
        id=uuid4(),
        workspace_id=value.workspace_id,
        task_id=value.task_id,
        claim_ids=(uuid4(),),
        evidence_ids=(uuid4(),),
        gates=VerificationGates(
            entity_match=entity_match,
            temporal_match=temporal_match,
            evidence_support=evidence_support,
            authorization=authorization,
            operation_certainty=certainty,
            schema_validity=schema_validity,
        ),
        metrics=VerificationMetrics(
            completeness=completeness,
            freshness=freshness,
            source_quality=1.0,
            relevance=1.0,
            redundancy=1.0,
        ),
        verifier_version="verifier.v1",
        policy_version="completion.v1",
        input_snapshot_hash="a" * 64,
        created_at=datetime.now(UTC),
    )


def test_authorization_gap_is_not_missing_evidence() -> None:
    req = requirement()
    result = assess_requirement(
        req,
        verification(req, authorization=False),
        assessment_id=uuid4(),
        evaluator_version="policy.v1",
    )
    assert result.status is AssessmentStatus.BLOCKED
    assert result.gap_type is GapType.AUTHORIZATION
    assert gap_action(result) is RecoveryAction.AWAIT_AUTHORIZATION
    assert result.supporting_claim_ids == ()


def test_unknown_operation_requires_reconciliation_and_cannot_support_claims() -> None:
    req = requirement()
    result = assess_requirement(
        req,
        verification(req, certainty="UNKNOWN"),
        assessment_id=uuid4(),
        evaluator_version="policy.v1",
    )
    assert result.status is AssessmentStatus.UNKNOWN
    assert result.gap_type is GapType.UNKNOWN
    assert gap_action(result) is RecoveryAction.RECONCILE
    assert result.supporting_claim_ids == ()


@pytest.mark.parametrize(
    ("changes", "status", "gap"),
    (
        ({"certainty": "CONFLICTING"}, AssessmentStatus.CONFLICTING, GapType.CONFLICT),
        ({"schema_validity": False}, AssessmentStatus.UNVERIFIABLE, GapType.INVALID),
        ({"entity_match": False}, AssessmentStatus.UNSUPPORTED, GapType.UNSUPPORTED),
        ({"evidence_support": False}, AssessmentStatus.UNSUPPORTED, GapType.MISSING),
        ({"completeness": 0.5}, AssessmentStatus.PARTIALLY_SUPPORTED, GapType.MISSING),
    ),
)
def test_assessment_applies_policy_priority(
    changes: dict[str, object], status: AssessmentStatus, gap: GapType
) -> None:
    req = requirement()
    result = assess_requirement(
        req,
        verification(req, **changes),
        assessment_id=uuid4(),
        evaluator_version="policy.v1",
    )
    assert result.status is status
    assert result.gap_type is gap


def test_stale_assessment_supersession_selects_only_the_effective_history() -> None:
    req = requirement(freshness=0.9)
    first = assess_requirement(
        req,
        verification(req, freshness=0.1),
        assessment_id=uuid4(),
        evaluator_version="policy.v1",
    )
    later_verification = verification(req, freshness=1.0)
    later_verification = later_verification.model_copy(
        update={"created_at": later_verification.created_at + timedelta(seconds=1)}
    )
    second = assess_requirement(
        req,
        later_verification,
        assessment_id=uuid4(),
        evaluator_version="policy.v1",
        supersedes_id=first.id,
    )
    assert first.status is AssessmentStatus.STALE
    assert second.status is AssessmentStatus.SUPPORTED
    assert effective_assessments((first, second)) == (second,)
