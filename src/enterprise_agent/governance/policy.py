from enum import StrEnum
from typing import Literal
from uuid import UUID

from enterprise_agent.contracts.domain import CapabilityDescriptor, Classification, Contract, Risk


class Role(StrEnum):
    WORKSPACE_OWNER = "WORKSPACE_OWNER"
    WORKSPACE_ADMIN = "WORKSPACE_ADMIN"
    MEMBER = "MEMBER"
    AUDITOR = "AUDITOR"


ADMIN_PERMISSIONS = frozenset(
    {
        "knowledge.read",
        "knowledge.write",
        "supplier.read",
        "supplier.write",
        "skill.install",
        "mcp.manage",
        "audit.read",
        "workspace.manage",
    }
)
ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.WORKSPACE_OWNER: ADMIN_PERMISSIONS,
    Role.WORKSPACE_ADMIN: ADMIN_PERMISSIONS,
    Role.MEMBER: frozenset({"knowledge.read", "knowledge.write", "supplier.read"}),
    Role.AUDITOR: frozenset({"knowledge.read", "supplier.read", "audit.read"}),
}


class Decision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


class Subject(Contract):
    actor_id: UUID
    actor_type: Literal["USER", "SERVICE_ACCOUNT", "SYSTEM", "AGENT"]
    workspace_id: UUID
    role: Role
    granted_permissions: frozenset[str] = frozenset()


class PolicyInput(Contract):
    subject: Subject
    resource_workspace_id: UUID
    resource_id: UUID
    capability: CapabilityDescriptor
    capability_allowlist: frozenset[str]
    classification: Classification
    external_provider: bool
    policy_version: str


class PolicyDecision(Contract):
    decision: Decision
    reason: str
    policy_version: str


def evaluate(value: PolicyInput) -> PolicyDecision:
    def result(decision: Decision, reason: str) -> PolicyDecision:
        return PolicyDecision(decision=decision, reason=reason, policy_version=value.policy_version)

    cap = value.capability
    if value.subject.workspace_id != value.resource_workspace_id:
        return result(Decision.DENY, "workspace mismatch")
    if cap.id not in value.capability_allowlist:
        return result(Decision.DENY, "capability outside execution allowlist")
    if cap.health_status not in {"ACTIVE", "DEPRECATED"}:
        return result(Decision.DENY, "capability unavailable")
    permissions = ROLE_PERMISSIONS[value.subject.role] | value.subject.granted_permissions
    if not cap.required_permissions <= permissions:
        return result(Decision.DENY, "permission missing")
    if value.external_provider and value.classification in {
        Classification.RESTRICTED,
        Classification.CONFIDENTIAL,
    }:
        return result(Decision.DENY, "external data egress prohibited")
    if cap.risk_level == Risk.EXTERNAL_SIDE_EFFECT or cap.approval_policy == "REQUIRED":
        return result(Decision.REQUIRE_APPROVAL, "human decision required for operation")
    return result(Decision.ALLOW, "policy satisfied")
