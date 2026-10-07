import asyncio
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy


@dataclass(frozen=True)
class DurableTaskInput:
    task_id: str
    workspace_id: str
    step_keys: list[str]
    failure_hold_seconds: float = 0


@dataclass(frozen=True)
class DurableTaskResult:
    task_id: str
    completed_steps: list[str]
    state: str = "COMPLETED"


@dataclass(frozen=True)
class ApprovalWaitInput:
    workspace_id: str
    approval_id: str


@dataclass(frozen=True)
class ApprovalWaitResult:
    approval_id: str
    decision: str
    version: int


@dataclass(frozen=True)
class KnowledgeIngestionInput:
    workspace_id: str
    ingestion_id: str


@dataclass(frozen=True)
class KnowledgeIngestionResult:
    ingestion_id: str
    document_version_id: str
    state: str = "SUCCEEDED"


@dataclass(frozen=True)
class PlanExecutionInput:
    task_id: str
    # Defaults preserve decoding for workflows started before continuation
    # binding was added. The binding activity reconstructs missing values from
    # canonical PostgreSQL state before any governed step can run.
    active_plan_id: str = ""
    resume_token: str = ""
    runtime_version: str = ""


@dataclass(frozen=True)
class PlanExecutionResult:
    task_id: str
    state: str


PLAN_HISTORY_EVENT_LIMIT = 10_000
PLAN_WORKFLOW_AGE_LIMIT = timedelta(days=1)
PLAN_COMPLETED_STEP_LIMIT = 100
PLAN_REPLAN_LIMIT = 20


def _plan_history_limit_reached(
    *,
    history_event_count: int,
    workflow_age: timedelta,
    completed_step_count: int,
    replan_count: int,
    server_suggested: bool,
) -> bool:
    return (
        server_suggested
        or history_event_count >= PLAN_HISTORY_EVENT_LIMIT
        or workflow_age >= PLAN_WORKFLOW_AGE_LIMIT
        or completed_step_count >= PLAN_COMPLETED_STEP_LIMIT
        or replan_count >= PLAN_REPLAN_LIMIT
    )


@workflow.defn
class KnowledgeIngestionWorkflow:
    @workflow.run
    async def run(self, value: KnowledgeIngestionInput) -> KnowledgeIngestionResult:
        result = await workflow.execute_activity(
            "ingest_knowledge_document",
            {
                "workspace_id": value.workspace_id,
                "ingestion_id": value.ingestion_id,
            },
            start_to_close_timeout=timedelta(minutes=30),
            heartbeat_timeout=timedelta(seconds=15),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=2),
                maximum_interval=timedelta(seconds=15),
                # ModelGateway already performs three bounded transport attempts.
                # A second durable Activity attempt handles process/transient failures
                # without multiplying a missing dependency into fifteen requests.
                maximum_attempts=2,
            ),
        )
        return KnowledgeIngestionResult(
            ingestion_id=result["ingestion_id"],
            document_version_id=result["document_version_id"],
        )


@workflow.defn
class ApprovalWaitWorkflow:
    """Signal wakes execution; PostgreSQL remains the authority for the decision."""

    def __init__(self) -> None:
        self._notified = False

    @workflow.signal
    def approval_decided(self) -> None:
        self._notified = True

    @workflow.query
    def waiting_for_approval(self) -> bool:
        return not self._notified

    @workflow.run
    async def run(self, value: ApprovalWaitInput) -> ApprovalWaitResult:
        await workflow.wait_condition(lambda: self._notified)
        result = await workflow.execute_activity(
            "load_approval_decision",
            {"workspace_id": value.workspace_id, "approval_id": value.approval_id},
            start_to_close_timeout=timedelta(seconds=10),
            heartbeat_timeout=timedelta(seconds=2),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(milliseconds=250),
                maximum_interval=timedelta(seconds=2),
                maximum_attempts=5,
            ),
        )
        return ApprovalWaitResult(
            approval_id=result["approval_id"],
            decision=result["decision"],
            version=result["version"],
        )


