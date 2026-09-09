import os

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.client import Client

from enterprise_agent.contracts.domain import TaskGoal
from enterprise_agent.runtime.reconciler import reconcile_once
from enterprise_agent.runtime.workflows import DurableTaskInput, DurableTaskWorkflow
from enterprise_agent.storage.tables import (
    ConsistencyIncidentRow,
    OutboxEventRow,
    TaskRow,
)
from tests.integration.test_checkpoints import checkpoint_task


@pytest.fixture
async def database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _canonical_task(database):
    workspace_id, task_id, *_ = await checkpoint_task(database)
    async with database.begin() as session:
        task = await session.get(TaskRow, task_id)
        assert task is not None
        task.goal_payload = TaskGoal(
            workspace_id=workspace_id,
            goal="Reconcile supplier task",
            constraints=(),
            deliverables=("report",),
            time_budget_sec=600,
            token_budget=1000,
            cost_budget=1,
        ).model_dump(mode="json")
    return workspace_id, task_id


async def test_missing_temporal_execution_records_incident_and_enqueues_once(
    database,
) -> None:
    workspace_id, task_id = await _canonical_task(database)
    temporal = await Client.connect("127.0.0.1:57233")
    first = await reconcile_once(database, temporal, workspace_id=workspace_id)
    second = await reconcile_once(database, temporal, workspace_id=workspace_id)

    assert first.inspected == 1
    assert first.incidents == 1 and first.repairs_enqueued == 1
    assert second.valid == 1 and second.incidents == 0 and second.repairs_enqueued == 0
    async with database() as session:
        incidents = await session.scalar(
            select(func.count())
            .select_from(ConsistencyIncidentRow)
            .where(
                ConsistencyIncidentRow.workspace_id == workspace_id,
                ConsistencyIncidentRow.task_id == task_id,
            )
        )
        repairs = await session.scalar(
            select(func.count())
            .select_from(OutboxEventRow)
            .where(
                OutboxEventRow.workspace_id == workspace_id,
                OutboxEventRow.aggregate_id == task_id,
                OutboxEventRow.event_type == "task.execution.requested",
                OutboxEventRow.state == "PENDING",
            )
        )
    assert incidents == 1
    assert repairs == 1


async def test_running_temporal_and_postgres_combination_is_valid(database) -> None:
    workspace_id, task_id = await _canonical_task(database)
    temporal = await Client.connect("127.0.0.1:57233")
    handle = await temporal.start_workflow(
        DurableTaskWorkflow.run,
        DurableTaskInput(task_id=str(task_id), workspace_id=str(workspace_id), step_keys=[]),
        id=f"task-{task_id}",
        task_queue="reconciler-unpolled",
    )
    try:
        summary = await reconcile_once(database, temporal, workspace_id=workspace_id)
        assert summary.inspected == 1
        assert summary.valid == 1
        assert summary.incidents == 0
        assert summary.repairs_enqueued == 0
    finally:
        await handle.terminate("test cleanup")
