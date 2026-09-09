import hashlib
import os
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.api.contracts import TaskView
from enterprise_agent.contracts.domain import (
    CapabilityDescriptor,
    ExecutionSnapshot,
    FailureCode,
    TaskGoal,
    TaskState,
    VersionRef,
)
from enterprise_agent.domain.states import DomainError, transition
from enterprise_agent.identity.contracts import Identity
from enterprise_agent.identity.service import get_workspace
from enterprise_agent.runtime.budgets import new_task_budget
from enterprise_agent.runtime.skill_planner import (
    build_skill_plan,
    capability_version_ref,
    resolve_skill_capabilities,
    skill_version_ref,
)
from enterprise_agent.storage.outbox import enqueue
from enterprise_agent.storage.plans import persist_plan, persist_snapshot
from enterprise_agent.storage.skills import load_enabled_skill
from enterprise_agent.storage.steps import materialize_plan_steps
from enterprise_agent.storage.tables import AuditRow, RuntimeGuardRow, TaskRow


def version_ref(identifier: str, version: str) -> VersionRef:
    digest = hashlib.sha256(f"{identifier}:{version}".encode()).hexdigest()
    return VersionRef(identifier=identifier, version=version, sha256=digest)


def current_snapshot(
    workspace_id: UUID,
    *,
    skill_versions: tuple[VersionRef, ...] = (),
    capability_versions: tuple[VersionRef, ...] = (),
) -> ExecutionSnapshot:
    runtime_version = "0.1.0"
    model_version = os.environ.get("MODEL_NAME", "unconfigured")
    return ExecutionSnapshot(
        snapshot_id=uuid4(),
        workspace_id=workspace_id,
        runtime_version=runtime_version,
        workflow_definition_version="1.0.0",
        planner_prompt_version=version_ref("planner", "1.0.0"),
        router_prompt_version=version_ref("router", "1.0.0"),
        reviewer_prompt_version=version_ref("reviewer", "1.0.0"),
        model_config_versions=(version_ref("model.default", model_version),),
        skill_versions=skill_versions,
        capability_versions=(
            version_ref("runtime.core", runtime_version),
            *capability_versions,
        ),
        policy_version=version_ref("policy.default", "1.0.0"),
        knowledge_document_refs=(),
        context_policy_version=version_ref("context.default", "1.0.0"),
        schema_versions=(version_ref("domain", "1"),),
        feature_flags=frozenset(),
        created_at=datetime.now(UTC),
    )


def task_view(row: TaskRow) -> TaskView:
    if row.execution_snapshot_id is None or row.goal_payload is None:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "task has no execution snapshot")
    return TaskView(
        task_id=row.id,
        workspace_id=row.workspace_id,
        state=TaskState(row.state),
        version=row.version,
        execution_snapshot_id=row.execution_snapshot_id,
        goal=TaskGoal.model_validate(row.goal_payload),
    )


async def create_task(
    session: AsyncSession,
    identity: Identity,
    goal: TaskGoal,
    trace_id: str,
    capabilities: tuple[CapabilityDescriptor, ...] = (),
) -> TaskView:
    await get_workspace(session, identity, goal.workspace_id)
    package = None
    selected_capabilities: tuple[CapabilityDescriptor, ...] = ()
    if goal.requested_skill is not None:
        package = await load_enabled_skill(session, goal.workspace_id, goal.requested_skill)
        selected_capabilities = resolve_skill_capabilities(package, capabilities)
    snapshot = current_snapshot(
        goal.workspace_id,
        skill_versions=() if package is None else (skill_version_ref(package),),
        capability_versions=tuple(
            capability_version_ref(item) for item in selected_capabilities
        ),
    )
    await persist_snapshot(session, snapshot)
    task_id = uuid4()
    row = TaskRow(
        id=task_id,
        workspace_id=goal.workspace_id,
        created_by=identity.user_id,
        state=TaskState.CREATED.value,
        version=1,
        execution_snapshot_id=snapshot.snapshot_id,
        goal_payload=goal.model_dump(mode="json"),
    )
    session.add(row)
    await session.flush()
    if package is not None:
        row.state = transition(TaskState.CREATED, TaskState.PLANNING).value
        row.version += 1
        plan = build_skill_plan(
            task_id=task_id,
            execution_snapshot_id=snapshot.snapshot_id,
            goal=goal,
            package=package,
        )
        await persist_plan(session, plan)
        await materialize_plan_steps(session, plan)
        row.state = transition(TaskState.PLANNING, TaskState.READY).value
        row.version += 1
        await enqueue(
            session,
            uuid5(NAMESPACE_URL, f"task-execution-requested:{goal.workspace_id}:{task_id}"),
            goal.workspace_id,
            "TASK_RUN",
            task_id,
            "task.execution.requested",
            {
                "workflow_id": f"task-{task_id}",
                "task_queue": os.environ.get(
                    "TEMPORAL_TASK_QUEUE", "enterprise-agent-runtime"
                ),
                "workspace_id": str(goal.workspace_id),
                "task_id": str(task_id),
                "active_plan_id": str(plan.plan_id),
                "resume_token": f"initial:{task_id}",
                "runtime_version": snapshot.runtime_version,
            },
        )
        session.add(
            AuditRow(
                workspace_id=goal.workspace_id,
                actor_type="AGENT",
                actor_id=identity.user_id,
                action="plan.created.from_skill",
                task_id=task_id,
                resource_type="PLAN",
                resource_id=plan.plan_id,
                result={
                    "skill": package.manifest.name,
                    "skill_version": package.manifest.version,
                    "plan_version": plan.version,
                },
                trace_id=trace_id,
            )
        )
    session.add(new_task_budget(row.id, goal))
    session.add(RuntimeGuardRow(task_id=row.id, workspace_id=goal.workspace_id))
    session.add(
        AuditRow(
            workspace_id=goal.workspace_id,
            actor_type="USER",
            actor_id=identity.user_id,
            action="task.created",
            task_id=row.id,
            resource_type="TASK_RUN",
            resource_id=row.id,
            result={"state": row.state, "version": row.version},
            trace_id=trace_id,
        )
    )
    await session.flush()
    return task_view(row)


async def list_tasks(
    session: AsyncSession, identity: Identity, workspace_id: UUID
) -> tuple[TaskView, ...]:
    await get_workspace(session, identity, workspace_id)
    rows = (
        await session.scalars(
            select(TaskRow)
            .where(TaskRow.workspace_id == workspace_id, TaskRow.goal_payload.is_not(None))
            .order_by(TaskRow.created_at, TaskRow.id)
        )
    ).all()
    return tuple(task_view(row) for row in rows)


async def get_task(
    session: AsyncSession, identity: Identity, workspace_id: UUID, task_id: UUID
) -> TaskView:
    await get_workspace(session, identity, workspace_id)
    row = await session.scalar(
        select(TaskRow).where(TaskRow.workspace_id == workspace_id, TaskRow.id == task_id)
    )
    if row is None:
        raise DomainError(FailureCode.POLICY_DENIED, "task unavailable")
    return task_view(row)