@workflow.defn
class PlanExecutionWorkflow:
    """Deterministic DAG scheduler; PostgreSQL remains canonical via Activities."""

    def __init__(self) -> None:
        self._paused = False
        self._cancel_requested = False

    @workflow.signal
    def pause(self) -> None:
        self._paused = True

    @workflow.signal
    def resume(self) -> None:
        self._paused = False

    @workflow.signal
    def cancel(self) -> None:
        self._cancel_requested = True
        self._paused = False

    @workflow.query
    def control_state(self) -> str:
        if self._cancel_requested:
            return "CANCELLING"
        return "PAUSED" if self._paused else "RUNNING"

    @workflow.run
    async def run(self, value: PlanExecutionInput) -> PlanExecutionResult:
        binding = await workflow.execute_activity(
            "load_plan_execution_binding",
            {
                "task_id": value.task_id,
                "active_plan_id": value.active_plan_id,
                "resume_token": value.resume_token,
                "runtime_version": value.runtime_version,
            },
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=RetryPolicy(maximum_attempts=5),
        )
        workspace_id = binding["workspace_id"]
        runtime_version = value.runtime_version or binding["runtime_version"]
        admission_lease = None
        try:
            while True:
                if self._paused and not self._cancel_requested:
                    if admission_lease is not None:
                        await workflow.execute_activity(
                            "release_plan_admission",
                            admission_lease,
                            start_to_close_timeout=timedelta(seconds=10),
                            retry_policy=RetryPolicy(maximum_attempts=5),
                        )
                        admission_lease = None
                    await workflow.wait_condition(
                        lambda: not self._paused or self._cancel_requested
                    )
                    continue
                if self._cancel_requested:
                    await workflow.execute_activity(
                        "finalize_task_cancel",
                        {"task_id": value.task_id, "workspace_id": workspace_id},
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=RetryPolicy(maximum_attempts=5),
                    )
                    await workflow.execute_activity(
                        "materialize_chat_task_turn",
                        {"task_id": value.task_id, "workspace_id": workspace_id},
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(maximum_attempts=5),
                    )
                    return PlanExecutionResult(task_id=value.task_id, state="CANCELLED")
                if admission_lease is None:
                    admission = await workflow.execute_activity(
                        "acquire_plan_admission",
                        {"task_id": value.task_id, "workspace_id": workspace_id},
                        start_to_close_timeout=timedelta(seconds=15),
                        heartbeat_timeout=timedelta(seconds=5),
                        retry_policy=RetryPolicy(maximum_attempts=5),
                    )
                    if not admission["allowed"]:
                        await workflow.sleep(timedelta(seconds=1))
                        continue
                    admission_lease = {
                        "lease_id": admission["lease_id"],
                        "keys": admission["keys"],
                        "expires_at_ms": admission["expires_at_ms"],
                    }
                else:
                    admission_lease = await workflow.execute_activity(
                        "heartbeat_plan_admission",
                        admission_lease,
                        start_to_close_timeout=timedelta(seconds=10),
                        heartbeat_timeout=timedelta(seconds=5),
                        retry_policy=RetryPolicy(maximum_attempts=5),
                    )
                state = await workflow.execute_activity(
                    "load_plan_execution_state",
                    {"task_id": value.task_id, "workspace_id": workspace_id},
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=RetryPolicy(maximum_attempts=5),
                )
                if state["state"] in {"COMPLETED", "CANCELLED", "FAILED"}:
                    await workflow.execute_activity(
                        "materialize_chat_task_turn",
                        {"task_id": value.task_id, "workspace_id": workspace_id},
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(maximum_attempts=5),
                    )
                    return PlanExecutionResult(
                        task_id=value.task_id,
                        state=state["state"],
                    )
                info = workflow.info()
                if _plan_history_limit_reached(
                    history_event_count=info.get_current_history_length(),
                    workflow_age=workflow.now() - info.workflow_start_time,
                    completed_step_count=state["completed_step_count"],
                    replan_count=state["replan_count"],
                    server_suggested=info.is_continue_as_new_suggested(),
                ):
                    resume_token = str(workflow.uuid4())
                    continuation = await workflow.execute_activity(
                        "persist_plan_continuation",
                        {
                            "workspace_id": workspace_id,
                            "task_id": value.task_id,
                            "active_plan_id": state["plan_id"],
                            "resume_token": resume_token,
                            "runtime_version": runtime_version,
                        },
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(maximum_attempts=5),
                    )
                    await workflow.execute_activity(
                        "release_plan_admission",
                        admission_lease,
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=RetryPolicy(maximum_attempts=5),
                    )
                    admission_lease = None
                    workflow.continue_as_new(PlanExecutionInput(**continuation))
                if state["state"] == "RETRY_WAIT":
                    await workflow.execute_activity(
                        "release_plan_admission",
                        admission_lease,
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=RetryPolicy(maximum_attempts=5),
                    )
                    admission_lease = None
                    await workflow.sleep(
                        timedelta(seconds=max(0.05, state["retry_after_seconds"]))
                    )
                    continue
                if state["state"] == "WAITING_HITL":
                    approval_ids = state["approval_ids"]
                    if not approval_ids:
                        raise RuntimeError("canonical HITL state returned no approval")
                    await workflow.execute_activity(
                        "release_plan_admission",
                        admission_lease,
                        start_to_close_timeout=timedelta(seconds=10),
                        retry_policy=RetryPolicy(maximum_attempts=5),
                    )
                    admission_lease = None
                    for approval_id in approval_ids:
                        await workflow.execute_child_workflow(
                            ApprovalWaitWorkflow.run,
                            ApprovalWaitInput(
                                workspace_id=workspace_id,
                                approval_id=approval_id,
                            ),
                            id="approval-wait-" + approval_id,
                        )
                    continue
                if state["state"] == "REPLAN_REQUIRED":
                    result = await workflow.execute_activity(
                        "generate_and_apply_replan",
                        {
                            "workspace_id": workspace_id,
                            "task_id": value.task_id,
                            "plan_id": state["plan_id"],
                            "failed_step_keys": state["failed_step_keys"],
                            "trigger": state["replan_trigger"],
                            "reason": state["replan_reason"],
                        },
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=RetryPolicy(
                            initial_interval=timedelta(seconds=1),
                            maximum_interval=timedelta(seconds=10),
                            maximum_attempts=3,
                        ),
                    )
                    if result["status"] not in {"APPLIED", "ABORTED"}:
                        raise RuntimeError("Replan activity returned an invalid status")
                    continue
                ready_step_ids = state["ready_step_ids"]
                if not ready_step_ids:
                    raise RuntimeError("canonical plan returned no ready steps")
                calls = [
                    workflow.execute_activity(
                        "execute_governed_plan_step",
                        {
                            "workspace_id": workspace_id,
                            "task_id": value.task_id,
                            "step_id": step_id,
                            "attempt_id": str(workflow.uuid4()),
                        },
                        start_to_close_timeout=timedelta(minutes=5),
                        heartbeat_timeout=timedelta(seconds=15),
                        retry_policy=RetryPolicy(
                            initial_interval=timedelta(milliseconds=250),
                            maximum_interval=timedelta(seconds=5),
                            maximum_attempts=5,
                        ),
                    )
                    for step_id in ready_step_ids
                ]
                await asyncio.gather(*calls)
        finally:
            if admission_lease is not None:
                await workflow.execute_activity(
                    "release_plan_admission",
                    admission_lease,
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=RetryPolicy(maximum_attempts=5),
                )


