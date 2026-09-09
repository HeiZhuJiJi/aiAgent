from typing import cast
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.api.contracts import UsageView
from enterprise_agent.storage.tables import UsageRecordRow


async def list_usage_records(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    task_id: UUID | None = None,
) -> tuple[UsageView, ...]:
    query = select(UsageRecordRow).where(UsageRecordRow.workspace_id == workspace_id)
    if task_id is not None:
        query = query.where(UsageRecordRow.task_id == task_id)
    rows = (
        await session.scalars(query.order_by(UsageRecordRow.created_at, UsageRecordRow.id))
    ).all()
    return tuple(
        UsageView(
            usage_id=row.id,
            workspace_id=row.workspace_id,
            task_id=row.task_id,
            evaluation_run_id=row.evaluation_run_id,
            kind=row.kind,
            units=row.units,
            tokens=row.tokens,
            cost=row.cost,
            detail=cast(dict[str, JsonValue], row.detail),
            created_at=row.created_at,
        )
        for row in rows
    )
