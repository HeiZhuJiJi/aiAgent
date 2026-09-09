import asyncio
import os
import subprocess
import sys
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.client import Client
from temporalio.service import RPCError

from enterprise_agent.runtime.workflows import DurableTaskInput, DurableTaskWorkflow
from enterprise_agent.storage.tables import DomainCommandRow, TaskRow
from tests.integration.test_fencing import seed


async def command_count(sessions, task_id):
    async with sessions() as session:
        return await session.scalar(
            select(func.count())
            .select_from(DomainCommandRow)
            .where(DomainCommandRow.task_id == task_id)
        )


def start_worker(task_queue: str) -> subprocess.Popen[bytes]:
    environment = os.environ.copy()
    environment["TEMPORAL_ADDRESS"] = "127.0.0.1:57233"
    environment["TEMPORAL_TASK_QUEUE"] = task_queue
    return subprocess.Popen(
        [sys.executable, "-m", "apps.worker.main"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


async def test_actual_worker_process_crash_resumes_without_recommitting_completed_step():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    workspace_id, task_id, _ = await seed(sessions)
    client = await Client.connect("127.0.0.1:57233")
    task_queue = "crash-test-" + str(uuid4())
    workflow_id = "recovery-" + str(uuid4())
    worker_a = start_worker(task_queue)
    worker_b = None
    handle = None
    completed = False
    try:
        handle = await client.start_workflow(
            DurableTaskWorkflow.run,
            DurableTaskInput(
                task_id=str(task_id),
                workspace_id=str(workspace_id),
                step_keys=["protected-step", "after-recovery"],
                failure_hold_seconds=30,
            ),
            id=workflow_id,
            task_queue=task_queue,
        )
        for _ in range(100):
            if worker_a.poll() is not None:
                _, error = worker_a.communicate(timeout=1)
                raise AssertionError(
                    "first worker exited before activity commit: "
                    + error.decode("utf-8", errors="replace")[-2000:]
                )
            if await command_count(sessions, task_id) == 1:
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("first activity did not commit before crash deadline")
        worker_a.kill()
        worker_a.wait(timeout=10)
        worker_b = start_worker(task_queue)
        result = await asyncio.wait_for(handle.result(), timeout=20)
        assert result.completed_steps == ["protected-step", "after-recovery"]
        assert await command_count(sessions, task_id) == 2
        async with sessions() as session:
            task = await session.scalar(
                select(TaskRow).where(
                    TaskRow.workspace_id == workspace_id,
                    TaskRow.id == task_id,
                )
            )
            assert task is not None and task.state == "COMPLETED"
            rows = (
                await session.scalars(
                    select(DomainCommandRow)
                    .where(DomainCommandRow.task_id == task_id)
                    .order_by(DomainCommandRow.committed_at)
                )
            ).all()
            assert [row.step_key for row in rows] == ["protected-step", "after-recovery"]
            assert len({row.id for row in rows}) == 2
        completed = True
    finally:
        for process in (worker_a, worker_b):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        if handle is not None and not completed:
            try:
                await handle.terminate("failure-test cleanup")
            except RPCError:
                pass
        await engine.dispose()
