import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID, uuid4

import httpx
from dotenv import dotenv_values
from minio import Minio
from temporalio.client import Client
from temporalio.worker import Worker

from apps.api.main import create_app
from enterprise_agent.runtime.activities import ingest_knowledge_document
from enterprise_agent.runtime.knowledge_ingestion import KnowledgeIngestor
from enterprise_agent.runtime.outbox_dispatcher import dispatch_once
from enterprise_agent.runtime.workflows import KnowledgeIngestionWorkflow
from enterprise_agent.storage.knowledge import EMBEDDING_DIMENSION, lexical_candidates
from tests.integration.test_api_identity import register_and_login


class EmbeddingHandler(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        assert self.path == "/v1/embeddings"
        assert request["model"] == "BAAI/bge-m3"
        type(self).calls += 1
        vector = [0.0] * EMBEDDING_DIMENSION
        vector[0] = 1.0
        body = json.dumps(
            {
                "data": [
                    {"index": index, "embedding": vector}
                    for index, _ in enumerate(request["input"])
                ]
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


async def test_api_outbox_temporal_activity_commits_real_knowledge_index(monkeypatch):
    password = dotenv_values(".env").get("MINIO_ROOT_PASSWORD")
    assert password
    EmbeddingHandler.calls = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), EmbeddingHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    task_queue = "knowledge-ingestion-" + str(uuid4())
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("MINIO_ENDPOINT", "127.0.0.1:59000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "platform")
    monkeypatch.setenv("MINIO_SECRET_KEY", str(password))
    monkeypatch.setenv("KNOWLEDGE_BUCKET", "runtime-temporal-knowledge")
    monkeypatch.setenv("TEMPORAL_TASK_QUEUE", task_queue)
    monkeypatch.setenv("EMBEDDING_MODE", "local")
    monkeypatch.setenv("EMBEDDING_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("EMBEDDING_NAME", "BAAI/bge-m3")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    ingestor = KnowledgeIngestor(
        Minio(
            "127.0.0.1:59000",
            access_key="platform",
            secret_key=str(password),
            secure=False,
        ),
        bucket="runtime-temporal-knowledge",
    )
    app = create_app(os.environ["TEST_DATABASE_URL"], knowledge_ingestor=ingestor)
    temporal = await Client.connect("127.0.0.1:57233")
    worker = Worker(
        temporal,
        task_queue=task_queue,
        workflows=[KnowledgeIngestionWorkflow],
        activities=[ingest_knowledge_document],
    )
    try:
        async with (
            worker,
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client,
        ):
            owner = await register_and_login(client, "temporal-knowledge")
            workspace_id = (
                await client.post(
                    "/workspaces", headers=owner, json={"name": "Temporal Knowledge"}
                )
            ).json()["workspace_id"]
            uploaded = await client.post(
                "/knowledge/documents",
                params={
                    "workspace_id": workspace_id,
                    "document_id": str(uuid4()),
                    "name": "supplier.md",
                },
                headers={**owner, "Content-Type": "text/markdown"},
                content=b"# Supplier\nSanctions screening is clear.\n",
            )
            assert uploaded.status_code == 202
            lease = await dispatch_once(
                app.state.sessions,
                temporal,
                "knowledge-test-dispatcher",
                workspace_id=UUID(workspace_id),
            )
            assert lease is not None
            workflow_id = lease.payload["workflow_id"]
            assert isinstance(workflow_id, str)
            result = await asyncio.wait_for(
                temporal.get_workflow_handle(workflow_id).result(), timeout=20
            )
            assert result["state"] == "SUCCEEDED"
            status = await client.get(
                f"/knowledge/documents/{uploaded.json()['ingestion_id']}",
                params={"workspace_id": workspace_id},
                headers=owner,
            )
            assert status.status_code == 200
            assert status.json()["state"] == "SUCCEEDED"
            assert status.json()["document_version_id"] == result["document_version_id"]
            async with app.state.sessions() as session:
                hits = await lexical_candidates(
                    session, UUID(workspace_id), "sanctions"
                )
            assert hits and hits[0].content == "Sanctions screening is clear."
            assert EmbeddingHandler.calls == 1
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
