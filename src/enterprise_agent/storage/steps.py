from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import (
    FailureCode,
    Plan,
    PlanStepSpec,
    StepState,
    TaskState,
    TaskStep,
)
from enterprise_agent.domain.states import DomainError, transition
from enterprise_agent.storage.tables import AuditRow, StepRow, TaskRow


def plan_step_id(plan_id: UUID, step_key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"enterprise-plan-step:{plan_id}:{step_key}")


async def materialize_plan_steps(
    session: AsyncSession,
    plan: Plan,
    initial_states: dict[str, StepState] | None = None,
) -> tuple[TaskStep, ...]:
    initial_states = initial_states or {}
    unknown = set(initial_states) - {item.step_key for item in plan.steps}
    invalid = {
        key: state
        for key, state in initial_states.items()
        if state not in {StepState.COMPLETED, StepState.SKIPPED}
    }
    if unknown or invalid:
        raise DomainError(
            FailureCode.VALIDATION_FAILED,
            "initial plan step states must reference plan keys and be completed or skipped",
        )
    satisfied = {
        key
        for key, state in initial_states.items()
        if state in {StepState.COMPLETED, StepState.SKIPPED}
    }
    for spec in plan.steps:
        step_id = plan_step_id(plan.plan_id, spec.step_key)
        payload = spec.model_dump(mode="json")
        state = initial_states.get(spec.step_key)
        if state is None:
            state = (
                StepState.READY
                if set(spec.dependencies) <= satisfied
                else StepState.BLOCKED
            )
        await session.execute(
            insert(StepRow)
            .values(
                id=step_id,
                workspace_id=plan.workspace_id,
                task_id=plan.task_id,
                plan_id=plan.plan_id,
                step_key=spec.step_key,
                spec_payload=payload,
                state=state.value,
                version=1,
                fencing_token=0,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        row = await session.scalar(
            select(StepRow).where(
                StepRow.workspace_id == plan.workspace_id,
                StepRow.id == step_id,
            )
        )
        if (
            row is None
            or row.task_id != plan.task_id
            or row.plan_id != plan.plan_id
            or row.step_key != spec.step_key
            or row.spec_payload != payload
        ):
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "materialized plan step identity changed",
            )
    return await list_task_steps(session, plan.workspace_id, plan.task_id)


async def list_task_steps(
    session: AsyncSession, workspace_id: UUID, task_id: UUID
) -> tuple[TaskStep, ...]:
    rows = (
        await session.scalars(
            select(StepRow)
            .where(
                StepRow.workspace_id == workspace_id,
                StepRow.task_id == task_id,
                StepRow.plan_id.is_not(None),
                StepRow.spec_payload.is_not(None),
            )
            .order_by(StepRow.step_key, StepRow.id)
        )
    ).all()
    return tuple(
        TaskStep.model_validate(
            {
                "step_id": row.id,
                "workspace_id": row.workspace_id,
                "task_id": row.task_id,
                "plan_id": row.plan_id,
                "spec": row.spec_payload,
                "state": row.state,
                "version": row.version,
                "fencing_token": row.fencing_token,
            }
        )
        for row in rows
    )


async def advance_plan_after_step(
    session: AsyncSession,
    workspace_id: UUID,
    task_id: UUID,
    plan_id: UUID,
    actor_id: UUID,
    trace_id: str,
) -> tuple[UUID, ...]:
    """Unlock satisfied DAG nodes and finish the canonical task atomically."""
    task = await session.scalar(
        select(TaskRow)
        .where(TaskRow.workspace_id == workspace_id, TaskRow.id == task_id)
        .with_for_update()
    )
    rows = (
        await session.scalars(
            select(StepRow)
            .where(
                StepRow.workspace_id == workspace_id,
                StepRow.task_id == task_id,
                StepRow.plan_id == plan_id,
            )
            .order_by(StepRow.step_key, StepRow.id)
            .with_for_update()
        )
    ).all()
    if task is None or not rows:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "plan execution state unavailable")

    successful = {
        row.step_key
        for row in rows
        if row.step_key is not None
        and StepState(row.state) in {StepState.COMPLETED, StepState.SKIPPED}
    }
    unlocked: list[UUID] = []
    for row in rows:
        if StepState(row.state) is not StepState.BLOCKED:
            continue
        if row.spec_payload is None:
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "materialized step has no immutable specification",
            )
        spec = PlanStepSpec.model_validate(row.spec_payload)
        if set(spec.dependencies) <= successful:
            row.state = transition(StepState.BLOCKED, StepState.READY).value
            row.version += 1
            unlocked.append(row.id)
            session.add(
                AuditRow(
                    workspace_id=workspace_id,
                    actor_type="AGENT",
                    actor_id=actor_id,
                    task_id=task_id,
                    step_id=row.id,
                    action="step.dependencies_satisfied",
                    resource_type="TASK_STEP",
                    resource_id=row.id,
                    result={"state": StepState.READY.value, "plan_id": str(plan_id)},
                    trace_id=trace_id,
                )
            )

    states = {StepState(row.state) for row in rows}
    if states <= {StepState.COMPLETED, StepState.SKIPPED}:
        current = TaskState(task.state)
        if current is TaskState.RUNNING:
            task.state = transition(current, TaskState.COMPLETED).value
            task.version += 1
            session.add(
                AuditRow(
                    workspace_id=workspace_id,
                    actor_type="AGENT",
                    actor_id=actor_id,
                    task_id=task_id,
                    action="task.completed",
                    resource_type="TASK_RUN",
                    resource_id=task_id,
                    result={"state": TaskState.COMPLETED.value, "plan_id": str(plan_id)},
                    trace_id=trace_id,
                )
            )
        elif current is not TaskState.COMPLETED:
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "completed plan is bound to a non-running task",
            )
    await session.flush()
    return tuple(unlocked)
