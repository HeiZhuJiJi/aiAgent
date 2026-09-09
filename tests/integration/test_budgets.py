import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.runtime.budgets import BudgetDelta, BudgetKind, reserve_budget
from enterprise_agent.storage.tables import AuditRow, TaskBudgetRow, UsageRecordRow
from tests.integration.test_fencing import seed


async def test_concurrent_budget_reservations_are_atomic_and_fail_closed():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    database = async_sessionmaker(engine, expire_on_commit=False)
    workspace_id, task_id, _ = await seed(database)
    async with database.begin() as session:
        session.add(
            TaskBudgetRow(
                task_id=task_id,
                workspace_id=workspace_id,
                max_steps=10,
                max_replans=2,
                max_llm_calls=10,
                max_capability_calls=1,
                max_tokens=100,
                max_cost=1,
                deadline=datetime.now(UTC) + timedelta(hours=1),
            )
        )

    async def reserve():
        async with database.begin() as session:
            return await reserve_budget(
                session,
                workspace_id,
                task_id,
                uuid4(),
                "trace-budget",
                BudgetDelta(kind=BudgetKind.CAPABILITY_CALL, cost=0.25),
            )

    decisions = await asyncio.gather(reserve(), reserve())
    assert sorted(item.allowed for item in decisions) == [False, True]
    async with database() as session:
        budget = await session.get(TaskBudgetRow, task_id)
        exceeded_audits = await session.scalar(
            select(func.count())
            .select_from(AuditRow)
            .where(
                AuditRow.resource_id == task_id,
                AuditRow.action == "budget.exceeded.capability_call",
            )
        )
        usage = (
            await session.scalars(
                select(UsageRecordRow).where(
                    UsageRecordRow.workspace_id == workspace_id,
                    UsageRecordRow.task_id == task_id,
                )
            )
        ).all()
        assert budget is not None
        assert budget.used_capability_calls == 1
        assert budget.used_cost == 0.25
        assert budget.state == "EXCEEDED"
        assert exceeded_audits == 1
        assert len(usage) == 1
        assert usage[0].kind == "CAPABILITY_CALL"
        assert usage[0].units == 1 and usage[0].cost == 0.25
    await engine.dispose()


async def test_expired_deadline_rejects_before_usage_changes():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    database = async_sessionmaker(engine, expire_on_commit=False)
    workspace_id, task_id, _ = await seed(database)
    async with database.begin() as session:
        session.add(
            TaskBudgetRow(
                task_id=task_id,
                workspace_id=workspace_id,
                max_steps=10,
                max_replans=2,
                max_llm_calls=10,
                max_capability_calls=10,
                max_tokens=100,
                max_cost=1,
                deadline=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
    async with database.begin() as session:
        decision = await reserve_budget(
            session,
            workspace_id,
            task_id,
            uuid4(),
            "trace-deadline",
            BudgetDelta(kind=BudgetKind.STEP),
        )
        assert not decision.allowed and decision.reason == "deadline"
    async with database() as session:
        budget = await session.get(TaskBudgetRow, task_id)
        usage_count = await session.scalar(
            select(func.count()).select_from(UsageRecordRow).where(
                UsageRecordRow.workspace_id == workspace_id,
                UsageRecordRow.task_id == task_id,
            )
        )
        assert budget is not None
        assert budget.used_steps == 0 and budget.state == "EXCEEDED"
        assert usage_count == 0
    await engine.dispose()
