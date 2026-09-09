import os
from uuid import uuid4

import httpx

from apps.api.main import create_app
from tests.integration.test_api_identity import register_and_login


async def test_conversation_and_message_api_is_idempotent_and_workspace_scoped():
    app = create_app(os.environ["TEST_DATABASE_URL"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        owner = await register_and_login(client, "conversation-owner")
        outsider = await register_and_login(client, "conversation-outsider")
        workspace_response = await client.post(
            "/workspaces", headers=owner, json={"name": "Conversation Workspace"}
        )
        workspace_id = workspace_response.json()["workspace_id"]
        conversation_id = str(uuid4())
        create_body = {
            "conversation_id": conversation_id,
            "workspace_id": workspace_id,
            "title": "Supplier analysis",
        }
        created = await client.post("/conversations", headers=owner, json=create_body)
        replay = await client.post("/conversations", headers=owner, json=create_body)
        assert created.status_code == replay.status_code == 201
        assert created.json() == replay.json()
        listed = await client.get(
            f"/workspaces/{workspace_id}/conversations", headers=owner
        )
        assert [item["conversation_id"] for item in listed.json()] == [conversation_id]

        message_id = str(uuid4())
        message_body = {
            "message_id": message_id,
            "workspace_id": workspace_id,
            "content": "Assess Supplier A using the approved evidence.",
        }
        message = await client.post(
            f"/conversations/{conversation_id}/messages",
            headers=owner,
            json=message_body,
        )
        message_replay = await client.post(
            f"/conversations/{conversation_id}/messages",
            headers=owner,
            json=message_body,
        )
        assert message.status_code == message_replay.status_code == 201
        assert message.json() == message_replay.json()
        assert message.json()["role"] == "USER"
        messages = await client.get(
            f"/conversations/{conversation_id}/messages",
            headers=owner,
            params={"workspace_id": workspace_id},
        )
        assert [item["message_id"] for item in messages.json()] == [message_id]

        forbidden_list = await client.get(
            f"/workspaces/{workspace_id}/conversations", headers=outsider
        )
        forbidden_write = await client.post(
            f"/conversations/{conversation_id}/messages",
            headers=outsider,
            json={**message_body, "message_id": str(uuid4())},
        )
        assert forbidden_list.status_code == forbidden_write.status_code == 403
        assert forbidden_write.json()["error"]["code"] == "POLICY_DENIED"
