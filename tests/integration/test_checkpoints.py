import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.runtime.activities import (
    load_plan_execution_binding,
    persist_plan_continuation,
)
from enterprise_agent.storage.attempts import acquire_attempt
from enterprise_agent.storage.checkpoints import create_checkpoint, load_latest_checkpoint
from enterprise_agent.storage.operations import OperationIntent, prepare_operation
from enterprise_agent.storage.plans import persist_plan, persist_snapshot
from enterprise_agent.storage.tables import CheckpointRow, TaskBudgetRow, TaskRow
from tests.integration.test_fencing import seed
from tests.integration.test_immutable_plans import plan, snapshot


@pytest.fixture
async def database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def checkpoint_task(database):
    workspace_id, task_id, step_id = await seed(database)
    frozen = snapshot(workspace_id)
    active_plan = plan(workspace_id, task_id, frozen.snapshot_id)
    attempt_id = uuid4()
    async with database.begin() as session:
        await persist_snapshot(session, frozen)
        task = await session.get(TaskRow, task_id)
        assert task is not None
        task.execution_snapshot_id = frozen.snapshot_id
        await session.flush()
        await persist_plan(session, active_plan)
        session.add(
            TaskBudgetRow(
                task_id=task_id,
                workspace_id=workspace_id,
                max_steps=10,
                max_replans=2,
                max_llm_calls=10,
                max_capability_calls=10,
                max_tokens=1000,
                max_cost=10,
                deadline=datetime.now(UTC) + timedelta(hours=1),
                used_steps=2,
                used_tokens=120,
            )
        )
        fencing_token = (
            await acquire_attempt(
                session,
                workspace_id,
                step_id,
                attempt_id,
                "checkpoint-worker",
                "trace-checkpoint",
            )
        ).fencing_token
        operation = await prepare_operation(
            session,
            OperationIntent(
                workspace_id=workspace_id,
                step_id=step_id,
                logical_key="checkpoint-operation",
                capability_id="supplier.lookup",
                capability_version="1.0.0",
                arguments={"supplier_id": str(uuid4())},
            ),
            attempt_id,
            fencing_token,
        )
    return workspace_id, task_id, step_id, attempt_id, frozen, active_plan, operation


async def test_checkpoint_persists_only_reconstructable_references_and_replays(database):
    workspace_id, task_id, step_id, attempt_id, frozen, active_plan, operation = (
        await checkpoint_task(database)
    )
    artifact_id = uuid4()
    async with database.begin() as session:
        first = await create_checkpoint(
            session,
            workspace_id,
            task_id,
            resume_token="resume-token-0001",
            context_summary="Supplier evidence collection is running.",
            artifact_refs=(artifact_id,),
        )
        replay = await create_checkpoint(
            session,
            workspace_id,
            task_id,
            resume_token="resume-token-0001",
            context_summary="Supplier evidence collection is running.",
            artifact_refs=(artifact_id,),
        )
    assert first == replay
    assert first.sequence == 1
    assert first.active_plan_id == active_plan.plan_id
    assert first.running_step == step_id
    assert first.attempt == attempt_id
    assert first.operation_refs == (operation.operation_id,)
    assert first.execution_snapshot_id == frozen.snapshot_id
    assert first.artifact_refs == (artifact_id,)
    assert first.budget_usage["steps"] == 2
    assert first.budget_usage["tokens"] == 120
    payload_text = str(first.model_dump(mode="json"))
    assert "Supplier evidence collection is running." in payload_text
    assert "Python" not in payload_text
    async with database() as session:
        latest = await load_latest_checkpoint(session, workspace_id, task_id)
        foreign = await load_latest_checkpoint(session, uuid4(), task_id)
    assert latest == first
    assert foreign is None


@pytest.mark.parametrize(
    "statement",
    [
        update(CheckpointRow).values(sequence=99),
        delete(CheckpointRow),
    ],
)
async def test_database_rejects_checkpoint_mutation(database, statement):
    workspace_id, task_id, *_ = await checkpoint_task(database)
    async with database.begin() as session:
        checkpoint = await create_checkpoint(
            session,
            workspace_id,
            task_id,
            resume_token="immutable-token-01",
            context_summary="Safe resume boundary.",
        )
    with pytest.raises(DBAPIError):
        async with database.begin() as session:
            await session.execute(
                statement.where(CheckpointRow.id == checkpoint.checkpoint_id)
            )


async def test_continuation_activity_persists_and_revalidates_canonical_binding(
    database, monkeypatch
):
    workspace_id, task_id, _, _, frozen, active_plan, _ = await checkpoint_task(database)
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    token = "continue-" + str(uuid4())
    persisted = await persist_plan_continuation(
        {
            "workspace_id": str(workspace_id),
            "task_id": str(task_id),
            "active_plan_id": str(active_plan.plan_id),
            "resume_token": token,
            "runtime_version": frozen.runtime_version,
        }
    )

    assert set(persisted) == {
        "task_id",
        "active_plan_id",
        "resume_token",
        "runtime_version",
    }
    binding = await load_plan_execution_binding(persisted)
    assert binding == {
        "workspace_id": str(workspace_id),
        "active_plan_id": str(active_plan.plan_id),
        "resume_token": token,
        "runtime_version": frozen.runtime_version,
    }
    async with database() as session:
        checkpoint = await load_latest_checkpoint(session, workspace_id, task_id)
    assert checkpoint is not None
    assert checkpoint.resume_token == token
    assert checkpoint.active_plan_id == active_plan.plan_id
