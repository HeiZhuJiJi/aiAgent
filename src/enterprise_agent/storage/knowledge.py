import hashlib
import json
import re
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
        select(KnowledgeDocumentRow).where(KnowledgeDocumentRow.id == document_id).with_for_update()
    )
    if document is None:
        return None
    if document.deleted_at is not None:
        raise DomainError(FailureCode.POLICY_DENIED, "knowledge document unavailable")
    if document.workspace_id != workspace_id or document.name != name:
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
    elif document.deleted_at is not None:
        raise DomainError(FailureCode.POLICY_DENIED, "knowledge document unavailable")
    elif document.workspace_id != workspace_id or document.name != name:
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
    version = (
        int(
            await session.scalar(
                select(func.coalesce(func.max(KnowledgeDocumentVersionRow.version), 0)).where(
                    KnowledgeDocumentVersionRow.workspace_id == workspace_id,
                    KnowledgeDocumentVersionRow.document_id == document_id,
                )
            )
            or 0
        )
        + 1
    )
    version_id = uuid5(
        NAMESPACE_URL, f"knowledge-version:{workspace_id}:{document_id}:{source_hash}"
    )
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
    document.media_type = media_type
    document.version += 1
    await session.flush()
    return version_id


def _vector_literal(embedding: tuple[float, ...]) -> str:
    if len(embedding) != EMBEDDING_DIMENSION:
        raise DomainError(FailureCode.VALIDATION_FAILED, "embedding dimension mismatch")
    return "[" + ",".join(format(value, ".9g") for value in embedding) + "]"


async def lexical_candidates(
    session: AsyncSession,
    workspace_id: UUID,
    query: str,
    limit: int = 50,
    *,
    document_version_id: UUID | None = None,
) -> tuple[RetrievalCandidate, ...]:
    rows = (
        (
            await session.execute(
                text("""
                SELECT c.id, c.content, c.locator, c.document_version_id, d.name AS document_name
                FROM knowledge_chunks c
                JOIN knowledge_document_versions v
                  ON v.workspace_id = c.workspace_id AND v.id = c.document_version_id
                JOIN knowledge_documents d
                  ON d.workspace_id = v.workspace_id AND d.id = v.document_id
                WHERE c.workspace_id = :workspace_id
                  AND d.deleted_at IS NULL
                  AND (
                    CAST(:document_version_id AS uuid) IS NULL
                    OR c.document_version_id = CAST(:document_version_id AS uuid)
                  )
                  AND (
                    CAST(:document_version_id AS uuid) IS NOT NULL
                    OR v.version = (
                      SELECT MAX(latest.version)
                      FROM knowledge_document_versions latest
                      WHERE latest.workspace_id = v.workspace_id
                        AND latest.document_id = v.document_id
                    )
                  )
                  AND c.search_vector @@ plainto_tsquery('simple', :query)
                ORDER BY ts_rank_cd(c.search_vector, plainto_tsquery('simple', :query)) DESC,
                         c.id
                LIMIT :limit
            """),
                {
                    "workspace_id": workspace_id,
                    "document_version_id": document_version_id,
                    "query": query,
                    "limit": limit,
                },
            )
        )
        .mappings()
        .all()
    )
    return tuple(
        RetrievalCandidate(
            chunk_id=str(row["id"]),
            content=str(row["content"]),
            locator=json.dumps(row["locator"], sort_keys=True),
            source_id=str(row["document_version_id"]),
            source_name=str(row["document_name"]),
        )
        for row in rows
    )


