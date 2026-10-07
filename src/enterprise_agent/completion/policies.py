"""Pure completion status derivation."""

from collections.abc import Iterable

from enterprise_agent.completion.assessment import effective_assessments
from enterprise_agent.contracts.domain import (
    AssessmentStatus,
    CompletionStatus,
    ContractVersion,
    Requirement,
    RequirementAssessment,
)


def derive_completion_status(
    contract: ContractVersion,
    requirements: Iterable[Requirement],
    assessments: Iterable[RequirementAssessment],
    *,
    delivery_gates_passed: bool = True,
) -> CompletionStatus:
    """Derive status from current policy and effective assessments only."""
    requirements_by_id = {item.id: item for item in requirements}
    current = {
        item.requirement_id: item
        for item in effective_assessments(assessments)
        if item.requirement_id in requirements_by_id
    }
    mandatory = tuple(item for item in requirements_by_id.values() if item.mandatory)
    if not delivery_gates_passed:
        return CompletionStatus.UNAVAILABLE
    if delivery_gates_passed and all(
        current.get(item.id) is not None
        and current[item.id].status is AssessmentStatus.SUPPORTED
        for item in mandatory
    ):
        return CompletionStatus.COMPLETE
    has_supported_requirement = any(
        item.status in {AssessmentStatus.SUPPORTED, AssessmentStatus.PARTIALLY_SUPPORTED}
        for item in current.values()
    )
    allow_partial = contract.completion_policy.get("allow_partial") is True
    if has_supported_requirement and allow_partial:
        return CompletionStatus.PARTIAL
    return CompletionStatus.UNAVAILABLE