@workflow.defn
class DurableTaskWorkflow:
    """Deterministic shell; all PostgreSQL and time-based work runs in activities."""

    def __init__(self) -> None:
        self._paused = False
        self._cancel_requested = False

    @workflow.signal
    def pause(self) -> None:
        self._paused = True

    @workflow.signal
    def resume(self) -> None:
        self._paused = False

    @workflow.signal
    def cancel(self) -> None:
        self._cancel_requested = True
        self._paused = False

    @workflow.query
    def control_state(self) -> str:
        if self._cancel_requested:
            return "CANCELLING"
        return "PAUSED" if self._paused else "RUNNING"

    @workflow.run
    async def run(self, value: DurableTaskInput) -> DurableTaskResult:
        completed: list[str] = []
        for index, step_key in enumerate(value.step_keys):
            await workflow.wait_condition(
                lambda: not self._paused or self._cancel_requested
            )
            if self._cancel_requested:
                await workflow.execute_activity(
                    "finalize_task_cancel",
                    {"task_id": value.task_id, "workspace_id": value.workspace_id},
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(milliseconds=250),
                        maximum_interval=timedelta(seconds=2),
                        maximum_attempts=5,
                    ),
                )
                return DurableTaskResult(
                    task_id=value.task_id,
                    completed_steps=completed,
                    state="CANCELLED",
                )
            command_id = workflow.uuid4()
            result = await workflow.execute_activity(
                "commit_domain_step",
                {
                    "command_id": str(command_id),
                    "task_id": value.task_id,
                    "workspace_id": value.workspace_id,
                    "step_key": step_key,
                    "final_step": index == len(value.step_keys) - 1,
                    "failure_hold_seconds": value.failure_hold_seconds if index == 0 else 0,
                },
                start_to_close_timeout=timedelta(seconds=45),
                heartbeat_timeout=timedelta(seconds=2),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(milliseconds=250),
                    maximum_interval=timedelta(seconds=2),
                    maximum_attempts=10,
                ),
            )
            if result["step_key"] != step_key:
                raise RuntimeError("activity returned the wrong durable step")
            completed.append(step_key)
        return DurableTaskResult(task_id=value.task_id, completed_steps=completed)
