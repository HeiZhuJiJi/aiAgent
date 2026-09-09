import hashlib
import json
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import (
    ApprovalRequest,
    ApprovalResult,
    AttemptState,
    FailureCode,
    StepState,
    TaskState,
)
from enterprise_agent.domain.states import DomainError, transition
from enterprise_agent.storage.operations import OperationIntent, operation_digest, require_owner
from enterprise_agent.storage.tables import (
    ApprovalRow,
    AuditRow,
    EvidenceRow,
    OperationRow,
    StepRow,
    TaskRow,
)


def request_digest(request: ApprovalRequest) -> tuple[dict[str, object], str]:
    payload = request.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return payload, hashlib.sha256(encoded).hexdigest()


async def request_approval(
    session: AsyncSession,
    request: ApprovalRequest,
    attempt_id: UUID,
    fencing_token: int,
) -> ApprovalRow:
    payload, digest = request_digest(request)
    existing = await session.scalar(
        select(ApprovalRow).where(
            ApprovalRow.workspace_id == request.workspace_id,
            ApprovalRow.id == request.approval_id,
        )
    )
    if existing is not None:
        if existing.request_hash != digest:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "immutable approval mismatch")
        return existing
    operation_step_id = await _operation_step(session, request)
    attempt = await require_owner(
        session, request.workspace_id, operation_step_id, attempt_id, fencing_token
    )
    operation = await session.scalar(
        select(OperationRow).where(
            OperationRow.workspace_id == request.workspace_id,
            OperationRow.id == request.operation_id,
        )
    )
    step = await session.scalar(
        select(StepRow)
        .where(StepRow.workspace_id == request.workspace_id, StepRow.id == operation_step_id)
        .with_for_update()
    )
    task = await session.scalar(
        select(TaskRow)
        .where(TaskRow.workspace_id == request.workspace_id, TaskRow.id == request.task_id)
        .with_for_update()
    )
    if operation is None or step is None or task is None or step.task_id != request.task_id:
        raise DomainError(FailureCode.POLICY_DENIED, "approval resource unavailable")
    if (
        operation.capability_id != request.capability_id
        or operation.capability_version != request.capability_version
        or operation.parameter_hash != request.parameter_hash
        or operation.parameter_hash
        != operation_digest(
            OperationIntent(
                workspace_id=request.workspace_id,
                step_id=operation.step_id,
                logical_key=operation.logical_key,
                capability_id=request.capability_id,
                capability_version=request.capability_version,
                arguments=request.parameters,
            )
        )
        or operation.state != "PREPARED"
    ):
        raise DomainError(FailureCode.VALIDATION_FAILED, "approval does not match operation")
    now = await session.scalar(select(func.clock_timestamp()))
    if now is None:
        raise DomainError(FailureCode.DB_UNAVAILABLE, "database clock unavailable")
    if request.expires_at <= now:
        raise DomainError(FailureCode.APPROVAL_REJECTED, "approval already expired")
    if request.evidence_ids:
        count = await session.scalar(
            select(func.count())
            .select_from(EvidenceRow)
            .where(
                EvidenceRow.workspace_id == request.workspace_id,
                EvidenceRow.id.in_(request.evidence_ids),
            )
        )
        if count != len(set(request.evidence_ids)):
            raise DomainError(FailureCode.POLICY_DENIED, "approval evidence unavailable")
    await session.execute(
        insert(ApprovalRow)
        .values(
            id=request.approval_id,
            workspace_id=request.workspace_id,
            task_id=request.task_id,
            step_id=step.id,
            operation_id=request.operation_id,
            request_hash=digest,
            request_payload=payload,
            expires_at=request.expires_at,
        )
        .on_conflict_do_nothing()
    )
    row = await session.scalar(
        select(ApprovalRow).where(
            ApprovalRow.workspace_id == request.workspace_id,
            ApprovalRow.id == request.approval_id,
        )
    )
    if row is None or row.request_hash != digest:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "immutable approval mismatch")
    if task.state != TaskState.RUNNING or step.state != StepState.RUNNING:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION, "approval requires running task and step"
        )
    task.state = transition(TaskState(task.state), TaskState.WAITING_HITL).value
    task.version += 1
    step.state = transition(StepState(step.state), StepState.WAITING_HITL).value
    step.version += 1
    step.lease_expires_at = None
    attempt.state = transition(AttemptState(attempt.state), AttemptState.CANCELLED).value
    attempt.completed_at = now
    session.add(
        AuditRow(
            workspace_id=request.workspace_id,
            actor_type="AGENT",
            actor_id=attempt_id,
            task_id=request.task_id,
            step_id=step.id,
            resource_type="APPROVAL_REQUEST",
            resource_id=request.approval_id,
            capability_id=request.capability_id,
            policy_decision="REQUIRE_APPROVAL",
            approval_id=request.approval_id,
            action="approval.requested",
            result={"state": "PENDING"},
            trace_id=attempt.trace_id,
        )
    )
    await session.flush()
    return row


