import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from enterprise_agent.contracts.domain import (
    ConsistencyIncidentView,
    FailureCode,
    StepState,
    TaskState,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.governance.policy import ROLE_PERMISSIONS, Role
from enterprise_agent.observability.metrics import RECONCILIATION_TOTAL, RECOVERY_TOTAL
from enterprise_agent.runtime.conversation_results import (
    materialize_chat_task_turn,
    reconcile_abandoned_direct_turns,
    reconcile_abandoned_planned_turns,
)
from enterprise_agent.storage.outbox import enqueue
from enterprise_agent.storage.plans import load_snapshot
from enterprise_agent.storage.tables import (
    ConsistencyIncidentRow,
    OutboxEventRow,
    PlanRow,
    StepRow,
    TaskRow,
    Workspace,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconcileSummary:
    inspected: int
    valid: int
    incidents: int
    repairs_enqueued: int


_ACTIVE = {
    TaskState.PLANNING,
    TaskState.READY,
    TaskState.RUNNING,
    TaskState.WAITING_HITL,
    TaskState.PAUSED,
    TaskState.CANCELLING,
}
_TERMINAL = {TaskState.CANCELLED, TaskState.COMPLETED, TaskState.FAILED}
_DEFAULT_DIRECT_TURN_TIMEOUT_SECONDS = 900


def _direct_turn_timeout_seconds() -> int:
    raw = os.environ.get(
        "DIRECT_TURN_TIMEOUT_SECONDS",
        str(_DEFAULT_DIRECT_TURN_TIMEOUT_SECONDS),
    )
    try:
        value = int(raw)
    except ValueError:
        LOGGER.warning(
            "invalid DIRECT_TURN_TIMEOUT_SECONDS=%r; using %s",
            raw,
            _DEFAULT_DIRECT_TURN_TIMEOUT_SECONDS,
        )
        return _DEFAULT_DIRECT_TURN_TIMEOUT_SECONDS
    return max(1, value)


async def _temporal_status(client: Client, workflow_id: str) -> str:
    try:
        description = await client.get_workflow_handle(workflow_id).describe()
    except RPCError as exc:
        if exc.status is RPCStatusCode.NOT_FOUND:
            return "NOT_FOUND"
        raise
    if description.status is None:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "Temporal description has no execution status",
        )
    return WorkflowExecutionStatus(description.status).name


async def _record_incident(
    session: AsyncSession,
    *,
    task: TaskRow,
    temporal_status: str,
    repair: str,
    repair_failure: str | None = None,
) -> tuple[ConsistencyIncidentRow, bool]:
    incident_id = uuid5(
        NAMESPACE_URL,
        f"runtime-consistency:{task.workspace_id}:{task.id}:{task.state}:{temporal_status}",
    )
    existing = await session.scalar(
        select(ConsistencyIncidentRow).where(
            ConsistencyIncidentRow.workspace_id == task.workspace_id,
            ConsistencyIncidentRow.id == incident_id,
        )
    )
    if existing is not None:
        return existing, False
    detail: dict[str, JsonValue] = {
        "postgres_state": task.state,
        "temporal_status": temporal_status,
        "repair": repair,
    }
    if repair_failure is not None:
        detail["repair_failure_code"] = FailureCode.CONSISTENCY_VIOLATION.value
        detail["repair_failure"] = repair_failure
    row = ConsistencyIncidentRow(
        id=incident_id,
        workspace_id=task.workspace_id,
        task_id=task.id,
        severity="HIGH",
        incident_type="POSTGRES_TEMPORAL_STATE_MISMATCH",
        detail=detail,
    )
    session.add(row)
    await session.flush()
    return row, True


async def _execution_repair_failure(
    session: AsyncSession,
    task: TaskRow,
) -> str | None:
    if task.execution_snapshot_id is None:
        return "active task has no execution snapshot"
    plan = await session.scalar(
        select(PlanRow.id)
        .where(
            PlanRow.workspace_id == task.workspace_id,
            PlanRow.task_id == task.id,
        )
        .order_by(PlanRow.version.desc())
        .limit(1)
    )
    if plan is None:
        return "active task has no Plan"
    step_states = (
        await session.scalars(
            select(StepRow.state).where(
                StepRow.workspace_id == task.workspace_id,
                StepRow.task_id == task.id,
                StepRow.plan_id == plan,
            )
        )
    ).all()
    if not step_states:
        return "active task Plan has no materialized steps"
    if not any(
        StepState(value)
        in {
            StepState.READY,
            StepState.RUNNING,
            StepState.RETRY_WAIT,
            StepState.WAITING_HITL,
            StepState.FAILED,
        }
        for value in step_states
    ):
        return "active task Plan has no executable or recoverable step"
    try:
        await load_snapshot(session, task.workspace_id, task.execution_snapshot_id)
    except DomainError as exc:
        if exc.code is FailureCode.CONSISTENCY_VIOLATION:
            return exc.detail
        raise
    return None


async def _enqueue_missing_execution(
    session: AsyncSession, task: TaskRow, incident_id: UUID
) -> bool:
    if task.execution_snapshot_id is None:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "active task has no execution snapshot",
        )
    plan = await session.scalar(
        select(PlanRow)
        .where(
            PlanRow.workspace_id == task.workspace_id,
            PlanRow.task_id == task.id,
        )
        .order_by(PlanRow.version.desc())
        .limit(1)
    )
    if plan is None:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "active task has no Plan")
    snapshot = await load_snapshot(session, task.workspace_id, task.execution_snapshot_id)
    # One recovery command identity represents one immutable execution request.
    # Reusing only the incident id is unsafe after a replan or snapshot change:
    # the Outbox correctly rejects the changed payload under the old id.
    event_id = uuid5(
        NAMESPACE_URL,
        (
            f"runtime-consistency-repair:{incident_id}:{plan.id}:"
            f"{task.execution_snapshot_id}:{snapshot.runtime_version}"
        ),
    )
    existed = await session.get(OutboxEventRow, event_id)
    await enqueue(
        session,
        event_id,
        task.workspace_id,
        "TASK_RUN",
        task.id,
        "task.execution.requested",
        {
            "workflow_id": f"task-{task.id}",
            "task_queue": os.environ.get("TEMPORAL_TASK_QUEUE", "enterprise-agent-runtime"),
            "workspace_id": str(task.workspace_id),
            "task_id": str(task.id),
            "active_plan_id": str(plan.id),
            "resume_token": f"initial:{task.id}",
            "runtime_version": snapshot.runtime_version,
            "consistency_incident_id": str(incident_id),
        },
    )
    return existed is None


