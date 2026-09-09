import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from enterprise_agent.contracts.domain import FailureCode, TaskState
from enterprise_agent.domain.states import DomainError
from enterprise_agent.governance.policy import Role
from enterprise_agent.identity.contracts import (
    Identity,
    RetentionPolicyUpdate,
    RetentionPolicyView,
    WorkspaceDeletionRequest,
    WorkspaceDeletionView,
)
from enterprise_agent.identity.service import get_workspace
from enterprise_agent.storage.outbox import enqueue
from enterprise_agent.storage.tables import (
    AuditRow,
    RetentionPolicyRow,
    TaskRow,
    Workspace,
    WorkspaceDeletionRequestRow,
    WorkspaceMemberRow,
)

LOGGER = logging.getLogger(__name__)
_TERMINAL = {
    TaskState.CANCELLED.value,
    TaskState.COMPLETED.value,
    TaskState.FAILED.value,
}


def _retention_view(row: RetentionPolicyRow) -> RetentionPolicyView:
    return RetentionPolicyView(
        workspace_id=row.workspace_id,
        version=row.version,
        conversation_retention_days=row.conversation_retention_days,
        trace_retention_days=row.trace_retention_days,
        artifact_retention_days=row.artifact_retention_days,
        document_retention_days=row.document_retention_days,
        evaluation_retention_days=row.evaluation_retention_days,
        audit_retention_days=row.audit_retention_days,
        retain_audit_tombstone=row.retain_audit_tombstone,
        updated_at=row.updated_at,
    )


def _deletion_view(row: WorkspaceDeletionRequestRow) -> WorkspaceDeletionView:
    return WorkspaceDeletionView(
        request_id=row.id,
        workspace_id=row.workspace_id,
        state=row.state,
        version=row.version,
        active_task_count=row.active_task_count,
        safe_error=row.safe_error,
        requested_at=row.requested_at,
        completed_at=row.completed_at,
    )


def _require_admin(role: Role) -> None:
    if role not in {Role.WORKSPACE_OWNER, Role.WORKSPACE_ADMIN}:
        raise DomainError(
            FailureCode.POLICY_DENIED,
            "workspace retention administration permission required",
        )


async def get_retention_policy(
    session: AsyncSession, identity: Identity, workspace_id: UUID
) -> RetentionPolicyView:
    workspace = await get_workspace(session, identity, workspace_id)
    _require_admin(workspace.role)
    row = await session.get(RetentionPolicyRow, workspace_id)
    if row is None:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION, "workspace retention policy missing"
        )
    return _retention_view(row)


async def update_retention_policy(
    session: AsyncSession,
    identity: Identity,
    workspace_id: UUID,
    value: RetentionPolicyUpdate,
    trace_id: str,
) -> RetentionPolicyView:
    workspace = await get_workspace(session, identity, workspace_id)
    _require_admin(workspace.role)
    row = await session.scalar(
        update(RetentionPolicyRow)
        .where(
            RetentionPolicyRow.workspace_id == workspace_id,
            RetentionPolicyRow.version == value.expected_version,
        )
        .values(
            conversation_retention_days=value.conversation_retention_days,
            trace_retention_days=value.trace_retention_days,
            artifact_retention_days=value.artifact_retention_days,
            document_retention_days=value.document_retention_days,
            evaluation_retention_days=value.evaluation_retention_days,
            audit_retention_days=value.audit_retention_days,
            retain_audit_tombstone=value.retain_audit_tombstone,
            version=RetentionPolicyRow.version + 1,
            updated_at=func.clock_timestamp(),
        )
        .returning(RetentionPolicyRow)
    )
    if row is None:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "stale retention policy")
    session.add(
        AuditRow(
            workspace_id=workspace_id,
            actor_type="USER",
            actor_id=identity.user_id,
            action="retention.updated",
            resource_type="RETENTION_POLICY",
            resource_id=workspace_id,
            result={"version": row.version},
            trace_id=trace_id,
        )
    )
    await session.flush()
    return _retention_view(row)