async def semantic_candidates(
    session: AsyncSession,
    workspace_id: UUID,
    embedding: tuple[float, ...],
    limit: int = 50,
    *,
    document_version_id: UUID | None = None,
) -> tuple[RetrievalCandidate, ...]:
    rows = (
        (
            await session.execute(
                text("""
                SELECT c.id, c.content, c.locator, c.document_version_id, d.name AS document_name
                FROM knowledge_chunks c
                JOIN knowledge_document_versions v
                  ON v.workspace_id = c.workspace_id AND v.id = c.document_version_id
                JOIN knowledge_documents d
                  ON d.workspace_id = v.workspace_id AND d.id = v.document_id
                WHERE c.workspace_id = :workspace_id
                  AND d.deleted_at IS NULL
                  AND (
                    CAST(:document_version_id AS uuid) IS NULL
                    OR c.document_version_id = CAST(:document_version_id AS uuid)
                  )
                  AND (
                    CAST(:document_version_id AS uuid) IS NOT NULL
                    OR v.version = (
                      SELECT MAX(latest.version)
                      FROM knowledge_document_versions latest
                      WHERE latest.workspace_id = v.workspace_id
                        AND latest.document_id = v.document_id
                    )
                  )
                ORDER BY c.embedding <=> CAST(:embedding AS vector), c.id
                LIMIT :limit
            """),
                {
                    "workspace_id": workspace_id,
                    "document_version_id": document_version_id,
                    "embedding": _vector_literal(embedding),
                    "limit": limit,
                },
            )
        )
        .mappings()
        .all()
    )
    return tuple(
        RetrievalCandidate(
            chunk_id=str(row["id"]),
            content=str(row["content"]),
            locator=json.dumps(row["locator"], sort_keys=True),
            source_id=str(row["document_version_id"]),
            source_name=str(row["document_name"]),
        )
        for row in rows
    )


