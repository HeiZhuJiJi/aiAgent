import os

import httpx

from apps.api.main import create_app
from tests.integration.test_api_identity import register_and_login


async def test_task_trace_is_workspace_scoped_and_has_runtime_root() -> None:
    app = create_app(os.environ["TEST_DATABASE_URL"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client,
    ):
        owner = await register_and_login(client, "trace-owner")
        outsider = await register_and_login(client, "trace-outsider")
        workspace_id = (
            await client.post("/workspaces", headers=owner, json={"name": "Trace Workspace"})
        ).json()["workspace_id"]
        task = await client.post(
            "/tasks",
            headers=owner,
            json={
                "goal": "Trace this durable supplier task",
                "constraints": [],
                "deliverables": ["trace"],
                "workspace_id": workspace_id,
                "time_budget_sec": 60,
                "token_budget": 1000,
                "cost_budget": 1,
                "user_context": "",
            },
        )
        assert task.status_code == 201
        task_id = task.json()["task_id"]

        response = await client.get(
            f"/tasks/{task_id}/trace",
            headers=owner,
            params={"workspace_id": workspace_id},
        )
        assert response.status_code == 200
        trace = response.json()
        assert trace["task_id"] == task_id
        assert [item["kind"] for item in trace["nodes"]] == ["ROUTER", "TASK"]
        assert trace["nodes"][0]["parent_id"] == "task:" + task_id
        assert trace["nodes"][1]["parent_id"] is None

        denied = await client.get(
            f"/tasks/{task_id}/trace",
            headers=outsider,
            params={"workspace_id": workspace_id},
        )
        assert denied.status_code == 403