async def _operation_step(session: AsyncSession, request: ApprovalRequest) -> UUID:
    step_id = await session.scalar(
        select(OperationRow.step_id).where(
            OperationRow.workspace_id == request.workspace_id,
            OperationRow.id == request.operation_id,
        )
    )
    if step_id is None:
        raise DomainError(FailureCode.POLICY_DENIED, "operation unavailable")
    return step_id


async def decide_approval(
    session: AsyncSession,
    workspace_id: UUID,
    result: ApprovalResult,
    expected_version: int,
) -> ApprovalRow:
    row = await session.scalar(
        select(ApprovalRow)
        .where(ApprovalRow.workspace_id == workspace_id, ApprovalRow.id == result.approval_id)
        .with_for_update()
    )
    if row is None:
        raise DomainError(FailureCode.POLICY_DENIED, "approval unavailable")
    if row.state != "PENDING" or row.version != expected_version:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "approval state/version mismatch")
    now = await session.scalar(select(func.clock_timestamp()))
    if now is None:
        raise DomainError(FailureCode.DB_UNAVAILABLE, "database clock unavailable")
    task = await session.scalar(
        select(TaskRow)
        .where(TaskRow.workspace_id == workspace_id, TaskRow.id == row.task_id)
        .with_for_update()
    )
    step = await session.scalar(
        select(StepRow)
        .where(StepRow.workspace_id == workspace_id, StepRow.id == row.step_id)
        .with_for_update()
    )
    if task is None or step is None:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "approval task or step missing")
    request = ApprovalRequest.model_validate(row.request_payload)
    if row.expires_at <= now:
        row.state = "EXPIRED"
        row.decided_at = now
        row.version += 1
        task.state = transition(TaskState(task.state), TaskState.FAILED).value
        step.state = transition(StepState(step.state), StepState.FAILED).value
        task.version += 1
        step.version += 1
        session.add(
            AuditRow(
                workspace_id=workspace_id,
                actor_type="USER",
                actor_id=result.actor_id,
                task_id=row.task_id,
                step_id=row.step_id,
                resource_type="APPROVAL_REQUEST",
                resource_id=row.id,
                capability_id=request.capability_id,
                policy_decision="REQUIRE_APPROVAL",
                approval_id=row.id,
                action="approval.expired",
                result={"state": "EXPIRED"},
                trace_id="approval:" + str(row.id),
            )
        )
        await session.flush()
        return row
    row.state = result.decision
    row.decision = result.decision
    row.actor_id = result.actor_id
    row.decided_at = result.decided_at
    row.version += 1
    if result.decision == "APPROVED":
        task.state = transition(TaskState(task.state), TaskState.RUNNING).value
        step.state = transition(StepState(step.state), StepState.READY).value
    else:
        task.state = transition(TaskState(task.state), TaskState.FAILED).value
        step.state = transition(StepState(step.state), StepState.FAILED).value
    task.version += 1
    step.version += 1
    session.add(
        AuditRow(
            workspace_id=workspace_id,
            actor_type="USER",
            actor_id=result.actor_id,
            task_id=row.task_id,
            step_id=row.step_id,
            resource_type="APPROVAL_REQUEST",
            resource_id=row.id,
            capability_id=request.capability_id,
            policy_decision="REQUIRE_APPROVAL",
            approval_id=row.id,
            action="approval." + result.decision.lower(),
            result={"state": result.decision},
            trace_id="approval:" + str(row.id),
        )
    )
    await session.flush()
    return row


async def require_approved_operation(
    session: AsyncSession, workspace_id: UUID, operation_id: UUID, approval_id: UUID
) -> ApprovalRow:
    row = await session.scalar(
        select(ApprovalRow).where(
            ApprovalRow.workspace_id == workspace_id,
            ApprovalRow.id == approval_id,
            ApprovalRow.operation_id == operation_id,
        )
    )
    if row is None or row.state != "APPROVED":
        raise DomainError(FailureCode.APPROVAL_REJECTED, "approved operation binding required")
    operation = await session.scalar(
        select(OperationRow).where(
            OperationRow.workspace_id == workspace_id, OperationRow.id == operation_id
        )
    )
    request = ApprovalRequest.model_validate(row.request_payload)
    if (
        operation is None
        or request.parameter_hash != operation.parameter_hash
        or request.capability_id != operation.capability_id
        or request.capability_version != operation.capability_version
    ):
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "approval operation binding changed")
    return row
