import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.contracts.domain import (
    CapabilityRequirement,
    Evidence,
    ExecutionSnapshot,
    Failure,
    FailureCode,
    Plan,
    PlanStepSpec,
    Risk,
    TaskGoal,
    VersionRef,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.runtime.budgets import new_task_budget
from enterprise_agent.storage.attempts import (
    acquire_attempt,
    commit_evidence,
    fail_attempt,
    list_task_attempts,
)
from enterprise_agent.storage.plans import persist_plan, persist_snapshot
from enterprise_agent.storage.steps import list_task_steps, materialize_plan_steps
from enterprise_agent.storage.tables import RuntimeGuardRow, StepRow, TaskRow, Workspace


def version_ref(identifier: str) -> VersionRef:
    return VersionRef(identifier=identifier, version="1", sha256="c" * 64)


def snapshot_for(workspace_id) -> ExecutionSnapshot:
    return ExecutionSnapshot(
        snapshot_id=uuid4(),
        workspace_id=workspace_id,
        runtime_version="test",
        workflow_definition_version="1",
        planner_prompt_version=version_ref("planner"),
        router_prompt_version=version_ref("router"),
        reviewer_prompt_version=version_ref("reviewer"),
        model_config_versions=(version_ref("model"),),
        skill_versions=(),
        capability_versions=(),
        policy_version=version_ref("policy"),
        knowledge_document_refs=(),
        context_policy_version=version_ref("context"),
        schema_versions=(version_ref("schema"),),
        feature_flags=frozenset(),
        created_at=datetime.now(UTC),
    )


def spec(step_key: str, dependencies: tuple[str, ...]) -> PlanStepSpec:
    return PlanStepSpec(
        step_key=step_key,
        objective=f"Execute {step_key}",
        dependencies=dependencies,
        capability_requirements=(
            CapabilityRequirement(capability_id="test.read", version_range=">=1,<2"),
        ),
        expected_output="evidence",
        success_criteria=("evidence persisted",),
        risk_hint=Risk.READ,
        execution_mode="CAPABILITY",
        parallelizable=True,
        max_attempts=3,
    )


def evidence(workspace_id, task_id, step_id, attempt_id, label: str) -> Evidence:
    return Evidence(
        evidence_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        step_id=step_id,
        source_type="MCP",
        source_id=label,
        locator=f"step:{label}",
        content=f"{label} completed",
        content_hash="a" * 64,
        source_hash="b" * 64,
        source_version="1",
        confidence=1,
        relevance=1,
        retrieved_at=datetime.now(UTC),
        generated_by=attempt_id,
        derived_from=(),
    )


@pytest.mark.asyncio
async def test_plan_dag_unlocks_join_and_completes_task_atomically() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    workspace_id, task_id, plan_id = uuid4(), uuid4(), uuid4()
    snapshot = snapshot_for(workspace_id)
    goal = TaskGoal(
        goal="exercise parallel DAG",
        constraints=(),
        deliverables=("result",),
        workspace_id=workspace_id,
        time_budget_sec=600,
        token_budget=1000,
        cost_budget=1,
    )
    plan = Plan(
        plan_id=plan_id,
        task_id=task_id,
        workspace_id=workspace_id,
        execution_snapshot_id=snapshot.snapshot_id,
        version=1,
        goal=goal.goal,
        constraints=goal.constraints,
        deliverables=goal.deliverables,
        steps=(
            spec("source-a", ()),
            spec("source-b", ()),
            spec("join", ("source-a", "source-b")),
        ),
    )
    async with sessions.begin() as session:
        session.add(Workspace(id=workspace_id, name="plan-lifecycle"))
        await session.flush()
        await persist_snapshot(session, snapshot)
        session.add(
            TaskRow(
                id=task_id,
                workspace_id=workspace_id,
                state="READY",
                version=1,
                execution_snapshot_id=snapshot.snapshot_id,
                goal_payload=goal.model_dump(mode="json"),
            )
        )
        await session.flush()
        await persist_plan(session, plan)
        await materialize_plan_steps(session, plan)
        session.add(new_task_budget(task_id, goal))
        session.add(RuntimeGuardRow(task_id=task_id, workspace_id=workspace_id))

    async def complete(step_key: str) -> None:
        async with sessions.begin() as session:
            steps = await list_task_steps(session, workspace_id, task_id)
            step = next(item for item in steps if item.spec.step_key == step_key)
            attempt_id = uuid4()
            attempt = await acquire_attempt(
                session,
                workspace_id,
                step.step_id,
                attempt_id,
                "dag-worker",
                "trace-dag",
            )
            await commit_evidence(
                session,
                workspace_id,
                attempt_id,
                attempt.fencing_token,
                (evidence(workspace_id, task_id, step.step_id, attempt_id, step_key),),
            )

    await asyncio.wait_for(
        asyncio.gather(complete("source-a"), complete("source-b")),
        timeout=10,
    )
    async with sessions() as session:
        states = {
            item.spec.step_key: item.state.value
            for item in await list_task_steps(session, workspace_id, task_id)
        }
        task = await session.get(TaskRow, task_id)
    assert states == {"join": "READY", "source-a": "COMPLETED", "source-b": "COMPLETED"}
    assert task is not None and task.state == "RUNNING"

    await complete("join")
    async with sessions() as session:
        task = await session.get(TaskRow, task_id)
        states = {
            item.spec.step_key: item.state.value
            for item in await list_task_steps(session, workspace_id, task_id)
        }
    assert set(states.values()) == {"COMPLETED"}
    assert task is not None and task.state == "COMPLETED" and task.version == 3
    await engine.dispose()


@pytest.mark.asyncio
async def test_classified_failure_waits_then_stops_at_plan_max_attempts() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    workspace_id, task_id, step_id = uuid4(), uuid4(), uuid4()
    retry_spec = spec("retry-step", ()).model_copy(update={"max_attempts": 2})
    async with sessions.begin() as session:
        session.add(Workspace(id=workspace_id, name="attempt-retry"))
        await session.flush()
        session.add(TaskRow(id=task_id, workspace_id=workspace_id, state="RUNNING"))
        await session.flush()
        session.add(RuntimeGuardRow(task_id=task_id, workspace_id=workspace_id))
        session.add(
            StepRow(
                id=step_id,
                workspace_id=workspace_id,
                task_id=task_id,
                state="READY",
                spec_payload=retry_spec.model_dump(mode="json"),
            )
        )

    first_id = uuid4()
    async with sessions.begin() as session:
        first = await acquire_attempt(
            session, workspace_id, step_id, first_id, "retry-worker", "trace-retry"
        )
        state = await fail_attempt(
            session,
            workspace_id,
            first_id,
            first.fencing_token,
            Failure(
                code=FailureCode.CAPABILITY_TIMEOUT,
                safe_detail="read timed out",
                trace_id="trace-retry",
            ),
            retryable=True,
            retry_delay_seconds=60,
        )
    assert state.value == "RETRY_WAIT"
    with pytest.raises(DomainError, match="retry is not due"):
        async with sessions.begin() as session:
            await acquire_attempt(
                session, workspace_id, step_id, uuid4(), "retry-worker", "trace-retry"
            )

    async with sessions.begin() as session:
        step = await session.get(StepRow, step_id)
        assert step is not None
        step.retry_available_at = datetime.now(UTC) - timedelta(seconds=1)
    second_id = uuid4()
    async with sessions.begin() as session:
        second = await acquire_attempt(
            session, workspace_id, step_id, second_id, "retry-worker", "trace-retry"
        )
        state = await fail_attempt(
            session,
            workspace_id,
            second_id,
            second.fencing_token,
            Failure(
                code=FailureCode.CAPABILITY_TIMEOUT,
                safe_detail="read timed out again",
                trace_id="trace-retry",
            ),
            retryable=True,
            retry_delay_seconds=0.1,
        )
    assert state.value == "FAILED"
    async with sessions() as session:
        attempts = await list_task_attempts(session, workspace_id, task_id)
        step = await session.get(StepRow, step_id)
    assert [item.failure.code for item in attempts if item.failure is not None] == [
        FailureCode.CAPABILITY_TIMEOUT,
        FailureCode.CAPABILITY_TIMEOUT,
    ]
    assert step is not None and step.state == "FAILED" and step.retry_available_at is None
    await engine.dispose()
