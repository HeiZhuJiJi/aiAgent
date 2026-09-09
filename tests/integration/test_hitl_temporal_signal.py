import os
from datetime import UTC, datetime
from uuid import uuid4

from temporalio.client import Client

from enterprise_agent.contracts.domain import ApprovalResult
from enterprise_agent.runtime.workflows import ApprovalWaitInput, ApprovalWaitWorkflow
from enterprise_agent.storage.approvals import decide_approval, request_approval
from tests.failure.test_worker_crash import start_worker
from tests.security.test_hitl import approval_request, setup_operation


async def test_temporal_signal_resumes_from_canonical_postgres_approval():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    database = async_sessionmaker(engine, expire_on_commit=False)
    workspace_id, task_id, _, attempt_id, token, intent, prepared, digest = await setup_operation(
        database
    )
    request = approval_request(workspace_id, task_id, intent, prepared, digest)
    async with database.begin() as session:
        await request_approval(session, request, attempt_id, token)
    task_queue = "approval-signal-" + str(uuid4())
    worker = start_worker(task_queue)
    client = await Client.connect("127.0.0.1:57233")
    handle = None
    completed = False
    try:
        handle = await client.start_workflow(
            ApprovalWaitWorkflow.run,
            ApprovalWaitInput(workspace_id=str(workspace_id), approval_id=str(request.approval_id)),
            id="approval-wait-" + str(uuid4()),
            task_queue=task_queue,
        )
        assert await handle.query(ApprovalWaitWorkflow.waiting_for_approval)
        async with database.begin() as session:
            await decide_approval(
                session,
                workspace_id,
                ApprovalResult(
                    approval_id=request.approval_id,
                    decision="APPROVED",
                    actor_id=uuid4(),
                    decided_at=datetime.now(UTC),
                ),
                expected_version=1,
            )
        await handle.signal(ApprovalWaitWorkflow.approval_decided)
        result = await handle.result()
        assert result.approval_id == str(request.approval_id)
        assert result.decision == "APPROVED"
        assert result.version == 2
        completed = True
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=10)
        if handle is not None and not completed:
            await handle.terminate("integration-test cleanup")
        await engine.dispose()