def _summary_sample_ordinals(total_chunks: int, sample_limit: int) -> tuple[int, ...]:
    """Choose deterministic, document-wide chunk positions for bounded summarization."""
    if total_chunks < 0 or sample_limit <= 0:
        raise ValueError("summary sampling bounds are invalid")
    if total_chunks <= sample_limit:
        return tuple(range(total_chunks))
    if sample_limit == 1:
        return (0,)
    denominator = sample_limit - 1
    return tuple(
        (index * (total_chunks - 1) + denominator // 2) // denominator
        for index in range(sample_limit)
    )


async def document_summary_candidates(
    session: AsyncSession,
    workspace_id: UUID,
    document_version_id: UUID,
    *,
    limit: int = 5,
    samples_per_result: int = 4,
    max_complete_characters: int | None = None,
) -> tuple[RetrievalCandidate, ...]:
    """Return bounded, ordered coverage of one immutable document version.

    Whole-document summarization is not a similarity-search problem. For small
    documents every chunk is retained; larger documents are sampled uniformly
    from beginning to end. Several original chunks are packed into each result
    so the existing Evidence/result bound remains intact without relabelling
    generated text as document Evidence.
    """
    if limit <= 0 or samples_per_result <= 0:
        raise ValueError("document summary limits must be positive")
    document_name = await session.scalar(
        select(KnowledgeDocumentRow.name).select_from(KnowledgeDocumentVersionRow)
        .join(
            KnowledgeDocumentRow,
            (KnowledgeDocumentRow.workspace_id == KnowledgeDocumentVersionRow.workspace_id)
            & (KnowledgeDocumentRow.id == KnowledgeDocumentVersionRow.document_id),
        )
        .where(
            KnowledgeDocumentVersionRow.workspace_id == workspace_id,
            KnowledgeDocumentVersionRow.id == document_version_id,
            KnowledgeDocumentRow.deleted_at.is_(None),
        )
    )
    if document_name is None:
        return ()
    total_chunks = int(
        await session.scalar(
            select(func.count(KnowledgeChunkRow.id)).where(
                KnowledgeChunkRow.workspace_id == workspace_id,
                KnowledgeChunkRow.document_version_id == document_version_id,
            )
        )
        or 0
    )
    if total_chunks == 0:
        return ()
    sample_count = limit * samples_per_result
    if max_complete_characters is not None and total_chunks <= 200:
        total_characters = int(await session.scalar(
            select(func.sum(func.length(KnowledgeChunkRow.content))).where(
                KnowledgeChunkRow.workspace_id == workspace_id,
                KnowledgeChunkRow.document_version_id == document_version_id,
            )
        ) or 0)
        if total_characters + total_chunks * 2 <= max_complete_characters:
            sample_count = total_chunks
    ordinals = _summary_sample_ordinals(total_chunks, sample_count)
    rows = list(
        (
            await session.scalars(
                select(KnowledgeChunkRow)
                .where(
                    KnowledgeChunkRow.workspace_id == workspace_id,
                    KnowledgeChunkRow.document_version_id == document_version_id,
                    KnowledgeChunkRow.ordinal.in_(ordinals),
                )
                .order_by(KnowledgeChunkRow.ordinal)
            )
        ).all()
    )
    if not rows:
        return ()

    result_count = min(limit, len(rows))
    coverage_complete = len(rows) == total_chunks
    candidates: list[RetrievalCandidate] = []
    for result_index in range(result_count):
        start = result_index * len(rows) // result_count
        end = (result_index + 1) * len(rows) // result_count
        selected = rows[start:end]
        content = "\n\n".join(row.content for row in selected)
        digest = hashlib.sha256(content.encode()).hexdigest()
        locator = {
            "mode": "DOCUMENT_SUMMARY_COVERAGE",
            "document_name": document_name,
            "document_chunk_count": total_chunks,
            "selected_chunk_count": len(rows),
            "selected_ordinals": [row.ordinal for row in selected],
            "source_locators": [row.locator for row in selected],
            "coverage_complete": coverage_complete,
        }
        candidates.append(
            RetrievalCandidate(
                chunk_id=f"summary:{document_version_id}:{digest}",
                content=content,
                locator=json.dumps(locator, sort_keys=True),
                source_id=str(document_version_id),
                source_name=document_name,
            )
        )
    return tuple(candidates)


async def document_neighborhood_candidates(
    session: AsyncSession,
    workspace_id: UUID,
    document_version_id: UUID,
    anchor_chunk_id: UUID,
    *,
    limit: int = 4,
    chunks_per_result: int = 4,
) -> tuple[RetrievalCandidate, ...]:
    """Return bounded, contiguous source text around a retrieved policy section.

    A heading-only markdown chunk must not crowd out the paragraphs that follow
    it. Packed results preserve the original locators and remain within the
    caller's Evidence count limit.
    """
    if limit <= 0 or chunks_per_result <= 0:
        raise ValueError("document neighborhood limits must be positive")
    anchor = await session.scalar(
        select(KnowledgeChunkRow)
        .join(
            KnowledgeDocumentVersionRow,
            (KnowledgeDocumentVersionRow.workspace_id == KnowledgeChunkRow.workspace_id)
            & (KnowledgeDocumentVersionRow.id == KnowledgeChunkRow.document_version_id),
        )
        .join(
            KnowledgeDocumentRow,
            (KnowledgeDocumentRow.workspace_id == KnowledgeDocumentVersionRow.workspace_id)
            & (KnowledgeDocumentRow.id == KnowledgeDocumentVersionRow.document_id),
        )
        .where(
            KnowledgeChunkRow.workspace_id == workspace_id,
            KnowledgeChunkRow.document_version_id == document_version_id,
            KnowledgeChunkRow.id == anchor_chunk_id,
            KnowledgeDocumentRow.deleted_at.is_(None),
        )
    )
    if anchor is None:
        return ()
    start = max(0, anchor.ordinal - 2)
    rows = list(
        (
            await session.scalars(
                select(KnowledgeChunkRow)
                .where(
                    KnowledgeChunkRow.workspace_id == workspace_id,
                    KnowledgeChunkRow.document_version_id == document_version_id,
                    KnowledgeChunkRow.ordinal >= start,
                )
                .order_by(KnowledgeChunkRow.ordinal)
                .limit(limit * chunks_per_result)
            )
        ).all()
    )
    candidates: list[RetrievalCandidate] = []
    for offset in range(0, len(rows), chunks_per_result):
        selected = rows[offset : offset + chunks_per_result]
        content = "\n\n".join(row.content for row in selected)
        locator = {
            "mode": "DOCUMENT_NEIGHBORHOOD",
            "selected_ordinals": [row.ordinal for row in selected],
            "source_locators": [row.locator for row in selected],
        }
        candidates.append(
            RetrievalCandidate(
                chunk_id=f"neighborhood:{document_version_id}:{selected[0].ordinal}",
                content=content,
                locator=json.dumps(locator, sort_keys=True),
                source_id=str(document_version_id),
            )
        )
    return tuple(candidates)


async def document_procedure_candidates(
    session: AsyncSession,
    workspace_id: UUID,
    document_version_id: UUID,
    query: str,
    *,
    limit: int = 5,
) -> tuple[RetrievalCandidate, ...]:
    """Read the matching procedural sections, not only one similar heading.

    Markdown ingestion stores headings and their text as separate small chunks.
    A broad workflow question needs several heading/body pairs (for example,
    preparation, verification, and training). PDF ingestion instead yields a
    few larger chunks, which are kept separately so later ranking can choose
    the relevant passage rather than a concatenated document introduction.
    """
    if limit <= 0:
        raise ValueError("document procedure limit must be positive")
    active_document = (
        await session.execute(
            select(KnowledgeDocumentVersionRow.id, KnowledgeDocumentRow.name)
            .join(
                KnowledgeDocumentRow,
                (KnowledgeDocumentRow.workspace_id == KnowledgeDocumentVersionRow.workspace_id)
                & (KnowledgeDocumentRow.id == KnowledgeDocumentVersionRow.document_id),
            )
            .where(
                KnowledgeDocumentVersionRow.workspace_id == workspace_id,
                KnowledgeDocumentVersionRow.id == document_version_id,
                KnowledgeDocumentRow.deleted_at.is_(None),
            )
        )
    ).first()
    if active_document is None:
        return ()
    rows = list(
        (
            await session.scalars(
                select(KnowledgeChunkRow)
                .where(
                    KnowledgeChunkRow.workspace_id == workspace_id,
                    KnowledgeChunkRow.document_version_id == document_version_id,
                )
                .order_by(KnowledgeChunkRow.ordinal)
                .limit(256)
            )
        ).all()
    )
    if not rows:
        return ()
    subject = re.sub(
        r"^[\s，。！？?]+|[\s，。！？?]+$",
        "",
        re.sub(
            r"(?:流程|步骤|怎么|如何|是什么|请问|我想|我要|办理|一下|有哪些|什么|\d+天)",
            "",
            query,
        ),
    )
    terms = tuple(
        dict.fromkeys(
            part[index : index + 2]
            for part in re.findall(r"[\u3400-\u9fff]{2,}", subject)
            for index in range(len(part) - 1)
        )
    )
    headings = [index for index, row in enumerate(rows) if re.match(r"^#{1,6}\s+", row.content)]
    sections: list[list[KnowledgeChunkRow]] = []
    for position, heading_index in enumerate(headings):
        heading = rows[heading_index].content
        if not terms or not any(term in heading for term in terms):
            continue
        end = headings[position + 1] if position + 1 < len(headings) else len(rows)
        sections.append(rows[heading_index : min(end, heading_index + 4)])
        if len(sections) == limit:
            break
    if not sections and len(rows) <= limit * 4:
        result_count = min(limit, len(rows))
        sections = [
            rows[index * len(rows) // result_count : (index + 1) * len(rows) // result_count]
            for index in range(result_count)
        ]
    candidates: list[RetrievalCandidate] = []
    for section in sections:
        content = "\n\n".join(row.content for row in section)
        candidates.append(
            RetrievalCandidate(
                chunk_id=f"procedure:{document_version_id}:{section[0].ordinal}",
                content=content,
                locator=json.dumps(
                    {
                        "mode": "DOCUMENT_PROCEDURE_SECTION",
                        "document_name": active_document[1],
                        "selected_ordinals": [row.ordinal for row in section],
                        "source_locators": [row.locator for row in section],
                    },
                    sort_keys=True,
                ),
                source_id=str(document_version_id),
            )
        )
    return tuple(candidates)
