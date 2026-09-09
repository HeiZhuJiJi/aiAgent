import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.contracts.domain import (
    CapabilityRequirement,
    ExecutionSnapshot,
    Plan,
    PlanStepSpec,
    Risk,
    VersionRef,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.plans import (
    load_plan_versions,
    load_snapshot,
    persist_plan,
    persist_snapshot,
)
from enterprise_agent.storage.tables import ExecutionSnapshotRow, PlanRow
from tests.integration.test_fencing import seed


@pytest.fixture
async def database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def version_ref(identifier: str) -> VersionRef:
    return VersionRef(identifier=identifier, version="1.0.0", sha256="a" * 64)


def snapshot(workspace_id, snapshot_id=None) -> ExecutionSnapshot:
    return ExecutionSnapshot(
        snapshot_id=snapshot_id or uuid4(),
        workspace_id=workspace_id,
        runtime_version="0.1.0",
        workflow_definition_version="1.0.0",
        planner_prompt_version=version_ref("planner"),
        router_prompt_version=version_ref("router"),
        reviewer_prompt_version=version_ref("reviewer"),
        model_config_versions=(version_ref("model"),),
        skill_versions=(),
        capability_versions=(version_ref("capability"),),
        policy_version=version_ref("policy"),
        knowledge_document_refs=(),
        context_policy_version=version_ref("context"),
        schema_versions=(version_ref("domain"),),
        feature_flags=frozenset(),
        created_at=datetime.now(UTC),
    )


def plan(workspace_id, task_id, snapshot_id, version=1, plan_id=None) -> Plan:
    step = PlanStepSpec(
        step_key="research",
        objective="collect evidence",
        capability_requirements=(
            CapabilityRequirement(capability_id="knowledge.read", version_range=">=1,<2"),
        ),
        expected_output="evidence",
        success_criteria=("grounded",),
        risk_hint=Risk.READ,
        execution_mode="CAPABILITY",
        parallelizable=True,
        max_attempts=3,
    )
    return Plan(
        plan_id=plan_id or uuid4(),
        task_id=task_id,
        workspace_id=workspace_id,
        execution_snapshot_id=snapshot_id,
        version=version,
        goal="research",
        constraints=(),
        deliverables=("report",),
        steps=(step,),
    )


async def test_snapshot_and_plan_replay_is_idempotent_and_versions_are_append_only(database):
    workspace_id, task_id, _ = await seed(database)
    frozen = snapshot(workspace_id)
    first = plan(workspace_id, task_id, frozen.snapshot_id)
    second = plan(workspace_id, task_id, frozen.snapshot_id, version=2)
    async with database.begin() as session:
        await persist_snapshot(session, frozen)
        await persist_snapshot(session, frozen)
        await persist_plan(session, first)
        await persist_plan(session, first)
        await persist_plan(session, second)
    async with database.begin() as session:
        restored_snapshot = await load_snapshot(session, workspace_id, frozen.snapshot_id)
        restored_plans = await load_plan_versions(session, workspace_id, task_id)
    assert restored_snapshot == frozen
    assert restored_plans == (first, second)
    with pytest.raises(DomainError, match="immutable snapshot mismatch"):
        async with database.begin() as session:
            await persist_snapshot(
                session, frozen.model_copy(update={"runtime_version": "changed"})
            )
    with pytest.raises(DomainError, match="plan version already exists"):
        async with database.begin() as session:
            await persist_plan(
                session,
                plan(workspace_id, task_id, frozen.snapshot_id, version=2, plan_id=uuid4()),
            )


@pytest.mark.parametrize(
    "statement",
    [
        update(ExecutionSnapshotRow).values(content_hash="b" * 64),
        delete(ExecutionSnapshotRow),
        update(PlanRow).values(version=99),
        delete(PlanRow),
    ],
)
async def test_database_rejects_snapshot_and_plan_mutation(database, statement):
    workspace_id, task_id, _ = await seed(database)
    frozen = snapshot(workspace_id)
    current = plan(workspace_id, task_id, frozen.snapshot_id)
    async with database.begin() as session:
        await persist_snapshot(session, frozen)
        await persist_plan(session, current)
    with pytest.raises(DBAPIError):
        async with database.begin() as session:
            await session.execute(
                statement.where(
                    statement.table.c.workspace_id == workspace_id,
                    statement.table.c.id
                    == (
                        frozen.snapshot_id
                        if statement.table.name == "execution_snapshots"
                        else current.plan_id
                    ),
                )
            )


async def test_plan_cannot_bind_snapshot_from_another_workspace(database):
    workspace_a, task_a, _ = await seed(database)
    workspace_b, _, _ = await seed(database)
    foreign = snapshot(workspace_b)
    async with database.begin() as session:
        await persist_snapshot(session, foreign)
    with pytest.raises(DomainError, match="snapshot unavailable"):
        async with database.begin() as session:
            await persist_plan(session, plan(workspace_a, task_a, foreign.snapshot_id))
