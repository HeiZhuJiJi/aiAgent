import asyncio
import os
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select
from temporalio.client import Client
from temporalio.service import RPCError

from apps.api.main import create_app
from enterprise_agent.runtime.outbox_dispatcher import dispatch_once
from enterprise_agent.runtime.workflows import DurableTaskInput, DurableTaskWorkflow
from enterprise_agent.storage.tables import DomainCommandRow, TaskRow
from tests.failure.test_worker_crash import start_worker
from tests.integration.test_api_identity import register_and_login


async def create_running_api_task(client, app, label):
    headers = await register_and_login(client, label)
    created_workspace = await client.post(
        "/workspaces", headers=headers, json={"name": "Task control workspace"}
    )
    assert created_workspace.status_code == 201
    workspace_id = UUID(created_workspace.json()["workspace_id"])
    created_task = await client.post(
        "/tasks",
        headers=headers,
        json={
            "goal": "Exercise durable task controls",
            "constraints": [],
            "deliverables": ["result"],
            "workspace_id": str(workspace_id),
            "time_budget_sec": 600,
            "token_budget": 1000,
            "cost_budget": 1,
            "user_context": "",
        },
    )
    assert created_task.status_code == 201
    task_id = UUID(created_task.json()["task_id"])
    async with app.state.sessions.begin() as session:
        task = await session.get(TaskRow, task_id)
        assert task is not None
        task.state = "RUNNING"
    return headers, workspace_id, task_id


async def wait_for_control_state(handle, expected):
    for _ in range(100):
        try:
            if await handle.query(DurableTaskWorkflow.control_state) == expected:
                return
        except RPCError:
            pass
        await asyncio.sleep(0.05)
    raise AssertionError("workflow did not reach control state " + expected)


async def test_pause_and_resume_api_events_control_real_temporal_workflow():
    app = create_app(os.environ["TEST_DATABASE_URL"])
    client = await Client.connect("127.0.0.1:57233")
    task_queue = "task-control-" + str(uuid4())
    worker = None
    handle = None
    completed = False
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        headers, workspace_id, task_id = await create_running_api_task(http, app, "pause-resume")
        handle = await client.start_workflow(
            DurableTaskWorkflow.run,
            DurableTaskInput(
                task_id=str(task_id),
                workspace_id=str(workspace_id),
                step_keys=["after-resume"],
            ),
            id="task-" + str(task_id),
            task_queue=task_queue,
        )
        try:
            paused = await http.post(
                f"/workspaces/{workspace_id}/tasks/{task_id}/pause",
                headers=headers,
                json={"expected_version": 1},
            )
            assert paused.status_code == 200 and paused.json()["state"] == "PAUSED"
            assert (
                await dispatch_once(
                    app.state.sessions,
                    client,
                    "task-control-publisher",
                    workspace_id,
                )
            ).event_type == "task.control.requested"
            worker = start_worker(task_queue)
            await wait_for_control_state(handle, "PAUSED")
            async with app.state.sessions() as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(DomainCommandRow)
                    .where(DomainCommandRow.task_id == task_id)
                )
                assert count == 0

            resumed = await http.post(
                f"/workspaces/{workspace_id}/tasks/{task_id}/resume",
                headers=headers,
                json={"expected_version": 2},
            )
            assert resumed.status_code == 200 and resumed.json()["state"] == "READY"
            await dispatch_once(
                app.state.sessions,
                client,
                "task-control-publisher",
                workspace_id,
            )
            result = await asyncio.wait_for(handle.result(), timeout=20)
            assert result.state == "COMPLETED"
            assert result.completed_steps == ["after-resume"]
            async with app.state.sessions() as session:
                task = await session.get(TaskRow, task_id)
                assert task is not None and task.state == "COMPLETED"
            completed = True
        finally:
            if worker is not None and worker.poll() is None:
                worker.kill()
                worker.wait(timeout=10)
            if handle is not None and not completed:
                try:
                    await handle.terminate("task-control test cleanup")
                except RPCError:
                    pass


async def test_cancel_api_event_finishes_real_temporal_workflow_and_postgres():
    app = create_app(os.environ["TEST_DATABASE_URL"])
    client = await Client.connect("127.0.0.1:57233")
    task_queue = "task-cancel-" + str(uuid4())
    worker = None
    handle = None
    completed = False
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        headers, workspace_id, task_id = await create_running_api_task(http, app, "cancel")
        handle = await client.start_workflow(
            DurableTaskWorkflow.run,
            DurableTaskInput(
                task_id=str(task_id),
                workspace_id=str(workspace_id),
                step_keys=["must-not-run"],
            ),
            id="task-" + str(task_id),
            task_queue=task_queue,
        )
        try:
            cancelled = await http.post(
                f"/workspaces/{workspace_id}/tasks/{task_id}/cancel",
                headers=headers,
                json={"expected_version": 1},
            )
            assert cancelled.status_code == 200
            await dispatch_once(
                app.state.sessions,
                client,
                "task-cancel-publisher",
                workspace_id,
            )
            worker = start_worker(task_queue)
            result = await asyncio.wait_for(handle.result(), timeout=20)
            assert result.state == "CANCELLED"
            assert result.completed_steps == []
            async with app.state.sessions() as session:
                task = await session.get(TaskRow, task_id)
                count = await session.scalar(
                    select(func.count())
                    .select_from(DomainCommandRow)
                    .where(DomainCommandRow.task_id == task_id)
                )
                assert task is not None and task.state == "CANCELLED"
                assert count == 0
            completed = True
        finally:
            if worker is not None and worker.poll() is None:
                worker.kill()
                worker.wait(timeout=10)
            if handle is not None and not completed:
                try:
                    await handle.terminate("task-cancel test cleanup")
                except RPCError:
                    pass
