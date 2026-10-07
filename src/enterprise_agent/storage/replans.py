import hashlib
import json
from typing import Literal, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import (
    Contract,
    FailureCode,
    Plan,
    PlanStepSpec,
    ReplanDecision,
    StepState,
    TaskState,
)
from enterprise_agent.domain.states import DomainError, transition
from enterprise_agent.runtime.budgets import BudgetDelta, BudgetKind, reserve_budget
from enterprise_agent.runtime.loop_guard import check_replan_loop
from enterprise_agent.storage.plans import load_plan_versions, load_snapshot, persist_plan
from enterprise_agent.storage.steps import materialize_plan_steps
from enterprise_agent.storage.tables import (
    AuditRow,
    PlanRow,
    ReplanDecisionRow,
    StepRow,
    TaskRow,
)

ReplanTrigger = Literal[
    "TERMINAL_FAILURE",
    "EVIDENCE_INSUFFICIENT",
    "CAPABILITY_UNAVAILABLE",
    "VALIDATION_FAILURE",
    "USER_MODIFICATION",
    "BUDGET_DANGER",
]


class AppliedReplan(Contract):
    replan_id: UUID
    workspace_id: UUID
    task_id: UUID
    from_plan_id: UUID
    to_plan_id: UUID | None
    status: Literal["APPLIED", "ABORTED"]
    decision: ReplanDecision


def select_replan_trigger(
    *,
    terminal_failure: bool = False,
    evidence_insufficient: bool = False,
    capability_unavailable: bool = False,
    repeated_validation_failure: bool = False,
    user_modification: bool = False,
    budget_danger: bool = False,
) -> ReplanTrigger | None:
    """Choose the first frozen deterministic trigger in a stable priority order."""
    triggers: tuple[tuple[bool, ReplanTrigger], ...] = (
        (terminal_failure, "TERMINAL_FAILURE"),
        (evidence_insufficient, "EVIDENCE_INSUFFICIENT"),
        (capability_unavailable, "CAPABILITY_UNAVAILABLE"),
        (repeated_validation_failure, "VALIDATION_FAILURE"),
        (user_modification, "USER_MODIFICATION"),
        (budget_danger, "BUDGET_DANGER"),
    )
    return next((name for active, name in triggers if active), None)


def _decision_payload(decision: ReplanDecision) -> tuple[dict[str, object], str]:
    payload = decision.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return payload, hashlib.sha256(encoded).hexdigest()


def validate_replan_decision(decision: ReplanDecision, current: Plan) -> None:
    current_keys = {item.step_key for item in current.steps}
    affected = set(decision.affected_steps)
    if len(affected) != len(decision.affected_steps) or not affected <= current_keys:
        raise DomainError(FailureCode.VALIDATION_FAILED, "replan affected steps are invalid")
    action = decision.action
    if action in {"REPLACE_STEP", "EXPAND_STEP"}:
        if not affected or not decision.new_steps:
            raise DomainError(
                FailureCode.VALIDATION_FAILED,
                "replacement and expansion require affected and new steps",
            )
    elif action == "ADD_STEP":
        if affected or not decision.new_steps:
            raise DomainError(
                FailureCode.VALIDATION_FAILED,
                "add step requires only new steps",
            )
    elif action in {"RETRY_STEP", "SKIP_STEP"}:
        if not affected or decision.new_steps:
            raise DomainError(
                FailureCode.VALIDATION_FAILED,
                "retry and skip require affected steps without new steps",
            )
    elif action == "ABORT_TASK" and decision.new_steps:
        raise DomainError(FailureCode.VALIDATION_FAILED, "abort cannot add steps")


def _next_steps(current: Plan, decision: ReplanDecision) -> tuple[PlanStepSpec, ...]:
    if decision.action in {"REPLACE_STEP", "EXPAND_STEP"}:
        affected = set(decision.affected_steps)
        retained = tuple(item for item in current.steps if item.step_key not in affected)
        return retained + tuple(decision.new_steps)
    if decision.action == "ADD_STEP":
        return current.steps + tuple(decision.new_steps)
    return current.steps


async def _validate_snapshot_capabilities(
    session: AsyncSession, plan: Plan
) -> None:
    snapshot = await load_snapshot(session, plan.workspace_id, plan.execution_snapshot_id)
    versions = {item.identifier: item.version for item in snapshot.capability_versions}
    for step in plan.steps:
        for requirement in step.capability_requirements:
            version = versions.get(requirement.capability_id)
            if version is None:
                raise DomainError(
                    FailureCode.CAPABILITY_UNAVAILABLE,
                    "replan capability is absent from execution snapshot",
                )
            try:
                compatible = SpecifierSet(requirement.version_range).contains(
                    version, prereleases=True
                )
            except InvalidSpecifier as exc:
                raise DomainError(
                    FailureCode.VALIDATION_FAILED,
                    "replan capability version range is invalid",
                ) from exc
            if not compatible:
                raise DomainError(
                    FailureCode.CAPABILITY_UNAVAILABLE,
                    "replan capability version is outside the frozen snapshot",
                )


