import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from enterprise_agent.observability.metrics import start_worker_metrics_server
from enterprise_agent.operations.lifecycle import run_lifecycle_monitor
from enterprise_agent.runtime.activities import (
    commit_domain_step,
    execute_governed_plan_step,
    finalize_task_cancel,
    generate_and_apply_replan,
    ingest_knowledge_document,
    load_approval_decision,
    load_plan_execution_binding,
    load_plan_execution_state,
    persist_plan_continuation,
)
from enterprise_agent.runtime.outbox_dispatcher import run_outbox_dispatcher
from enterprise_agent.runtime.reconciler import run_reconciler
from enterprise_agent.runtime.workflows import (
    ApprovalWaitWorkflow,
    DurableTaskWorkflow,
    KnowledgeIngestionWorkflow,
    PlanExecutionWorkflow,
)


async def main() -> None:
    start_worker_metrics_server(int(os.environ.get("WORKER_METRICS_PORT", "9464")))
    client = await Client.connect(os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:57233"))
    worker = Worker(
        client,
        task_queue=os.environ.get("TEMPORAL_TASK_QUEUE", "enterprise-agent-runtime"),
        workflows=[
            DurableTaskWorkflow,
            ApprovalWaitWorkflow,
            KnowledgeIngestionWorkflow,
            PlanExecutionWorkflow,
        ],
        activities=[
            commit_domain_step,
            execute_governed_plan_step,
            finalize_task_cancel,
            generate_and_apply_replan,
            load_approval_decision,
            load_plan_execution_binding,
            load_plan_execution_state,
            persist_plan_continuation,
            ingest_knowledge_document,
        ],
    )
    async with worker, asyncio.TaskGroup() as tasks:
        tasks.create_task(run_outbox_dispatcher(client))
        tasks.create_task(run_reconciler(client))
        tasks.create_task(run_lifecycle_monitor())


if __name__ == "__main__":
    asyncio.run(main())
