import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.client import Client

from enterprise_agent.contracts.domain import FailureCode, TaskGoal
from enterprise_agent.domain.states import DomainError
from enterprise_agent.runtime import reconciler
from enterprise_agent.runtime.reconciler import reconcile_once
from enterprise_agent.runtime.workflows import DurableTaskInput, DurableTaskWorkflow
from enterprise_agent.storage.plans import persist_plan, persist_snapshot
from enterprise_agent.storage.steps import materialize_plan_steps
from enterprise_agent.storage.tables import (
    ConsistencyIncidentRow,
    OutboxEventRow,
    TaskRow,
)
from tests.integration.test_checkpoints import checkpoint_task
from tests.integration.test_immutable_plans import plan, snapshot


@pytest.fixture
async def database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _canonical_task(database):
    workspace_id, task_id, *_rest, active_plan, _operation = await checkpoint_task(database)
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
        await materialize_plan_steps(session, active_plan)
    return workspace_id, task_id


async def _canonical_task_for_workspace(database, workspace_id):
    task_id = uuid4()
    frozen = snapshot(workspace_id)
    active_plan = plan(workspace_id, task_id, frozen.snapshot_id)
    async with database.begin() as session:
        await persist_snapshot(session, frozen)
        session.add(
            TaskRow(
                id=task_id,
                workspace_id=workspace_id,
                state="RUNNING",
                execution_snapshot_id=frozen.snapshot_id,
                goal_payload=TaskGoal(
                    workspace_id=workspace_id,
                    goal="Reconcile supplier task",
                    constraints=(),
                    deliverables=("report",),
                    time_budget_sec=600,
                    token_budget=1000,
                    cost_budget=1,
                ).model_dump(mode="json"),
            )
        )
        await session.flush()
        await persist_plan(session, active_plan)
        await materialize_plan_steps(session, active_plan)
    return task_id


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


async def test_unrepairable_task_records_manual_incident_without_aborting_repairs(
    database,
) -> None:
    workspace_id, repairable_task_id = await _canonical_task(database)
    malformed_task_id = uuid4()
    async with database.begin() as session:
        session.add(
            TaskRow(
                id=malformed_task_id,
                workspace_id=workspace_id,
                state="RUNNING",
                goal_payload=TaskGoal(
                    workspace_id=workspace_id,
                    goal="Malformed historical task",
                    constraints=(),
                    deliverables=("manual review",),
                    time_budget_sec=600,
                    token_budget=1000,
                    cost_budget=1,
                ).model_dump(mode="json"),
            )
        )

    temporal = await Client.connect("127.0.0.1:57233")
    summary = await reconcile_once(database, temporal, workspace_id=workspace_id)

    assert summary.inspected == 2
    assert summary.incidents == 2
    assert summary.repairs_enqueued == 1
    async with database() as session:
        manual = await session.scalar(
            select(ConsistencyIncidentRow).where(
                ConsistencyIncidentRow.workspace_id == workspace_id,
                ConsistencyIncidentRow.task_id == malformed_task_id,
            )
        )
        repairable_events = await session.scalar(
            select(func.count())
            .select_from(OutboxEventRow)
            .where(
                OutboxEventRow.workspace_id == workspace_id,
                OutboxEventRow.aggregate_id == repairable_task_id,
                OutboxEventRow.event_type == "task.execution.requested",
            )
        )
        malformed_events = await session.scalar(
            select(func.count())
            .select_from(OutboxEventRow)
            .where(
                OutboxEventRow.workspace_id == workspace_id,
                OutboxEventRow.aggregate_id == malformed_task_id,
                OutboxEventRow.event_type == "task.execution.requested",
            )
        )
    assert manual is not None
    assert manual.detail["repair"] == "MANUAL_REVIEW_REQUIRED"
    assert manual.detail["repair_failure_code"] == "CONSISTENCY_VIOLATION"
    assert manual.detail["repair_failure"] == "active task has no execution snapshot"
    assert repairable_events == 1
    assert malformed_events == 0


async def test_one_repair_failure_does_not_abort_other_tasks(
    database,
    monkeypatch,
) -> None:
    workspace_id, failing_task_id = await _canonical_task(database)
    repairable_task_id = await _canonical_task_for_workspace(database, workspace_id)
    original_enqueue = reconciler._enqueue_missing_execution

    async def enqueue_or_fail(session, task, incident_id):
        if task.id == failing_task_id:
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "historical outbox identity mismatch",
            )
        return await original_enqueue(session, task, incident_id)

    monkeypatch.setattr(reconciler, "_enqueue_missing_execution", enqueue_or_fail)
    temporal = await Client.connect("127.0.0.1:57233")
    summary = await reconcile_once(database, temporal, workspace_id=workspace_id)

    assert summary.inspected == 2
    assert summary.incidents == 1
    assert summary.repairs_enqueued == 1
    async with database() as session:
        failing_events = await session.scalar(
            select(func.count())
            .select_from(OutboxEventRow)
            .where(
                OutboxEventRow.aggregate_id == failing_task_id,
                OutboxEventRow.event_type == "task.execution.requested",
            )
        )
        repairable_events = await session.scalar(
            select(func.count())
            .select_from(OutboxEventRow)
            .where(
                OutboxEventRow.aggregate_id == repairable_task_id,
                OutboxEventRow.event_type == "task.execution.requested",
            )
        )
    assert failing_events == 0
    assert repairable_events == 1


async def test_plan_without_materialized_steps_requires_manual_review(database) -> None:
    workspace_id, task_id, *_rest, active_plan, _operation = await checkpoint_task(database)
    malformed_plan = active_plan.model_copy(
        update={"plan_id": uuid4(), "version": active_plan.version + 1}
    )
    async with database.begin() as session:
        await persist_plan(session, malformed_plan)
        task = await session.get(TaskRow, task_id)
        assert task is not None
        task.goal_payload = TaskGoal(
            workspace_id=workspace_id,
            goal="Malformed plan recovery check",
            constraints=(),
            deliverables=("manual review",),
            time_budget_sec=600,
            token_budget=1000,
            cost_budget=1,
        ).model_dump(mode="json")

    temporal = await Client.connect("127.0.0.1:57233")
    summary = await reconcile_once(database, temporal, workspace_id=workspace_id)

    assert summary.inspected == 1
    assert summary.incidents == 1
    assert summary.repairs_enqueued == 0
    async with database() as session:
        incident = await session.scalar(
            select(ConsistencyIncidentRow).where(
                ConsistencyIncidentRow.workspace_id == workspace_id,
                ConsistencyIncidentRow.task_id == task_id,
            )
        )
    assert incident is not None
    assert incident.detail["repair"] == "MANUAL_REVIEW_REQUIRED"
    assert incident.detail["repair_failure"] == "active task Plan has no materialized steps"
