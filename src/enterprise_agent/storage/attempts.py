from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import (
    AttemptState,
    Evidence,
    Failure,
    FailureCode,
    PlanStepSpec,
    StepAttempt,
    StepState,
    TaskState,
)
from enterprise_agent.domain.states import DomainError, transition
from enterprise_agent.runtime.loop_guard import check_evidence_progress
from enterprise_agent.storage.runtime_locks import lock_task_runtime
from enterprise_agent.storage.steps import advance_plan_after_step
from enterprise_agent.storage.tables import AttemptRow, AuditRow, EvidenceRow, StepRow


async def list_task_attempts(
    session: AsyncSession, workspace_id: UUID, task_id: UUID
) -> tuple[StepAttempt, ...]:
    rows = (
        await session.scalars(
            select(AttemptRow)
            .join(
                StepRow,
                (StepRow.workspace_id == AttemptRow.workspace_id)
                & (StepRow.id == AttemptRow.step_id),
            )
            .where(
                AttemptRow.workspace_id == workspace_id,
                StepRow.task_id == task_id,
            )
            .order_by(AttemptRow.started_at, AttemptRow.id)
        )
    ).all()
    return tuple(
        StepAttempt(
            attempt_id=row.id,
            workspace_id=row.workspace_id,
            step_id=row.step_id,
            worker_id=row.worker_id,
            state=AttemptState(row.state),
            fencing_token=row.fencing_token,
            lease_expires_at=row.lease_expires_at,
            started_at=row.started_at,
            completed_at=row.completed_at,
            failure=(
                None
                if row.failure_payload is None
                else Failure.model_validate(row.failure_payload)
            ),
            trace_id=row.trace_id,
        )
        for row in rows
    )


async def list_task_evidence(
    session: AsyncSession, workspace_id: UUID, task_id: UUID
) -> tuple[Evidence, ...]:
    rows = (
        await session.scalars(
            select(EvidenceRow)
            .join(
                StepRow,
                (StepRow.workspace_id == EvidenceRow.workspace_id)
                & (StepRow.id == EvidenceRow.step_id),
            )
            .where(
                EvidenceRow.workspace_id == workspace_id,
                StepRow.task_id == task_id,
            )
            .order_by(EvidenceRow.id)
        )
    ).all()
    return tuple(Evidence.model_validate(row.payload) for row in rows)


async def acquire_attempt(
    session: AsyncSession,
    workspace_id: UUID,
    step_id: UUID,
    attempt_id: UUID,
    worker_id: str,
    trace_id: str,
    lease_seconds: int = 60,
) -> AttemptRow:
    if lease_seconds < 1:
        raise DomainError(FailureCode.VALIDATION_FAILED, "lease must be positive")
    task_id = await session.scalar(
        select(StepRow.task_id).where(
            StepRow.workspace_id == workspace_id, StepRow.id == step_id
        )
    )
    if task_id is None:
        raise DomainError(FailureCode.POLICY_DENIED, "step unavailable")
    task = await lock_task_runtime(session, workspace_id, task_id)
    step = await session.scalar(
        select(StepRow)
        .where(StepRow.workspace_id == workspace_id, StepRow.id == step_id)
        .with_for_update()
    )
    if step is None:
        raise DomainError(FailureCode.POLICY_DENIED, "step unavailable")
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    existing = await session.scalar(
        select(AttemptRow).where(
            AttemptRow.workspace_id == workspace_id, AttemptRow.id == attempt_id
        )
    )
    if existing is not None:
        if existing.step_id != step_id or existing.worker_id != worker_id:
            raise DomainError(FailureCode.VALIDATION_FAILED, "attempt command mismatch")
        if (
            existing.state != AttemptState.RUNNING
            or existing.fencing_token != step.fencing_token
            or existing.lease_expires_at <= now
        ):
            raise DomainError(FailureCode.STALE_ATTEMPT, "attempt no longer owns execution")
        return existing
    if step.state == StepState.RETRY_WAIT:
        if step.retry_available_at is None or step.retry_available_at > now:
            raise DomainError(FailureCode.VALIDATION_FAILED, "step retry is not due")
        step.state = transition(StepState.RETRY_WAIT, StepState.READY).value
        step.retry_available_at = None
        step.version += 1
    if step.task_id != task.id:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "step task binding changed")
    task_state = TaskState(task.state)
    if task_state is TaskState.READY:
        task.state = transition(TaskState.READY, TaskState.RUNNING).value
        task.version += 1
        session.add(
            AuditRow(
                workspace_id=workspace_id,
                actor_type="AGENT",
                actor_id=attempt_id,
                action="task.started",
                task_id=task.id,
                resource_type="TASK_RUN",
                resource_id=task.id,
                result={"state": TaskState.RUNNING.value},
                trace_id=trace_id,
            )
        )
    elif task_state is not TaskState.RUNNING:
        raise DomainError(FailureCode.VALIDATION_FAILED, "task is not executable")
    if step.state == StepState.RUNNING:
        if step.lease_expires_at is not None and step.lease_expires_at > now:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "active lease")
        prior = await session.scalar(
            select(AttemptRow).where(
                AttemptRow.workspace_id == workspace_id,
                AttemptRow.step_id == step_id,
                AttemptRow.fencing_token == step.fencing_token,
            )
        )
        if prior is not None and prior.state == AttemptState.RUNNING:
            prior.state = transition(AttemptState.RUNNING, AttemptState.UNKNOWN)
    else:
        step.state = transition(StepState(step.state), StepState.RUNNING)
    step.fencing_token += 1
    step.version += 1
    step.lease_expires_at = now + timedelta(seconds=lease_seconds)
    attempt = AttemptRow(
        id=attempt_id,
        workspace_id=workspace_id,
        step_id=step_id,
        worker_id=worker_id,
        trace_id=trace_id,
        fencing_token=step.fencing_token,
        state=AttemptState.RUNNING,
        lease_expires_at=step.lease_expires_at,
    )
    session.add(attempt)
    await session.flush()
    return attempt

