from dataclasses import dataclass
from typing import Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import FailureCode, KnowledgeIngestionView
from enterprise_agent.domain.states import DomainError
from enterprise_agent.identity.contracts import Identity
from enterprise_agent.storage.outbox import enqueue
from enterprise_agent.storage.tables import AuditRow, KnowledgeIngestionJobRow


@dataclass(frozen=True)
class KnowledgeJobClaim:
    ingestion_id: UUID
    workspace_id: UUID
    document_id: UUID
    name: str
    media_type: str
    source_hash: str
    object_name: str
    parser_version: str
    fencing_token: int


def knowledge_job_view(row: KnowledgeIngestionJobRow) -> KnowledgeIngestionView:
    return KnowledgeIngestionView(
        ingestion_id=row.id,
        workspace_id=row.workspace_id,
        document_id=row.document_id,
        name=row.name,
        media_type=row.media_type,
        source_hash=row.source_hash,
        parser_version=row.parser_version,
        state=cast(Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED"], row.state),
        version=row.version,
        document_version_id=row.document_version_id,
        safe_error=row.safe_error,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


async def request_knowledge_ingestion(
    session: AsyncSession,
    *,
    identity: Identity,
    workspace_id: UUID,
    document_id: UUID,
    name: str,
    media_type: str,
    source_hash: str,
    object_name: str,
    parser_version: str,
    trace_id: str,
    task_queue: str,
) -> KnowledgeIngestionView:
    ingestion_id = uuid5(
        NAMESPACE_URL, f"knowledge-ingestion:{workspace_id}:{document_id}:{source_hash}"
    )
    row = await session.scalar(
        select(KnowledgeIngestionJobRow)
        .where(
            KnowledgeIngestionJobRow.workspace_id == workspace_id,
            KnowledgeIngestionJobRow.id == ingestion_id,
        )
        .with_for_update()
    )
    if row is not None:
        identity_fields = (
            row.document_id,
            row.name,
            row.media_type,
            row.source_hash,
            row.object_name,
            row.parser_version,
        )
        requested_fields = (
            document_id,
            name,
            media_type,
            source_hash,
            object_name,
            parser_version,
        )
        if identity_fields != requested_fields:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "ingestion identity changed")
        return knowledge_job_view(row)
    row = KnowledgeIngestionJobRow(
        id=ingestion_id,
        workspace_id=workspace_id,
        document_id=document_id,
        name=name,
        media_type=media_type,
        source_hash=source_hash,
        object_name=object_name,
        parser_version=parser_version,
        state="PENDING",
    )
    session.add(row)
    await session.flush()
    event_id = uuid5(NAMESPACE_URL, f"knowledge-ingestion-requested:{ingestion_id}")
    await enqueue(
        session,
        event_id,
        workspace_id,
        "KnowledgeIngestion",
        ingestion_id,
        "knowledge.ingestion.requested",
        {
            "workflow_id": f"knowledge-ingestion-{ingestion_id}",
            "task_queue": task_queue,
            "workspace_id": str(workspace_id),
            "ingestion_id": str(ingestion_id),
        },
    )
    session.add(
        AuditRow(
            workspace_id=workspace_id,
            actor_type="USER",
            actor_id=identity.user_id,
            action="knowledge.ingestion.requested",
            resource_type="KNOWLEDGE_DOCUMENT",
            resource_id=document_id,
            result={"ingestion_id": str(ingestion_id), "source_hash": source_hash},
            trace_id=trace_id,
        )
    )
    await session.flush()
    return knowledge_job_view(row)


async def list_knowledge_ingestions(
    session: AsyncSession, workspace_id: UUID
) -> tuple[KnowledgeIngestionView, ...]:
    rows = (
        await session.scalars(
            select(KnowledgeIngestionJobRow)
            .where(KnowledgeIngestionJobRow.workspace_id == workspace_id)
            .order_by(KnowledgeIngestionJobRow.created_at, KnowledgeIngestionJobRow.id)
        )
    ).all()
    return tuple(knowledge_job_view(row) for row in rows)


async def get_knowledge_ingestion(
    session: AsyncSession, workspace_id: UUID, ingestion_id: UUID
) -> KnowledgeIngestionView:
    row = await session.scalar(
        select(KnowledgeIngestionJobRow).where(
            KnowledgeIngestionJobRow.workspace_id == workspace_id,
            KnowledgeIngestionJobRow.id == ingestion_id,
        )
    )
    if row is None:
        raise DomainError(FailureCode.POLICY_DENIED, "knowledge ingestion unavailable")
    return knowledge_job_view(row)


async def claim_knowledge_ingestion(
    session: AsyncSession, workspace_id: UUID, ingestion_id: UUID
) -> KnowledgeJobClaim | None:
    row = await session.scalar(
        select(KnowledgeIngestionJobRow)
        .where(
            KnowledgeIngestionJobRow.workspace_id == workspace_id,
            KnowledgeIngestionJobRow.id == ingestion_id,
        )
        .with_for_update()
    )
    if row is None:
        raise DomainError(FailureCode.VALIDATION_FAILED, "knowledge ingestion does not exist")
    if row.state == "SUCCEEDED":
        return None
    row.state = "RUNNING"
    row.version += 1
    row.fencing_token += 1
    row.safe_error = None
    await session.flush()
    return KnowledgeJobClaim(
        ingestion_id=row.id,
        workspace_id=row.workspace_id,
        document_id=row.document_id,
        name=row.name,
        media_type=row.media_type,
        source_hash=row.source_hash,
        object_name=row.object_name,
        parser_version=row.parser_version,
        fencing_token=row.fencing_token,
    )


async def complete_knowledge_ingestion(
    session: AsyncSession,
    claim: KnowledgeJobClaim,
    document_version_id: UUID,
) -> KnowledgeIngestionView:
    row = await session.scalar(
        select(KnowledgeIngestionJobRow)
        .where(
            KnowledgeIngestionJobRow.workspace_id == claim.workspace_id,
            KnowledgeIngestionJobRow.id == claim.ingestion_id,
        )
        .with_for_update()
    )
    if row is None or row.fencing_token != claim.fencing_token or row.state != "RUNNING":
        raise DomainError(FailureCode.STALE_ATTEMPT, "knowledge ingestion commit fenced")
    now = await session.scalar(select(func.clock_timestamp()))
    if now is None:
        raise DomainError(FailureCode.DB_UNAVAILABLE, "database clock unavailable")
    row.state = "SUCCEEDED"
    row.version += 1
    row.document_version_id = document_version_id
    row.completed_at = now
    row.safe_error = None
    await session.flush()
    return knowledge_job_view(row)


async def fail_knowledge_ingestion(
    session: AsyncSession, claim: KnowledgeJobClaim, safe_error: str
) -> None:
    row = await session.scalar(
        select(KnowledgeIngestionJobRow)
        .where(
            KnowledgeIngestionJobRow.workspace_id == claim.workspace_id,
            KnowledgeIngestionJobRow.id == claim.ingestion_id,
        )
        .with_for_update()
    )
    if row is None or row.fencing_token != claim.fencing_token or row.state != "RUNNING":
        raise DomainError(FailureCode.STALE_ATTEMPT, "knowledge ingestion failure fenced")
    row.state = "FAILED"
    row.version += 1
    row.safe_error = safe_error[:500]
    await session.flush()
