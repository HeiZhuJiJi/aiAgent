from dataclasses import dataclass
from typing import Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import FailureCode, KnowledgeIngestionView
from enterprise_agent.domain.states import DomainError
from enterprise_agent.identity.contracts import Identity
from enterprise_agent.storage.outbox import enqueue
from enterprise_agent.storage.tables import (
    AuditRow,
    KnowledgeDocumentRow,
    KnowledgeDocumentVersionRow,
    KnowledgeIngestionJobRow,
    OutboxEventRow,
)


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


def format_knowledge_ingestion_failure(
    code: FailureCode,
    phase: str,
    detail: str,
) -> str:
    normalized_phase = "_".join(phase.strip().upper().split()) or "UNKNOWN"
    normalized_detail = " ".join(detail.split()) or "knowledge ingestion failed"
    return f"{code.value} [{normalized_phase}]: {normalized_detail}"[:500]


def knowledge_job_view(
    row: KnowledgeIngestionJobRow,
    document_version: int | None = None,
) -> KnowledgeIngestionView:
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
        document_version=document_version,
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
        if row.deleted_at is not None:
            raise DomainError(FailureCode.POLICY_DENIED, "knowledge document unavailable")
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


async def retry_knowledge_ingestion(
    session: AsyncSession,
    *,
    identity: Identity,
    workspace_id: UUID,
    ingestion_id: UUID,
    expected_version: int,
    trace_id: str,
    task_queue: str,
) -> KnowledgeIngestionView:
    if expected_version <= 0 or not task_queue.strip():
        raise DomainError(FailureCode.VALIDATION_FAILED, "invalid ingestion retry request")
    row = await session.scalar(
        select(KnowledgeIngestionJobRow)
        .where(
            KnowledgeIngestionJobRow.workspace_id == workspace_id,
            KnowledgeIngestionJobRow.id == ingestion_id,
        )
        .with_for_update()
    )
    if row is None:
        raise DomainError(FailureCode.POLICY_DENIED, "knowledge ingestion unavailable")
    if row.deleted_at is not None:
        raise DomainError(FailureCode.POLICY_DENIED, "knowledge ingestion unavailable")

    retry_version = expected_version + 1
    event_id = uuid5(
        NAMESPACE_URL,
        f"knowledge-ingestion-retry-requested:{ingestion_id}:{retry_version}",
    )
    payload: dict[str, JsonValue] = {
        "workflow_id": f"knowledge-ingestion-{ingestion_id}-v{retry_version}",
        "task_queue": task_queue,
        "workspace_id": str(workspace_id),
        "ingestion_id": str(ingestion_id),
        "retry_version": retry_version,
    }

    # A response-loss replay carries the old expected_version. The worker may
    # already have moved the job beyond PENDING, so the canonical indication that
    # this logical command was accepted is its deterministic Outbox event rather
    # than the job's current state. Re-enqueueing verifies the immutable event
    # identity without duplicating work.
    existing_retry_event = await session.scalar(
        select(OutboxEventRow).where(
            OutboxEventRow.workspace_id == workspace_id,
            OutboxEventRow.id == event_id,
        )
    )
    if existing_retry_event is not None:
        await enqueue(
            session,
            event_id,
            workspace_id,
            "KnowledgeIngestion",
            ingestion_id,
            "knowledge.ingestion.requested",
            payload,
        )
        if row.version < retry_version:
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "knowledge ingestion retry event is ahead of canonical state",
            )
        return knowledge_job_view(row)
    if row.version != expected_version:
        raise DomainError(FailureCode.STALE_ATTEMPT, "knowledge ingestion version changed")
    if row.state != "FAILED":
        raise DomainError(
            FailureCode.VALIDATION_FAILED,
            "only failed knowledge ingestion can be retried",
        )

    previous_safe_error = row.safe_error
    row.state = "PENDING"
    row.version = retry_version
    row.safe_error = None
    row.completed_at = None
    row.document_version_id = None
    await session.flush()
    await enqueue(
        session,
        event_id,
        workspace_id,
        "KnowledgeIngestion",
        ingestion_id,
        "knowledge.ingestion.requested",
        payload,
    )
    session.add(
        AuditRow(
            workspace_id=workspace_id,
            actor_type="USER",
            actor_id=identity.user_id,
            action="knowledge.ingestion.retry_requested",
            resource_type="KNOWLEDGE_DOCUMENT",
            resource_id=row.document_id,
            result={
                "ingestion_id": str(ingestion_id),
                "retry_version": retry_version,
                "previous_safe_error": previous_safe_error,
            },
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
            .where(
                KnowledgeIngestionJobRow.workspace_id == workspace_id,
                KnowledgeIngestionJobRow.deleted_at.is_(None),
            )
            .order_by(KnowledgeIngestionJobRow.created_at, KnowledgeIngestionJobRow.id)
        )
    ).all()
    version_ids = tuple(
        row.document_version_id for row in rows if row.document_version_id is not None
    )
    version_by_id: dict[UUID, int] = {}
    if version_ids:
        versions = (
            await session.scalars(
                select(KnowledgeDocumentVersionRow).where(
                    KnowledgeDocumentVersionRow.workspace_id == workspace_id,
                    KnowledgeDocumentVersionRow.id.in_(version_ids),
                )
            )
        ).all()
        version_by_id = {row.id: row.version for row in versions}
    return tuple(
        knowledge_job_view(
            row,
            None if row.document_version_id is None else version_by_id.get(row.document_version_id),
        )
        for row in rows
    )


