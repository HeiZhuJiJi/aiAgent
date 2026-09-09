import asyncio
import os
from uuid import uuid4

import httpx
from sqlalchemy import select
from temporalio.client import Client
from temporalio.service import RPCError

from apps.api.main import create_app
from enterprise_agent.runtime.outbox_dispatcher import dispatch_once
from enterprise_agent.runtime.workflows import ApprovalWaitInput, ApprovalWaitWorkflow
from enterprise_agent.storage.approvals import request_approval
from enterprise_agent.storage.tables import OutboxEventRow, WorkspaceMemberRow
from tests.failure.test_worker_crash import start_worker
from tests.integration.test_api_identity import register_and_login
from tests.security.test_hitl import approval_request, setup_operation


async def test_approval_api_enforces_workspace_admin_and_optimistic_version():
    app = create_app(os.environ["TEST_DATABASE_URL"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        owner_headers = await register_and_login(client, "approval-owner")
        outsider_headers = await register_and_login(client, "approval-outsider")
        owner_id = (await client.get("/auth/me", headers=owner_headers)).json()["user_id"]
        (
            workspace_id,
            task_id,
            _,
            attempt_id,
            token,
            intent,
            prepared,
            digest,
        ) = await setup_operation(app.state.sessions)
        request = approval_request(workspace_id, task_id, intent, prepared, digest)
        async with app.state.sessions.begin() as session:
            session.add(
                WorkspaceMemberRow(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    user_id=owner_id,
                    role="WORKSPACE_OWNER",
                )
            )
            await request_approval(session, request, attempt_id, token)
        task_queue = "approval-api-outbox-" + str(uuid4())
        worker = start_worker(task_queue)
        temporal = await Client.connect("127.0.0.1:57233")
        handle = None
        completed = False
        try:
            handle = await temporal.start_workflow(
                ApprovalWaitWorkflow.run,
                ApprovalWaitInput(
                    workspace_id=str(workspace_id), approval_id=str(request.approval_id)
                ),
                id="approval-wait-" + str(request.approval_id),
                task_queue=task_queue,
            )
            listed = await client.get(
                "/approvals", headers=owner_headers, params={"workspace_id": str(workspace_id)}
            )
            assert listed.status_code == 200
            assert [item["approval_id"] for item in listed.json()] == [str(request.approval_id)]
            outsider = await client.get(
                "/approvals", headers=outsider_headers, params={"workspace_id": str(workspace_id)}
            )
            assert outsider.status_code == 403
            approved = await client.post(
                f"/approvals/{request.approval_id}/approve",
                headers=owner_headers,
                json={"workspace_id": str(workspace_id), "expected_version": 1},
            )
            assert approved.status_code == 200
            assert approved.json()["state"] == "APPROVED"
            assert approved.json()["actor_id"] == owner_id
            lease = await dispatch_once(
                app.state.sessions,
                temporal,
                "approval-api-dispatcher",
                workspace_id=workspace_id,
            )
            assert lease is not None and lease.event_type == "approval.decided"
            result = await asyncio.wait_for(handle.result(), timeout=15)
            assert result.decision == "APPROVED" and result.version == 2
            async with app.state.sessions() as session:
                outbox = await session.scalar(
                    select(OutboxEventRow).where(
                        OutboxEventRow.workspace_id == workspace_id,
                        OutboxEventRow.aggregate_id == request.approval_id,
                    )
                )
                assert outbox.event_type == "approval.decided"
                assert outbox.payload["decision"] == "APPROVED"
                assert outbox.state == "PUBLISHED"
            stale = await client.post(
                f"/approvals/{request.approval_id}/reject",
                headers=owner_headers,
                json={"workspace_id": str(workspace_id), "expected_version": 1},
            )
            assert stale.status_code == 409
            completed = True
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait(timeout=10)
            if handle is not None and not completed:
                try:
                    await handle.terminate("integration-test cleanup")
                except RPCError:
                    pass
