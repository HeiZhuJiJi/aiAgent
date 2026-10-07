import os
from uuid import uuid4

import httpx
from sqlalchemy import select

from apps.api.main import create_app
from enterprise_agent.storage.tables import AuditRow, ConversationRow
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

        route = await client.post(
            "/chat/route",
            headers=owner,
            json={
                "workspace_id": workspace_id,
                "conversation_id": conversation_id,
                "content": "今天有什么最新公开信息？",
                "web_mode": "AUTO",
            },
        )
        assert route.status_code == 200
        assert route.json()["route"] == "WEB"
        assert route.json()["reason"] in {"EXPLICIT_WEB", "CURRENT_INFORMATION"}

        unavailable_document = await client.post(
            "/chat/route",
            headers=owner,
            json={
                "workspace_id": workspace_id,
                "conversation_id": conversation_id,
                "content": "总结附件",
                "document_version_id": str(uuid4()),
            },
        )
        assert unavailable_document.status_code == 403
        assert unavailable_document.json()["error"]["code"] == "POLICY_DENIED"

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

        forbidden_route = await client.post(
            "/chat/route",
            headers=outsider,
            json={
                "workspace_id": workspace_id,
                "conversation_id": conversation_id,
                "content": "今天有什么新闻？",
            },
        )
        assert forbidden_route.status_code == 403


async def test_conversation_delete_hides_history_but_retains_audited_rows():
    app = create_app(os.environ["TEST_DATABASE_URL"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        owner = await register_and_login(client, "conversation-delete-owner")
        workspace_id = (
            await client.post("/workspaces", headers=owner, json={"name": "Delete Conversation"})
        ).json()["workspace_id"]
        conversation_id = str(uuid4())
        created = await client.post(
            "/conversations",
            headers=owner,
            json={
                "conversation_id": conversation_id,
                "workspace_id": workspace_id,
                "title": "Temporary conversation",
            },
        )
        deleted = await client.request(
            "DELETE",
            f"/conversations/{conversation_id}",
            headers=owner,
            json={"workspace_id": workspace_id, "expected_version": created.json()["version"]},
        )
        assert deleted.status_code == 204
        listed = await client.get(f"/workspaces/{workspace_id}/conversations", headers=owner)
        assert listed.json() == []
        messages = await client.get(
            f"/conversations/{conversation_id}/messages",
            headers=owner,
            params={"workspace_id": workspace_id},
        )
        assert messages.status_code == 403
        async with app.state.sessions() as session:
            row = await session.scalar(
                select(ConversationRow).where(ConversationRow.id == conversation_id)
            )
            audit = await session.scalar(
                select(AuditRow).where(
                    AuditRow.workspace_id == workspace_id,
                    AuditRow.action == "conversation.deleted",
                )
            )
        assert row is not None and row.deleted_at is not None
        assert audit is not None
