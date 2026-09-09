import os
from uuid import uuid4

import httpx

from apps.api.main import create_app
from enterprise_agent.storage.tables import WorkspaceMemberRow
from tests.integration.test_api_identity import register_and_login


async def test_audit_api_requires_explicit_permission_and_workspace_membership():
    app = create_app(os.environ["TEST_DATABASE_URL"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        owner = await register_and_login(client, "audit-owner")
        member = await register_and_login(client, "audit-member")
        outsider = await register_and_login(client, "audit-outsider")
        owner_identity = (await client.get("/auth/me", headers=owner)).json()
        member_identity = (await client.get("/auth/me", headers=member)).json()
        workspace = await client.post(
            "/workspaces", headers=owner, json={"name": "Audited Workspace"}
        )
        workspace_id = workspace.json()["workspace_id"]
        async with app.state.sessions.begin() as session:
            session.add(
                WorkspaceMemberRow(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    user_id=member_identity["user_id"],
                    role="MEMBER",
                )
            )

        allowed = await client.get("/audit", headers=owner, params={"workspace_id": workspace_id})
        member_denied = await client.get(
            "/audit", headers=member, params={"workspace_id": workspace_id}
        )
        outsider_denied = await client.get(
            "/audit", headers=outsider, params={"workspace_id": workspace_id}
        )
        assert allowed.status_code == 200
        assert allowed.json()[0]["action"] == "workspace.created"
        assert allowed.json()[0]["actor_type"] == "USER"
        assert allowed.json()[0]["resource_type"] == "WORKSPACE"
        assert allowed.json()[0]["result"] == {"version": 1}
        assert allowed.json()[0]["actor_id"] == owner_identity["user_id"]
        assert member_denied.status_code == outsider_denied.status_code == 403
        assert member_denied.json()["error"]["message"] == "audit permission required"
