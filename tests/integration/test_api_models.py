import asyncio
import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
import pytest
from sqlalchemy import select

from apps.api.main import create_app
from enterprise_agent.contracts.domain import FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.models.gateway import ModelGateway
from enterprise_agent.storage.tables import (
    AuditRow,
    ConversationMessageRow,
    ConversationTurnAttemptRow,
    ConversationTurnOutcomeRow,
    ConversationTurnRow,
    ModelConfigRow,
    SecretRow,
)
from tests.integration.test_api_identity import register_and_login


class _ModelHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        request = json.loads(self.rfile.read(length))
        assert self.path == "/v1/chat/completions"
        assert request["model"] == "local-test-model"
        assert self.headers["authorization"] == "Bearer local-secret-value"
        if request.get("stream"):
            body = (
                b'data: {"choices":[{"delta":{"content":"Hello "}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"from local."}}]}\n\n'
                b"data: [DONE]\n\n"
            )
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps(
            {
                "model": "local-test-model",
                "choices": [{"message": {"content": "I am the local test model."}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 6},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


async def test_exact_social_turn_skips_model_loading_but_commits_answer(monkeypatch) -> None:
    master = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("SECRET_MASTER_KEY", master)

    async def unexpected_stream(*_args, **_kwargs):
        raise AssertionError("an exact social turn must not call the model")

    monkeypatch.setattr(ModelGateway, "stream", unexpected_stream)
    app = create_app(os.environ["TEST_DATABASE_URL"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        owner = await register_and_login(client, "exact-social-fast-path-owner")
        workspace_id = UUID(
            (
                await client.post(
                    "/workspaces",
                    headers=owner,
                    json={"name": "Exact Social Fast Path"},
                )
            ).json()["workspace_id"]
        )
        config_id = uuid4()
        configured = await client.post(
            "/models",
            headers=owner,
            json={
                "config_id": str(config_id),
                "workspace_id": str(workspace_id),
                "kind": "CHAT",
                "provider": "OpenAICompatible",
                "base_url": "http://127.0.0.1:65534/v1",
                "model_name": "must-not-be-called",
                "external": False,
                "api_key": "local-secret-value",
            },
        )
        assert configured.status_code == 201
        conversation_id = uuid4()
        assert (
            await client.post(
                "/conversations",
                headers=owner,
                json={
                    "conversation_id": str(conversation_id),
                    "workspace_id": str(workspace_id),
                    "title": "Exact social turn",
                },
            )
        ).status_code == 201

        monkeypatch.delenv("SECRET_MASTER_KEY")
        user_message_id = uuid4()
        streamed = await client.post(
            "/chat/stream",
            headers=owner,
            json={
                "workspace_id": str(workspace_id),
                "conversation_id": str(conversation_id),
                "model_config_id": str(config_id),
                "user_message_id": str(user_message_id),
                "content": "晚上好",
            },
        )

        assert streamed.status_code == 200
        assert "event: error" not in streamed.text
        assert "晚上好！有什么我可以帮你的吗？" in streamed.text
        assert '"model": "deterministic-fast-path"' in streamed.text
        async with app.state.sessions() as session:
            turn = await session.scalar(
                select(ConversationTurnRow).where(
                    ConversationTurnRow.workspace_id == workspace_id,
                    ConversationTurnRow.user_message_id == user_message_id,
                )
            )
            assert turn is not None
            outcome = await session.scalar(
                select(ConversationTurnOutcomeRow).where(
                    ConversationTurnOutcomeRow.workspace_id == workspace_id,
                    ConversationTurnOutcomeRow.turn_id == turn.id,
                )
            )
            assert outcome is not None
            assert outcome.status == "ANSWERED"
            assistant = await session.get(ConversationMessageRow, outcome.assistant_message_id)
            assert assistant is not None
            assert assistant.content == "晚上好！有什么我可以帮你的吗？"


async def test_failed_direct_fallback_persists_terminal_audit(monkeypatch) -> None:
    master = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("SECRET_MASTER_KEY", master)

    async def failed_stream(self, _messages, _classification, **_kwargs):
        if False:
            yield "unreachable"
        raise DomainError(
            FailureCode.LLM_SERVER_ERROR,
            f"{self.config.model} failed",
        )

    monkeypatch.setattr(ModelGateway, "stream", failed_stream)
    app = create_app(os.environ["TEST_DATABASE_URL"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        owner = await register_and_login(client, "failed-direct-fallback-owner")
        workspace_id = UUID(
            (
                await client.post(
                    "/workspaces",
                    headers=owner,
                    json={"name": "Failed Direct Fallback"},
                )
            ).json()["workspace_id"]
        )
        config_ids: list[str] = []
        for index, model in enumerate(("primary-chat", "fallback-chat")):
            response = await client.post(
                "/models",
                headers=owner,
                json={
                    "config_id": str(uuid4()),
                    "workspace_id": str(workspace_id),
                    "kind": "CHAT",
                    "provider": "OpenAICompatible",
                    "base_url": f"http://127.0.0.1:{65100 + index}/v1",
                    "model_name": model,
                    "external": False,
                    "api_key": f"never-log-{model}",
                },
            )
            assert response.status_code == 201
            config_ids.append(response.json()["config_id"])
        conversation_id = uuid4()
        assert (
            await client.post(
                "/conversations",
                headers=owner,
                json={
                    "conversation_id": str(conversation_id),
                    "workspace_id": str(workspace_id),
                    "title": "Failed fallback audit",
                },
            )
        ).status_code == 201

        streamed = await client.post(
            "/chat/stream",
            headers=owner,
            json={
                "workspace_id": str(workspace_id),
                "conversation_id": str(conversation_id),
                "model_config_id": config_ids[0],
                "fallback_model_config_id": config_ids[1],
                "user_message_id": str(uuid4()),
                "content": "Hello there",
            },
        )

        assert streamed.status_code == 200
        assert "event: error" in streamed.text
        async with app.state.sessions() as session:
            audit = await session.scalar(
                select(AuditRow).where(
                    AuditRow.workspace_id == workspace_id,
                    AuditRow.action == "model.fallback.attempted",
                )
            )

        assert audit is not None
        assert audit.result == {
            "trigger": FailureCode.LLM_SERVER_ERROR.value,
            "outcome": "FAILED",
            "failure_code": FailureCode.LLM_SERVER_ERROR.value,
            "primary_config_id": config_ids[0],
            "fallback_config_id": config_ids[1],
            "primary_provider": "OpenAICompatible",
            "primary_model": "primary-chat",
            "fallback_provider": "OpenAICompatible",
            "fallback_model": "fallback-chat",
        }
        serialized_audit = json.dumps(audit.result)
        assert "never-log" not in serialized_audit
        assert "Hello there" not in serialized_audit


async def test_workspace_model_config_encrypts_key_probes_and_isolates(
    monkeypatch,
) -> None:
    master = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("SECRET_MASTER_KEY", master)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    app = create_app(os.environ["TEST_DATABASE_URL"])
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client,
        ):
            owner = await register_and_login(client, "model-owner")
            outsider = await register_and_login(client, "model-outsider")
            workspace_id = UUID(
                (
                    await client.post(
                        "/workspaces",
                        headers=owner,
                        json={"name": "Model Config Workspace"},
                    )
                ).json()["workspace_id"]
            )
            config_id = uuid4()
            payload = {
                "config_id": str(config_id),
                "workspace_id": str(workspace_id),
                "kind": "CHAT",
                "provider": "OpenAICompatible",
                "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                "model_name": "local-test-model",
                "external": False,
                "api_key": "local-secret-value",
            }
            created = await client.post("/models", headers=owner, json=payload)
            assert created.status_code == 201
            assert created.json()["has_secret"] is True
            assert "local-secret-value" not in created.text
            replay = await client.post("/models", headers=owner, json=payload)
            assert replay.status_code == 201
            assert replay.json() == created.json()
            duplicate = await client.post(
                "/models",
                headers=owner,
                json={**payload, "config_id": str(uuid4())},
            )
            assert duplicate.status_code == 409
            assert "already exists" in duplicate.json()["error"]["message"]

            listed = await client.get(
                "/models", headers=owner, params={"workspace_id": str(workspace_id)}
            )
            assert listed.status_code == 200
            assert [item["config_id"] for item in listed.json()] == [str(config_id)]
            assert (
                await client.get(
                    "/models",
                    headers=outsider,
                    params={"workspace_id": str(workspace_id)},
                )
            ).status_code == 403

            updated = await client.put(
                f"/models/{config_id}",
                headers=owner,
                json={
                    "workspace_id": str(workspace_id),
                    "expected_version": 1,
                    "kind": "CHAT",
                    "provider": "Qwen",
                    "base_url": payload["base_url"],
                    "model_name": "local-test-model",
                    "external": False,
                },
            )
            assert updated.status_code == 200
            assert updated.json()["provider"] == "Qwen"
            assert updated.json()["has_secret"] is True
            assert updated.json()["version"] == 2
            assert updated.json()["config_id"] != str(config_id)
            updated_id = updated.json()["config_id"]

            disabled = await client.post(
                f"/models/{updated_id}/state",
                headers=owner,
                json={
                    "workspace_id": str(workspace_id),
                    "expected_version": 2,
                    "enabled": False,
                },
            )
            assert disabled.status_code == 200
            assert disabled.json()["enabled"] is False
            assert disabled.json()["version"] == 3
            disabled_id = disabled.json()["config_id"]
            assert (
                await client.post(
                    f"/models/{updated_id}/probe",
                    headers=owner,
                    params={"workspace_id": str(workspace_id)},
                )
            ).status_code == 409
            enabled = await client.post(
                f"/models/{disabled_id}/state",
                headers=owner,
                json={
                    "workspace_id": str(workspace_id),
                    "expected_version": 3,
                    "enabled": True,
                },
            )
            assert enabled.status_code == 200
            assert enabled.json()["enabled"] is True
            assert enabled.json()["version"] == 4
            active_config_id = enabled.json()["config_id"]

            probe = await client.post(
                f"/models/{active_config_id}/probe",
                headers=owner,
                params={"workspace_id": str(workspace_id)},
            )
            assert probe.status_code == 200
            assert probe.json() == {
                "schema_version": 1,
                "config_id": active_config_id,
                "ok": True,
                "model": "local-test-model",
                "content": "I am the local test model.",
                "input_tokens": 7,
                "output_tokens": 6,
            }
            assert (
                await client.post(
                    f"/models/{active_config_id}/probe",
                    headers=outsider,
                    params={"workspace_id": str(workspace_id)},
                )
            ).status_code == 403

            conversation_id = uuid4()
            conversation = await client.post(
                "/conversations",
                headers=owner,
                json={
                    "conversation_id": str(conversation_id),
                    "workspace_id": str(workspace_id),
                    "title": "Streaming conversation",
                },
            )
            assert conversation.status_code == 201
            user_message_id = uuid4()
            streamed = await client.post(
                "/chat/stream",
                headers=owner,
                json={
                    "workspace_id": str(workspace_id),
                    "conversation_id": str(conversation_id),
                    "model_config_id": active_config_id,
                    "user_message_id": str(user_message_id),
                    "content": "Introduce yourself",
                },
            )
            assert streamed.status_code == 200
            assert "event: token" in streamed.text
            assert "Hello " in streamed.text and "from local." in streamed.text
            assert "event: done" in streamed.text
            messages = await client.get(
                f"/conversations/{conversation_id}/messages",
                headers=owner,
                params={"workspace_id": str(workspace_id)},
            )
            assert [(item["role"], item["content"]) for item in messages.json()] == [
                ("USER", "Introduce yourself"),
                ("ASSISTANT", "Hello from local."),
            ]

            async with app.state.sessions() as session:
                config = await session.get(ModelConfigRow, config_id)
                assert config is not None and config.secret_id is not None
                secret = await session.get(SecretRow, config.secret_id)
                assert secret is not None
                assert b"local-secret-value" not in secret.ciphertext
                audits = (
                    await session.scalars(
                        select(AuditRow).where(
                            AuditRow.workspace_id == workspace_id,
                            AuditRow.action == "model.configure",
                        )
                    )
                ).all()
                assert len(audits) == 1
                assert "local-secret-value" not in json.dumps(audits[0].result)
                turns = (
                    await session.scalars(
                        select(ConversationTurnRow).where(
                            ConversationTurnRow.workspace_id == workspace_id,
                            ConversationTurnRow.conversation_id == conversation_id,
                        )
                    )
                ).all()
                assert len(turns) == 1
                assert turns[0].state == "COMPLETED"
                assert turns[0].context_eligible
                attempts = (
                    await session.scalars(
                        select(ConversationTurnAttemptRow).where(
                            ConversationTurnAttemptRow.workspace_id == workspace_id,
                            ConversationTurnAttemptRow.turn_id == turns[0].id,
                        )
                    )
                ).all()
                outcomes = (
                    await session.scalars(
                        select(ConversationTurnOutcomeRow).where(
                            ConversationTurnOutcomeRow.workspace_id == workspace_id,
                            ConversationTurnOutcomeRow.turn_id == turns[0].id,
                        )
                    )
                ).all()
                assert len(attempts) == 1 and attempts[0].state == "SUCCEEDED"
                assert len(outcomes) == 1
                assert outcomes[0].status == "ANSWERED"
                assert outcomes[0].assistant_message_id == uuid5(
                    NAMESPACE_URL,
                    f"direct-chat-message:{workspace_id}:{conversation_id}:{turns[0].id}",
                )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


async def test_cancelling_stream_body_cancels_turn_without_creating_outcome(
    monkeypatch,
) -> None:
    master = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("SECRET_MASTER_KEY", master)
    stream_started = asyncio.Event()
    never_complete = asyncio.Event()

    async def stalled_stream(self, messages, classification, **_kwargs):
        stream_started.set()
        yield "partial answer"
        await never_complete.wait()

    monkeypatch.setattr(ModelGateway, "stream", stalled_stream)
    app = create_app(os.environ["TEST_DATABASE_URL"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        owner = await register_and_login(client, "cancelled-stream-owner")
        workspace_id = UUID(
            (
                await client.post(
                    "/workspaces",
                    headers=owner,
                    json={"name": "Cancelled Stream Workspace"},
                )
            ).json()["workspace_id"]
        )
        config = await client.post(
            "/models",
            headers=owner,
            json={
                "config_id": str(uuid4()),
                "workspace_id": str(workspace_id),
                "kind": "CHAT",
                "provider": "OpenAICompatible",
                "base_url": "http://127.0.0.1:65534/v1",
                "model_name": "local-test-model",
                "external": False,
                "api_key": "local-secret-value",
            },
        )
        assert config.status_code == 201
        conversation_id = uuid4()
        created = await client.post(
            "/conversations",
            headers=owner,
            json={
                "conversation_id": str(conversation_id),
                "workspace_id": str(workspace_id),
                "title": "Cancelled stream",
            },
        )
        assert created.status_code == 201
        current_information_message_id = uuid4()
        rejected_current_information = await client.post(
            "/chat/stream",
            headers=owner,
            json={
                "workspace_id": str(workspace_id),
                "conversation_id": str(conversation_id),
                "model_config_id": config.json()["config_id"],
                "user_message_id": str(current_information_message_id),
                "content": "北京今天天气怎么样",
            },
        )
        assert rejected_current_information.status_code == 409
        assert rejected_current_information.json()["error"]["code"] == "VALIDATION_FAILED"
        user_message_id = uuid4()
        request_task = asyncio.create_task(
            client.post(
                "/chat/stream",
                headers=owner,
                json={
                    "workspace_id": str(workspace_id),
                    "conversation_id": str(conversation_id),
                    "model_config_id": config.json()["config_id"],
                    "user_message_id": str(user_message_id),
                    "content": "Introduce yourself",
                },
            )
        )
        await asyncio.wait_for(stream_started.wait(), timeout=3)
        request_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request_task

        async with app.state.sessions() as session:
            turn = await session.scalar(
                select(ConversationTurnRow).where(
                    ConversationTurnRow.workspace_id == workspace_id,
                    ConversationTurnRow.conversation_id == conversation_id,
                    ConversationTurnRow.user_message_id == user_message_id,
                )
            )
            assert turn is not None
            rejected_message = await session.get(
                ConversationMessageRow,
                current_information_message_id,
            )
            rejected_turn = await session.scalar(
                select(ConversationTurnRow).where(
                    ConversationTurnRow.workspace_id == workspace_id,
                    ConversationTurnRow.user_message_id == current_information_message_id,
                )
            )
            attempts = (
                await session.scalars(
                    select(ConversationTurnAttemptRow).where(
                        ConversationTurnAttemptRow.workspace_id == workspace_id,
                        ConversationTurnAttemptRow.turn_id == turn.id,
                    )
                )
            ).all()
            outcomes = (
                await session.scalars(
                    select(ConversationTurnOutcomeRow).where(
                        ConversationTurnOutcomeRow.workspace_id == workspace_id,
                        ConversationTurnOutcomeRow.turn_id == turn.id,
                    )
                )
            ).all()

        assert turn.state == "CANCELLED"
        assert not turn.context_eligible
        assert rejected_message is None
        assert rejected_turn is None
        assert len(attempts) == 1
        assert attempts[0].state == "CANCELLED"
        assert outcomes == []
