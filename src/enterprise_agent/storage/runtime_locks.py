from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.tables import TaskRow


async def lock_task_runtime(
    session: AsyncSession, workspace_id: UUID, task_id: UUID
) -> TaskRow:
    """Serialize canonical runtime mutations for one task before child row locks."""
    task = await session.scalar(
        select(TaskRow)
        .where(TaskRow.workspace_id == workspace_id, TaskRow.id == task_id)
        .with_for_update()
    )
    if task is None:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "task runtime unavailable")
    return task
