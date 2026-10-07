import os

import httpx
from sqlalchemy import select

from apps.api.main import create_app
from enterprise_agent.storage.tables import AuditRow, Workspace
from tests.integration.test_api_identity import register_and_login


async def test_retention_policy_is_versioned_validated_and_audited() -> None:
    app = create_app(os.environ["TEST_DATABASE_URL"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client,
    ):
        owner = await register_and_login(client, "retention-owner")
        workspace_id = (
            await client.post(
                "/workspaces", headers=owner, json={"name": "Retention Workspace"}
            )
        ).json()["workspace_id"]
        current = await client.get(
            f"/workspaces/{workspace_id}/retention", headers=owner
        )
        assert current.status_code == 200
        payload = {
            "expected_version": current.json()["version"],
            "conversation_retention_days": 90,
            "trace_retention_days": 14,
            "artifact_retention_days": 180,
            "document_retention_days": 180,
            "evaluation_retention_days": 365,
            "audit_retention_days": None,
            "retain_audit_tombstone": True,
        }
        updated = await client.put(
            f"/workspaces/{workspace_id}/retention", headers=owner, json=payload
        )
        assert updated.status_code == 200
        assert updated.json()["version"] == current.json()["version"] + 1
        assert updated.json()["trace_retention_days"] == 14
        stale = await client.put(
            f"/workspaces/{workspace_id}/retention", headers=owner, json=payload
        )
        assert stale.status_code == 409
        async with app.state.sessions() as session:
            audit = await session.scalar(
                select(AuditRow).where(
                    AuditRow.workspace_id == workspace_id,
                    AuditRow.action == "retention.updated",
                )
            )
        assert audit is not None and audit.result["version"] == updated.json()["version"]


async def test_workspace_delete_stops_at_safe_ready_to_purge_boundary() -> None:
    app = create_app(os.environ["TEST_DATABASE_URL"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client,
    ):
        owner = await register_and_login(client, "deletion-owner")
        workspace = await client.post(
            "/workspaces", headers=owner, json={"name": "Deletion Boundary Workspace"}
        )
        workspace_id = workspace.json()["workspace_id"]
        requested = await client.request(
            "DELETE",
            f"/workspaces/{workspace_id}",
            headers=owner,
            json={"expected_version": workspace.json()["version"]},
        )
        assert requested.status_code == 202
        assert requested.json()["state"] == "READY_TO_PURGE"
        assert requested.json()["active_task_count"] == 0
        status = await client.get(
            f"/workspace-deletions/{requested.json()['request_id']}", headers=owner
        )
        assert status.status_code == 200 and status.json()["state"] == "READY_TO_PURGE"
        assert (await client.get("/workspaces", headers=owner)).json() == []
        async with app.state.sessions() as session:
            stored = await session.get(Workspace, workspace_id)
        assert stored is not None
        assert stored.lifecycle_state == "DELETING" and not stored.enabled
