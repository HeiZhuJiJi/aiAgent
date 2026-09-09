import os
from uuid import uuid4

import pytest
from dotenv import dotenv_values
from minio import Minio
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.artifacts import ArtifactStore
from enterprise_agent.storage.tables import ArtifactRow
from tests.integration.test_fencing import seed


@pytest.fixture
async def database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def artifact_store() -> ArtifactStore:
    password = dotenv_values(".env").get("MINIO_ROOT_PASSWORD")
    assert password
    return ArtifactStore(
        Minio(
            "127.0.0.1:59000",
            access_key="platform",
            secret_key=str(password),
            secure=False,
        ),
        bucket="runtime-integration-artifacts",
    )


async def test_real_minio_artifact_roundtrip_and_idempotent_replay(database, artifact_store):
    workspace_id, task_id, _ = await seed(database)
    artifact_id = uuid4()
    content = b"# Supplier Report\n\nGrounded risk findings.\n"
    async with database.begin() as session:
        first = await artifact_store.put(
            session,
            artifact_id=artifact_id,
            workspace_id=workspace_id,
            task_id=task_id,
            content=content,
            media_type="text/markdown",
            claim_ids=(),
        )
    async with database.begin() as session:
        replay = await artifact_store.put(
            session,
            artifact_id=artifact_id,
            workspace_id=workspace_id,
            task_id=task_id,
            content=content,
            media_type="text/markdown",
            claim_ids=(),
        )
        restored = await artifact_store.get(session, workspace_id, artifact_id)
    assert replay == first
    assert restored == content
    assert first.storage_uri.startswith("s3://runtime-integration-artifacts/")

    with pytest.raises(DomainError, match="immutable artifact mismatch"):
        async with database.begin() as session:
            await artifact_store.put(
                session,
                artifact_id=artifact_id,
                workspace_id=workspace_id,
                task_id=task_id,
                content=b"changed",
                media_type="text/markdown",
                claim_ids=(),
            )


@pytest.mark.parametrize(
    "statement",
    [update(ArtifactRow).values(content_hash="d" * 64), delete(ArtifactRow)],
)
async def test_database_rejects_artifact_metadata_mutation(database, artifact_store, statement):
    workspace_id, task_id, _ = await seed(database)
    artifact_id = uuid4()
    async with database.begin() as session:
        await artifact_store.put(
            session,
            artifact_id=artifact_id,
            workspace_id=workspace_id,
            task_id=task_id,
            content=b"immutable",
            media_type="application/octet-stream",
            claim_ids=(),
        )
    with pytest.raises(DBAPIError):
        async with database.begin() as session:
            await session.execute(statement.where(ArtifactRow.id == artifact_id))
