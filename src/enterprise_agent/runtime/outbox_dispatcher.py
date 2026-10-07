import asyncio
import os
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from enterprise_agent.contracts.domain import FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.runtime.workflows import (
    ApprovalWaitWorkflow,
    DurableTaskWorkflow,
    KnowledgeIngestionInput,
    KnowledgeIngestionWorkflow,
    PlanExecutionInput,
    PlanExecutionWorkflow,
)
from enterprise_agent.storage.outbox import OutboxLease, claim_next, mark_published, reschedule


class Publisher(Protocol):
    async def __call__(self, client: Client, lease: OutboxLease) -> None: ...


async def publish_temporal(client: Client, lease: OutboxLease) -> None:
    if lease.event_type == "task.execution.requested":
        workflow_id = lease.payload.get("workflow_id")
        task_queue = lease.payload.get("task_queue")
        workspace_id = lease.payload.get("workspace_id")
        task_id = lease.payload.get("task_id")
        active_plan_id = lease.payload.get("active_plan_id")
        resume_token = lease.payload.get("resume_token")
        runtime_version = lease.payload.get("runtime_version")
        if (
            not isinstance(workflow_id, str)
            or not workflow_id
            or not isinstance(task_queue, str)
            or not task_queue
            or not isinstance(workspace_id, str)
            or not workspace_id
            or not isinstance(task_id, str)
            or not task_id
            or not isinstance(active_plan_id, str)
            or not active_plan_id
            or not isinstance(resume_token, str)
            or not resume_token
            or not isinstance(runtime_version, str)
            or not runtime_version
        ):
            raise DomainError(
                FailureCode.VALIDATION_FAILED, "task execution event payload is incomplete"
            )
        try:
            await client.start_workflow(
                PlanExecutionWorkflow.run,
                PlanExecutionInput(
                    task_id=task_id,
                    active_plan_id=active_plan_id,
                    resume_token=resume_token,
                    runtime_version=runtime_version,
                ),
                id=workflow_id,
                task_queue=task_queue,
            )
        except WorkflowAlreadyStartedError:
            pass
        return
    if lease.event_type == "knowledge.ingestion.requested":
        workflow_id = lease.payload.get("workflow_id")
        task_queue = lease.payload.get("task_queue")
        workspace_id = lease.payload.get("workspace_id")
        ingestion_id = lease.payload.get("ingestion_id")
        if (
            not isinstance(workflow_id, str)
            or not workflow_id
            or not isinstance(task_queue, str)
            or not task_queue
            or not isinstance(workspace_id, str)
            or not workspace_id
            or not isinstance(ingestion_id, str)
            or not ingestion_id
        ):
            raise DomainError(
                FailureCode.VALIDATION_FAILED, "knowledge event payload is incomplete"
            )
        try:
            await client.start_workflow(
                KnowledgeIngestionWorkflow.run,
                KnowledgeIngestionInput(
                    workspace_id=workspace_id,
                    ingestion_id=ingestion_id,
                ),
                id=workflow_id,
                task_queue=task_queue,
            )
        except WorkflowAlreadyStartedError:
            pass
        return
    if lease.event_type == "approval.decided":
        workflow_id = lease.payload.get("workflow_id")
        if not isinstance(workflow_id, str) or not workflow_id:
            raise DomainError(FailureCode.VALIDATION_FAILED, "approval event has no workflow id")
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal(ApprovalWaitWorkflow.approval_decided)
        return
    if lease.event_type == "task.control.requested":
        workflow_id = lease.payload.get("workflow_id")
        action = lease.payload.get("action")
        if not isinstance(workflow_id, str) or not workflow_id:
            raise DomainError(FailureCode.VALIDATION_FAILED, "task event has no workflow id")
        handle = client.get_workflow_handle(workflow_id)
        if action == "pause":
            await handle.signal(DurableTaskWorkflow.pause)
        elif action == "resume":
            await handle.signal(DurableTaskWorkflow.resume)
        elif action == "cancel":
            await handle.signal(DurableTaskWorkflow.cancel)
        else:
            raise DomainError(FailureCode.VALIDATION_FAILED, "task event has invalid action")
        return
    raise DomainError(
        FailureCode.CAPABILITY_UNAVAILABLE,
        "outbox publisher does not support event type " + lease.event_type,
    )


async def dispatch_once(
    sessions: async_sessionmaker[AsyncSession],
    client: Client,
    worker_id: str,
    workspace_id: UUID | None = None,
    after_workspace_id: UUID | None = None,
    allowed_task_queues: Sequence[str] | None = None,
    publisher: Publisher | None = None,
) -> OutboxLease | None:
    async with sessions.begin() as session:
        lease = await claim_next(
            session,
            worker_id,
            workspace_id=workspace_id,
            after_workspace_id=after_workspace_id,
            allowed_task_queues=allowed_task_queues,
        )
    if lease is None:
        return None
    try:
        await (publisher or publish_temporal)(client, lease)
    except Exception as exc:
        async with sessions.begin() as session:
            await reschedule(session, lease, worker_id, type(exc).__name__, delay_seconds=1)
        raise
    async with sessions.begin() as session:
        await mark_published(session, lease, worker_id)
    return lease


async def run_outbox_dispatcher(
    client: Client, allowed_task_queues: Sequence[str] | None = None
) -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    worker_id = os.environ.get("OUTBOX_WORKER_ID", "temporal-worker-outbox")
    last_workspace_id: UUID | None = None
    try:
        while True:
            try:
                lease = await dispatch_once(
                    sessions,
                    client,
                    worker_id,
                    after_workspace_id=last_workspace_id,
                    allowed_task_queues=allowed_task_queues,
                )
            except Exception:  # noqa: BLE001 - dispatcher isolates one external publish failure
                await asyncio.sleep(1)
                continue
            if lease is None:
                await asyncio.sleep(0.25)
            else:
                last_workspace_id = lease.workspace_id
    finally:
        await engine.dispose()
