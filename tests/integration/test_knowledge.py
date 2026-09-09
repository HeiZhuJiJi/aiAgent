import os
from uuid import uuid4

import pytest
from dotenv import dotenv_values
from minio import Minio
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.domain.states import DomainError
from enterprise_agent.runtime.knowledge_ingestion import KnowledgeIngestor
from enterprise_agent.storage.knowledge import (
    EMBEDDING_DIMENSION,
    ChunkInput,
    lexical_candidates,
    persist_document_version,
    semantic_candidates,
)
from enterprise_agent.storage.tables import KnowledgeChunkRow, KnowledgeDocumentVersionRow
from tests.integration.test_fencing import seed


@pytest.fixture
async def database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def vector(axis: int) -> tuple[float, ...]:
    values = [0.0] * EMBEDDING_DIMENSION
    values[axis] = 1.0
    return tuple(values)


async def test_real_postgres_fts_pgvector_and_version_replay(database):
    workspace_id, _, _ = await seed(database)
    document_id = uuid4()
    chunks = (
        ChunkInput("supplier sanctions compliance risk", {"page": 2}, 4, vector(0)),
        ChunkInput("supplier revenue and liquidity", {"page": 3}, 4, vector(1)),
    )
    arguments = {
        "workspace_id": workspace_id,
        "document_id": document_id,
        "name": "supplier.pdf",
        "media_type": "application/pdf",
        "source_hash": "a" * 64,
        "storage_uri": "s3://knowledge/supplier.pdf",
        "parser_version": "parser-1.0.0+bge-m3",
        "chunks": chunks,
    }
    async with database.begin() as session:
        version_id = await persist_document_version(session, **arguments)
        replay = await persist_document_version(session, **arguments)
    assert replay == version_id
    async with database() as session:
        lexical = await lexical_candidates(session, workspace_id, "sanctions")
        semantic = await semantic_candidates(session, workspace_id, vector(0))
        foreign = await lexical_candidates(session, uuid4(), "sanctions")
    assert lexical[0].content == chunks[0].content
    assert semantic[0].content == chunks[0].content
    assert '"page": 2' in lexical[0].locator
    assert foreign == ()

    changed = arguments | {"chunks": (chunks[1],)}
    with pytest.raises(DomainError, match="replay changed"):
        async with database.begin() as session:
            await persist_document_version(session, **changed)


async def test_end_to_end_parse_embed_minio_and_postgres_ingestion(database):
    workspace_id, _, _ = await seed(database)
    password = dotenv_values(".env").get("MINIO_ROOT_PASSWORD")
    assert password
    ingestor = KnowledgeIngestor(
        Minio(
            "127.0.0.1:59000",
            access_key="platform",
            secret_key=str(password),
            secure=False,
        ),
        bucket="runtime-integration-knowledge",
    )
    calls: list[tuple[str, ...]] = []

    async def embed(texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        calls.append(texts)
        return tuple(vector(0) for _ in texts)

    arguments = {
        "workspace_id": workspace_id,
        "document_id": uuid4(),
        "name": "supplier.md",
        "media_type": "text/markdown",
        "blob": b"# Supplier Profile\nSanctions screening is clear.\n",
        "parser_version": "parser-1.0.0+bge-m3",
        "token_counter": lambda value: len(value.split()),
        "embedder": embed,
    }
    async with database.begin() as session:
        first = await ingestor.ingest(session, **arguments)
    async with database.begin() as session:
        replay = await ingestor.ingest(session, **arguments)
        hits = await lexical_candidates(session, workspace_id, "sanctions")
    assert first == replay
    assert len(calls) == 1
    assert hits and hits[0].locator == '{"line": 2, "segment": 1}'


@pytest.mark.parametrize(
    "statement",
    [
        update(KnowledgeDocumentVersionRow).values(parser_version="changed"),
        delete(KnowledgeChunkRow),
    ],
)
async def test_database_rejects_knowledge_version_or_chunk_mutation(database, statement):
    workspace_id, _, _ = await seed(database)
    async with database.begin() as session:
        await persist_document_version(
            session,
            workspace_id=workspace_id,
            document_id=uuid4(),
            name="immutable.txt",
            media_type="text/plain",
            source_hash="b" * 64,
            storage_uri="s3://knowledge/immutable.txt",
            parser_version="parser-1",
            chunks=(ChunkInput("immutable knowledge", {"line": 1}, 2, vector(2)),),
        )
    with pytest.raises(DBAPIError):
        async with database.begin() as session:
            await session.execute(statement.where(statement.table.c.workspace_id == workspace_id))
