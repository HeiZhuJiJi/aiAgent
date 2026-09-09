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
    TaskState,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.governance.policy import ROLE_PERMISSIONS, Role
from enterprise_agent.observability.metrics import RECONCILIATION_TOTAL, RECOVERY_TOTAL
from enterprise_agent.storage.outbox import enqueue
from enterprise_agent.storage.plans import load_snapshot
from enterprise_agent.storage.tables import (
    ConsistencyIncidentRow,
    OutboxEventRow,
    PlanRow,
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
    row = ConsistencyIncidentRow(
        id=incident_id,
        workspace_id=task.workspace_id,
        task_id=task.id,
        severity="HIGH",
        incident_type="POSTGRES_TEMPORAL_STATE_MISMATCH",
        detail={
            "postgres_state": task.state,
            "temporal_status": temporal_status,
            "repair": repair,
        },
    )
    session.add(row)
    await session.flush()
    return row, True


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
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION, "active task has no Plan"
        )
    snapshot = await load_snapshot(
        session, task.workspace_id, task.execution_snapshot_id
    )
    event_id = uuid5(NAMESPACE_URL, f"runtime-consistency-repair:{incident_id}")
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
            "task_queue": os.environ.get(
                "TEMPORAL_TASK_QUEUE", "enterprise-agent-runtime"
            ),
            "workspace_id": str(task.workspace_id),
            "task_id": str(task.id),
            "active_plan_id": str(plan.id),
            "resume_token": f"initial:{task.id}",
            "runtime_version": snapshot.runtime_version,
            "consistency_incident_id": str(incident_id),
        },
    )
    return existed is None


async def reconcile_once(
    sessions: async_sessionmaker[AsyncSession],
    client: Client,
    *,
    workspace_id: UUID | None = None,
) -> ReconcileSummary:
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
            await read_session.scalars(
                statement.order_by(TaskRow.created_at, TaskRow.id)
            )
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
            task = await session.scalar(
                select(TaskRow).where(TaskRow.id == task_id).with_for_update()
            )
            if task is None:
                continue
            postgres = TaskState(task.state)
            temporal = statuses[task_id]
            if postgres is TaskState.CREATED:
                if temporal == "NOT_FOUND":
                    valid += 1
                    continue
            elif postgres in _ACTIVE and temporal == "RUNNING" or postgres in _TERMINAL and temporal != "RUNNING":
                valid += 1
                continue

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
                    valid += 1
                    continue
                incident, created = await _record_incident(
                    session,
                    task=task,
                    temporal_status=temporal,
                    repair="OUTBOX_ENQUEUED",
                )
                incidents += int(created)
                repairs += int(
                    await _enqueue_missing_execution(session, task, incident.id)
                )
                continue

            _, created = await _record_incident(
                session,
                task=task,
                temporal_status=temporal,
                repair="MANUAL_REVIEW_REQUIRED",
            )
            incidents += int(created)
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
