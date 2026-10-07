import hashlib
import json
import os
from datetime import UTC, datetime
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.api.contracts import TaskView
from enterprise_agent.contracts.domain import (
    AnswerContract,
    CapabilityDescriptor,
    ContractVersion,
    ExecutionSnapshot,
    FailureCode,
    TaskGoal,
    TaskState,
    VersionRef,
)
from enterprise_agent.domain.states import DomainError, transition
from enterprise_agent.governance.secrets import SecretCipher
from enterprise_agent.identity.contracts import Identity
from enterprise_agent.identity.service import get_workspace
from enterprise_agent.models.settings import load_model_settings
from enterprise_agent.runtime.budgets import new_task_budget
from enterprise_agent.runtime.skill_planner import (
    build_skill_plan,
    capability_version_ref,
    resolve_skill_capabilities,
    skill_version_ref,
)
from enterprise_agent.storage.completion import (
    create_contract_version,
    create_intended_requirement_supports,
    create_requirement,
    requirements_for_contract,
)
from enterprise_agent.storage.model_configs import (
    default_model_version_ref,
    load_chat_model_gateway_chain,
)
from enterprise_agent.storage.outbox import enqueue
from enterprise_agent.storage.plans import persist_plan, persist_snapshot
from enterprise_agent.storage.skills import load_enabled_skill
from enterprise_agent.storage.steps import materialize_plan_steps
from enterprise_agent.storage.tables import AuditRow, RuntimeGuardRow, TaskRow


def version_ref(identifier: str, version: str) -> VersionRef:
    digest = hashlib.sha256(f"{identifier}:{version}".encode()).hexdigest()
    return VersionRef(identifier=identifier, version=version, sha256=digest)


def research_task_id(
    workspace_id: UUID,
    conversation_id: UUID,
    user_message_id: UUID,
) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"conversation-research-task:{workspace_id}:{conversation_id}:{user_message_id}",
    )


async def _selected_model_version_refs(
    session: AsyncSession, goal: TaskGoal
) -> tuple[VersionRef, ...] | None:
    if not goal.user_context.strip():
        return None
    try:
        context = json.loads(goal.user_context)
    except json.JSONDecodeError:
        return None
    if not isinstance(context, dict):
        return None
    primary_value = context.get("model_config_id")
    fallback_value = context.get("fallback_model_config_id")
    if primary_value is None:
        if fallback_value is not None:
            raise DomainError(
                FailureCode.VALIDATION_FAILED,
                "fallback model configuration requires a selected primary model",
            )
        return None
    try:
        primary_config_id = UUID(str(primary_value))
        fallback_config_id = None if fallback_value is None else UUID(str(fallback_value))
    except ValueError:
        raise DomainError(
            FailureCode.VALIDATION_FAILED,
            "selected model configuration is invalid",
        ) from None
    master_key = os.environ.get("SECRET_MASTER_KEY")
    if not master_key:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "secret master key is not configured",
        )
    chain = await load_chat_model_gateway_chain(
        session,
        goal.workspace_id,
        primary_config_id,
        fallback_config_id,
        SecretCipher(master_key),
    )
    return tuple(
        version_ref(
            f"model.config:{row.id}",
            f"{row.version}:{config.provider}:{config.model}",
        )
        for config, _, row in chain
    )