async def _reconcile_locked_task(
    session: AsyncSession,
    task: TaskRow,
    temporal: str,
) -> tuple[int, int, int]:
    postgres = TaskState(task.state)
    if postgres is TaskState.CREATED:
        if temporal == "NOT_FOUND":
            return 1, 0, 0
    elif (
        postgres in _ACTIVE
        and temporal == "RUNNING"
        or postgres in _TERMINAL
        and temporal != "RUNNING"
    ):
        return 1, 0, 0

    if postgres in _ACTIVE and temporal == "NOT_FOUND":
        pending = await session.scalar(
            select(OutboxEventRow.id).where(
                OutboxEventRow.workspace_id == task.workspace_id,
                OutboxEventRow.aggregate_id == task.id,
                OutboxEventRow.event_type == "task.execution.requested",
                OutboxEventRow.state.in_(("PENDING", "LEASED")),
            )
        )
        if pending is not None:
            return 1, 0, 0
        repair_failure = await _execution_repair_failure(session, task)
        if repair_failure is not None:
            _, created = await _record_incident(
                session,
                task=task,
                temporal_status=temporal,
                repair="MANUAL_REVIEW_REQUIRED",
                repair_failure=repair_failure,
            )
            return 0, int(created), 0
        incident, created = await _record_incident(
            session,
            task=task,
            temporal_status=temporal,
            repair="OUTBOX_ENQUEUED",
        )
        repair_enqueued = await _enqueue_missing_execution(session, task, incident.id)
        return 0, int(created), int(repair_enqueued)

    _, created = await _record_incident(
        session,
        task=task,
        temporal_status=temporal,
        repair="MANUAL_REVIEW_REQUIRED",
    )
    return 0, int(created), 0


