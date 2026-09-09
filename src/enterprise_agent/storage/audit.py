from typing import Literal, cast
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.api.contracts import AuditView
from enterprise_agent.contracts.domain import FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.governance.policy import ROLE_PERMISSIONS, Role
from enterprise_agent.storage.tables import AuditRow


async def list_audit_events(
    session: AsyncSession, workspace_id: UUID, role: Role
) -> tuple[AuditView, ...]:
    if "audit.read" not in ROLE_PERMISSIONS[role]:
        raise DomainError(FailureCode.POLICY_DENIED, "audit permission required")
    rows = (
        await session.scalars(
            select(AuditRow)
            .where(AuditRow.workspace_id == workspace_id)
            .order_by(AuditRow.created_at, AuditRow.id)
        )
    ).all()
    return tuple(
        AuditView(
            audit_id=row.id,
            workspace_id=row.workspace_id,
            actor_type=cast(
                Literal["USER", "SERVICE_ACCOUNT", "SYSTEM", "AGENT"], row.actor_type
            ),
            actor_id=row.actor_id,
            task_id=row.task_id,
            step_id=row.step_id,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            capability_id=row.capability_id,
            policy_decision=row.policy_decision,
            approval_id=row.approval_id,
            result=cast(dict[str, JsonValue] | None, row.result),
            trace_id=row.trace_id,
            created_at=row.created_at,
        )
        for row in rows
    )
