import os
from uuid import uuid4

import httpx
from sqlalchemy import select

from apps.api.main import create_app
from enterprise_agent.storage.plans import load_snapshot
from enterprise_agent.storage.tables import (
    OutboxEventRow,
    RuntimeGuardRow,
    TaskBudgetRow,
    TaskRow,
)


async def register_and_login(client: httpx.AsyncClient, label: str) -> dict[str, str]:
    email = f"{label}-{uuid4()}@example.test"
    password = "correct horse battery staple"
    registered = await client.post("/auth/register", json={"email": email, "password": password})
    assert registered.status_code == 201
    assert "password" not in registered.text.casefold()
    logged_in = await client.post("/auth/login", json={"email": email, "password": password})
    assert logged_in.status_code == 200
    token = logged_in.json()["access_token"]
    assert token and token not in registered.text
    return {"Authorization": "Bearer " + token}


async def test_local_auth_and_api_workspace_isolation():
    app = create_app(os.environ["TEST_DATABASE_URL"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        first = await register_and_login(client, "first")
        second = await register_and_login(client, "second")
        me = await client.get("/auth/me", headers=first)
        assert me.status_code == 200
        created = await client.post("/workspaces", headers=first, json={"name": "First Workspace"})
        assert created.status_code == 201
        workspace_id = created.json()["workspace_id"]
        own_list = await client.get("/workspaces", headers=first)
        assert own_list.status_code == 200
        assert [item["workspace_id"] for item in own_list.json()] == [workspace_id]
        forbidden = await client.get(f"/workspaces/{workspace_id}", headers=second)
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "POLICY_DENIED"
        assert forbidden.headers["x-trace-id"] == forbidden.json()["error"]["trace_id"]
        assert (await client.get("/workspaces", headers=second)).json() == []
        task_created = await client.post(
            "/tasks",
            headers=first,
            json={
                "goal": "Assess supplier risk",
                "constraints": ["approval required"],
                "deliverables": ["report"],
                "workspace_id": workspace_id,
                "time_budget_sec": 600,
                "token_budget": 10000,
                "cost_budget": 5,
                "user_context": "",
            },
        )
        assert task_created.status_code == 201
        task = task_created.json()
        assert task["execution_snapshot_id"]
        listed = await client.get(f"/workspaces/{workspace_id}/tasks", headers=first)
        assert [item["task_id"] for item in listed.json()] == [task["task_id"]]
        task_forbidden = await client.get(
            f"/workspaces/{workspace_id}/tasks/{task['task_id']}", headers=second
        )
        assert task_forbidden.status_code == 403
        async with app.state.sessions() as session:
            stored_task = await session.get(TaskRow, task["task_id"])
            assert stored_task is not None
            budget = await session.get(TaskBudgetRow, stored_task.id)
            guard = await session.get(RuntimeGuardRow, stored_task.id)
            snapshot = await load_snapshot(
                session, stored_task.workspace_id, stored_task.execution_snapshot_id
            )
            assert snapshot.snapshot_id == stored_task.execution_snapshot_id
            assert snapshot.runtime_version == "0.1.0"
            assert budget is not None
            assert budget.max_tokens == 10000 and budget.max_cost == 5
            assert guard is not None and guard.state == "ACTIVE"
        async with app.state.sessions.begin() as session:
            stored_task = await session.get(TaskRow, task["task_id"])
            stored_task.state = "RUNNING"
        paused = await client.post(
            f"/workspaces/{workspace_id}/tasks/{task['task_id']}/pause",
            headers=first,
            json={"expected_version": 1},
        )
        assert paused.status_code == 200
        assert paused.json()["state"] == "PAUSED" and paused.json()["version"] == 2
        resumed = await client.post(
            f"/workspaces/{workspace_id}/tasks/{task['task_id']}/resume",
            headers=first,
            json={"expected_version": 2},
        )
        assert resumed.status_code == 200
        assert resumed.json()["state"] == "READY" and resumed.json()["version"] == 3
        stale = await client.post(
            f"/workspaces/{workspace_id}/tasks/{task['task_id']}/pause",
            headers=first,
            json={"expected_version": 2},
        )
        assert stale.status_code == 409
        cancelled = await client.post(
            f"/workspaces/{workspace_id}/tasks/{task['task_id']}/cancel",
            headers=first,
            json={"expected_version": 3},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "CANCELLING"
        async with app.state.sessions() as session:
            events = (
                await session.scalars(
                        select(OutboxEventRow).where(
                            OutboxEventRow.workspace_id == workspace_id,
                            OutboxEventRow.aggregate_id == task["task_id"],
                        ).order_by(OutboxEventRow.created_at, OutboxEventRow.id)
                    )
                ).all()
            assert [event.payload["action"] for event in events] == [
                "pause",
                "resume",
                "cancel",
            ]
        unauthenticated = await client.get("/workspaces")
        assert unauthenticated.status_code == 403
        assert set(unauthenticated.json()) == {"error"}
        logged_out = await client.post("/auth/logout", headers=first)
        assert logged_out.status_code == 204
        assert (await client.get("/auth/me", headers=first)).status_code == 403


async def test_duplicate_email_and_bad_login_use_uniform_errors():
    app = create_app(os.environ["TEST_DATABASE_URL"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        email = f"duplicate-{uuid4()}@example.test"
        body = {"email": email, "password": "a secure local password"}
        assert (await client.post("/auth/register", json=body)).status_code == 201
        duplicate = await client.post("/auth/register", json={**body, "email": email.upper()})
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "VALIDATION_FAILED"
        bad = await client.post(
            "/auth/login", json={"email": email, "password": "wrong password value"}
        )
        assert bad.status_code == 403
        assert bad.json()["error"]["message"] == "invalid credentials"
