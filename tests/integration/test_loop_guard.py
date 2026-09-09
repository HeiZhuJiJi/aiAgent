import os
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.runtime.loop_guard import check_replan_loop
from enterprise_agent.storage.attempts import acquire_attempt, commit_evidence
from enterprise_agent.storage.tables import (
    AttemptRow,
    AuditRow,
    RuntimeGuardRow,
    StepRow,
    TaskRow,
    Workspace,
)
from tests.integration.test_fencing import seed


async def test_three_steps_without_new_evidence_abort_task_and_attempt():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    database = async_sessionmaker(engine, expire_on_commit=False)
    workspace_id, task_id = uuid4(), uuid4()
    step_ids = [uuid4(), uuid4(), uuid4()]
    async with database.begin() as session:
        session.add(Workspace(id=workspace_id, name="no-evidence-loop"))
        await session.flush()
        session.add(TaskRow(id=task_id, workspace_id=workspace_id, state="RUNNING"))
        await session.flush()
        session.add(RuntimeGuardRow(task_id=task_id, workspace_id=workspace_id))
        session.add_all(
            StepRow(
                id=step_id,
                workspace_id=workspace_id,
                task_id=task_id,
                state="READY",
            )
            for step_id in step_ids
        )

    outcomes = []
    attempt_ids = []
    for step_id in step_ids:
        attempt_id = uuid4()
        attempt_ids.append(attempt_id)
        async with database.begin() as session:
            token = (
                await acquire_attempt(
                    session,
                    workspace_id,
                    step_id,
                    attempt_id,
                    "evidence-worker",
                    "trace-no-evidence",
                )
            ).fencing_token
            outcomes.append(
                await commit_evidence(session, workspace_id, attempt_id, token, ())
            )
    assert outcomes == [True, True, False]
    async with database() as session:
        task = await session.get(TaskRow, task_id)
        final_step = await session.get(StepRow, step_ids[-1])
        final_attempt = await session.get(AttemptRow, attempt_ids[-1])
        guard = await session.get(RuntimeGuardRow, task_id)
        audit_count = await session.scalar(
            select(func.count())
            .select_from(AuditRow)
            .where(
                AuditRow.resource_id == task_id,
                AuditRow.action == "runtime.loop_aborted.no_evidence",
            )
        )
        assert task is not None and task.state == "FAILED"
        assert final_step is not None and final_step.state == "FAILED"
        assert final_attempt is not None and final_attempt.state == "FAILED"
        assert guard is not None and guard.state == "ABORTED"
        assert audit_count == 1
    await engine.dispose()


async def test_fourth_consecutive_replan_aborts_task():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    database = async_sessionmaker(engine, expire_on_commit=False)
    workspace_id, task_id, _ = await seed(database)
    actor_id = uuid4()
    decisions = []
    for _ in range(4):
        async with database.begin() as session:
            decisions.append(
                await check_replan_loop(
                    session,
                    workspace_id,
                    task_id,
                    actor_id,
                    "trace-replan-loop",
                )
            )
    assert [item.allowed for item in decisions] == [True, True, True, False]
    async with database() as session:
        task = await session.get(TaskRow, task_id)
        guard = await session.get(RuntimeGuardRow, task_id)
        assert task is not None and task.state == "FAILED"
        assert guard is not None and guard.abort_reason == "consecutive replans"
    await engine.dispose()
