from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import Contract, FailureCode, TaskGoal
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.operations import require_owner
from enterprise_agent.storage.runtime_locks import lock_task_runtime
from enterprise_agent.storage.tables import (
    AuditRow,
    PlanRow,
    ReplanDecisionRow,
    StepRow,
    TaskBudgetRow,
    UsageRecordRow,
)

_BOUNDED_QA_SKILLS = frozenset(
    {
        "web-research",
        "document-analysis",
        "grounded-research",
    }
)


class BudgetKind(StrEnum):
    STEP = "STEP"
    REPLAN = "REPLAN"
    LLM_CALL = "LLM_CALL"
    CAPABILITY_CALL = "CAPABILITY_CALL"


class BudgetDelta(Contract):
    kind: BudgetKind
    units: int = Field(default=1, ge=0)
    tokens: int = Field(default=0, ge=0)
    cost: float = Field(default=0, ge=0, allow_inf_nan=False)


class BudgetDecision(Contract):
    allowed: bool
    reason: str | None = None
    version: int = Field(ge=1)


def new_task_budget(task_id: UUID, goal: TaskGoal) -> TaskBudgetRow:
    return TaskBudgetRow(
        task_id=task_id,
        workspace_id=goal.workspace_id,
        max_steps=100,
        # Bounded question-answering must either recover once or terminate with a
        # truthful partial/unavailable result. Repeating the same bounded pipeline
        # consumes the user's latency and token budget without adding authority or
        # evidence. Durable enterprise tasks keep the larger recovery allowance.
        max_replans=1 if goal.requested_skill in _BOUNDED_QA_SKILLS else 5,
        max_llm_calls=50,
        max_capability_calls=100,
        max_tokens=goal.token_budget,
        max_cost=goal.cost_budget,
        deadline=datetime.now(UTC) + timedelta(seconds=goal.time_budget_sec),
    )


async def reserve_budget(
    session: AsyncSession,
    workspace_id: UUID,
    task_id: UUID,
    actor_id: UUID,
    trace_id: str,
    delta: BudgetDelta,
) -> BudgetDecision:
    # Usage records reference the task through a foreign key. Locking the task before
    # the budget keeps that implicit key-share lock in the same global order as
    # attempt and capability transactions.
    await lock_task_runtime(session, workspace_id, task_id)
    row = await session.scalar(
        select(TaskBudgetRow)
        .where(
            TaskBudgetRow.workspace_id == workspace_id,
            TaskBudgetRow.task_id == task_id,
        )
        .with_for_update()
    )
    if row is None:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "task budget unavailable")
    now = await session.scalar(select(func.clock_timestamp()))
    if now is None:
        raise DomainError(FailureCode.DB_UNAVAILABLE, "database clock unavailable")
    increments = {
        BudgetKind.STEP: ("used_steps", "max_steps"),
        BudgetKind.REPLAN: ("used_replans", "max_replans"),
        BudgetKind.LLM_CALL: ("used_llm_calls", "max_llm_calls"),
        BudgetKind.CAPABILITY_CALL: ("used_capability_calls", "max_capability_calls"),
    }
    used_name, max_name = increments[delta.kind]
    reason = None
    if row.state == "EXCEEDED":
        reason = row.last_exceeded_reason or "budget previously exceeded"
    elif now >= row.deadline:
        reason = "deadline"
    elif getattr(row, used_name) + delta.units > getattr(row, max_name):
        reason = delta.kind.value.casefold()
    elif row.used_tokens + delta.tokens > row.max_tokens:
        reason = "tokens"
    elif row.used_cost + delta.cost > row.max_cost:
        reason = "cost"
    if reason is not None:
        if row.state != "EXCEEDED":
            row.state = "EXCEEDED"
            row.last_exceeded_reason = reason
            row.version += 1
            session.add(
                AuditRow(
                    workspace_id=workspace_id,
                    actor_type="AGENT",
                    actor_id=actor_id,
                    task_id=task_id,
                    resource_type="TASK_BUDGET",
                    resource_id=task_id,
                    action="budget.exceeded." + reason,
                    result={"state": "EXCEEDED", "reason": reason},
                    trace_id=trace_id,
                )
            )
        await session.flush()
        return BudgetDecision(allowed=False, reason=reason, version=row.version)
    setattr(row, used_name, getattr(row, used_name) + delta.units)
    row.used_tokens += delta.tokens
    row.used_cost += delta.cost
    row.version += 1
    session.add(
        UsageRecordRow(
            id=uuid4(),
            workspace_id=workspace_id,
            task_id=task_id,
            evaluation_run_id=None,
            kind=delta.kind.value,
            units=delta.units,
            tokens=delta.tokens,
            cost=delta.cost,
            detail={
                "actor_id": str(actor_id),
                "trace_id": trace_id,
                "budget_version": row.version,
            },
        )
    )
    await session.flush()
    return BudgetDecision(allowed=True, version=row.version)