async def request_workspace_deletion(
    session: AsyncSession,
    identity: Identity,
    workspace_id: UUID,
    value: WorkspaceDeletionRequest,
    trace_id: str,
) -> WorkspaceDeletionView:
    workspace_view = await get_workspace(session, identity, workspace_id)
    if workspace_view.role is not Role.WORKSPACE_OWNER:
        raise DomainError(
            FailureCode.POLICY_DENIED, "workspace owner permission required"
        )
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == workspace_id).with_for_update()
    )
    if workspace is None or workspace.version != value.expected_version:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "stale workspace version")
    existing = await session.scalar(
        select(WorkspaceDeletionRequestRow).where(
            WorkspaceDeletionRequestRow.workspace_id == workspace_id
        )
    )
    if existing is not None:
        return _deletion_view(existing)

    tasks = (
        await session.scalars(
            select(TaskRow)
            .where(
                TaskRow.workspace_id == workspace_id,
                TaskRow.state.not_in(_TERMINAL),
            )
            .with_for_update()
        )
    ).all()
    for task in tasks:
        if task.state == TaskState.CANCELLING.value:
            continue
        task.state = TaskState.CANCELLING.value
        task.version += 1
        await enqueue(
            session,
            uuid5(NAMESPACE_URL, f"workspace-delete-cancel:{workspace_id}:{task.id}"),
            workspace_id,
            "TaskRun",
            task.id,
            "task.control.requested",
            {
                "task_id": str(task.id),
                "workflow_id": "task-" + str(task.id),
                "action": "cancel",
                "state": TaskState.CANCELLING.value,
                "version": task.version,
            },
        )
    workspace.enabled = False
    workspace.lifecycle_state = "DELETING"
    workspace.version += 1
    request_id = uuid5(NAMESPACE_URL, f"workspace-deletion:{workspace_id}")
    row = WorkspaceDeletionRequestRow(
        id=request_id,
        workspace_id=workspace_id,
        requested_by=identity.user_id,
        retain_audit_tombstone=value.retain_audit_tombstone,
        state="DRAINING" if tasks else "READY_TO_PURGE",
        active_task_count=len(tasks),
    )
    session.add(row)
    session.add(
        AuditRow(
            workspace_id=workspace_id,
            actor_type="USER",
            actor_id=identity.user_id,
            action="workspace.deletion_requested",
            resource_type="WORKSPACE",
            resource_id=workspace_id,
            result={
                "state": row.state,
                "active_task_count": len(tasks),
                "retain_audit_tombstone": value.retain_audit_tombstone,
            },
            trace_id=trace_id,
        )
    )
    await session.flush()
    return _deletion_view(row)


async def get_workspace_deletion(
    session: AsyncSession, identity: Identity, request_id: UUID
) -> WorkspaceDeletionView:
    row = await session.scalar(
        select(WorkspaceDeletionRequestRow)
        .join(
            WorkspaceMemberRow,
            WorkspaceMemberRow.workspace_id
            == WorkspaceDeletionRequestRow.workspace_id,
        )
        .where(
            WorkspaceDeletionRequestRow.id == request_id,
            WorkspaceMemberRow.user_id == identity.user_id,
        )
    )
    if row is None:
        raise DomainError(FailureCode.POLICY_DENIED, "deletion request unavailable")
    return _deletion_view(row)


async def refresh_deletion_requests(
    sessions: async_sessionmaker[AsyncSession],
) -> int:
    ready = 0
    async with sessions.begin() as session:
        rows = (
            await session.scalars(
                select(WorkspaceDeletionRequestRow)
                .where(WorkspaceDeletionRequestRow.state == "DRAINING")
                .order_by(WorkspaceDeletionRequestRow.requested_at)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in rows:
            active = cast(
                int,
                await session.scalar(
                    select(func.count())
                    .select_from(TaskRow)
                    .where(
                        TaskRow.workspace_id == row.workspace_id,
                        TaskRow.state.not_in(_TERMINAL),
                    )
                ),
            )
            row.active_task_count = active
            row.version += 1
            row.updated_at = datetime.now(UTC)
            if active == 0:
                row.state = "READY_TO_PURGE"
                ready += 1
    return ready


async def run_lifecycle_monitor() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    interval = max(1.0, float(os.environ.get("LIFECYCLE_INTERVAL_SECONDS", "10")))
    try:
        while True:
            try:
                await refresh_deletion_requests(sessions)
            except Exception:
                LOGGER.exception("workspace lifecycle scan failed")
            await asyncio.sleep(interval)
    finally:
        await engine.dispose()
