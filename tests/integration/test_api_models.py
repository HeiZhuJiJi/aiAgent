import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from apps.api.main import create_app
from enterprise_agent.storage.tables import AuditRow, ModelConfigRow, SecretRow
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

            probe = await client.post(
                f"/models/{config_id}/probe",
                headers=owner,
                params={"workspace_id": str(workspace_id)},
            )
            assert probe.status_code == 200
            assert probe.json() == {
                "schema_version": 1,
                "config_id": str(config_id),
                "ok": True,
                "model": "local-test-model",
                "content": "I am the local test model.",
                "input_tokens": 7,
                "output_tokens": 6,
            }
            assert (
                await client.post(
                    f"/models/{config_id}/probe",
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
            streamed = await client.post(
                "/chat/stream",
                headers=owner,
                json={
                    "workspace_id": str(workspace_id),
                    "conversation_id": str(conversation_id),
                    "model_config_id": str(config_id),
                    "user_message_id": str(uuid4()),
                    "assistant_message_id": str(uuid4()),
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
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
