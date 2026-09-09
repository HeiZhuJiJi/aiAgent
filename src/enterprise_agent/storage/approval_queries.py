from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.api.contracts import ApprovalView
from enterprise_agent.contracts.domain import ApprovalRequest, ApprovalResult, FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.governance.policy import Role
from enterprise_agent.identity.contracts import Identity
from enterprise_agent.identity.service import get_workspace
from enterprise_agent.storage.approvals import decide_approval
from enterprise_agent.storage.outbox import enqueue
from enterprise_agent.storage.tables import ApprovalRow


def approval_view(row: ApprovalRow) -> ApprovalView:
    return ApprovalView(
        approval_id=row.id,
        workspace_id=row.workspace_id,
        task_id=row.task_id,
        step_id=row.step_id,
        operation_id=row.operation_id,
        request=ApprovalRequest.model_validate(row.request_payload),
        state=cast(Literal["PENDING", "APPROVED", "REJECTED", "EXPIRED"], row.state),
        version=row.version,
        actor_id=row.actor_id,
        decided_at=row.decided_at,
        created_at=row.created_at,
    )


async def list_approvals(
    session: AsyncSession,
    identity: Identity,
    workspace_id: UUID,
    state: str | None = None,
) -> tuple[ApprovalView, ...]:
    await get_workspace(session, identity, workspace_id)
    query = select(ApprovalRow).where(ApprovalRow.workspace_id == workspace_id)
    if state is not None:
        if state not in {"PENDING", "APPROVED", "REJECTED", "EXPIRED"}:
            raise DomainError(FailureCode.VALIDATION_FAILED, "invalid approval state filter")
        query = query.where(ApprovalRow.state == state)
    rows = (await session.scalars(query.order_by(ApprovalRow.created_at, ApprovalRow.id))).all()
    return tuple(approval_view(row) for row in rows)


async def decide_as_user(
    session: AsyncSession,
    identity: Identity,
    workspace_id: UUID,
    approval_id: UUID,
    decision: Literal["APPROVED", "REJECTED"],
    expected_version: int,
) -> ApprovalView:
    workspace = await get_workspace(session, identity, workspace_id)
    if workspace.role not in {Role.WORKSPACE_OWNER, Role.WORKSPACE_ADMIN}:
        raise DomainError(FailureCode.POLICY_DENIED, "approval permission missing")
    if decision not in {"APPROVED", "REJECTED"}:
        raise DomainError(FailureCode.VALIDATION_FAILED, "invalid approval decision")
    row = await decide_approval(
        session,
        workspace_id,
        ApprovalResult(
            approval_id=approval_id,
            decision=decision,
            actor_id=identity.user_id,
            decided_at=datetime.now(UTC),
        ),
        expected_version,
    )
    await enqueue(
        session,
        uuid4(),
        workspace_id,
        "ApprovalRequest",
        approval_id,
        "approval.decided",
        {
            "approval_id": str(approval_id),
            "decision": row.state,
            "version": row.version,
            "workflow_id": "approval-wait-" + str(approval_id),
        },
    )
    return approval_view(row)
