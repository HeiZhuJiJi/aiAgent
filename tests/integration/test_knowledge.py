import json
import os
from uuid import UUID, uuid4

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
    document_neighborhood_candidates,
    document_procedure_candidates,
    document_summary_candidates,
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


async def test_policy_heading_retrieval_includes_following_procedure_and_is_workspace_scoped(
    database,
):
    workspace_id, _, _ = await seed(database)
    chunks = (
        ChunkInput("Policy metadata", {"line": 1}, 3, vector(0)),
        ChunkInput("Onboarding background checks", {"line": 2}, 3, vector(0)),
        ChunkInput("# Onboarding process", {"line": 3}, 3, vector(0)),
        ChunkInput("Submit identity and qualification documents.", {"line": 4}, 7, vector(0)),
        ChunkInput("Open accounts after verification.", {"line": 5}, 6, vector(0)),
        ChunkInput("Complete the induction training.", {"line": 6}, 6, vector(0)),
    )
    async with database.begin() as session:
        version_id = await persist_document_version(
            session,
            workspace_id=workspace_id,
            document_id=uuid4(),
            name="onboarding-policy.md",
            media_type="text/markdown",
            source_hash="7" * 64,
            storage_uri="s3://knowledge/onboarding-policy.md",
            parser_version="parser-test",
            chunks=chunks,
        )
    async with database() as session:
        heading = next(
            hit for hit in await lexical_candidates(session, workspace_id, "Onboarding process")
            if hit.content == "# Onboarding process"
        )
        nearby = await document_neighborhood_candidates(
            session, workspace_id, version_id, UUID(heading.chunk_id), limit=2
        )
        foreign = await document_neighborhood_candidates(
            session, uuid4(), version_id, UUID(heading.chunk_id), limit=2
        )
    assert len(nearby) == 2
    assert "Submit identity" in nearby[0].content
    assert "Complete the induction training" in nearby[1].content
    assert foreign == ()


async def test_procedure_retrieval_covers_multiple_matching_sections(database):
    workspace_id, _, _ = await seed(database)
    chunks = (
        ChunkInput("# 一、目的", {"line": 1}, 2, vector(0)),
        ChunkInput("This policy governs employees.", {"line": 2}, 4, vector(0)),
        ChunkInput("# 二、入职资料", {"line": 3}, 3, vector(0)),
        ChunkInput("Submit identity and qualification documents.", {"line": 4}, 6, vector(0)),
        ChunkInput("# 三、入职准备", {"line": 5}, 3, vector(0)),
        ChunkInput("Open accounts after verification.", {"line": 6}, 5, vector(0)),
        ChunkInput("# 四、入职培训", {"line": 7}, 3, vector(0)),
        ChunkInput("Complete the induction training.", {"line": 8}, 5, vector(0)),
    )
    async with database.begin() as session:
        version_id = await persist_document_version(
            session,
            workspace_id=workspace_id,
            document_id=uuid4(),
            name="onboarding-policy.md",
            media_type="text/markdown",
            source_hash="e" * 64,
            storage_uri="s3://knowledge/onboarding-policy.md",
            parser_version="parser-test",
            chunks=chunks,
        )
    async with database() as session:
        sections = await document_procedure_candidates(
            session, workspace_id, version_id, "入职流程", limit=4
        )
        foreign = await document_procedure_candidates(
            session, uuid4(), version_id, "入职流程", limit=4
        )
    assert len(sections) == 3
    assert "Submit identity" in sections[0].content
    assert "Open accounts" in sections[1].content
    assert "induction training" in sections[2].content
    assert foreign == ()


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


async def test_retrieval_can_be_scoped_to_one_selected_immutable_version(database):
    workspace_id, _, _ = await seed(database)
    async with database.begin() as session:
        selected_version = await persist_document_version(
            session,
            workspace_id=workspace_id,
            document_id=uuid4(),
            name="selected.txt",
            media_type="text/plain",
            source_hash="d" * 64,
            storage_uri="s3://knowledge/selected.txt",
            parser_version="parser-1",
            chunks=(
                ChunkInput("shared term selected fact", {"line": 1}, 4, vector(0)),
            ),
        )
        other_version = await persist_document_version(
            session,
            workspace_id=workspace_id,
            document_id=uuid4(),
            name="other.txt",
            media_type="text/plain",
            source_hash="e" * 64,
            storage_uri="s3://knowledge/other.txt",
            parser_version="parser-1",
            chunks=(
                ChunkInput("shared term unrelated fact", {"line": 1}, 4, vector(0)),
            ),
        )

    async with database() as session:
        lexical = await lexical_candidates(
            session,
            workspace_id,
            "shared term",
            document_version_id=selected_version,
        )
        semantic = await semantic_candidates(
            session,
            workspace_id,
            vector(0),
            document_version_id=selected_version,
        )

    assert {item.source_id for item in lexical} == {str(selected_version)}
    assert {item.source_id for item in semantic} == {str(selected_version)}
    assert str(other_version) not in {item.source_id for item in (*lexical, *semantic)}


