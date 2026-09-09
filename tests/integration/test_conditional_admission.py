import hashlib
import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.contracts.domain import (
    CapabilityRequirement,
    Evidence,
    ExecutionSnapshot,
    Plan,
    PlanStepSpec,
    Risk,
    TaskGoal,
    VersionRef,
)
from enterprise_agent.runtime.activities import execute_governed_plan_step
from enterprise_agent.runtime.budgets import new_task_budget
from enterprise_agent.storage.attempts import (
    acquire_attempt,
    commit_evidence,
    list_task_evidence,
)
from enterprise_agent.storage.plans import persist_plan, persist_snapshot
from enterprise_agent.storage.steps import materialize_plan_steps, plan_step_id
from enterprise_agent.storage.tables import (
    ApprovalRow,
    AuditRow,
    CapabilityCallRow,
    OperationRow,
    RuntimeGuardRow,
    StepRow,
    TaskRow,
    UserRow,
    Workspace,
    WorkspaceMemberRow,
)


@pytest.fixture
async def database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    yield sessions
    await engine.dispose()


def _ref(identifier: str) -> VersionRef:
    return VersionRef(identifier=identifier, version="1.0.0", sha256="a" * 64)


async def test_ineligible_supplier_finishes_with_audited_noop_and_no_external_effect(
    database, monkeypatch
):
    workspace_id = uuid4()
    task_id = uuid4()
    snapshot_id = uuid4()
    plan_id = uuid4()
    actor_id = uuid4()
    supplier_id = uuid4()
    goal = TaskGoal(
        goal="Assess supplier and admit only when eligible",
        constraints=("never admit a rejected supplier",),
        deliverables=("risk report",),
        workspace_id=workspace_id,
        time_budget_sec=600,
        token_budget=10_000,
        cost_budget=10,
        requested_skill="supplier-risk",
        user_context=json.dumps({"supplier_id": str(supplier_id)}),
    )
    snapshot = ExecutionSnapshot(
        snapshot_id=snapshot_id,
        workspace_id=workspace_id,
        runtime_version="0.1.0",
        workflow_definition_version="1.0.0",
        planner_prompt_version=_ref("planner"),
        router_prompt_version=_ref("router"),
        reviewer_prompt_version=_ref("reviewer"),
        model_config_versions=(_ref("model"),),
        skill_versions=(_ref("supplier-risk"),),
        capability_versions=(
            _ref("model.structured_output"),
            _ref("procurement.create_supplier_admission_request"),
        ),
        policy_version=_ref("policy"),
        knowledge_document_refs=(),
        context_policy_version=_ref("context"),
        schema_versions=(_ref("domain"),),
        feature_flags=frozenset(),
        created_at=datetime.now(UTC),
    )
    analyze = PlanStepSpec(
        step_key="analyze-risk",
        objective="Produce the governed eligibility decision",
        capability_requirements=(
            CapabilityRequirement(
                capability_id="model.structured_output", version_range=">=1,<2"
            ),
        ),
        expected_output="structured decision",
        success_criteria=("decision cites evidence",),
        risk_hint=Risk.READ,
        execution_mode="CAPABILITY",
        parallelizable=False,
        max_attempts=2,
    )
    admission = PlanStepSpec(
        step_key="create-admission-if-eligible",
        objective="Create admission only for an eligible supplier",
        dependencies=("analyze-risk",),
        capability_requirements=(
            CapabilityRequirement(
                capability_id="procurement.create_supplier_admission_request",
                version_range=">=1,<2",
            ),
        ),
        expected_output="admission identity or governed no-op",
        success_criteria=("rejected supplier creates no external effect",),
        risk_hint=Risk.EXTERNAL_SIDE_EFFECT,
        execution_mode="CAPABILITY",
        parallelizable=False,
        max_attempts=1,
    )
    plan = Plan(
        plan_id=plan_id,
        task_id=task_id,
        workspace_id=workspace_id,
        execution_snapshot_id=snapshot_id,
        version=1,
        goal=goal.goal,
        constraints=goal.constraints,
        deliverables=goal.deliverables,
        steps=(analyze, admission),
    )
    async with database.begin() as session:
        session.add(Workspace(id=workspace_id, name="conditional-admission"))
        session.add(
            UserRow(
                id=actor_id,
                email=f"{actor_id}@example.test",
                normalized_email=f"{actor_id}@example.test",
                password_hash="integration-test",
            )
        )
        await session.flush()
        session.add(
            WorkspaceMemberRow(
                workspace_id=workspace_id,
                user_id=actor_id,
                role="WORKSPACE_OWNER",
            )
        )
        await persist_snapshot(session, snapshot)
        session.add(
            TaskRow(
                id=task_id,
                workspace_id=workspace_id,
                state="READY",
                execution_snapshot_id=snapshot_id,
                goal_payload=goal.model_dump(mode="json"),
                created_by=actor_id,
            )
        )
        await session.flush()
        session.add(new_task_budget(task_id, goal))
        session.add(RuntimeGuardRow(task_id=task_id, workspace_id=workspace_id))
        await persist_plan(session, plan)
        await materialize_plan_steps(session, plan)

    analysis_attempt_id = uuid4()
    analysis_step_id = plan_step_id(plan_id, analyze.step_key)
    content = json.dumps(
        {
            "decision": "REJECT",
            "evidence_sufficient": True,
            "risk_level": "HIGH",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(content.encode()).hexdigest()
    async with database.begin() as session:
        attempt = await acquire_attempt(
            session,
            workspace_id,
            analysis_step_id,
            analysis_attempt_id,
            "analysis-worker",
            "trace-analysis",
        )
        await commit_evidence(
            session,
            workspace_id,
            analysis_attempt_id,
            attempt.fencing_token,
            (
                Evidence(
                    evidence_id=uuid4(),
                    workspace_id=workspace_id,
                    task_id=task_id,
                    step_id=analysis_step_id,
                    source_type="DERIVED",
                    source_id="model.structured_output",
                    locator="builtin:model.structured_output",
                    content=content,
                    content_hash=digest,
                    source_hash=digest,
                    source_version="1.0.0",
                    confidence=1,
                    relevance=1,
                    retrieved_at=datetime.now(UTC),
                    generated_by=analysis_attempt_id,
                    derived_from=(),
                ),
            ),
        )

    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setattr(
        "enterprise_agent.runtime.activities._activity_trace_id",
        lambda: "trace-conditional-admission",
    )
    monkeypatch.setattr(
        "enterprise_agent.runtime.activities.activity.heartbeat",
        lambda *args: None,
    )
    admission_step_id = plan_step_id(plan_id, admission.step_key)
    result = await execute_governed_plan_step(
        {
            "workspace_id": str(workspace_id),
            "task_id": str(task_id),
            "step_id": str(admission_step_id),
            "attempt_id": str(uuid4()),
        }
    )

    assert result["state"] == "COMPLETED"
    async with database() as session:
        task = await session.get(TaskRow, task_id)
        step = await session.get(StepRow, admission_step_id)
        evidence = await list_task_evidence(session, workspace_id, task_id)
        operation_count = await session.scalar(
            select(func.count()).select_from(OperationRow).where(
                OperationRow.workspace_id == workspace_id,
                OperationRow.step_id == admission_step_id,
            )
        )
        call_count = await session.scalar(
            select(func.count()).select_from(CapabilityCallRow).where(
                CapabilityCallRow.workspace_id == workspace_id,
                CapabilityCallRow.step_id == admission_step_id,
            )
        )
        approval_count = await session.scalar(
            select(func.count()).select_from(ApprovalRow).where(
                ApprovalRow.workspace_id == workspace_id,
                ApprovalRow.step_id == admission_step_id,
            )
        )
        audit_count = await session.scalar(
            select(func.count()).select_from(AuditRow).where(
                AuditRow.workspace_id == workspace_id,
                AuditRow.step_id == admission_step_id,
                AuditRow.action == "admission.noop",
            )
        )
    assert task is not None and task.state == "COMPLETED"
    assert step is not None and step.state == "COMPLETED"
    assert any(item.source_id == "runtime.conditional_admission" for item in evidence)
    assert operation_count == call_count == approval_count == 0
    assert audit_count == 1
