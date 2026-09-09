from uuid import uuid4

import pytest
from pydantic import ValidationError

from enterprise_agent.contracts.domain import (
    AttemptState,
    CapabilityRequirement,
    Plan,
    PlanStepSpec,
    ReviewResult,
    Risk,
    StepState,
    TaskState,
)
from enterprise_agent.domain.states import DomainError, transition


def step(key, deps=()):
    return PlanStepSpec(
        step_key=key,
        objective="research",
        dependencies=deps,
        capability_requirements=(
            CapabilityRequirement(capability_id="read", version_range=">=1,<2"),
        ),
        expected_output="evidence",
        success_criteria=("grounded",),
        risk_hint=Risk.READ,
        execution_mode="CAPABILITY",
        parallelizable=True,
        max_attempts=3,
    )


@pytest.mark.parametrize(
    "steps",
    [
        (step("a", ("b",)), step("b", ("a",))),
        (step("a"), step("a")),
        (step("a", ("absent",)),),
        (step("a", ("a",)),),
    ],
)
def test_invalid_dag(steps):
    with pytest.raises(ValidationError):
        Plan(
            plan_id=uuid4(),
            task_id=uuid4(),
            workspace_id=uuid4(),
            execution_snapshot_id=uuid4(),
            version=1,
            goal="research",
            constraints=(),
            deliverables=("report",),
            steps=steps,
        )


@pytest.mark.parametrize(
    "current,target",
    [
        (TaskState.COMPLETED, TaskState.RUNNING),
        (StepState.COMPLETED, StepState.READY),
        (AttemptState.SUCCEEDED, AttemptState.RUNNING),
        (AttemptState.UNKNOWN, AttemptState.RUNNING),
        (TaskState.CANCELLING, TaskState.COMPLETED),
    ],
)
def test_illegal_transition(current, target):
    with pytest.raises(DomainError):
        transition(current, target)


def test_reconcile_unknown():
    assert transition(AttemptState.UNKNOWN, AttemptState.SUCCEEDED) == AttemptState.SUCCEEDED


def test_review_rejects_ungrounded_acceptance():
    with pytest.raises(ValidationError):
        ReviewResult(
            accepted=True,
            missing_deliverables=(),
            unsupported_claims=(uuid4(),),
            constraint_violations=(),
            citation_errors=(),
        )
