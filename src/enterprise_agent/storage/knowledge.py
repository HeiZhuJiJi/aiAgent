import hashlib
import json
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import JsonValue
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.runtime.retrieval import RetrievalCandidate
from enterprise_agent.storage.tables import (
    KnowledgeChunkRow,
    KnowledgeDocumentRow,
    KnowledgeDocumentVersionRow,
)

EMBEDDING_DIMENSION = 1024


@dataclass(frozen=True)
class ChunkInput:
    content: str
    locator: dict[str, JsonValue]
    token_count: int
    embedding: tuple[float, ...]


async def find_document_version_replay(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    document_id: UUID,
    name: str,
    media_type: str,
    source_hash: str,
    storage_uri: str,
    parser_version: str,
) -> UUID | None:
    """Return an immutable version before expensive parsing and embedding on replay."""
    document = await session.scalar(
        select(KnowledgeDocumentRow)
        .where(KnowledgeDocumentRow.id == document_id)
        .with_for_update()
    )
    if document is None:
        return None
    if (
        document.workspace_id != workspace_id
        or document.name != name
        or document.media_type != media_type
    ):
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "document identity changed")
    existing = await session.scalar(
        select(KnowledgeDocumentVersionRow).where(
            KnowledgeDocumentVersionRow.workspace_id == workspace_id,
            KnowledgeDocumentVersionRow.document_id == document_id,
            KnowledgeDocumentVersionRow.source_hash == source_hash,
        )
    )
    if existing is None:
        return None
    if existing.storage_uri != storage_uri or existing.parser_version != parser_version:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "document version replay changed storage or parser identity",
        )
    return existing.id


async def persist_document_version(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    document_id: UUID,
    name: str,
    media_type: str,
    source_hash: str,
    storage_uri: str,
    parser_version: str,
    chunks: tuple[ChunkInput, ...],
) -> UUID:
    if len(source_hash) != 64 or not chunks:
        raise DomainError(FailureCode.VALIDATION_FAILED, "invalid source hash or empty document")
    if any(
        not item.content.strip()
        or item.token_count <= 0
        or len(item.embedding) != EMBEDDING_DIMENSION
        for item in chunks
    ):
        raise DomainError(FailureCode.VALIDATION_FAILED, "invalid knowledge chunk")
    document = await session.scalar(
        select(KnowledgeDocumentRow).where(KnowledgeDocumentRow.id == document_id).with_for_update()
    )
    if document is None:
        document = KnowledgeDocumentRow(
            id=document_id,
            workspace_id=workspace_id,
            name=name,
            media_type=media_type,
        )
        session.add(document)
        await session.flush()
    elif (
        document.workspace_id != workspace_id
        or document.name != name
        or document.media_type != media_type
    ):
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "document identity changed")
    existing = await session.scalar(
        select(KnowledgeDocumentVersionRow).where(
            KnowledgeDocumentVersionRow.workspace_id == workspace_id,
            KnowledgeDocumentVersionRow.document_id == document_id,
            KnowledgeDocumentVersionRow.source_hash == source_hash,
        )
    )
    if existing is not None:
        stored_chunks = (
            await session.scalars(
                select(KnowledgeChunkRow)
                .where(
                    KnowledgeChunkRow.workspace_id == workspace_id,
                    KnowledgeChunkRow.document_version_id == existing.id,
                )
                .order_by(KnowledgeChunkRow.ordinal)
            )
        ).all()
        expected_hashes = [
            hashlib.sha256(item.content.strip().encode()).hexdigest() for item in chunks
        ]
        if (
            existing.storage_uri != storage_uri
            or existing.parser_version != parser_version
            or [row.content_hash for row in stored_chunks] != expected_hashes
            or [row.locator for row in stored_chunks] != [dict(item.locator) for item in chunks]
            or [row.token_count for row in stored_chunks] != [item.token_count for item in chunks]
        ):
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "document version replay changed indexed content",
            )
        return existing.id
    version = int(
        await session.scalar(
            select(func.coalesce(func.max(KnowledgeDocumentVersionRow.version), 0)).where(
                KnowledgeDocumentVersionRow.workspace_id == workspace_id,
                KnowledgeDocumentVersionRow.document_id == document_id,
            )
        )
        or 0
    ) + 1
    version_id = uuid5(NAMESPACE_URL, f"knowledge-version:{workspace_id}:{document_id}:{source_hash}")
    session.add(
        KnowledgeDocumentVersionRow(
            id=version_id,
            workspace_id=workspace_id,
            document_id=document_id,
            version=version,
            source_hash=source_hash,
            storage_uri=storage_uri,
            parser_version=parser_version,
        )
    )
    await session.flush()
    for ordinal, item in enumerate(chunks):
        content = item.content.strip()
        digest = hashlib.sha256(content.encode()).hexdigest()
        session.add(
            KnowledgeChunkRow(
                id=uuid5(NAMESPACE_URL, f"knowledge-chunk:{version_id}:{ordinal}:{digest}"),
                workspace_id=workspace_id,
                document_version_id=version_id,
                ordinal=ordinal,
                content=content,
                content_hash=digest,
                locator=dict(item.locator),
                token_count=item.token_count,
                embedding=list(item.embedding),
            )
        )
    document.version += 1
    await session.flush()
    return version_id


def _vector_literal(embedding: tuple[float, ...]) -> str:
    if len(embedding) != EMBEDDING_DIMENSION:
        raise DomainError(FailureCode.VALIDATION_FAILED, "embedding dimension mismatch")
    return "[" + ",".join(format(value, ".9g") for value in embedding) + "]"


async def lexical_candidates(
    session: AsyncSession, workspace_id: UUID, query: str, limit: int = 50
) -> tuple[RetrievalCandidate, ...]:
    rows = (
        await session.execute(
            text("""
                SELECT c.id, c.content, c.locator, c.document_version_id
                FROM knowledge_chunks c
                WHERE c.workspace_id = :workspace_id
                  AND c.search_vector @@ plainto_tsquery('simple', :query)
                ORDER BY ts_rank_cd(c.search_vector, plainto_tsquery('simple', :query)) DESC,
                         c.id
                LIMIT :limit
            """),
            {"workspace_id": workspace_id, "query": query, "limit": limit},
        )
    ).mappings().all()
    return tuple(
        RetrievalCandidate(
            chunk_id=str(row["id"]),
            content=str(row["content"]),
            locator=json.dumps(row["locator"], sort_keys=True),
            source_id=str(row["document_version_id"]),
        )
        for row in rows
    )


async def semantic_candidates(
    session: AsyncSession,
    workspace_id: UUID,
    embedding: tuple[float, ...],
    limit: int = 50,
) -> tuple[RetrievalCandidate, ...]:
    rows = (
        await session.execute(
            text("""
                SELECT c.id, c.content, c.locator, c.document_version_id
                FROM knowledge_chunks c
                WHERE c.workspace_id = :workspace_id
                ORDER BY c.embedding <=> CAST(:embedding AS vector), c.id
                LIMIT :limit
            """),
            {"workspace_id": workspace_id, "embedding": _vector_literal(embedding), "limit": limit},
        )
    ).mappings().all()
    return tuple(
        RetrievalCandidate(
            chunk_id=str(row["id"]), content=str(row["content"]),
            locator=json.dumps(row["locator"], sort_keys=True),
            source_id=str(row["document_version_id"]),
        )
        for row in rows
    )
