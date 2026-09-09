from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.api.contracts import TaskTraceView, TraceNode
from enterprise_agent.contracts.domain import TaskState
from enterprise_agent.storage.tables import (
    ApprovalRow,
    ArtifactRow,
    AttemptRow,
    AuditRow,
    CapabilityCallRow,
    EvidenceRow,
    PlanRow,
    ReplanDecisionRow,
    StepRow,
    TaskRow,
)


def _datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _safe_failure(payload: dict[str, object] | None) -> tuple[str | None, str | None]:
    if not payload:
        return None, None
    code = payload.get("code")
    detail = payload.get("safe_detail")
    return (str(code) if code else None, str(detail)[:500] if detail else None)


async def load_task_trace(
    session: AsyncSession, workspace_id: UUID, task_id: UUID
) -> TaskTraceView:
    task = await session.scalar(
        select(TaskRow).where(TaskRow.workspace_id == workspace_id, TaskRow.id == task_id)
    )
    if task is None:
        raise ValueError("task unavailable after authorization")
    plans = (
        await session.scalars(
            select(PlanRow)
            .where(PlanRow.workspace_id == workspace_id, PlanRow.task_id == task_id)
            .order_by(PlanRow.version)
        )
    ).all()
    steps = (
        await session.scalars(
            select(StepRow)
            .where(StepRow.workspace_id == workspace_id, StepRow.task_id == task_id)
            .order_by(StepRow.plan_id, StepRow.step_key, StepRow.id)
        )
    ).all()
    step_ids = tuple(item.id for item in steps)
    attempts = (
        (
            await session.scalars(
                select(AttemptRow)
                .where(
                    AttemptRow.workspace_id == workspace_id,
                    AttemptRow.step_id.in_(step_ids),
                )
                .order_by(AttemptRow.started_at, AttemptRow.id)
            )
        ).all()
        if step_ids
        else []
    )
    attempt_ids = tuple(item.id for item in attempts)
    evidence = (
        (
            await session.scalars(
                select(EvidenceRow).where(
                    EvidenceRow.workspace_id == workspace_id,
                    EvidenceRow.attempt_id.in_(attempt_ids),
                )
            )
        ).all()
        if attempt_ids
        else []
    )
    calls = (
        await session.scalars(
            select(CapabilityCallRow)
            .where(
                CapabilityCallRow.workspace_id == workspace_id,
                CapabilityCallRow.task_id == task_id,
            )
            .order_by(CapabilityCallRow.created_at, CapabilityCallRow.id)
        )
    ).all()
    approvals = (
        await session.scalars(
            select(ApprovalRow)
            .where(ApprovalRow.workspace_id == workspace_id, ApprovalRow.task_id == task_id)
            .order_by(ApprovalRow.created_at, ApprovalRow.id)
        )
    ).all()
    replans = (
        await session.scalars(
            select(ReplanDecisionRow)
            .where(
                ReplanDecisionRow.workspace_id == workspace_id,
                ReplanDecisionRow.task_id == task_id,
            )
            .order_by(ReplanDecisionRow.created_at, ReplanDecisionRow.id)
        )
    ).all()
    artifacts = (
        await session.scalars(
            select(ArtifactRow)
            .where(ArtifactRow.workspace_id == workspace_id, ArtifactRow.task_id == task_id)
            .order_by(ArtifactRow.created_at, ArtifactRow.id)
        )
    ).all()
    audits = (
        await session.scalars(
            select(AuditRow)
            .where(AuditRow.workspace_id == workspace_id, AuditRow.task_id == task_id)
            .order_by(AuditRow.created_at, AuditRow.id)
        )
    ).all()

    task_node = "task:" + str(task.id)
    nodes = [
        TraceNode(
            node_id=task_node,
            parent_id=None,
            kind="TASK",
            label="Task",
            state=task.state,
            occurred_at=task.created_at,
            trace_id=None,
        ),
        TraceNode(
            node_id="router:" + str(task.id),
            parent_id=task_node,
            kind="ROUTER",
            label="Complex task routed to durable runtime",
            state="COMPLETED",
            occurred_at=task.created_at,
            trace_id=None,
        ),
    ]
    plan_times = {item.id: item.created_at for item in plans}
    for plan in plans:
        nodes.append(
            TraceNode(
                node_id="plan:" + str(plan.id),
                parent_id=task_node,
                kind="PLAN",
                label=f"Plan v{plan.version}",
                state="PERSISTED",
                occurred_at=plan.created_at,
                trace_id=None,
            )
        )
    for step in steps:
        nodes.append(
            TraceNode(
                node_id="step:" + str(step.id),
                parent_id="plan:" + str(step.plan_id) if step.plan_id else task_node,
                kind="STEP",
                label=step.step_key or "Step",
                state=step.state,
                occurred_at=plan_times.get(step.plan_id) if step.plan_id else task.created_at,
                trace_id=None,
            )
        )
    for attempt in attempts:
        failure_code, safe_detail = _safe_failure(attempt.failure_payload)
        attempt_node = "attempt:" + str(attempt.id)
        nodes.append(
            TraceNode(
                node_id=attempt_node,
                parent_id="step:" + str(attempt.step_id),
                kind="ATTEMPT",
                label=f"Attempt token {attempt.fencing_token}",
                state=attempt.state,
                occurred_at=attempt.started_at,
                trace_id=attempt.trace_id,
            )
        )
        if failure_code:
            nodes.append(
                TraceNode(
                    node_id="failure:" + str(attempt.id),
                    parent_id=attempt_node,
                    kind="FAILURE",
                    label=failure_code,
                    state="RECORDED",
                    occurred_at=attempt.completed_at,
                    trace_id=attempt.trace_id,
                    safe_detail=safe_detail,
                )
            )
    for item in evidence:
        payload = item.payload
        nodes.append(
            TraceNode(
                node_id="evidence:" + str(item.id),
                parent_id="attempt:" + str(item.attempt_id),
                kind="EVIDENCE",
                label=f"{payload.get('source_type', 'UNKNOWN')} Evidence",
                state="COMMITTED",
                occurred_at=_datetime(payload.get("retrieved_at")),
                trace_id=None,
            )
        )
    for call in calls:
        call_node = "capability:" + str(call.id)
        nodes.append(
            TraceNode(
                node_id=call_node,
                parent_id="attempt:" + str(call.attempt_id),
                kind="CAPABILITY",
                label=call.capability_id,
                state=call.state,
                occurred_at=call.created_at,
                trace_id=call.trace_id,
            )
        )
        boundary_kind: Literal["LLM", "MCP"] | None = (
            "LLM"
            if call.capability_id.startswith("model.")
            else "MCP"
            if call.capability_id.startswith("procurement.")
            else None
        )
        if boundary_kind:
            nodes.append(
                TraceNode(
                    node_id=boundary_kind.casefold() + ":" + str(call.id),
                    parent_id=call_node,
                    kind=boundary_kind,
                    label=f"{boundary_kind} provider boundary",
                    state=call.state,
                    occurred_at=call.completed_at or call.created_at,
                    trace_id=call.trace_id,
                )
            )
    for approval in approvals:
        nodes.append(
            TraceNode(
                node_id="hitl:" + str(approval.id),
                parent_id="step:" + str(approval.step_id),
                kind="HITL",
                label="Operation-bound human approval",
                state=approval.state,
                occurred_at=approval.decided_at or approval.created_at,
                trace_id="approval:" + str(approval.id),
            )
        )
    for replan in replans:
        nodes.append(
            TraceNode(
                node_id="replan:" + str(replan.id),
                parent_id="plan:" + str(replan.from_plan_id),
                kind="REPLAN",
                label=str(replan.payload.get("trigger", "Replan")),
                state=replan.status,
                occurred_at=replan.created_at,
                trace_id=None,
            )
        )
    for artifact in artifacts:
        nodes.append(
            TraceNode(
                node_id="artifact:" + str(artifact.id),
                parent_id=task_node,
                kind="ARTIFACT",
                label="Immutable artifact",
                state="COMMITTED",
                occurred_at=artifact.created_at,
                trace_id=None,
            )
        )
    for audit in audits:
        if audit.action.startswith(("policy.", "task.recovery", "operation.reconcile")):
            nodes.append(
                TraceNode(
                    node_id="event:" + str(audit.id),
                    parent_id="step:" + str(audit.step_id) if audit.step_id else task_node,
                    kind="EVENT",
                    label=audit.action,
                    state=audit.policy_decision,
                    occurred_at=audit.created_at,
                    trace_id=audit.trace_id,
                )
            )
    if task.state in {"COMPLETED", "FAILED", "CANCELLED"}:
        terminal_times = [
            value
            for value in (
                *(item.completed_at for item in attempts),
                *(item.created_at for item in artifacts),
            )
            if value is not None
        ]
        nodes.append(
            TraceNode(
                node_id="finished:" + str(task.id),
                parent_id=task_node,
                kind="FINISHED",
                label="Finished",
                state=task.state,
                occurred_at=max(terminal_times) if terminal_times else task.created_at,
                trace_id=None,
            )
        )
    nodes.sort(key=lambda item: (item.occurred_at or task.created_at, item.node_id))
    return TaskTraceView(
        workspace_id=workspace_id,
        task_id=task_id,
        state=cast(TaskState, task.state),
        nodes=tuple(nodes),
    )