def _view(row: ReplanDecisionRow) -> AppliedReplan:
    return AppliedReplan(
        replan_id=row.id,
        workspace_id=row.workspace_id,
        task_id=row.task_id,
        from_plan_id=row.from_plan_id,
        to_plan_id=row.to_plan_id,
        status=cast(Literal["APPLIED", "ABORTED"], row.status),
        decision=ReplanDecision.model_validate(row.payload),
    )


async def apply_replan(
    session: AsyncSession,
    workspace_id: UUID,
    task_id: UUID,
    actor_id: UUID,
    trace_id: str,
    decision: ReplanDecision,
    *,
    expected_from_plan_id: UUID | None = None,
    actor_type: Literal["USER", "AGENT"] = "AGENT",
) -> AppliedReplan:
    """Atomically append a Plan version and supersede its prior executable DAG."""
    task = await session.scalar(
        select(TaskRow)
        .where(TaskRow.workspace_id == workspace_id, TaskRow.id == task_id)
        .with_for_update()
    )
    current_row = await session.scalar(
        select(PlanRow)
        .where(PlanRow.workspace_id == workspace_id, PlanRow.task_id == task_id)
        .order_by(PlanRow.version.desc())
        .limit(1)
        .with_for_update()
    )
    if task is None or current_row is None:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "replan source is unavailable")
    payload, digest = _decision_payload(decision)
    if expected_from_plan_id is not None:
        existing = await session.scalar(
            select(ReplanDecisionRow).where(
                ReplanDecisionRow.workspace_id == workspace_id,
                ReplanDecisionRow.task_id == task_id,
                ReplanDecisionRow.from_plan_id == expected_from_plan_id,
            )
        )
        if existing is not None:
            if existing.content_hash != digest:
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION,
                    "immutable replan decision differs from replay",
                )
            return _view(existing)
        if current_row.id != expected_from_plan_id:
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "replan source is not the latest Plan",
            )
    else:
        # Backward-compatible replay detection for callers that predate explicit
        # source-plan fencing. Production callers always provide the source Plan.
        replay = await session.scalar(
            select(ReplanDecisionRow).where(
                ReplanDecisionRow.workspace_id == workspace_id,
                ReplanDecisionRow.task_id == task_id,
                ReplanDecisionRow.to_plan_id == current_row.id,
            )
        )
        if replay is not None:
            if replay.content_hash != digest:
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION,
                    "latest plan was produced by a different replan decision",
                )
            return _view(replay)
    existing = await session.scalar(
        select(ReplanDecisionRow).where(
            ReplanDecisionRow.workspace_id == workspace_id,
            ReplanDecisionRow.task_id == task_id,
            ReplanDecisionRow.from_plan_id == current_row.id,
        )
    )
    if existing is not None:
        if existing.content_hash != digest:
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "immutable replan decision differs from replay",
            )
        return _view(existing)
    if TaskState(task.state) is not TaskState.RUNNING:
        raise DomainError(FailureCode.VALIDATION_FAILED, "only a running task can replan")
    versions = await load_plan_versions(session, workspace_id, task_id)
    if not versions or versions[-1].plan_id != current_row.id:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "latest plan verification failed")
    current = versions[-1]
    validate_replan_decision(decision, current)
    old_steps = (
        await session.scalars(
            select(StepRow)
            .where(
                StepRow.workspace_id == workspace_id,
                StepRow.task_id == task_id,
                StepRow.plan_id == current.plan_id,
            )
            .with_for_update()
        )
    ).all()
    if any(
        StepState(item.state) in {StepState.RUNNING, StepState.WAITING_HITL}
        for item in old_steps
    ):
        raise DomainError(
            FailureCode.VALIDATION_FAILED,
            "active or approval-waiting steps must settle before replan",
        )

    budget = await reserve_budget(
        session,
        workspace_id,
        task_id,
        actor_id,
        trace_id,
        BudgetDelta(kind=BudgetKind.REPLAN, cost=decision.budget_impact),
    )
    guard_allowed = True
    guard_reason: str | None = None
    if budget.allowed:
        guard = await check_replan_loop(session, workspace_id, task_id, actor_id, trace_id)
        guard_allowed = guard.allowed
        guard_reason = guard.reason
    replan_id = uuid5(
        NAMESPACE_URL,
        f"enterprise-replan:{workspace_id}:{task_id}:{current.plan_id}:{digest}",
    )
    if not budget.allowed or not guard_allowed or decision.action == "ABORT_TASK":
        if TaskState(task.state) is TaskState.RUNNING:
            task.state = transition(TaskState.RUNNING, TaskState.FAILED).value
            task.version += 1
        reason = (
            budget.reason
            if not budget.allowed
            else guard_reason if not guard_allowed else decision.reason
        )
        row = ReplanDecisionRow(
            id=replan_id,
            workspace_id=workspace_id,
            task_id=task_id,
            from_plan_id=current.plan_id,
            to_plan_id=None,
            schema_version=decision.schema_version,
            status="ABORTED",
            actor_id=actor_id,
            content_hash=digest,
            payload=payload,
        )
        session.add(row)
        session.add(
            AuditRow(
                workspace_id=workspace_id,
                actor_type=actor_type,
                actor_id=actor_id,
                task_id=task_id,
                action="replan.aborted",
                resource_type="REPLAN_DECISION",
                resource_id=replan_id,
                result={"reason": reason, "trigger": decision.trigger},
                trace_id=trace_id,
            )
        )
        await session.flush()
        return _view(row)

    task.state = transition(TaskState.RUNNING, TaskState.PLANNING).value
    task.version += 1
    next_plan_id = uuid5(NAMESPACE_URL, f"enterprise-plan:{replan_id}:{current.version + 1}")
    next_plan = Plan(
        plan_id=next_plan_id,
        task_id=task_id,
        workspace_id=workspace_id,
        execution_snapshot_id=current.execution_snapshot_id,
        version=current.version + 1,
        goal=current.goal,
        constraints=current.constraints,
        deliverables=current.deliverables,
        steps=_next_steps(current, decision),
    )
    await _validate_snapshot_capabilities(session, next_plan)
    await persist_plan(session, next_plan)

    old_by_key = {item.step_key: item for item in old_steps if item.step_key is not None}
    next_keys = {item.step_key for item in next_plan.steps}
    current_specs = {item.step_key: item for item in current.steps}
    next_specs = {item.step_key: item for item in next_plan.steps}
    initial_states: dict[str, StepState] = {}
    if decision.preserve_completed_steps:
        for key, item in old_by_key.items():
            state = StepState(item.state)
            if (
                key in next_keys
                and state in {StepState.COMPLETED, StepState.SKIPPED}
                and current_specs.get(key) == next_specs[key]
                and key not in decision.affected_steps
            ):
                initial_states[key] = state
    if decision.action == "SKIP_STEP":
        initial_states.update({key: StepState.SKIPPED for key in decision.affected_steps})
    await materialize_plan_steps(session, next_plan, initial_states)

    for item in old_steps:
        state = StepState(item.state)
        if state in {
            StepState.READY,
            StepState.BLOCKED,
            StepState.RETRY_WAIT,
            StepState.FAILED,
        }:
            item.state = transition(state, StepState.SUPERSEDED).value
            item.version += 1
            item.retry_available_at = None
    task.state = transition(TaskState.PLANNING, TaskState.READY).value
    task.version += 1
    row = ReplanDecisionRow(
        id=replan_id,
        workspace_id=workspace_id,
        task_id=task_id,
        from_plan_id=current.plan_id,
        to_plan_id=next_plan.plan_id,
        schema_version=decision.schema_version,
        status="APPLIED",
        actor_id=actor_id,
        content_hash=digest,
        payload=payload,
    )
    session.add(row)
    session.add(
        AuditRow(
            workspace_id=workspace_id,
            actor_type=actor_type,
            actor_id=actor_id,
            task_id=task_id,
            action="replan.applied",
            resource_type="REPLAN_DECISION",
            resource_id=replan_id,
            result={
                "trigger": decision.trigger,
                "action": decision.action,
                "from_plan_id": str(current.plan_id),
                "to_plan_id": str(next_plan.plan_id),
                "plan_version": next_plan.version,
            },
            trace_id=trace_id,
        )
    )
    await session.flush()
    return _view(row)