async def _record_actual_llm_usage_locked(
    session: AsyncSession,
    workspace_id: UUID,
    task_id: UUID,
    actor_id: UUID,
    trace_id: str,
    *,
    usage_record_id: UUID,
    tokens: int,
    cost: float,
    step_id: UUID | None,
    detail: dict[str, object],
) -> int:
    row = await session.scalar(
        select(TaskBudgetRow)
        .where(
            TaskBudgetRow.workspace_id == workspace_id,
            TaskBudgetRow.task_id == task_id,
        )
        .with_for_update()
    )
    if row is None:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "task budget unavailable")
    now = await session.scalar(select(func.clock_timestamp()))
    if now is None:
        raise DomainError(FailureCode.DB_UNAVAILABLE, "database clock unavailable")
    exceeded_reason: str | None = None
    if row.state != "EXCEEDED":
        if now >= row.deadline:
            exceeded_reason = "deadline"
        elif row.used_tokens + tokens > row.max_tokens:
            exceeded_reason = "tokens"
        elif row.used_cost + cost > row.max_cost:
            exceeded_reason = "cost"
        if exceeded_reason is not None:
            row.state = "EXCEEDED"
            row.last_exceeded_reason = exceeded_reason
            session.add(
                AuditRow(
                    workspace_id=workspace_id,
                    actor_type="AGENT",
                    actor_id=actor_id,
                    task_id=task_id,
                    step_id=step_id,
                    resource_type="TASK_BUDGET",
                    resource_id=task_id,
                    action="budget.exceeded." + exceeded_reason,
                    result={"state": "EXCEEDED", "reason": exceeded_reason},
                    trace_id=trace_id,
                )
            )
    row.used_tokens += tokens
    row.used_cost += cost
    row.version += 1
    session.add(
        UsageRecordRow(
            id=usage_record_id,
            workspace_id=workspace_id,
            task_id=task_id,
            evaluation_run_id=None,
            kind=BudgetKind.LLM_CALL.value,
            units=0,
            tokens=tokens,
            cost=cost,
            detail={
                "actor_id": str(actor_id),
                "trace_id": trace_id,
                "budget_version": row.version,
                "post_call_actual": True,
                **detail,
            },
        )
    )
    await session.flush()
    return row.version


