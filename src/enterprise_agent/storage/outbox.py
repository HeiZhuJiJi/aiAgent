import hashlib
import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import Contract, FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.tables import OutboxEventRow


class OutboxLease(Contract):
    event_id: UUID
    workspace_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    payload: dict[str, JsonValue]
    attempts: int
    lease_token: int


def payload_digest(payload: dict[str, JsonValue]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


async def enqueue(
    session: AsyncSession,
    event_id: UUID,
    workspace_id: UUID,
    aggregate_type: str,
    aggregate_id: UUID,
    event_type: str,
    payload: dict[str, JsonValue],
) -> OutboxEventRow:
    digest = payload_digest(payload)
    now = await session.scalar(select(func.clock_timestamp()))
    if now is None:
        raise DomainError(FailureCode.DB_UNAVAILABLE, "database clock unavailable")
    await session.execute(
        insert(OutboxEventRow)
        .values(
            id=event_id,
            workspace_id=workspace_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload_hash=digest,
            payload=payload,
            available_at=now,
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    row = await session.scalar(
        select(OutboxEventRow).where(
            OutboxEventRow.workspace_id == workspace_id, OutboxEventRow.id == event_id
        )
    )
    if (
        row is None
        or row.payload_hash != digest
        or row.aggregate_type != aggregate_type
        or row.aggregate_id != aggregate_id
        or row.event_type != event_type
    ):
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "outbox event identity mismatch")
    return row


async def claim_next(
    session: AsyncSession,
    worker_id: str,
    lease_seconds: int = 30,
    workspace_id: UUID | None = None,
    after_workspace_id: UUID | None = None,
    allowed_task_queues: Sequence[str] | None = None,
) -> OutboxLease | None:
    if not worker_id or lease_seconds <= 0:
        raise DomainError(FailureCode.VALIDATION_FAILED, "invalid outbox lease request")
    # Serialize claims for one workspace so concurrent selectors cannot observe
    # an empty candidate set while earlier lease updates are still committing.
    lock_scope = "global" if workspace_id is None else str(workspace_id)
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(lock_scope, 0)))
    )
    now = await session.scalar(select(func.clock_timestamp()))
    if now is None:
        raise DomainError(FailureCode.DB_UNAVAILABLE, "database clock unavailable")
    query = select(OutboxEventRow).where(
        OutboxEventRow.available_at <= now,
        or_(
            OutboxEventRow.state == "PENDING",
            and_(
                OutboxEventRow.state == "LEASED",
                OutboxEventRow.lease_expires_at <= now,
            ),
        ),
    )
    if workspace_id is not None:
        query = query.where(OutboxEventRow.workspace_id == workspace_id)
    if allowed_task_queues is not None:
        queue_bound_events = OutboxEventRow.event_type.in_(
            ("task.execution.requested", "knowledge.ingestion.requested")
        )
        unbound_events = OutboxEventRow.event_type.in_(
            ("approval.decided", "task.control.requested")
        )
        query = query.where(
            or_(
                unbound_events,
                and_(
                    queue_bound_events,
                    OutboxEventRow.payload["task_queue"].astext.in_(allowed_task_queues),
                ),
            )
        )
    ordering: tuple[object, ...]
    if workspace_id is None and after_workspace_id is not None:
        ordering = (
            case((OutboxEventRow.workspace_id > after_workspace_id, 0), else_=1),
            OutboxEventRow.workspace_id,
            OutboxEventRow.available_at,
            OutboxEventRow.created_at,
            OutboxEventRow.id,
        )
    elif workspace_id is None:
        ordering = (
            OutboxEventRow.workspace_id,
            OutboxEventRow.available_at,
            OutboxEventRow.created_at,
            OutboxEventRow.id,
        )
    else:
        ordering = (
            OutboxEventRow.available_at,
            OutboxEventRow.created_at,
            OutboxEventRow.id,
        )
    row = await session.scalar(
        query.order_by(*ordering).limit(1).with_for_update(skip_locked=True)
    )
    if row is None:
        return None
    row.state = "LEASED"
    row.version += 1
    row.attempts += 1
    row.lease_owner = worker_id
    row.lease_token += 1
    row.lease_expires_at = now + timedelta(seconds=lease_seconds)
    await session.flush()
    return OutboxLease.model_validate(
        {
            "event_id": row.id,
            "workspace_id": row.workspace_id,
            "aggregate_type": row.aggregate_type,
            "aggregate_id": row.aggregate_id,
            "event_type": row.event_type,
            "payload": row.payload,
            "attempts": row.attempts,
            "lease_token": row.lease_token,
        }
    )


async def _locked_lease(
    session: AsyncSession, lease: OutboxLease, worker_id: str
) -> tuple[OutboxEventRow, datetime]:
    row = await session.scalar(
        select(OutboxEventRow)
        .where(
            OutboxEventRow.workspace_id == lease.workspace_id,
            OutboxEventRow.id == lease.event_id,
        )
        .with_for_update()
    )
    now = await session.scalar(select(func.clock_timestamp()))
    if (
        row is None
        or now is None
        or row.state != "LEASED"
        or row.lease_owner != worker_id
        or row.lease_token != lease.lease_token
        or row.lease_expires_at is None
        or row.lease_expires_at <= now
    ):
        raise DomainError(FailureCode.STALE_ATTEMPT, "outbox lease rejected")
    return row, now


async def mark_published(session: AsyncSession, lease: OutboxLease, worker_id: str) -> None:
    row, now = await _locked_lease(session, lease, worker_id)
    row.state = "PUBLISHED"
    row.version += 1
    row.published_at = now
    row.lease_owner = None
    row.lease_expires_at = None
    await session.flush()


async def reschedule(
    session: AsyncSession,
    lease: OutboxLease,
    worker_id: str,
    safe_error: str,
    delay_seconds: int,
    max_attempts: int = 10,
) -> None:
    if delay_seconds < 0 or max_attempts <= 0:
        raise DomainError(FailureCode.VALIDATION_FAILED, "invalid outbox retry policy")
    row, now = await _locked_lease(session, lease, worker_id)
    row.state = "FAILED" if row.attempts >= max_attempts else "PENDING"
    row.version += 1
    row.available_at = now + timedelta(seconds=delay_seconds)
    row.last_error = safe_error[:500]
    row.lease_owner = None
    row.lease_expires_at = None
    await session.flush()
