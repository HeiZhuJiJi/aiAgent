import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.contracts.domain import FailureCode, TaskGoal
from enterprise_agent.domain.states import DomainError
from enterprise_agent.runtime.budgets import (
    BudgetDelta,
    BudgetKind,
    new_task_budget,
    record_actual_llm_usage,
    reserve_budget,
)
from enterprise_agent.storage.attempts import acquire_attempt
from enterprise_agent.storage.tables import AuditRow, StepRow, TaskBudgetRow, UsageRecordRow
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


async def test_actual_llm_usage_crosses_boundary_and_records_after_exceeded_with_fencing():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    database = async_sessionmaker(engine, expire_on_commit=False)
    workspace_id, task_id, step_id = await seed(database)
    attempt_id = uuid4()
    async with database.begin() as session:
        session.add(
            TaskBudgetRow(
                task_id=task_id,
                workspace_id=workspace_id,
                max_steps=10,
                max_replans=2,
                max_llm_calls=10,
                max_capability_calls=10,
                max_tokens=10,
                max_cost=1,
                deadline=datetime.now(UTC) + timedelta(hours=1),
                used_llm_calls=1,
                used_tokens=8,
            )
        )
    async with database.begin() as session:
        token = (
            await acquire_attempt(
                session,
                workspace_id,
                step_id,
                attempt_id,
                "usage-worker",
                "trace-actual-usage",
            )
        ).fencing_token
    async with database.begin() as session:
        await record_actual_llm_usage(
            session,
            workspace_id,
            task_id,
            step_id,
            attempt_id,
            token,
            "trace-actual-usage",
            tokens=5,
        )
    async with database.begin() as session:
        denied = await reserve_budget(
            session,
            workspace_id,
            task_id,
            attempt_id,
            "trace-denied-after-boundary",
            BudgetDelta(kind=BudgetKind.LLM_CALL, units=0, tokens=7),
        )
        assert not denied.allowed and denied.reason == "tokens"
    async with database.begin() as session:
        await record_actual_llm_usage(
            session,
            workspace_id,
            task_id,
            step_id,
            attempt_id,
            token,
            "trace-actual-after-exceeded",
            tokens=7,
        )

    async with database() as session:
        budget = await session.get(TaskBudgetRow, task_id)
        actual_usage = (
            await session.scalars(
                select(UsageRecordRow).where(
                    UsageRecordRow.workspace_id == workspace_id,
                    UsageRecordRow.task_id == task_id,
                    UsageRecordRow.kind == BudgetKind.LLM_CALL.value,
                )
            )
        ).all()
        exceeded_audits = await session.scalar(
            select(func.count()).select_from(AuditRow).where(
                AuditRow.workspace_id == workspace_id,
                AuditRow.resource_id == task_id,
                AuditRow.action == "budget.exceeded.tokens",
            )
        )
    assert budget is not None
    assert budget.state == "EXCEEDED"
    assert budget.used_tokens == 20
    assert sorted(item.tokens for item in actual_usage) == [5, 7]
    assert all(item.detail["post_call_actual"] is True for item in actual_usage)
    assert exceeded_audits == 1

    async with database.begin() as session:
        await session.execute(
            update(StepRow)
            .where(StepRow.workspace_id == workspace_id, StepRow.id == step_id)
            .values(lease_expires_at=func.now())
        )
    async with database.begin() as session:
        await acquire_attempt(
            session,
            workspace_id,
            step_id,
            uuid4(),
            "replacement-worker",
            "trace-replacement",
        )
    with pytest.raises(DomainError) as raised:
        async with database.begin() as session:
            await record_actual_llm_usage(
                session,
                workspace_id,
                task_id,
                step_id,
                attempt_id,
                token,
                "trace-stale-usage",
                tokens=3,
            )
    assert raised.value.code is FailureCode.STALE_ATTEMPT
    async with database() as session:
        budget = await session.get(TaskBudgetRow, task_id)
        usage_count = await session.scalar(
            select(func.count()).select_from(UsageRecordRow).where(
                UsageRecordRow.workspace_id == workspace_id,
                UsageRecordRow.task_id == task_id,
                UsageRecordRow.kind == BudgetKind.LLM_CALL.value,
            )
        )
    assert budget is not None and budget.used_tokens == 20
    assert usage_count == 2
    await engine.dispose()


def _task_goal(workspace_id, requested_skill: str) -> TaskGoal:
    return TaskGoal(
        goal="answer the request",
        constraints=(),
        deliverables=("answer",),
        workspace_id=workspace_id,
        time_budget_sec=600,
        token_budget=24_000,
        cost_budget=10,
        requested_skill=requested_skill,
    )


@pytest.mark.parametrize(
    "requested_skill",
    ("web-research", "document-analysis", "grounded-research"),
)
async def test_bounded_qa_budget_admits_only_one_replan(requested_skill: str):
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    database = async_sessionmaker(engine, expire_on_commit=False)
    workspace_id, task_id, _ = await seed(database)
    async with database.begin() as session:
        session.add(new_task_budget(task_id, _task_goal(workspace_id, requested_skill)))

    async with database.begin() as session:
        first = await reserve_budget(
            session,
            workspace_id,
            task_id,
            uuid4(),
            "trace-bounded-replan-1",
            BudgetDelta(kind=BudgetKind.REPLAN),
        )
    async with database.begin() as session:
        second = await reserve_budget(
            session,
            workspace_id,
            task_id,
            uuid4(),
            "trace-bounded-replan-2",
            BudgetDelta(kind=BudgetKind.REPLAN),
        )

    assert first.allowed
    assert not second.allowed and second.reason == "replan"
    async with database() as session:
        budget = await session.get(TaskBudgetRow, task_id)
        accepted_replans = await session.scalar(
            select(func.count()).select_from(UsageRecordRow).where(
                UsageRecordRow.workspace_id == workspace_id,
                UsageRecordRow.task_id == task_id,
                UsageRecordRow.kind == BudgetKind.REPLAN.value,
            )
        )
    assert budget is not None
    assert budget.max_replans == 1 and budget.used_replans == 1
    assert budget.state == "EXCEEDED"
    assert accepted_replans == 1
    await engine.dispose()


async def test_durable_task_budget_keeps_five_replans():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    database = async_sessionmaker(engine, expire_on_commit=False)
    workspace_id, task_id, _ = await seed(database)
    async with database.begin() as session:
        session.add(new_task_budget(task_id, _task_goal(workspace_id, "supplier-risk")))

    decisions = []
    for index in range(6):
        async with database.begin() as session:
            decisions.append(
                await reserve_budget(
                    session,
                    workspace_id,
                    task_id,
                    uuid4(),
                    f"trace-durable-replan-{index + 1}",
                    BudgetDelta(kind=BudgetKind.REPLAN),
                )
            )

    assert all(item.allowed for item in decisions[:5])
    assert not decisions[5].allowed and decisions[5].reason == "replan"
    async with database() as session:
        budget = await session.get(TaskBudgetRow, task_id)
    assert budget is not None
    assert budget.max_replans == 5 and budget.used_replans == 5
    await engine.dispose()
