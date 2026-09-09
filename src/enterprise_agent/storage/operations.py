import hashlib
import json
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, JsonValue
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import Contract, FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.observability.metrics import (
    RECONCILIATION_TOTAL,
    STALE_ATTEMPT_REJECTED_TOTAL,
    UNKNOWN_OPERATION_TOTAL,
)
from enterprise_agent.storage.tables import (
    AttemptRow,
    AuditRow,
    CapabilityCallRow,
    OperationRow,
    StepRow,
)


class OperationAction(StrEnum):
    EXECUTE = "EXECUTE"
    RECONCILE = "RECONCILE"
    RETURN_RESULT = "RETURN_RESULT"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"


class OperationIntent(Contract):
    workspace_id: UUID
    step_id: UUID
    logical_key: str = Field(min_length=1, max_length=200)
    capability_id: str
    capability_version: str
    arguments: dict[str, JsonValue]


async def _task_for_step(session: AsyncSession, workspace_id: UUID, step_id: UUID) -> UUID:
    task_id = await session.scalar(
        select(StepRow.task_id).where(
            StepRow.workspace_id == workspace_id,
            StepRow.id == step_id,
        )
    )
    if task_id is None:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "operation step task missing")
    return task_id


class OperationDecision(Contract):
    operation_id: UUID
    action: OperationAction
    version: int
    result: dict[str, JsonValue] | None = None


def operation_digest(intent: OperationIntent) -> str:
    return hashlib.sha256(
        json.dumps(
            intent.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


async def require_owner(
    session: AsyncSession, workspace_id: UUID, step_id: UUID, attempt_id: UUID, token: int
) -> AttemptRow:
    step = await session.scalar(
        select(StepRow)
        .where(StepRow.workspace_id == workspace_id, StepRow.id == step_id)
        .with_for_update()
    )
    attempt = await session.scalar(
        select(AttemptRow).where(
            AttemptRow.workspace_id == workspace_id, AttemptRow.id == attempt_id
        )
    )
    now = await session.scalar(select(func.clock_timestamp()))
    if now is None:
        raise DomainError(FailureCode.DB_UNAVAILABLE, "database clock unavailable")
    if (
        step is None
        or attempt is None
        or attempt.step_id != step_id
        or token != step.fencing_token
        or token != attempt.fencing_token
        or step.state != "RUNNING"
        or attempt.state != "RUNNING"
        or step.lease_expires_at is None
        or step.lease_expires_at <= now
    ):
        STALE_ATTEMPT_REJECTED_TOTAL.inc()
        raise DomainError(FailureCode.STALE_ATTEMPT, "operation owner rejected")
    return attempt


def decision(row: OperationRow) -> OperationDecision:
    action = {
        "PREPARED": OperationAction.EXECUTE,
        "DISPATCHED": OperationAction.RECONCILE,
        "UNKNOWN": OperationAction.RECONCILE,
        "SUCCEEDED": OperationAction.RETURN_RESULT,
        "FAILED": OperationAction.TERMINAL_FAILURE,
    }[row.state]
    return OperationDecision.model_validate(
        {"operation_id": row.id, "action": action, "version": row.version, "result": row.result}
    )


async def prepare_operation(
    session: AsyncSession, intent: OperationIntent, attempt_id: UUID, token: int
) -> OperationDecision:
    await require_owner(session, intent.workspace_id, intent.step_id, attempt_id, token)
    operation_id = uuid5(
        NAMESPACE_URL,
        f"enterprise-operation:{intent.workspace_id}:{intent.step_id}:{intent.logical_key}",
    )
    digest = operation_digest(intent)
    await session.execute(
        insert(OperationRow)
        .values(
            id=operation_id,
            workspace_id=intent.workspace_id,
            step_id=intent.step_id,
            logical_key=intent.logical_key,
            capability_id=intent.capability_id,
            capability_version=intent.capability_version,
            parameter_hash=digest,
            state="PREPARED",
            version=1,
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    row = await session.scalar(
        select(OperationRow)
        .where(OperationRow.workspace_id == intent.workspace_id, OperationRow.id == operation_id)
        .with_for_update()
    )
    if row is None:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "operation insert disappeared")
    if row.parameter_hash != digest:
        raise DomainError(FailureCode.VALIDATION_FAILED, "stable operation parameters changed")
    return decision(row)


async def locked_operation(
    session: AsyncSession, workspace_id: UUID, operation_id: UUID, attempt_id: UUID, token: int
) -> tuple[OperationRow, AttemptRow]:
    step_id = await session.scalar(
        select(OperationRow.step_id).where(
            OperationRow.workspace_id == workspace_id, OperationRow.id == operation_id
        )
    )
    if step_id is None:
        raise DomainError(FailureCode.POLICY_DENIED, "operation unavailable")
    attempt = await require_owner(session, workspace_id, step_id, attempt_id, token)
    row = await session.scalar(
        select(OperationRow)
        .where(OperationRow.workspace_id == workspace_id, OperationRow.id == operation_id)
        .with_for_update()
    )
    if row is None or row.step_id != step_id:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "operation changed while locking")
    return row, attempt


async def mark_dispatched(
    session: AsyncSession,
    workspace_id: UUID,
    operation_id: UUID,
    attempt_id: UUID,
    token: int,
    expected_version: int,
    dispatch_id: UUID,
    approval_id: UUID,
) -> OperationDecision:
    from enterprise_agent.storage.approvals import require_approved_operation

    row, attempt = await locked_operation(session, workspace_id, operation_id, attempt_id, token)
    await require_approved_operation(session, workspace_id, operation_id, approval_id)
    return await _commit_dispatch(
        session, row, attempt, expected_version, dispatch_id, token
    )


async def mark_dispatched_by_policy(
    session: AsyncSession,
    workspace_id: UUID,
    operation_id: UUID,
    attempt_id: UUID,
    token: int,
    expected_version: int,
    dispatch_id: UUID,
    capability_call_id: UUID,
) -> OperationDecision:
    row, attempt = await locked_operation(session, workspace_id, operation_id, attempt_id, token)
    call = await session.scalar(
        select(CapabilityCallRow).where(
            CapabilityCallRow.workspace_id == workspace_id,
            CapabilityCallRow.id == capability_call_id,
            CapabilityCallRow.operation_id == operation_id,
            CapabilityCallRow.attempt_id == attempt_id,
            CapabilityCallRow.policy_decision == "ALLOW",
            CapabilityCallRow.state == "PENDING",
        )
    )
    if (
        call is None
        or call.capability_id != row.capability_id
        or call.capability_version != row.capability_version
    ):
        raise DomainError(
            FailureCode.POLICY_DENIED,
            "persisted policy authorization required for side effect dispatch",
        )
    return await _commit_dispatch(
        session, row, attempt, expected_version, dispatch_id, token
    )


async def _commit_dispatch(
    session: AsyncSession,
    row: OperationRow,
    attempt: AttemptRow,
    expected_version: int,
    dispatch_id: UUID,
    token: int,
) -> OperationDecision:
    if row.state != "PREPARED" or row.version != expected_version:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION, "dispatch requires prepared current operation"
        )
    row.state, row.dispatch_id, row.dispatch_fencing_token = "DISPATCHED", dispatch_id, token
    row.version += 1
    session.add(
        AuditRow(
            workspace_id=row.workspace_id,
            actor_type="AGENT",
            actor_id=attempt.id,
            task_id=await _task_for_step(session, row.workspace_id, row.step_id),
            step_id=row.step_id,
            resource_type="OPERATION",
            resource_id=row.id,
            capability_id=row.capability_id,
            action="operation.dispatch",
            result={"state": "DISPATCHED"},
            trace_id=attempt.trace_id,
        )
    )
    await session.flush()
    return decision(row)


async def record_outcome(
    session: AsyncSession,
    workspace_id: UUID,
    operation_id: UUID,
    attempt_id: UUID,
    token: int,
    dispatch_id: UUID,
    outcome: str,
    result: dict[str, JsonValue] | None,
) -> OperationDecision:
    if outcome not in {"SUCCEEDED", "FAILED", "UNKNOWN"}:
        raise DomainError(FailureCode.VALIDATION_FAILED, "invalid operation outcome")
    row, attempt = await locked_operation(session, workspace_id, operation_id, attempt_id, token)
    if row.dispatch_id != dispatch_id or row.dispatch_fencing_token != token:
        raise DomainError(FailureCode.STALE_ATTEMPT, "dispatch fenced")
    if row.state == outcome and row.result == result:
        return decision(row)
    if row.state != "DISPATCHED":
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION, "outcome requires dispatched operation"
        )
    if outcome == "SUCCEEDED" and result is None:
        raise DomainError(FailureCode.VALIDATION_FAILED, "success requires result")
    row.state = outcome
    row.result = None if result is None else dict(result)
    row.version += 1
    session.add(
        AuditRow(
            workspace_id=workspace_id,
            actor_type="AGENT",
            actor_id=attempt_id,
            task_id=await _task_for_step(session, workspace_id, row.step_id),
            step_id=row.step_id,
            resource_type="OPERATION",
            resource_id=operation_id,
            capability_id=row.capability_id,
            action="operation." + outcome.lower(),
            result={"state": outcome},
            trace_id=attempt.trace_id,
        )
    )
    await session.flush()
    if outcome == "UNKNOWN":
        UNKNOWN_OPERATION_TOTAL.inc()
    return decision(row)


