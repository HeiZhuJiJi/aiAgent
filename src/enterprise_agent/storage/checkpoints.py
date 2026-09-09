import hashlib
import json
from datetime import UTC, datetime
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import Checkpoint, FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.tables import (
    AttemptRow,
    CheckpointRow,
    EvidenceRow,
    OperationRow,
    PlanRow,
    StepRow,
    TaskBudgetRow,
    TaskRow,
)


def _canonical(value: Checkpoint) -> tuple[dict[str, object], str]:
    payload = cast(dict[str, object], value.model_dump(mode="json"))
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return payload, hashlib.sha256(encoded).hexdigest()


def _restore(row: CheckpointRow) -> Checkpoint:
    value = Checkpoint.model_validate(row.payload)
    _, digest = _canonical(value)
    if digest != row.content_hash:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "checkpoint hash mismatch")
    return value


async def create_checkpoint(
    session: AsyncSession,
    workspace_id: UUID,
    task_id: UUID,
    *,
    resume_token: str,
    context_summary: str,
    artifact_refs: tuple[UUID, ...] = (),
) -> Checkpoint:
    task = await session.scalar(
        select(TaskRow)
        .where(TaskRow.workspace_id == workspace_id, TaskRow.id == task_id)
        .with_for_update()
    )
    if task is None:
        raise DomainError(FailureCode.POLICY_DENIED, "task unavailable")
    if task.execution_snapshot_id is None:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "task has no execution snapshot")
    existing = await session.scalar(
        select(CheckpointRow).where(
            CheckpointRow.workspace_id == workspace_id,
            CheckpointRow.task_id == task_id,
            CheckpointRow.resume_token == resume_token,
        )
    )
    if existing is not None:
        restored = _restore(existing)
        if (
            restored.context_summary != context_summary
            or restored.artifact_refs != artifact_refs
        ):
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "checkpoint resume token reused with changed content",
            )
        return restored

    sequence = int(
        await session.scalar(
            select(func.coalesce(func.max(CheckpointRow.sequence), 0)).where(
                CheckpointRow.workspace_id == workspace_id,
                CheckpointRow.task_id == task_id,
            )
        )
        or 0
    ) + 1
    active_plan_id = await session.scalar(
        select(PlanRow.id)
        .where(PlanRow.workspace_id == workspace_id, PlanRow.task_id == task_id)
        .order_by(PlanRow.version.desc())
        .limit(1)
    )
    steps = (
        await session.scalars(
            select(StepRow).where(
                StepRow.workspace_id == workspace_id,
                StepRow.task_id == task_id,
            )
        )
    ).all()
    completed_steps = tuple(sorted((row.id for row in steps if row.state == "COMPLETED"), key=str))
    running = sorted((row.id for row in steps if row.state == "RUNNING"), key=str)
    if len(running) > 1:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "checkpoint boundary has multiple running steps",
        )
    running_step = running[0] if running else None
    attempt = None
    if running_step is not None:
        attempt = await session.scalar(
            select(AttemptRow.id)
            .where(
                AttemptRow.workspace_id == workspace_id,
                AttemptRow.step_id == running_step,
                AttemptRow.state == "RUNNING",
            )
            .order_by(AttemptRow.fencing_token.desc())
            .limit(1)
        )
    step_ids = tuple(row.id for row in steps)
    evidence_refs: tuple[UUID, ...] = ()
    operation_refs: tuple[UUID, ...] = ()
    if step_ids:
        evidence_refs = tuple(
            sorted(
                (
                    await session.scalars(
                        select(EvidenceRow.id).where(
                            EvidenceRow.workspace_id == workspace_id,
                            EvidenceRow.step_id.in_(step_ids),
                        )
                    )
                ).all(),
                key=str,
            )
        )
        operation_refs = tuple(
            sorted(
                (
                    await session.scalars(
                        select(OperationRow.id).where(
                            OperationRow.workspace_id == workspace_id,
                            OperationRow.step_id.in_(step_ids),
                        )
                    )
                ).all(),
                key=str,
            )
        )
    budget = await session.scalar(
        select(TaskBudgetRow).where(
            TaskBudgetRow.workspace_id == workspace_id,
            TaskBudgetRow.task_id == task_id,
        )
    )
    budget_usage: dict[str, JsonValue] = {}
    if budget is not None:
        budget_usage = {
            "steps": budget.used_steps,
            "replans": budget.used_replans,
            "llm_calls": budget.used_llm_calls,
            "capability_calls": budget.used_capability_calls,
            "tokens": budget.used_tokens,
            "cost": budget.used_cost,
            "state": budget.state,
            "version": budget.version,
        }
    created_at = datetime.now(UTC)
    checkpoint = Checkpoint(
        checkpoint_id=uuid5(
            NAMESPACE_URL,
            f"enterprise-checkpoint:{workspace_id}:{task_id}:{resume_token}",
        ),
        workspace_id=workspace_id,
        task_id=task_id,
        sequence=sequence,
        active_plan_id=active_plan_id,
        completed_steps=completed_steps,
        running_step=running_step,
        attempt=attempt,
        evidence_refs=evidence_refs,
        artifact_refs=artifact_refs,
        budget_usage=budget_usage,
        context_summary=context_summary,
        operation_refs=operation_refs,
        execution_snapshot_id=task.execution_snapshot_id,
        resume_token=resume_token,
        created_at=created_at,
    )
    payload, digest = _canonical(checkpoint)
    await session.execute(
        insert(CheckpointRow).values(
            id=checkpoint.checkpoint_id,
            workspace_id=workspace_id,
            task_id=task_id,
            schema_version=checkpoint.schema_version,
            sequence=sequence,
            resume_token=resume_token,
            content_hash=digest,
            payload=payload,
            created_at=created_at,
        )
    )
    return checkpoint


async def load_latest_checkpoint(
    session: AsyncSession, workspace_id: UUID, task_id: UUID
) -> Checkpoint | None:
    row = await session.scalar(
        select(CheckpointRow)
        .where(
            CheckpointRow.workspace_id == workspace_id,
            CheckpointRow.task_id == task_id,
        )
        .order_by(CheckpointRow.sequence.desc())
        .limit(1)
    )
    return None if row is None else _restore(row)
