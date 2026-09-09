from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import Contract, FailureCode, TaskGoal
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.runtime_locks import lock_task_runtime
from enterprise_agent.storage.tables import AuditRow, TaskBudgetRow, UsageRecordRow


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
        max_replans=5,
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