async def test_workspace_retrieval_uses_only_each_active_documents_latest_version(database):
    workspace_id, _, _ = await seed(database)
    document_id = uuid4()
    async with database.begin() as session:
        old_version = await persist_document_version(
            session,
            workspace_id=workspace_id,
            document_id=document_id,
            name="leave-policy.txt",
            media_type="text/plain",
            source_hash="1" * 64,
            storage_uri="s3://knowledge/leave-policy-v1.txt",
            parser_version="parser-1",
            chunks=(ChunkInput("leave policy old approval rule", {"line": 1}, 5, vector(0)),),
        )
        latest_version = await persist_document_version(
            session,
            workspace_id=workspace_id,
            document_id=document_id,
            name="leave-policy.txt",
            media_type="text/plain",
            source_hash="2" * 64,
            storage_uri="s3://knowledge/leave-policy-v2.txt",
            parser_version="parser-1",
            chunks=(ChunkInput("leave policy current approval rule", {"line": 1}, 5, vector(0)),),
        )

    async with database() as session:
        workspace_lexical = await lexical_candidates(session, workspace_id, "leave policy")
        workspace_semantic = await semantic_candidates(session, workspace_id, vector(0))
        selected_old = await lexical_candidates(
            session,
            workspace_id,
            "leave policy",
            document_version_id=old_version,
        )

    assert {item.source_id for item in workspace_lexical} == {str(latest_version)}
    assert {item.source_id for item in workspace_semantic} == {str(latest_version)}
    assert {item.source_id for item in selected_old} == {str(old_version)}


async def test_document_summary_candidates_preserve_order_scope_and_bounded_coverage(database):
    workspace_id, _, _ = await seed(database)
    selected_chunks = tuple(
        ChunkInput(
            f"selected section {index}",
            {"page": index + 1},
            3,
            vector(index % 4),
        )
        for index in range(7)
    )
    async with database.begin() as session:
        selected_version = await persist_document_version(
            session,
            workspace_id=workspace_id,
            document_id=uuid4(),
            name="summary.txt",
            media_type="text/plain",
            source_hash="f" * 64,
            storage_uri="s3://knowledge/summary.txt",
            parser_version="parser-1",
            chunks=selected_chunks,
        )
        await persist_document_version(
            session,
            workspace_id=workspace_id,
            document_id=uuid4(),
            name="other-summary.txt",
            media_type="text/plain",
            source_hash="0" * 64,
            storage_uri="s3://knowledge/other-summary.txt",
            parser_version="parser-1",
            chunks=(ChunkInput("foreign section", {"page": 99}, 2, vector(0)),),
        )

    async with database() as session:
        candidates = await document_summary_candidates(
            session,
            workspace_id,
            selected_version,
            limit=2,
            samples_per_result=2,
        )

    assert len(candidates) == 2
    assert {item.source_id for item in candidates} == {str(selected_version)}
    assert all(item.source_name == "summary.txt" for item in candidates)
    from enterprise_agent.runtime.conversation_results import _document_citation
    assert all(_document_citation(item.locator).startswith("summary.txt 第") for item in candidates)
    assert all("foreign section" not in item.content for item in candidates)
    locators = [json.loads(item.locator) for item in candidates]
    selected_ordinals = [
        ordinal
        for locator in locators
        for ordinal in locator["selected_ordinals"]
    ]
    assert selected_ordinals == sorted(selected_ordinals)
    assert selected_ordinals[0] == 0
    assert selected_ordinals[-1] == 6
    assert all(locator["document_chunk_count"] == 7 for locator in locators)
    assert all(locator["coverage_complete"] is False for locator in locators)

    async with database() as session:
        complete = await document_summary_candidates(session, workspace_id, selected_version, limit=1, samples_per_result=2, max_complete_characters=32000)
    assert len(complete) == 1
    locator = json.loads(complete[0].locator)
    assert locator["coverage_complete"] is True
    assert locator["selected_ordinals"] == list(range(7))
    assert all(f"selected section {index}" in complete[0].content for index in range(7))
    assert "foreign section" not in complete[0].content


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
