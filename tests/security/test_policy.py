from uuid import uuid4

import pytest

from enterprise_agent.contracts.domain import (
    CapabilityDescriptor,
    Classification,
    RetryPolicy,
    Risk,
)
from enterprise_agent.governance.policy import Decision, PolicyInput, Role, Subject, evaluate


def policy(role=Role.WORKSPACE_ADMIN):
    w = uuid4()
    cap = CapabilityDescriptor(
        id="admission",
        name="Admission",
        version="1.0.0",
        kind="MCP",
        description="Create admission",
        input_schema={},
        output_schema={},
        tags=(),
        risk_level=Risk.EXTERNAL_SIDE_EFFECT,
        side_effect_mode="EXTERNAL",
        idempotency_mode="NATIVE_IDEMPOTENCY",
        compensation_mode="MANUAL",
        compensation_capability=None,
        reconciliation_capability="admission.lookup",
        timeout_sec=10,
        retry_policy=RetryPolicy(max_attempts=3, initial_delay_sec=1, max_delay_sec=10),
        required_permissions=frozenset({"supplier.write"}),
        approval_policy="REQUIRED",
        provider="procurement",
        health_status="ACTIVE",
        cost_hint=0,
    )
    return PolicyInput(
        subject=Subject(actor_id=uuid4(), actor_type="USER", workspace_id=w, role=role),
        resource_workspace_id=w,
        resource_id=uuid4(),
        capability=cap,
        capability_allowlist=frozenset({"admission"}),
        classification=Classification.INTERNAL,
        external_provider=False,
        policy_version="1.0.0",
    )


def test_admin_still_requires_hitl():
    assert evaluate(policy()).decision == Decision.REQUIRE_APPROVAL


def test_member_denied():
    assert evaluate(policy(Role.MEMBER)).decision == Decision.DENY


def test_cross_workspace_denied():
    assert (
        evaluate(
            policy(Role.WORKSPACE_OWNER).model_copy(update={"resource_workspace_id": uuid4()})
        ).decision
        == Decision.DENY
    )


@pytest.mark.parametrize("classification", [Classification.RESTRICTED, Classification.CONFIDENTIAL])
def test_egress_denied(classification):
    assert (
        evaluate(
            policy().model_copy(
                update={"external_provider": True, "classification": classification}
            )
        ).decision
        == Decision.DENY
    )


def test_allowlist_denied():
    assert (
        evaluate(policy().model_copy(update={"capability_allowlist": frozenset()})).decision
        == Decision.DENY
    )