async def reconcile_once(
    sessions: async_sessionmaker[AsyncSession],
    client: Client,
    *,
    workspace_id: UUID | None = None,
    direct_turn_timeout_seconds: int | None = None,
) -> ReconcileSummary:
    timeout_seconds = (
        _direct_turn_timeout_seconds()
        if direct_turn_timeout_seconds is None
        else direct_turn_timeout_seconds
    )
    async with sessions.begin() as direct_session:
        planned_turns_recovered = await reconcile_abandoned_planned_turns(
            direct_session,
            stale_after_seconds=timeout_seconds,
            workspace_id=workspace_id,
        )
        direct_turns_recovered = await reconcile_abandoned_direct_turns(
            direct_session,
            stale_after_seconds=timeout_seconds,
            workspace_id=workspace_id,
        )
    if direct_turns_recovered:
        RECONCILIATION_TOTAL.labels("direct_turn_recovered").inc(direct_turns_recovered)
        RECOVERY_TOTAL.inc(direct_turns_recovered)
    if planned_turns_recovered:
        RECONCILIATION_TOTAL.labels("planned_turn_recovered").inc(
            planned_turns_recovered
        )
        RECOVERY_TOTAL.inc(planned_turns_recovered)

    async with sessions() as read_session:
        statement = (
            select(TaskRow)
            .join(Workspace, Workspace.id == TaskRow.workspace_id)
            .where(
                TaskRow.goal_payload.is_not(None),
                Workspace.enabled.is_(True),
                Workspace.lifecycle_state == "ACTIVE",
            )
        )
        if workspace_id is not None:
            statement = statement.where(TaskRow.workspace_id == workspace_id)
        candidates = (
            await read_session.scalars(statement.order_by(TaskRow.created_at, TaskRow.id))
        ).all()
        snapshots = [(task.id, f"task-{task.id}") for task in candidates]
    statuses: dict[UUID, str] = {}
    for task_id, workflow_id in snapshots:
        statuses[task_id] = await _temporal_status(client, workflow_id)

    valid = 0
    incidents = 0
    repairs = 0
    async with sessions.begin() as session:
        for task_id, _ in snapshots:
            try:
                async with session.begin_nested():
                    task = await session.scalar(
                        select(TaskRow).where(TaskRow.id == task_id).with_for_update()
                    )
                    if task is None:
                        continue
                    if TaskState(task.state) in _TERMINAL:
                        await materialize_chat_task_turn(
                            session,
                            workspace_id=task.workspace_id,
                            task_id=task.id,
                            trace_id=f"reconcile-task-turn:{task.id}",
                        )
                    item_valid, item_incidents, item_repairs = await _reconcile_locked_task(
                        session,
                        task,
                        statuses[task_id],
                    )
            except DomainError as exc:
                RECONCILIATION_TOTAL.labels("runtime_item_failed").inc()
                LOGGER.warning(
                    "runtime reconciliation item failed task_id=%s code=%s detail=%s",
                    task_id,
                    exc.code.value,
                    exc.detail,
                )
                continue
            valid += item_valid
            incidents += item_incidents
            repairs += item_repairs
    summary = ReconcileSummary(
        inspected=len(snapshots),
        valid=valid,
        incidents=incidents,
        repairs_enqueued=repairs,
    )
    if valid:
        RECONCILIATION_TOTAL.labels("runtime_valid").inc(valid)
    if incidents:
        RECONCILIATION_TOTAL.labels("runtime_incident").inc(incidents)
    if repairs:
        RECONCILIATION_TOTAL.labels("runtime_repair_enqueued").inc(repairs)
        RECOVERY_TOTAL.inc(repairs)
    return summary


async def list_consistency_incidents(
    session: AsyncSession, workspace_id: UUID, role: Role
) -> tuple[ConsistencyIncidentView, ...]:
    if "audit.read" not in ROLE_PERMISSIONS[role]:
        raise DomainError(
            FailureCode.POLICY_DENIED, "consistency incident read permission required"
        )
    rows = (
        await session.scalars(
            select(ConsistencyIncidentRow)
            .where(ConsistencyIncidentRow.workspace_id == workspace_id)
            .order_by(ConsistencyIncidentRow.created_at, ConsistencyIncidentRow.id)
        )
    ).all()
    return tuple(
        ConsistencyIncidentView(
            incident_id=row.id,
            workspace_id=row.workspace_id,
            task_id=row.task_id,
            severity=cast(Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"], row.severity),
            incident_type=row.incident_type,
            detail=cast(dict[str, JsonValue], row.detail),
            created_at=row.created_at,
        )
        for row in rows
    )


async def run_reconciler(client: Client) -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    interval = max(1.0, float(os.environ.get("RECONCILE_INTERVAL_SECONDS", "10")))
    try:
        while True:
            try:
                await reconcile_once(sessions, client)
            except Exception:
                LOGGER.exception("runtime reconciliation scan failed")
            await asyncio.sleep(interval)
    finally:
        await engine.dispose()
