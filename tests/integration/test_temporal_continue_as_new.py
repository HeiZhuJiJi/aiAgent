from uuid import uuid4

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from enterprise_agent.runtime.workflows import (
    PLAN_COMPLETED_STEP_LIMIT,
    PlanExecutionInput,
    PlanExecutionWorkflow,
)


class ContinuationProbe:
    binding_calls = 0
    state_calls = 0
    persist_calls = 0


@activity.defn(name="load_plan_execution_binding")
async def probe_binding(value: dict[str, str]) -> dict[str, str]:
    assert set(value) == {
        "task_id",
        "active_plan_id",
        "resume_token",
        "runtime_version",
    }
    ContinuationProbe.binding_calls += 1
    return {
        "workspace_id": str(uuid4()),
        "active_plan_id": value["active_plan_id"]
        or "00000000-0000-0000-0000-000000000001",
        "resume_token": value["resume_token"] or "initial:legacy",
        "runtime_version": value["runtime_version"] or "0.1.0",
    }


@activity.defn(name="load_plan_execution_state")
async def probe_state(value: dict[str, str]) -> dict[str, object]:
    ContinuationProbe.state_calls += 1
    terminal = ContinuationProbe.state_calls > 1
    return {
        "state": "COMPLETED" if terminal else "READY",
        "ready_step_ids": [],
        "retry_after_seconds": 0,
        "approval_ids": [],
        "plan_id": "00000000-0000-0000-0000-000000000001",
        "completed_step_count": PLAN_COMPLETED_STEP_LIMIT,
        "replan_count": 0,
    }


@activity.defn(name="persist_plan_continuation")
async def probe_persist(value: dict[str, str]) -> dict[str, str]:
    assert set(value) == {
        "workspace_id",
        "task_id",
        "active_plan_id",
        "resume_token",
        "runtime_version",
    }
    ContinuationProbe.persist_calls += 1
    return {
        "task_id": value["task_id"],
        "active_plan_id": value["active_plan_id"],
        "resume_token": value["resume_token"],
        "runtime_version": value["runtime_version"],
    }


async def test_real_temporal_continues_with_only_frozen_minimal_payload() -> None:
    ContinuationProbe.binding_calls = 0
    ContinuationProbe.state_calls = 0
    ContinuationProbe.persist_calls = 0
    temporal = await Client.connect("127.0.0.1:57233")
    task_queue = "continue-as-new-" + str(uuid4())
    async with Worker(
        temporal,
        task_queue=task_queue,
        workflows=[PlanExecutionWorkflow],
        activities=[probe_binding, probe_state, probe_persist],
    ):
        result = await temporal.execute_workflow(
            PlanExecutionWorkflow.run,
            PlanExecutionInput(
                task_id=str(uuid4()),
                active_plan_id="00000000-0000-0000-0000-000000000001",
                resume_token="initial:probe",
                runtime_version="0.1.0",
            ),
            id="continue-as-new-test-" + str(uuid4()),
            task_queue=task_queue,
        )

    assert result.state == "COMPLETED"
    assert ContinuationProbe.persist_calls == 1
    assert ContinuationProbe.binding_calls == 2
    assert ContinuationProbe.state_calls == 2


async def test_real_temporal_decodes_legacy_plan_input() -> None:
    ContinuationProbe.binding_calls = 0
    ContinuationProbe.state_calls = 1
    ContinuationProbe.persist_calls = 0
    temporal = await Client.connect("127.0.0.1:57233")
    task_queue = "legacy-input-" + str(uuid4())
    async with Worker(
        temporal,
        task_queue=task_queue,
        workflows=[PlanExecutionWorkflow],
        activities=[probe_binding, probe_state, probe_persist],
    ):
        result = await temporal.execute_workflow(
            PlanExecutionWorkflow.run,
            PlanExecutionInput(task_id=str(uuid4())),
            id="legacy-input-test-" + str(uuid4()),
            task_queue=task_queue,
        )

    assert result.state == "COMPLETED"
    assert ContinuationProbe.binding_calls == 1
    assert ContinuationProbe.persist_calls == 0
