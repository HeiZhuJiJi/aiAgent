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
from enterprise_agent.storage.knowledge import ChunkInput, persist_document_version
from enterprise_agent.storage.knowledge_jobs import (
    claim_knowledge_ingestion,
    fail_knowledge_ingestion,
)
from enterprise_agent.storage.tables import (
    AuditRow,
    KnowledgeIngestionJobRow,
    OutboxEventRow,
)
from tests.integration.test_api_identity import register_and_login


async def test_succeeded_knowledge_document_exposes_a_workspace_scoped_text_preview():
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
        owner = await register_and_login(client, "knowledge-preview-owner")
        outsider = await register_and_login(client, "knowledge-preview-outsider")
        workspace_id = UUID(
            (
                await client.post(
                    "/workspaces", headers=owner, json={"name": "Preview Knowledge"}
                )
            ).json()["workspace_id"]
        )
        document_id = uuid4()
        ingestion_id = uuid4()
        source_hash = "8" * 64
        media_type = "text/markdown"
        async with app.state.sessions.begin() as session:
            document_version_id = await persist_document_version(
                session,
                workspace_id=workspace_id,
                document_id=document_id,
                name="制度说明.md",
                media_type=media_type,
                source_hash=source_hash,
                storage_uri="s3://knowledge/制度说明.md",
                parser_version="parser-test",
                chunks=(
                    ChunkInput(
                        content="第一段：请假制度。",
                        locator={"line": 1},
                        token_count=8,
                        embedding=(1.0,) + (0.0,) * 1023,
                    ),
                    ChunkInput(
                        content="第二段：审批流程。",
                        locator={"line": 2},
                        token_count=8,
                        embedding=(0.0, 1.0) + (0.0,) * 1022,
                    ),
                ),
            )
            session.add(
                KnowledgeIngestionJobRow(
                    id=ingestion_id,
                    workspace_id=workspace_id,
                    document_id=document_id,
                    name="制度说明.md",
                    media_type=media_type,
                    source_hash=source_hash,
                    object_name="knowledge/制度说明.md",
                    parser_version="parser-test",
                    state="SUCCEEDED",
                    version=1,
                    fencing_token=1,
                    document_version_id=document_version_id,
                )
            )

        preview = await client.get(
            f"/knowledge/documents/{ingestion_id}/preview",
            params={"workspace_id": str(workspace_id)},
            headers=owner,
        )
        assert preview.status_code == 200
        assert preview.json() == {
            "schema_version": 1,
            "ingestion_id": str(ingestion_id),
            "document_version_id": str(document_version_id),
            "name": "制度说明.md",
            "media_type": media_type,
            "content": "第一段：请假制度。\n\n第二段：审批流程。",
            "truncated": False,
        }
        denied = await client.get(
            f"/knowledge/documents/{ingestion_id}/preview",
            params={"workspace_id": str(workspace_id)},
            headers=outsider,
        )
        assert denied.status_code == 403


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
        failed_version = status.json()["version"]

        forbidden_retry = await client.post(
            f"/knowledge/documents/{first.json()['ingestion_id']}/retry",
            headers=outsider,
            json={
                "workspace_id": workspace_id,
                "expected_version": failed_version,
            },
        )
        assert forbidden_retry.status_code == 403

        retried = await client.post(
            f"/knowledge/documents/{first.json()['ingestion_id']}/retry",
            headers=owner,
            json={
                "workspace_id": workspace_id,
                "expected_version": failed_version,
            },
        )
        assert retried.status_code == 202
        assert retried.json()["state"] == "PENDING"
        assert retried.json()["version"] == failed_version + 1
        assert retried.json()["safe_error"] is None
        assert retried.json()["completed_at"] is None

        replayed_retry = await client.post(
            f"/knowledge/documents/{first.json()['ingestion_id']}/retry",
            headers=owner,
            json={
                "workspace_id": workspace_id,
                "expected_version": failed_version,
            },
        )
        assert replayed_retry.status_code == 202
        assert replayed_retry.json() == retried.json()

        async with app.state.sessions.begin() as session:
            retry_claim = await claim_knowledge_ingestion(
                session,
                UUID(workspace_id),
                UUID(first.json()["ingestion_id"]),
            )
        assert retry_claim is not None
        replayed_after_claim = await client.post(
            f"/knowledge/documents/{first.json()['ingestion_id']}/retry",
            headers=owner,
            json={
                "workspace_id": workspace_id,
                "expected_version": failed_version,
            },
        )
        assert replayed_after_claim.status_code == 202
        assert replayed_after_claim.json()["state"] == "RUNNING"
        assert replayed_after_claim.json()["version"] == retried.json()["version"] + 1

        async with app.state.sessions() as session:
            retry_events = (
                await session.scalars(
                    select(OutboxEventRow).where(
                        OutboxEventRow.workspace_id == UUID(workspace_id),
                        OutboxEventRow.event_type == "knowledge.ingestion.requested",
                    )
                )
            ).all()
            retry_audits = (
                await session.scalars(
                    select(AuditRow).where(
                        AuditRow.workspace_id == UUID(workspace_id),
                        AuditRow.action == "knowledge.ingestion.retry_requested",
                    )
                )
            ).all()
        assert len(retry_events) == 2
        assert len(retry_audits) == 1


async def test_failed_knowledge_document_can_be_soft_deleted_and_is_hidden():
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
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        owner = await register_and_login(client, "knowledge-delete-owner")
        workspace_id = (
            await client.post("/workspaces", headers=owner, json={"name": "Delete Knowledge"})
        ).json()["workspace_id"]
        document_id = str(uuid4())
        uploaded = await client.post(
            "/knowledge/documents",
            params={
                "workspace_id": workspace_id,
                "document_id": document_id,
                "name": "obsolete.md",
            },
            headers={**owner, "Content-Type": "text/markdown"},
            content=b"# Obsolete\nThis document should be hidden.\n",
        )
        async with app.state.sessions.begin() as session:
            claim = await claim_knowledge_ingestion(
                session,
                UUID(workspace_id),
                UUID(uploaded.json()["ingestion_id"]),
            )
            assert claim is not None
            await fail_knowledge_ingestion(session, claim, "expected deletion fixture")
        status = await client.get(
            f"/knowledge/documents/{uploaded.json()['ingestion_id']}",
            params={"workspace_id": workspace_id},
            headers=owner,
        )
        deleted = await client.request(
            "DELETE",
            f"/knowledge/documents/{document_id}",
            headers=owner,
            json={
                "workspace_id": workspace_id,
                "expected_version": status.json()["version"],
            },
        )
        assert deleted.status_code == 204
        listed = await client.get(
            "/knowledge/documents", params={"workspace_id": workspace_id}, headers=owner
        )
        assert listed.json() == []
        unavailable = await client.get(
            f"/knowledge/documents/{uploaded.json()['ingestion_id']}",
            params={"workspace_id": workspace_id},
            headers=owner,
        )
        assert unavailable.status_code == 403
        async with app.state.sessions() as session:
            row = await session.scalar(
                select(KnowledgeIngestionJobRow).where(
                    KnowledgeIngestionJobRow.id == UUID(uploaded.json()["ingestion_id"])
                )
            )
        assert row is not None and row.deleted_at is not None