async def list_replans(
    session: AsyncSession, workspace_id: UUID, task_id: UUID
) -> tuple[AppliedReplan, ...]:
    rows = (
        await session.scalars(
            select(ReplanDecisionRow)
            .where(
                ReplanDecisionRow.workspace_id == workspace_id,
                ReplanDecisionRow.task_id == task_id,
            )
            .order_by(ReplanDecisionRow.created_at, ReplanDecisionRow.id)
        )
    ).all()
    result: list[AppliedReplan] = []
    for row in rows:
        _, digest = _decision_payload(ReplanDecision.model_validate(row.payload))
        if digest != row.content_hash:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "replan hash mismatch")
        result.append(_view(row))
    return tuple(result)


async def get_replan_from_plan(
    session: AsyncSession, workspace_id: UUID, task_id: UUID, from_plan_id: UUID
) -> AppliedReplan | None:
    row = await session.scalar(
        select(ReplanDecisionRow).where(
            ReplanDecisionRow.workspace_id == workspace_id,
            ReplanDecisionRow.task_id == task_id,
            ReplanDecisionRow.from_plan_id == from_plan_id,
        )
    )
    if row is None:
        return None
    _, digest = _decision_payload(ReplanDecision.model_validate(row.payload))
    if digest != row.content_hash:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "replan hash mismatch")
    return _view(row)