async def get_knowledge_ingestion(
    session: AsyncSession, workspace_id: UUID, ingestion_id: UUID
) -> KnowledgeIngestionView:
    row = await session.scalar(
        select(KnowledgeIngestionJobRow).where(
            KnowledgeIngestionJobRow.workspace_id == workspace_id,
            KnowledgeIngestionJobRow.id == ingestion_id,
            KnowledgeIngestionJobRow.deleted_at.is_(None),
        )
    )
    if row is None:
        raise DomainError(FailureCode.POLICY_DENIED, "knowledge ingestion unavailable")
    document_version = None
    if row.document_version_id is not None:
        document_version = await session.scalar(
            select(KnowledgeDocumentVersionRow.version).where(
                KnowledgeDocumentVersionRow.workspace_id == workspace_id,
                KnowledgeDocumentVersionRow.id == row.document_version_id,
            )
        )
    return knowledge_job_view(row, document_version)


async def claim_knowledge_ingestion(
    session: AsyncSession, workspace_id: UUID, ingestion_id: UUID
) -> KnowledgeJobClaim | None:
    row = await session.scalar(
        select(KnowledgeIngestionJobRow)
        .where(
            KnowledgeIngestionJobRow.workspace_id == workspace_id,
            KnowledgeIngestionJobRow.id == ingestion_id,
            KnowledgeIngestionJobRow.deleted_at.is_(None),
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
    row.completed_at = None
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
            KnowledgeIngestionJobRow.deleted_at.is_(None),
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
    document_version = await session.scalar(
        select(KnowledgeDocumentVersionRow.version).where(
            KnowledgeDocumentVersionRow.workspace_id == claim.workspace_id,
            KnowledgeDocumentVersionRow.id == document_version_id,
        )
    )
    return knowledge_job_view(row, document_version)


async def fail_knowledge_ingestion(
    session: AsyncSession, claim: KnowledgeJobClaim, safe_error: str
) -> None:
    await record_knowledge_ingestion_attempt_failure(
        session,
        claim,
        safe_error,
        terminal=True,
    )


async def record_knowledge_ingestion_attempt_failure(
    session: AsyncSession,
    claim: KnowledgeJobClaim,
    safe_error: str,
    *,
    terminal: bool,
) -> KnowledgeIngestionView:
    row = await session.scalar(
        select(KnowledgeIngestionJobRow)
        .where(
            KnowledgeIngestionJobRow.workspace_id == claim.workspace_id,
            KnowledgeIngestionJobRow.id == claim.ingestion_id,
            KnowledgeIngestionJobRow.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if row is None or row.fencing_token != claim.fencing_token or row.state != "RUNNING":
        raise DomainError(FailureCode.STALE_ATTEMPT, "knowledge ingestion failure fenced")
    now = await session.scalar(select(func.clock_timestamp()))
    if now is None:
        raise DomainError(FailureCode.DB_UNAVAILABLE, "database clock unavailable")
    row.state = "FAILED" if terminal else "RUNNING"
    row.version += 1
    row.safe_error = safe_error[:500]
    row.completed_at = now if terminal else None
    await session.flush()
    return knowledge_job_view(row)


async def delete_knowledge_document(
    session: AsyncSession,
    *,
    identity: Identity,
    workspace_id: UUID,
    document_id: UUID,
    expected_version: int,
    trace_id: str,
) -> None:
    jobs = list(
        (
            await session.scalars(
                select(KnowledgeIngestionJobRow)
                .where(
                    KnowledgeIngestionJobRow.workspace_id == workspace_id,
                    KnowledgeIngestionJobRow.document_id == document_id,
                    KnowledgeIngestionJobRow.deleted_at.is_(None),
                )
                .order_by(
                    KnowledgeIngestionJobRow.created_at.desc(),
                    KnowledgeIngestionJobRow.id.desc(),
                )
                .with_for_update()
            )
        ).all()
    )
    if not jobs:
        raise DomainError(FailureCode.POLICY_DENIED, "knowledge document unavailable")
    latest = jobs[0]
    if latest.version != expected_version:
        raise DomainError(FailureCode.STALE_ATTEMPT, "knowledge document version changed")
    if any(row.state in {"PENDING", "RUNNING"} for row in jobs):
        raise DomainError(
            FailureCode.VALIDATION_FAILED,
            "knowledge document ingestion is still active",
        )
    document = await session.scalar(
        select(KnowledgeDocumentRow)
        .where(
            KnowledgeDocumentRow.workspace_id == workspace_id,
            KnowledgeDocumentRow.id == document_id,
            KnowledgeDocumentRow.deleted_at.is_(None),
        )
        .with_for_update()
    )
    now = await session.scalar(select(func.clock_timestamp()))
    if now is None:
        raise DomainError(FailureCode.DB_UNAVAILABLE, "database clock unavailable")
    for row in jobs:
        row.deleted_at = now
    if document is not None:
        document.deleted_at = now
        document.version += 1
    session.add(
        AuditRow(
            workspace_id=workspace_id,
            actor_type="USER",
            actor_id=identity.user_id,
            action="knowledge.document.deleted",
            resource_type="KNOWLEDGE_DOCUMENT",
            resource_id=document_id,
            result={
                "hidden_ingestion_count": len(jobs),
                "retained_immutable_versions": True,
            },
            trace_id=trace_id,
        )
    )
    await session.flush()
