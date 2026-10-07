from datetime import UTC, datetime
from uuid import uuid4

from enterprise_agent.completion.policies import derive_completion_status
from enterprise_agent.contracts.domain import (
    AssessmentStatus,
    CompletionStatus,
    ContractVersion,
    Requirement,
    RequirementAssessment,
    RequirementType,
)


def contract(*, workspace_id, task_id, allow_partial=True) -> ContractVersion:
    return ContractVersion(
        id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        version=1,
        content_hash="a" * 64,
        goal="Answer the task",
        completion_policy={"allow_partial": allow_partial, "version": "completion.v1"},
        created_by=uuid4(),
        created_at=datetime.now(UTC),
    )


def requirement(workspace_id, task_id, contract_id, *, mandatory=True) -> Requirement:
    return Requirement(
        id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        contract_version_id=contract_id,
        key="deliverable",
        type=RequirementType.READ,
        mandatory=mandatory,
        description="The requested deliverable is supported.",
        completion_rule="A verified claim supports the deliverable.",
    )


def assessment(requirement_value: Requirement, status: AssessmentStatus) -> RequirementAssessment:
    return RequirementAssessment(
        id=uuid4(),
        workspace_id=requirement_value.workspace_id,
        task_id=requirement_value.task_id,
        requirement_id=requirement_value.id,
        status=status,
        evaluator_version="completion-policy.v1",
        policy_version="completion.v1",
        input_snapshot_hash="b" * 64,
        assessed_at=datetime.now(UTC),
    )


def test_completion_models_keep_dynamic_state_out_of_definitions() -> None:
    assert "status" not in Requirement.model_fields
    assert "completion_status" not in Requirement.model_fields
    assert "completion_status" not in ContractVersion.model_fields
    assert "status" in RequirementAssessment.model_fields


def test_completion_status_is_derived_from_effective_assessments() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    current = contract(workspace_id=workspace_id, task_id=task_id)
    mandatory = requirement(workspace_id, task_id, current.id)
    optional = requirement(workspace_id, task_id, current.id, mandatory=False)

    assert derive_completion_status(current, (mandatory, optional), ()) is CompletionStatus.UNAVAILABLE
    assert (
        derive_completion_status(
            current,
            (mandatory, optional),
            (assessment(optional, AssessmentStatus.SUPPORTED),),
        )
        is CompletionStatus.PARTIAL
    )
    assert (
        derive_completion_status(
            current,
            (mandatory, optional),
            (
                assessment(mandatory, AssessmentStatus.SUPPORTED),
                assessment(optional, AssessmentStatus.SUPPORTED),
            ),
        )
        is CompletionStatus.COMPLETE
    )


def test_partial_completion_requires_contract_permission() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    current = contract(workspace_id=workspace_id, task_id=task_id, allow_partial=False)
    mandatory = requirement(workspace_id, task_id, current.id)
    assert (
        derive_completion_status(
            current,
            (mandatory,),
            (assessment(mandatory, AssessmentStatus.PARTIALLY_SUPPORTED),),
        )
        is CompletionStatus.UNAVAILABLE
    )


def test_failed_delivery_gate_cannot_be_overridden_by_supported_assessments() -> None:
    workspace_id, task_id = uuid4(), uuid4()
    current = contract(workspace_id=workspace_id, task_id=task_id)
    mandatory = requirement(workspace_id, task_id, current.id)
    assert (
        derive_completion_status(
            current,
            (mandatory,),
            (assessment(mandatory, AssessmentStatus.SUPPORTED),),
            delivery_gates_passed=False,
        )
        is CompletionStatus.UNAVAILABLE
    )