async def renew_attempt_lease(
    session: AsyncSession,
    workspace_id: UUID,
    attempt_id: UUID,
    token: int,
    *,
    lease_seconds: int = 60,
) -> AttemptRow:
    if lease_seconds < 1:
        raise DomainError(FailureCode.VALIDATION_FAILED, "lease must be positive")
    attempt = await session.scalar(
        select(AttemptRow).where(
            AttemptRow.workspace_id == workspace_id,
            AttemptRow.id == attempt_id,
        )
    )
    if attempt is None:
        raise DomainError(FailureCode.STALE_ATTEMPT, "attempt unavailable")
    task_id = await session.scalar(
        select(StepRow.task_id).where(
            StepRow.workspace_id == workspace_id,
            StepRow.id == attempt.step_id,
        )
    )
    if task_id is None:
        raise DomainError(FailureCode.STALE_ATTEMPT, "attempt step unavailable")
    await lock_task_runtime(session, workspace_id, task_id)
    step = await session.scalar(
        select(StepRow)
        .where(
            StepRow.workspace_id == workspace_id,
            StepRow.id == attempt.step_id,
        )
        .with_for_update()
    )
    now = await session.scalar(select(func.clock_timestamp()))
    if (
        step is None
        or now is None
        or attempt.state != AttemptState.RUNNING
        or StepState(step.state) is not StepState.RUNNING
        or token != step.fencing_token
        or token != attempt.fencing_token
        or step.lease_expires_at is None
        or step.lease_expires_at <= now
        or attempt.lease_expires_at <= now
    ):
        raise DomainError(
            FailureCode.STALE_ATTEMPT, "attempt lease renewal rejected"
        )
    renewed_until = now + timedelta(seconds=lease_seconds)
    step.lease_expires_at = renewed_until
    attempt.lease_expires_at = renewed_until
    await session.flush()
    return attempt




async def fail_attempt(
    session: AsyncSession,
    workspace_id: UUID,
    attempt_id: UUID,
    token: int,
    failure: Failure,
    *,
    retryable: bool,
    retry_delay_seconds: float,
) -> StepState:
    if retry_delay_seconds < 0:
        raise DomainError(FailureCode.VALIDATION_FAILED, "retry delay cannot be negative")
    attempt = await session.scalar(
        select(AttemptRow).where(
            AttemptRow.workspace_id == workspace_id,
            AttemptRow.id == attempt_id,
        )
    )
    if attempt is None:
        raise DomainError(FailureCode.STALE_ATTEMPT, "attempt unavailable")
    task_id = await session.scalar(
        select(StepRow.task_id).where(
            StepRow.workspace_id == workspace_id, StepRow.id == attempt.step_id
        )
    )
    if task_id is None:
        raise DomainError(FailureCode.STALE_ATTEMPT, "attempt step unavailable")
    await lock_task_runtime(session, workspace_id, task_id)
    step = await session.scalar(
        select(StepRow)
        .where(StepRow.workspace_id == workspace_id, StepRow.id == attempt.step_id)
        .with_for_update()
    )
    now = await session.scalar(select(func.clock_timestamp()))
    if (
        step is None
        or now is None
        or attempt.state != AttemptState.RUNNING
        or StepState(step.state) is not StepState.RUNNING
        or token != step.fencing_token
        or token != attempt.fencing_token
        or step.lease_expires_at is None
        or step.lease_expires_at <= now
    ):
        raise DomainError(FailureCode.STALE_ATTEMPT, "failure commit fencing rejected")
    max_attempts = 1
    if step.spec_payload is not None:
        max_attempts = PlanStepSpec.model_validate(step.spec_payload).max_attempts
    attempt_count = int(
        await session.scalar(
            select(func.count())
            .select_from(AttemptRow)
            .where(
                AttemptRow.workspace_id == workspace_id,
                AttemptRow.step_id == step.id,
            )
        )
        or 0
    )
    attempt.state = transition(AttemptState.RUNNING, AttemptState.FAILED).value
    attempt.completed_at = now
    attempt.failure_payload = failure.model_dump(mode="json")
    if retryable and attempt_count < max_attempts:
        step.state = transition(StepState.RUNNING, StepState.RETRY_WAIT).value
        step.retry_available_at = now + timedelta(seconds=retry_delay_seconds)
    else:
        step.state = transition(StepState.RUNNING, StepState.FAILED).value
        step.retry_available_at = None
    step.lease_expires_at = None
    step.version += 1
    session.add(
        AuditRow(
            workspace_id=workspace_id,
            actor_type="AGENT",
            actor_id=attempt_id,
            action="attempt.failed",
            task_id=step.task_id,
            step_id=step.id,
            resource_type="TASK_STEP",
            resource_id=step.id,
            result={
                "state": step.state,
                "failure_code": failure.code.value,
                "attempt_count": attempt_count,
                "max_attempts": max_attempts,
            },
            trace_id=attempt.trace_id,
        )
    )
    await session.flush()
    return StepState(step.state)


