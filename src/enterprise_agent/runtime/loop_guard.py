import hashlib
import json
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import (
    AttemptState,
    Contract,
    FailureCode,
    StepState,
    TaskState,
)
from enterprise_agent.domain.states import DomainError, transition
from enterprise_agent.storage.tables import (
    AttemptRow,
    AuditRow,
    RuntimeGuardRow,
    StepRow,
    TaskRow,
)


class LoopDecision(Contract):
    allowed: bool
    reason: str | None = None
    consecutive_calls: int


def capability_fingerprint(
    capability_id: str,
    capability_version: str,
    arguments: dict[str, JsonValue],
) -> str:
    encoded = json.dumps(
        {
            "capability_id": capability_id,
            "capability_version": capability_version,
            "arguments": arguments,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


async def check_capability_loop(
    session: AsyncSession,
    workspace_id: UUID,
    task_id: UUID,
    step_id: UUID,
    attempt_id: UUID,
    actor_id: UUID,
    trace_id: str,
    fingerprint: str,
    *,
    max_consecutive: int = 3,
) -> LoopDecision:
    guard = await session.scalar(
        select(RuntimeGuardRow)
        .where(
            RuntimeGuardRow.workspace_id == workspace_id,
            RuntimeGuardRow.task_id == task_id,
        )
        .with_for_update()
    )
    if guard is None:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "runtime loop guard unavailable")
    if guard.state == "ABORTED":
        return LoopDecision(
            allowed=False,
            reason=guard.abort_reason or "loop guard previously aborted task",
            consecutive_calls=guard.consecutive_capability_calls,
        )
    if guard.last_capability_fingerprint == fingerprint:
        guard.consecutive_capability_calls += 1
    else:
        guard.last_capability_fingerprint = fingerprint
        guard.consecutive_capability_calls = 1
    guard.version += 1
    if guard.consecutive_capability_calls <= max_consecutive:
        await session.flush()
        return LoopDecision(
            allowed=True,
            consecutive_calls=guard.consecutive_capability_calls,
        )

    await _abort_execution(
        session,
        workspace_id,
        task_id,
        step_id,
        attempt_id,
        actor_id,
        trace_id,
        guard,
        "repeated capability and arguments",
        "runtime.loop_aborted.capability",
    )
    return LoopDecision(
        allowed=False,
        reason=guard.abort_reason,
        consecutive_calls=guard.consecutive_capability_calls,
    )


async def check_evidence_progress(
    session: AsyncSession,
    workspace_id: UUID,
    task_id: UUID,
    step_id: UUID,
    attempt_id: UUID,
    actor_id: UUID,
    trace_id: str,
    new_evidence_count: int,
    *,
    max_consecutive_without_evidence: int = 2,
) -> LoopDecision:
    guard = await session.scalar(
        select(RuntimeGuardRow)
        .where(
            RuntimeGuardRow.workspace_id == workspace_id,
            RuntimeGuardRow.task_id == task_id,
        )
        .with_for_update()
    )
    if guard is None:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "runtime loop guard unavailable")
    if new_evidence_count > 0:
        guard.last_evidence_count += new_evidence_count
        guard.consecutive_no_evidence_steps = 0
        guard.consecutive_replans = 0
    else:
        guard.consecutive_no_evidence_steps += 1
    guard.version += 1
    if (
        guard.state == "ACTIVE"
        and guard.consecutive_no_evidence_steps <= max_consecutive_without_evidence
    ):
        await session.flush()
        return LoopDecision(
            allowed=True,
            consecutive_calls=guard.consecutive_no_evidence_steps,
        )
    if guard.state == "ACTIVE":
        await _abort_execution(
            session,
            workspace_id,
            task_id,
            step_id,
            attempt_id,
            actor_id,
            trace_id,
            guard,
            "steps produced no new evidence",
            "runtime.loop_aborted.no_evidence",
        )
    return LoopDecision(
        allowed=False,
        reason=guard.abort_reason,
        consecutive_calls=guard.consecutive_no_evidence_steps,
    )


async def check_replan_loop(
    session: AsyncSession,
    workspace_id: UUID,
    task_id: UUID,
    actor_id: UUID,
    trace_id: str,
    *,
    max_consecutive_replans: int = 3,
) -> LoopDecision:
    guard = await session.scalar(
        select(RuntimeGuardRow)
        .where(
            RuntimeGuardRow.workspace_id == workspace_id,
            RuntimeGuardRow.task_id == task_id,
        )
        .with_for_update()
    )
    task = await session.scalar(
        select(TaskRow)
        .where(TaskRow.workspace_id == workspace_id, TaskRow.id == task_id)
        .with_for_update()
    )
    if guard is None or task is None:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "replan guard unavailable")
    guard.consecutive_replans += 1
    guard.version += 1
    if guard.state == "ACTIVE" and guard.consecutive_replans <= max_consecutive_replans:
        await session.flush()
        return LoopDecision(allowed=True, consecutive_calls=guard.consecutive_replans)
    if guard.state == "ACTIVE":
        task.state = transition(TaskState(task.state), TaskState.FAILED).value
        task.version += 1
        guard.state = "ABORTED"
        guard.abort_reason = "consecutive replans"
        session.add(
            AuditRow(
                workspace_id=workspace_id,
                actor_type="AGENT",
                actor_id=actor_id,
                task_id=task_id,
                resource_type="RUNTIME_GUARD",
                resource_id=task_id,
                action="runtime.loop_aborted.replan",
                result={"state": "ABORTED", "reason": "consecutive replans"},
                trace_id=trace_id,
            )
        )
        await session.flush()
    return LoopDecision(
        allowed=False,
        reason=guard.abort_reason,
        consecutive_calls=guard.consecutive_replans,
    )


async def _abort_execution(
    session: AsyncSession,
    workspace_id: UUID,
    task_id: UUID,
    step_id: UUID,
    attempt_id: UUID,
    actor_id: UUID,
    trace_id: str,
    guard: RuntimeGuardRow,
    reason: str,
    action: str,
) -> None:
    task = await session.scalar(
        select(TaskRow)
        .where(TaskRow.workspace_id == workspace_id, TaskRow.id == task_id)
        .with_for_update()
    )
    step = await session.scalar(
        select(StepRow)
        .where(StepRow.workspace_id == workspace_id, StepRow.id == step_id)
        .with_for_update()
    )
    attempt = await session.scalar(
        select(AttemptRow)
        .where(
            AttemptRow.workspace_id == workspace_id,
            AttemptRow.id == attempt_id,
        )
        .with_for_update()
    )
    if task is not None and TaskState(task.state) not in {
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
    }:
        task.state = transition(TaskState(task.state), TaskState.FAILED).value
        task.version += 1
    if step is not None and StepState(step.state) == StepState.RUNNING:
        step.state = transition(StepState.RUNNING, StepState.FAILED).value
        step.version += 1
        step.lease_expires_at = None
    if attempt is not None and AttemptState(attempt.state) == AttemptState.RUNNING:
        attempt.state = transition(AttemptState.RUNNING, AttemptState.FAILED).value
    guard.state = "ABORTED"
    guard.abort_reason = reason
    session.add(
        AuditRow(
            workspace_id=workspace_id,
            actor_type="AGENT",
            actor_id=actor_id,
            task_id=task_id,
            step_id=step_id,
            resource_type="RUNTIME_GUARD",
            resource_id=task_id,
            action=action,
            result={"state": "ABORTED", "reason": reason},
            trace_id=trace_id,
        )
    )
    await session.flush()