def current_snapshot(
    workspace_id: UUID,
    *,
    skill_versions: tuple[VersionRef, ...] = (),
    capability_versions: tuple[VersionRef, ...] = (),
    model_config_versions: tuple[VersionRef, ...] | None = None,
) -> ExecutionSnapshot:
    runtime_version = "0.1.0"
    frozen_model_versions: tuple[VersionRef, ...]
    if model_config_versions is None:
        default_model, _ = load_model_settings()
        frozen_model_versions = (default_model_version_ref(default_model),)
    else:
        frozen_model_versions = model_config_versions
    return ExecutionSnapshot(
        snapshot_id=uuid4(),
        workspace_id=workspace_id,
        runtime_version=runtime_version,
        workflow_definition_version="1.0.0",
        planner_prompt_version=version_ref("planner", "1.0.0"),
        router_prompt_version=version_ref("router", "1.0.0"),
        reviewer_prompt_version=version_ref("reviewer", "1.0.0"),
        model_config_versions=frozen_model_versions,
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


async def _bootstrap_completion_contract(
    session: AsyncSession,
    *,
    task_id: UUID,
    goal: TaskGoal,
    created_by: UUID,
    created_at: datetime,
) -> ContractVersion:
    context: dict[str, object] = {}
    if goal.user_context.strip():
        try:
            parsed = json.loads(goal.user_context)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            context = parsed
    answer_contract_payload: dict[str, object] | None = None
    for candidate in (
        context.get("research_plan"),
        context.get("agent_decision"),
        context,
    ):
        if isinstance(candidate, dict) and isinstance(candidate.get("answer_contract"), dict):
            answer_contract_payload = candidate["answer_contract"]
            break
    answer_contract = (
        AnswerContract.model_validate(answer_contract_payload)
        if answer_contract_payload is not None
        else None
    )
    plan_context = context.get("research_plan")
    entity_scope = (
        tuple(
            item
            for item in plan_context["entity_scope"]
            if isinstance(item, str) and item.strip()
        )
        if isinstance(plan_context, dict) and isinstance(plan_context.get("entity_scope"), list)
        else ()
    )
    temporal_scope = () if answer_contract is None else (answer_contract.freshness,)
    contract_policy: dict[str, object] = {
        "allow_partial": (
            answer_contract is None
            or answer_contract.partial_answer_policy == "ALLOW_SUPPORTED_ONLY"
        ),
        "version": "1",
    }
    if answer_contract_payload is not None:
        contract_policy["answer_contract"] = answer_contract_payload
    contract_id = uuid5(NAMESPACE_URL, f"task-contract:{goal.workspace_id}:{task_id}:1")
    content = {
        "goal": goal.goal,
        "constraints": goal.constraints,
        "deliverables": goal.deliverables,
        "completion_policy": contract_policy,
    }
    content_hash = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    contract = ContractVersion(
        id=contract_id,
        workspace_id=goal.workspace_id,
        task_id=task_id,
        version=1,
        content_hash=content_hash,
        goal=goal.goal,
        constraints=goal.constraints,
        completion_policy=cast(dict[str, JsonValue], contract_policy),
        created_by=created_by,
        created_at=created_at,
    )
    await create_contract_version(session, contract)
    for requirement in requirements_for_contract(
        contract,
        legacy_descriptions=goal.deliverables,
        entity_scope=entity_scope,
        temporal_scope=temporal_scope,
    ):
        await create_requirement(session, requirement)
    return contract


async def create_task(
    session: AsyncSession,
    identity: Identity,
    goal: TaskGoal,
    trace_id: str,
    capabilities: tuple[CapabilityDescriptor, ...] = (),
    *,
    task_id: UUID | None = None,
) -> TaskView:
    await get_workspace(session, identity, goal.workspace_id)
    resolved_task_id = uuid4() if task_id is None else task_id
    if task_id is not None:
        existing = await session.scalar(
            select(TaskRow).where(
                TaskRow.workspace_id == goal.workspace_id,
                TaskRow.id == resolved_task_id,
            )
        )
        if existing is not None:
            if existing.created_by != identity.user_id or existing.goal_payload != goal.model_dump(
                mode="json"
            ):
                raise DomainError(
                    FailureCode.CONSISTENCY_VIOLATION,
                    "research task replay mismatch",
                )
            return task_view(existing)
    package = None
    selected_capabilities: tuple[CapabilityDescriptor, ...] = ()
    if goal.requested_skill is not None:
        package = await load_enabled_skill(session, goal.workspace_id, goal.requested_skill)
        selected_capabilities = resolve_skill_capabilities(package, capabilities)
    selected_models = await _selected_model_version_refs(session, goal)
    snapshot = current_snapshot(
        goal.workspace_id,
        skill_versions=() if package is None else (skill_version_ref(package),),
        capability_versions=tuple(capability_version_ref(item) for item in selected_capabilities),
        model_config_versions=selected_models,
    )
    await persist_snapshot(session, snapshot)
    row = TaskRow(
        id=resolved_task_id,
        workspace_id=goal.workspace_id,
        created_by=identity.user_id,
        state=TaskState.CREATED.value,
        version=1,
        execution_snapshot_id=snapshot.snapshot_id,
        goal_payload=goal.model_dump(mode="json"),
    )
    session.add(row)
    await session.flush()
    await _bootstrap_completion_contract(
        session,
        task_id=row.id,
        goal=goal,
        created_by=identity.user_id,
        created_at=datetime.now(UTC),
    )
    if package is not None:
        row.state = transition(TaskState.CREATED, TaskState.PLANNING).value
        row.version += 1
        plan = build_skill_plan(
            task_id=resolved_task_id,
            execution_snapshot_id=snapshot.snapshot_id,
            goal=goal,
            package=package,
        )
        await persist_plan(session, plan)
        await materialize_plan_steps(session, plan)
        await create_intended_requirement_supports(session, plan)
        row.state = transition(TaskState.PLANNING, TaskState.READY).value
        row.version += 1
        await enqueue(
            session,
            uuid5(
                NAMESPACE_URL,
                f"task-execution-requested:{goal.workspace_id}:{resolved_task_id}",
            ),
            goal.workspace_id,
            "TASK_RUN",
            resolved_task_id,
            "task.execution.requested",
            {
                "workflow_id": f"task-{resolved_task_id}",
                "task_queue": os.environ.get("TEMPORAL_TASK_QUEUE", "enterprise-agent-runtime"),
                "workspace_id": str(goal.workspace_id),
                "task_id": str(resolved_task_id),
                "active_plan_id": str(plan.plan_id),
                "resume_token": f"initial:{resolved_task_id}",
                "runtime_version": snapshot.runtime_version,
            },
        )
        session.add(
            AuditRow(
                workspace_id=goal.workspace_id,
                actor_type="AGENT",
                actor_id=identity.user_id,
                action="plan.created.from_skill",
                task_id=resolved_task_id,
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
