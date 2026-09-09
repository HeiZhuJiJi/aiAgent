from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.api.contracts import TaskView
from enterprise_agent.contracts.domain import FailureCode, TaskState
from enterprise_agent.domain.states import DomainError, transition
from enterprise_agent.identity.contracts import Identity
from enterprise_agent.identity.service import get_workspace
from enterprise_agent.storage.outbox import enqueue
from enterprise_agent.storage.tables import AuditRow, TaskRow
from enterprise_agent.storage.tasks import task_view

ACTION_TARGETS: dict[str, TaskState] = {
    "pause": TaskState.PAUSED,
    "resume": TaskState.READY,
    "cancel": TaskState.CANCELLING,
}


async def control_task(
    session: AsyncSession,
    identity: Identity,
    workspace_id: UUID,
    task_id: UUID,
    action: str,
    expected_version: int,
    trace_id: str,
) -> TaskView:
    await get_workspace(session, identity, workspace_id)
    row = await session.scalar(
        select(TaskRow).where(TaskRow.workspace_id == workspace_id, TaskRow.id == task_id)
    )
    if row is None:
        raise DomainError(FailureCode.POLICY_DENIED, "task unavailable")
    target = ACTION_TARGETS.get(action)
    if target is None:
        raise DomainError(FailureCode.VALIDATION_FAILED, "unsupported task control")
    current = TaskState(row.state)
    next_state = transition(current, target)
    changed = await session.scalar(
        update(TaskRow)
        .where(
            TaskRow.workspace_id == workspace_id,
            TaskRow.id == task_id,
            TaskRow.version == expected_version,
            TaskRow.state == current.value,
        )
        .values(state=next_state.value, version=TaskRow.version + 1)
        .returning(TaskRow)
    )
    if changed is None:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "stale task version")
    session.add(
        AuditRow(
            workspace_id=workspace_id,
            actor_type="USER",
            actor_id=identity.user_id,
            action="task." + action,
            task_id=task_id,
            resource_type="TASK_RUN",
            resource_id=task_id,
            result={"state": target.value, "version": expected_version + 1},
            trace_id=trace_id,
        )
    )
    await enqueue(
        session,
        uuid4(),
        workspace_id,
        "TaskRun",
        task_id,
        "task.control.requested",
        {
            "task_id": str(task_id),
            "workflow_id": "task-" + str(task_id),
            "action": action,
            "state": next_state.value,
            "version": changed.version,
        },
    )
    await session.flush()
    return task_view(changed)
