import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import ExecutionSnapshot, FailureCode, Plan
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.tables import ExecutionSnapshotRow, PlanRow, TaskRow


def canonical_payload(value: ExecutionSnapshot | Plan) -> tuple[dict[str, object], str]:
    payload = value.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return payload, hashlib.sha256(encoded).hexdigest()


def _verify_payload(
    value: ExecutionSnapshot | Plan, expected_hash: str, kind: str
) -> ExecutionSnapshot | Plan:
    _, actual_hash = canonical_payload(value)
    if actual_hash != expected_hash:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, f"{kind} hash mismatch")
    return value


async def persist_snapshot(
    session: AsyncSession, snapshot: ExecutionSnapshot
) -> ExecutionSnapshotRow:
    payload, digest = canonical_payload(snapshot)
    await session.execute(
        insert(ExecutionSnapshotRow)
        .values(
            id=snapshot.snapshot_id,
            workspace_id=snapshot.workspace_id,
            schema_version=snapshot.schema_version,
            content_hash=digest,
            payload=payload,
            created_at=snapshot.created_at,
        )
        .on_conflict_do_nothing()
    )
    row = await session.scalar(
        select(ExecutionSnapshotRow).where(
            ExecutionSnapshotRow.workspace_id == snapshot.workspace_id,
            ExecutionSnapshotRow.id == snapshot.snapshot_id,
        )
    )
    if row is None or row.content_hash != digest:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "immutable snapshot mismatch")
    return row


async def persist_plan(session: AsyncSession, plan: Plan) -> PlanRow:
    task = await session.scalar(
        select(TaskRow).where(
            TaskRow.workspace_id == plan.workspace_id,
            TaskRow.id == plan.task_id,
        )
    )
    if task is None:
        raise DomainError(FailureCode.POLICY_DENIED, "task unavailable")
    snapshot = await session.scalar(
        select(ExecutionSnapshotRow).where(
            ExecutionSnapshotRow.workspace_id == plan.workspace_id,
            ExecutionSnapshotRow.id == plan.execution_snapshot_id,
        )
    )
    if snapshot is None:
        raise DomainError(FailureCode.POLICY_DENIED, "execution snapshot unavailable")
    payload, digest = canonical_payload(plan)
    await session.execute(
        insert(PlanRow)
        .values(
            id=plan.plan_id,
            workspace_id=plan.workspace_id,
            task_id=plan.task_id,
            execution_snapshot_id=plan.execution_snapshot_id,
            schema_version=plan.schema_version,
            version=plan.version,
            content_hash=digest,
            payload=payload,
        )
        .on_conflict_do_nothing()
    )
    row = await session.scalar(
        select(PlanRow).where(
            PlanRow.workspace_id == plan.workspace_id,
            PlanRow.id == plan.plan_id,
        )
    )
    if row is None:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "plan version already exists")
    if row.content_hash != digest:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "immutable plan mismatch")
    return row


async def load_snapshot(
    session: AsyncSession, workspace_id: UUID, snapshot_id: UUID
) -> ExecutionSnapshot:
    row = await session.scalar(
        select(ExecutionSnapshotRow).where(
            ExecutionSnapshotRow.workspace_id == workspace_id,
            ExecutionSnapshotRow.id == snapshot_id,
        )
    )
    if row is None:
        raise DomainError(FailureCode.POLICY_DENIED, "execution snapshot unavailable")
    value = ExecutionSnapshot.model_validate(row.payload)
    verified = _verify_payload(value, row.content_hash, "execution snapshot")
    assert isinstance(verified, ExecutionSnapshot)
    return verified


async def load_plan_versions(
    session: AsyncSession, workspace_id: UUID, task_id: UUID
) -> tuple[Plan, ...]:
    rows = (
        await session.scalars(
            select(PlanRow)
            .where(PlanRow.workspace_id == workspace_id, PlanRow.task_id == task_id)
            .order_by(PlanRow.version)
        )
    ).all()
    values: list[Plan] = []
    for row in rows:
        value = Plan.model_validate(row.payload)
        verified = _verify_payload(value, row.content_hash, "plan")
        assert isinstance(verified, Plan)
        values.append(verified)
    return tuple(values)
