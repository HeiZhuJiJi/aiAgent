import asyncio
import os
from contextlib import AsyncExitStack

from temporalio.client import Client
from temporalio.worker import Worker

from enterprise_agent.observability.metrics import start_worker_metrics_server
from enterprise_agent.operations.lifecycle import run_lifecycle_monitor
from enterprise_agent.runtime.activities import (
    acquire_plan_admission,
    commit_domain_step,
    execute_governed_plan_step,
    finalize_task_cancel,
    generate_and_apply_replan,
    heartbeat_plan_admission,
    ingest_knowledge_document,
    load_approval_decision,
    load_plan_execution_binding,
    load_plan_execution_state,
    materialize_chat_task_turn_activity,
    persist_plan_continuation,
    release_plan_admission,
)
from enterprise_agent.runtime.outbox_dispatcher import run_outbox_dispatcher
from enterprise_agent.runtime.reconciler import run_reconciler
from enterprise_agent.runtime.workflows import (
    ApprovalWaitWorkflow,
    DurableTaskWorkflow,
    KnowledgeIngestionWorkflow,
    PlanExecutionWorkflow,
)


def resolve_temporal_task_queues(primary: str, legacy_csv: str) -> tuple[str, ...]:
    ordered = (primary.strip(), *(item.strip() for item in legacy_csv.split(",")))
    queues = tuple(dict.fromkeys(item for item in ordered if item))
    if not queues:
        raise ValueError("at least one Temporal task queue is required")
    return queues


async def main() -> None:
    start_worker_metrics_server(int(os.environ.get("WORKER_METRICS_PORT", "9464")))
    client = await Client.connect(os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:57233"))
    task_queues = resolve_temporal_task_queues(
        os.environ.get("TEMPORAL_TASK_QUEUE", "enterprise-agent-runtime"),
        os.environ.get("TEMPORAL_LEGACY_TASK_QUEUES", ""),
    )
    workers = tuple(
        Worker(
            client,
            task_queue=task_queue,
            workflows=[
                DurableTaskWorkflow,
                ApprovalWaitWorkflow,
                KnowledgeIngestionWorkflow,
                PlanExecutionWorkflow,
            ],
            activities=[
                acquire_plan_admission,
                commit_domain_step,
                execute_governed_plan_step,
                finalize_task_cancel,
                generate_and_apply_replan,
                heartbeat_plan_admission,
                load_approval_decision,
                load_plan_execution_binding,
                load_plan_execution_state,
                materialize_chat_task_turn_activity,
                persist_plan_continuation,
                release_plan_admission,
                ingest_knowledge_document,
            ],
        )
        for task_queue in task_queues
    )
    async with AsyncExitStack() as stack:
        for worker in workers:
            await stack.enter_async_context(worker)
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(run_outbox_dispatcher(client, task_queues))
            tasks.create_task(run_reconciler(client))
            tasks.create_task(run_lifecycle_monitor())


if __name__ == "__main__":
    asyncio.run(main())
