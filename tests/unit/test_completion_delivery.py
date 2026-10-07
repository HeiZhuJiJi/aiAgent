from datetime import UTC, datetime
from uuid import UUID, uuid4

from enterprise_agent.completion.assessment import assess_requirement
from enterprise_agent.completion.delivery import build_delivery_decision
from enterprise_agent.contracts.domain import (
    AssessmentStatus,
    ContractVersion,
    DeliveryDecisionType,
    ExecutionSnapshot,
    Requirement,
    RequirementType,
    VerificationGates,
    VerificationMetrics,
    VerificationResult,
    VersionRef,
)


def version_ref(name: str) -> VersionRef:
    return VersionRef(identifier=name, version="1.0.0", sha256="a" * 64)


def snapshot(workspace_id: UUID) -> ExecutionSnapshot:
    return ExecutionSnapshot(
        snapshot_id=uuid4(),
        workspace_id=workspace_id,
        runtime_version="runtime.v1",
        workflow_definition_version="workflow.v1",
        planner_prompt_version=version_ref("planner"),
        router_prompt_version=version_ref("router"),
        reviewer_prompt_version=version_ref("reviewer"),
        model_config_versions=(version_ref("model"),),
        skill_versions=(),
        capability_versions=(version_ref("capability"),),
        policy_version=version_ref("policy"),
        knowledge_document_refs=(),
        context_policy_version=version_ref("context"),
        schema_versions=(version_ref("domain"),),
        feature_flags=frozenset(),
        created_at=datetime.now(UTC),
    )


def contract(workspace_id: UUID, task_id: UUID) -> ContractVersion:
    return ContractVersion(
        id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        version=1,
        content_hash="b" * 64,
        goal="Answer the request",
        completion_policy={"allow_partial": True, "version": "delivery.v1"},
        created_by=uuid4(),
        created_at=datetime.now(UTC),
    )


def requirement(value: ContractVersion, *, mandatory: bool = True) -> Requirement:
    return Requirement(
        id=uuid4(),
        workspace_id=value.workspace_id,
        task_id=value.task_id,
        contract_version_id=value.id,
        key=str(uuid4()),
        type=RequirementType.ANALYSIS,
        mandatory=mandatory,
        description="A verified answer is required.",
        completion_rule="All verification gates pass.",
    )


def verification(value: Requirement, *, authorized: bool = True, certainty: str = "CERTAIN") -> VerificationResult:
    return VerificationResult(
        id=uuid4(),
        workspace_id=value.workspace_id,
        task_id=value.task_id,
        claim_ids=(uuid4(),),
        evidence_ids=(uuid4(),),
        gates=VerificationGates(
            entity_match=True,
            temporal_match=True,
            evidence_support=True,
            authorization=authorized,
            operation_certainty=certainty,
            schema_validity=True,
        ),
        metrics=VerificationMetrics(
            completeness=1.0,
            freshness=1.0,
            source_quality=1.0,
            relevance=1.0,
            redundancy=1.0,
        ),
        verifier_version="verifier.v1",
        policy_version="delivery.v1",
        input_snapshot_hash="c" * 64,
        created_at=datetime.now(UTC),
    )


def test_delivery_includes_only_claims_present_in_verification() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    current_contract = contract(workspace_id, task_id)
    req = requirement(current_contract)
    facts = verification(req)
    assessment = assess_requirement(
        req,
        facts,
        assessment_id=uuid4(),
        evaluator_version="delivery.v1",
    ).model_copy(update={"supporting_claim_ids": (facts.claim_ids[0], uuid4())})

    decision = build_delivery_decision(
        current_contract,
        (req,),
        (assessment,),
        (facts,),
        snapshot(workspace_id),
        decision_id=uuid4(),
        created_at=datetime.now(UTC),
    )
    assert decision.decision is DeliveryDecisionType.FULL_ANSWER
    assert decision.included_claim_ids == (facts.claim_ids[0],)
    assert len(decision.included_claim_ids) == 1


def test_unknown_operation_is_unavailable_and_not_successful() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    current_contract = contract(workspace_id, task_id)
    req = requirement(current_contract)
    facts = verification(req, certainty="UNKNOWN")
    assessment = assess_requirement(
        req,
        facts,
        assessment_id=uuid4(),
        evaluator_version="delivery.v1",
    )
    decision = build_delivery_decision(
        current_contract,
        (req,),
        (assessment,),
        (facts,),
        snapshot(workspace_id),
        decision_id=uuid4(),
        created_at=datetime.now(UTC),
    )
    assert decision.decision is DeliveryDecisionType.UNAVAILABLE
    assert "UNKNOWN" in decision.reason_codes
    assert decision.included_claim_ids == ()


def test_authorization_block_is_distinct_from_missing_evidence() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    current_contract = contract(workspace_id, task_id)
    req = requirement(current_contract)
    facts = verification(req, authorized=False)
    assessment = assess_requirement(
        req,
        facts,
        assessment_id=uuid4(),
        evaluator_version="delivery.v1",
    )
    decision = build_delivery_decision(
        current_contract,
        (req,),
        (assessment,),
        (facts,),
        snapshot(workspace_id),
        decision_id=uuid4(),
        created_at=datetime.now(UTC),
    )
    assert assessment.status is AssessmentStatus.BLOCKED
    assert decision.decision is DeliveryDecisionType.BLOCKED
    assert decision.reason_codes == ("AUTHORIZATION",)
    assert decision.included_claim_ids == ()


def test_supported_assessment_without_verified_claim_is_unavailable() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    current_contract = contract(workspace_id, task_id)
    req = requirement(current_contract)
    facts = verification(req).model_copy(update={"claim_ids": (), "evidence_ids": ()})
    assessment = assess_requirement(
        req,
        facts,
        assessment_id=uuid4(),
        evaluator_version="delivery.v1",
    )
    decision = build_delivery_decision(
        current_contract,
        (req,),
        (assessment,),
        (facts,),
        snapshot(workspace_id),
        decision_id=uuid4(),
        created_at=datetime.now(UTC),
    )
    assert assessment.status is AssessmentStatus.SUPPORTED
    assert decision.decision is DeliveryDecisionType.UNAVAILABLE
