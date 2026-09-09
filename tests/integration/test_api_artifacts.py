import os
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from minio import Minio

from apps.api.main import create_app
from enterprise_agent.storage.artifacts import ArtifactStore
from tests.integration.test_api_identity import register_and_login


async def test_artifact_metadata_and_download_api_enforce_workspace_membership():
    password = dotenv_values(".env").get("MINIO_ROOT_PASSWORD")
    assert password
    store = ArtifactStore(
        Minio(
            "127.0.0.1:59000",
            access_key="platform",
            secret_key=str(password),
            secure=False,
        ),
        bucket="runtime-api-artifacts",
    )
    app = create_app(os.environ["TEST_DATABASE_URL"], artifact_store=store)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        owner = await register_and_login(client, "artifact-owner")
        outsider = await register_and_login(client, "artifact-outsider")
        workspace = await client.post(
            "/workspaces", headers=owner, json={"name": "Artifact Workspace"}
        )
        workspace_id = workspace.json()["workspace_id"]
        task = await client.post(
            "/tasks",
            headers=owner,
            json={
                "goal": "Create a supplier report",
                "constraints": [],
                "deliverables": ["report"],
                "workspace_id": workspace_id,
                "time_budget_sec": 60,
                "token_budget": 1000,
                "cost_budget": 1,
                "user_context": "",
            },
        )
        task_id = task.json()["task_id"]
        artifact_id = uuid4()
        content = b"# Supplier report\nGrounded result.\n"
        async with app.state.sessions.begin() as session:
            manifest = await store.put(
                session,
                artifact_id=artifact_id,
                workspace_id=workspace_id,
                task_id=task_id,
                content=content,
                media_type="text/markdown",
                claim_ids=(),
            )

        listed = await client.get(
            "/artifacts", headers=owner, params={"workspace_id": workspace_id}
        )
        metadata = await client.get(
            f"/artifacts/{artifact_id}",
            headers=owner,
            params={"workspace_id": workspace_id},
        )
        download = await client.get(
            f"/artifacts/{artifact_id}/download",
            headers=owner,
            params={"workspace_id": workspace_id},
        )
        assert listed.status_code == metadata.status_code == download.status_code == 200
        assert listed.json() == [metadata.json()]
        assert metadata.json()["sha256"] == manifest.sha256
        assert download.content == content
        assert download.headers["etag"] == f'"{manifest.sha256}"'
        assert download.headers["content-type"].startswith("text/markdown")

        denied = await client.get(
            f"/artifacts/{artifact_id}/download",
            headers=outsider,
            params={"workspace_id": workspace_id},
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "POLICY_DENIED"