async def commit_evidence(
    session: AsyncSession,
    workspace_id: UUID,
    attempt_id: UUID,
    token: int,
    evidence: tuple[Evidence, ...],
) -> bool:
    attempt = await session.scalar(
        select(AttemptRow).where(
            AttemptRow.workspace_id == workspace_id, AttemptRow.id == attempt_id
        )
    )
    if attempt is None:
        raise DomainError(FailureCode.STALE_ATTEMPT, "attempt unavailable")
    task_id = await session.scalar(
        select(StepRow.task_id).where(
            StepRow.workspace_id == workspace_id, StepRow.id == attempt.step_id
        )
    )
    if task_id is None:
        raise DomainError(FailureCode.STALE_ATTEMPT, "attempt step unavailable")
    await lock_task_runtime(session, workspace_id, task_id)
    step = await session.scalar(
        select(StepRow)
        .where(StepRow.workspace_id == workspace_id, StepRow.id == attempt.step_id)
        .with_for_update()
    )
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    if step is None or token != step.fencing_token or token != attempt.fencing_token:
        raise DomainError(FailureCode.STALE_ATTEMPT, "fencing token rejected")
    if attempt.state == AttemptState.SUCCEEDED:
        stored = (
            await session.scalars(
                select(EvidenceRow).where(
                    EvidenceRow.workspace_id == workspace_id, EvidenceRow.attempt_id == attempt_id
                )
            )
        ).all()
        expected = {e.evidence_id: e.model_dump(mode="json") for e in evidence}
        if {e.id: e.payload for e in stored} != expected:
            raise DomainError(FailureCode.VALIDATION_FAILED, "replayed result differs")
        return True
    if (
        step.lease_expires_at is None
        or step.lease_expires_at <= now
        or attempt.state != AttemptState.RUNNING
    ):
        raise DomainError(FailureCode.STALE_ATTEMPT, "lease expired or attempt terminal")
    if len({e.evidence_id for e in evidence}) != len(evidence):
        raise DomainError(FailureCode.VALIDATION_FAILED, "duplicate evidence")
    progress = await check_evidence_progress(
        session,
        workspace_id,
        step.task_id,
        step.id,
        attempt_id,
        attempt_id,
        attempt.trace_id,
        len(evidence),
    )
    if not progress.allowed:
        return False
    for item in evidence:
        if (
            item.workspace_id != workspace_id
            or item.step_id != step.id
            or item.task_id != step.task_id
            or item.generated_by != attempt_id
        ):
            raise DomainError(FailureCode.POLICY_DENIED, "evidence ownership mismatch")
        session.add(
            EvidenceRow(
                id=item.evidence_id,
                workspace_id=workspace_id,
                step_id=step.id,
                attempt_id=attempt_id,
                payload=item.model_dump(mode="json"),
            )
        )
    attempt.state = transition(AttemptState.RUNNING, AttemptState.SUCCEEDED)
    attempt.completed_at = now
    step.state = transition(StepState(step.state), StepState.COMPLETED)
    step.version += 1
    session.add(
        AuditRow(
            workspace_id=workspace_id,
            actor_type="AGENT",
            actor_id=attempt_id,
            action="attempt.commit",
            task_id=step.task_id,
            step_id=step.id,
            resource_type="TASK_STEP",
            resource_id=step.id,
            result={"state": StepState.COMPLETED.value},
            trace_id=attempt.trace_id,
        )
    )
    if step.plan_id is not None:
        await advance_plan_after_step(
            session,
            workspace_id,
            step.task_id,
            step.plan_id,
            attempt_id,
            attempt.trace_id,
        )
    await session.flush()
    return True
