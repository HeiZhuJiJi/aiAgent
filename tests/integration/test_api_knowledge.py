import os
from uuid import UUID, uuid4

import httpx
import pytest
from dotenv import dotenv_values
from minio import Minio
from sqlalchemy import func, select

from apps.api.main import create_app
from enterprise_agent.domain.states import DomainError
from enterprise_agent.runtime.knowledge_ingestion import KnowledgeIngestor
from enterprise_agent.storage.knowledge_jobs import (
    claim_knowledge_ingestion,
    fail_knowledge_ingestion,
)
from enterprise_agent.storage.tables import AuditRow, KnowledgeIngestionJobRow, OutboxEventRow
from tests.integration.test_api_identity import register_and_login


async def test_knowledge_upload_is_staged_once_and_enqueued_transactionally():
    password = dotenv_values(".env").get("MINIO_ROOT_PASSWORD")
    assert password
    ingestor = KnowledgeIngestor(
        Minio(
            "127.0.0.1:59000",
            access_key="platform",
            secret_key=str(password),
            secure=False,
        ),
        bucket="runtime-api-knowledge",
    )
    app = create_app(os.environ["TEST_DATABASE_URL"], knowledge_ingestor=ingestor)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client,
    ):
        owner = await register_and_login(client, "knowledge-owner")
        outsider = await register_and_login(client, "knowledge-outsider")
        workspace = (
            await client.post("/workspaces", headers=owner, json={"name": "Knowledge Workspace"})
        ).json()
        workspace_id = workspace["workspace_id"]
        document_id = str(uuid4())
        params = {
            "workspace_id": workspace_id,
            "document_id": document_id,
            "name": "supplier.md",
        }
        blob = b"# Supplier\nSanctions screening is clear.\n"
        first = await client.post(
            "/knowledge/documents",
            params=params,
            headers={**owner, "Content-Type": "text/markdown"},
            content=blob,
        )
        assert first.status_code == 202
        assert first.json()["state"] == "PENDING"
        replay = await client.post(
            "/knowledge/documents",
            params=params,
            headers={**owner, "Content-Type": "text/markdown"},
            content=blob,
        )
        assert replay.status_code == 202
        assert replay.json()["ingestion_id"] == first.json()["ingestion_id"]
        listed = await client.get(
            "/knowledge/documents", params={"workspace_id": workspace_id}, headers=owner
        )
        assert listed.status_code == 200
        assert [item["ingestion_id"] for item in listed.json()] == [
            first.json()["ingestion_id"]
        ]
        denied = await client.get(
            f"/knowledge/documents/{first.json()['ingestion_id']}",
            params={"workspace_id": workspace_id},
            headers=outsider,
        )
        assert denied.status_code == 403
        stored_blob = await ingestor.load_blob(
            f"{workspace_id}/{document_id}/{first.json()['source_hash']}",
            first.json()["source_hash"],
        )
        assert stored_blob == blob
        async with app.state.sessions() as session:
            job_count = await session.scalar(
                select(func.count()).select_from(KnowledgeIngestionJobRow).where(
                    KnowledgeIngestionJobRow.workspace_id == UUID(workspace_id)
                )
            )
            events = (
                await session.scalars(
                    select(OutboxEventRow).where(
                        OutboxEventRow.workspace_id == UUID(workspace_id),
                        OutboxEventRow.event_type == "knowledge.ingestion.requested",
                    )
                )
            ).all()
            audits = (
                await session.scalars(
                    select(AuditRow).where(
                        AuditRow.workspace_id == UUID(workspace_id),
                        AuditRow.action == "knowledge.ingestion.requested",
                    )
                )
            ).all()
        assert job_count == 1
        assert len(events) == 1 and events[0].state == "PENDING"
        assert events[0].payload["ingestion_id"] == first.json()["ingestion_id"]
        assert len(audits) == 1 and audits[0].actor_type == "USER"

        async with app.state.sessions.begin() as session:
            old_claim = await claim_knowledge_ingestion(
                session, UUID(workspace_id), UUID(first.json()["ingestion_id"])
            )
        async with app.state.sessions.begin() as session:
            current_claim = await claim_knowledge_ingestion(
                session, UUID(workspace_id), UUID(first.json()["ingestion_id"])
            )
        assert old_claim is not None and current_claim is not None
        assert current_claim.fencing_token > old_claim.fencing_token
        with pytest.raises(DomainError, match="fenced"):
            async with app.state.sessions.begin() as session:
                await fail_knowledge_ingestion(session, old_claim, "stale worker")
        async with app.state.sessions.begin() as session:
            await fail_knowledge_ingestion(session, current_claim, "expected test failure")
        status = await client.get(
            f"/knowledge/documents/{first.json()['ingestion_id']}",
            params={"workspace_id": workspace_id},
            headers=owner,
        )
        assert status.json()["state"] == "FAILED"
        assert status.json()["safe_error"] == "expected test failure"