async def reconcile_operation(
    session: AsyncSession,
    workspace_id: UUID,
    operation_id: UUID,
    attempt_id: UUID,
    token: int,
    expected_version: int,
    conclusion: str,
    result: dict[str, JsonValue] | None = None,
) -> OperationDecision:
    row, attempt = await locked_operation(session, workspace_id, operation_id, attempt_id, token)
    if row.version != expected_version or row.state not in {"UNKNOWN", "DISPATCHED"}:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION, "reconciliation version/state mismatch"
        )
    if conclusion == "FOUND" and result is not None:
        row.state, row.result = "SUCCEEDED", dict(result)
    elif conclusion == "CONFIRMED_NOT_EXECUTED" and result is None:
        row.state = "PREPARED"
        row.dispatch_id, row.dispatch_fencing_token = None, None
    elif conclusion == "UNKNOWN" and result is None:
        row.state = "UNKNOWN"
    else:
        raise DomainError(FailureCode.VALIDATION_FAILED, "invalid reconciliation conclusion")
    row.version += 1
    session.add(
        AuditRow(
            workspace_id=workspace_id,
            actor_type="AGENT",
            actor_id=attempt_id,
            task_id=await _task_for_step(session, workspace_id, row.step_id),
            step_id=row.step_id,
            resource_type="OPERATION",
            resource_id=operation_id,
            capability_id=row.capability_id,
            action="operation.reconcile." + conclusion.lower(),
            result={"state": row.state, "conclusion": conclusion},
            trace_id=attempt.trace_id,
        )
    )
    await session.flush()
    RECONCILIATION_TOTAL.labels(conclusion).inc()
    return decision(row)