async def record_actual_llm_usage(
    session: AsyncSession,
    workspace_id: UUID,
    task_id: UUID,
    step_id: UUID,
    attempt_id: UUID,
    fencing_token: int,
    trace_id: str,
    *,
    tokens: int,
    cost: float = 0,
) -> int:
    """Record provider-reported usage after a call, without treating it as admission.

    A completed provider call is an accounting fact even when another concurrent
    reservation has already moved the budget to EXCEEDED.  The fact is still
    accepted only from the current fenced owner and is committed atomically with
    the canonical task budget and append-only usage ledger.
    """

    if tokens < 0 or cost < 0:
        raise DomainError(FailureCode.VALIDATION_FAILED, "actual model usage cannot be negative")
    await lock_task_runtime(session, workspace_id, task_id)
    await require_owner(
        session,
        workspace_id,
        step_id,
        attempt_id,
        fencing_token,
    )
    bound_task_id = await session.scalar(
        select(StepRow.task_id).where(
            StepRow.workspace_id == workspace_id,
            StepRow.id == step_id,
        )
    )
    if bound_task_id != task_id:
        raise DomainError(FailureCode.POLICY_DENIED, "actual model usage task binding mismatch")
    return await _record_actual_llm_usage_locked(
        session,
        workspace_id,
        task_id,
        attempt_id,
        trace_id,
        usage_record_id=uuid4(),
        tokens=tokens,
        cost=cost,
        step_id=step_id,
        detail={},
    )


async def record_actual_replan_llm_usage(
    session: AsyncSession,
    workspace_id: UUID,
    task_id: UUID,
    from_plan_id: UUID,
    actor_id: UUID,
    trace_id: str,
    settlement_id: str,
    *,
    tokens: int,
    cost: float = 0,
) -> int:
    """Settle one Replan provider reply against the still-current source Plan.

    Replan runs after the failed Step attempt has terminated, so it cannot borrow
    that attempt's ownership fence. The immutable source Plan is its authority:
    task mutation is locked, the task must still be running, the source must be the
    latest Plan, and no Replan decision may already exist for it. A deterministic
    settlement record makes a repeated callback idempotent while rejecting late
    callbacks after another Replan wins.
    """

    if tokens < 0 or cost < 0:
        raise DomainError(FailureCode.VALIDATION_FAILED, "actual model usage cannot be negative")
    if not settlement_id or len(settlement_id) > 512:
        raise DomainError(FailureCode.VALIDATION_FAILED, "invalid Replan usage settlement id")
    task = await lock_task_runtime(session, workspace_id, task_id)
    latest_plan_id = await session.scalar(
        select(PlanRow.id)
        .where(
            PlanRow.workspace_id == workspace_id,
            PlanRow.task_id == task_id,
        )
        .order_by(PlanRow.version.desc())
        .limit(1)
    )
    existing_replan_id = await session.scalar(
        select(ReplanDecisionRow.id).where(
            ReplanDecisionRow.workspace_id == workspace_id,
            ReplanDecisionRow.task_id == task_id,
            ReplanDecisionRow.from_plan_id == from_plan_id,
        )
    )
    if task.state != "RUNNING" or latest_plan_id != from_plan_id or existing_replan_id is not None:
        raise DomainError(FailureCode.STALE_ATTEMPT, "Replan usage owner rejected")

    usage_record_id = uuid5(
        NAMESPACE_URL,
        (
            f"enterprise-replan-llm-usage:{workspace_id}:{task_id}:"
            f"{from_plan_id}:{settlement_id}"
        ),
    )
    existing = await session.get(UsageRecordRow, usage_record_id)
    if existing is not None:
        if (
            existing.workspace_id != workspace_id
            or existing.task_id != task_id
            or existing.kind != BudgetKind.LLM_CALL.value
            or existing.tokens != tokens
            or existing.cost != cost
            or existing.detail.get("replan_from_plan_id") != str(from_plan_id)
            or existing.detail.get("settlement_id") != settlement_id
        ):
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "immutable Replan usage settlement differs from replay",
            )
        budget_version = await session.scalar(
            select(TaskBudgetRow.version).where(
                TaskBudgetRow.workspace_id == workspace_id,
                TaskBudgetRow.task_id == task_id,
            )
        )
        if budget_version is None:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "task budget unavailable")
        return budget_version

    return await _record_actual_llm_usage_locked(
        session,
        workspace_id,
        task_id,
        actor_id,
        trace_id,
        usage_record_id=usage_record_id,
        tokens=tokens,
        cost=cost,
        step_id=None,
        detail={
            "replan_from_plan_id": str(from_plan_id),
            "settlement_id": settlement_id,
        },
    )
