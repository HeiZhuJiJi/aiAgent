import asyncio
import io
import json
import os
import zipfile
from uuid import UUID, uuid4

import httpx
from dotenv import dotenv_values
from minio import Minio
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from apps.api.main import create_app
from enterprise_agent.procurement.demo import seed_demo_suppliers
from enterprise_agent.procurement.service import AdmissionRow
from enterprise_agent.runtime.admission import AdmissionLimits, RedisAdmissionController
from enterprise_agent.runtime.outbox_dispatcher import dispatch_once
from enterprise_agent.storage.skills import SkillArchiveStore
from enterprise_agent.storage.tables import OutboxEventRow, TaskRow
from tests.failure.test_worker_crash import start_worker
from tests.integration.test_api_identity import register_and_login
from tests.integration.test_skill_planner import supplier_skill_archive


def admission_skill_archive() -> bytes:
    capability_id = "procurement.create_supplier_admission_request"
    files = {
        "skill.yaml": json.dumps(
            {
                "name": "supplier-admission",
                "version": "1.0.0",
                "description": "Create an approval-gated supplier admission request.",
                "runtime_contract": 1,
                "dependencies": [
                    {"capability_id": capability_id, "version_range": ">=1,<2"}
                ],
            }
        ),
        "instructions.md": "Request human approval before creating supplier admission.",
        "workflow.yaml": json.dumps(
            {
                "steps": [
                    {
                        "step_key": "create-admission",
                        "objective": "Create the approved supplier admission request.",
                        "dependencies": [],
                        "capability_requirements": [
                            {"capability_id": capability_id, "version_range": ">=1,<2"}
                        ],
                        "expected_output": "immutable admission evidence",
                        "success_criteria": ["admission is bound to the approved operation"],
                        "risk_hint": "EXTERNAL_SIDE_EFFECT",
                        "execution_mode": "CAPABILITY",
                        "parallelizable": False,
                        "max_attempts": 3,
                    }
                ]
            }
        ),
        "schemas/input.schema.json": json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "required": ["requested_skill"],
                "properties": {"requested_skill": {"const": "supplier-admission"}},
            }
        ),
        "schemas/output.schema.json": json.dumps({"type": "object"}),
        "policies/permissions.yaml": json.dumps({"permissions": ["supplier.write"]}),
        "eval/cases.json": json.dumps(
            [
                {
                    "name": "supplier-admission-plan",
                    "input": {"requested_skill": "supplier-admission"},
                    "required_steps": ["create-admission"],
                }
            ]
        ),
    }
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return result.getvalue()


async def test_outbox_temporal_plan_executes_real_governed_mcp_step(monkeypatch) -> None:
    task_queue = "plan-mcp-" + str(uuid4())
    admission_namespace = "test:plan-admission:" + str(uuid4())
    monkeypatch.setenv("TEMPORAL_TASK_QUEUE", task_queue)
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:56379/0")
    monkeypatch.setenv("ADMISSION_NAMESPACE", admission_namespace)
    monkeypatch.setenv("ADMISSION_GLOBAL_TASKS", "8")
    monkeypatch.setenv("ADMISSION_WORKSPACE_TASKS", "1")
    monkeypatch.setenv("ADMISSION_MODEL_PROVIDER", "8")
    monkeypatch.setenv("ADMISSION_CAPABILITY", "8")
    monkeypatch.setenv("ADMISSION_MCP", "8")
    monkeypatch.setenv("ADMISSION_LEASE_MS", "60000")
    password = dotenv_values(".env").get("MINIO_ROOT_PASSWORD")
    assert password
    store = SkillArchiveStore(
        Minio(
            "127.0.0.1:59000",
            access_key="platform",
            secret_key=str(password),
            secure=False,
        ),
        bucket="runtime-plan-temporal-mcp",
    )
    app = create_app(os.environ["TEST_DATABASE_URL"], skill_archive_store=store)
    temporal = await Client.connect("127.0.0.1:57233")
    procurement_engine = create_async_engine(os.environ["PROCUREMENT_DATABASE_URL"])
    procurement = async_sessionmaker(procurement_engine, expire_on_commit=False)
    worker = None
    handle = None
    completed = False
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client,
    ):
        owner = await register_and_login(client, "temporal-plan-mcp")
        workspace_id = UUID(
            (
                await client.post(
                    "/workspaces",
                    headers=owner,
                    json={"name": "Temporal Plan MCP"},
                )
            ).json()["workspace_id"]
        )
        async with procurement.begin() as session:
            supplier = (await seed_demo_suppliers(session, workspace_id))[0]

        registered = (
            await client.post(
                "/mcp",
                headers=owner,
                json={
                    "workspace_id": str(workspace_id),
                    "name": "Procurement",
                    "provider": "procurement",
                },
            )
        ).json()
        connected = await client.post(
            f"/mcp/{registered['server_id']}/connect",
            headers=owner,
            json={"workspace_id": str(workspace_id), "expected_version": 1},
        )
        assert connected.status_code == 200

        pending = await client.post(
            "/skills/quarantine",
            params={
                "workspace_id": str(workspace_id),
                "name": "supplier-risk",
                "version": "1.0.0",
            },
            headers={**owner, "Content-Type": "application/zip"},
            content=supplier_skill_archive(),
        )
        installed = await client.post(
            "/skills/install",
            headers=owner,
            json={
                "workspace_id": str(workspace_id),
                "installation_id": pending.json()["installation_id"],
                "expected_version": 2,
            },
        )
        enabled = await client.post(
            f"/skills/{pending.json()['installation_id']}/enable",
            headers=owner,
            json={"workspace_id": str(workspace_id), "expected_version": 3},
        )
        assert installed.status_code == 200 and enabled.status_code == 200

        created = await client.post(
            "/tasks",
            headers=owner,
            json={
                "goal": "Retrieve the selected supplier profile",
                "constraints": ["cite the MCP result"],
                "deliverables": ["profile evidence"],
                "workspace_id": str(workspace_id),
                "time_budget_sec": 600,
                "token_budget": 1000,
                "cost_budget": 1,
                "requested_skill": "supplier-risk",
                "user_context": json.dumps({"supplier_id": str(supplier.supplier_id)}),
            },
        )
        assert created.status_code == 201
        task_id = created.json()["task_id"]
        redis = Redis.from_url(
            "redis://127.0.0.1:56379/0",
            decode_responses=True,
        )
        admission = RedisAdmissionController(
            redis,
            AdmissionLimits(
                global_tasks=8,
                workspace_tasks=1,
                model_provider=8,
                capability=8,
                mcp=8,
            ),
            admission_namespace,
        )
        blocked, blocker_lease = await admission.try_acquire_task(
            workspace_id,
            lease_ms=60_000,
        )
        assert blocked.allowed and blocker_lease is not None
        try:
            published = await dispatch_once(
                app.state.sessions,
                temporal,
                "plan-execution-publisher",
                workspace_id,
            )
            assert published is not None
            assert published.event_type == "task.execution.requested"
            handle = temporal.get_workflow_handle("task-" + task_id)
            worker = start_worker(task_queue)
            for _ in range(100):
                async with app.state.sessions() as session:
                    canonical = await session.get(TaskRow, UUID(task_id))
                    if (
                        canonical is not None
                        and canonical.state == "READY"
                        and canonical.schedule_block_reason
                        == "workspace_task_concurrency"
                    ):
                        break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError(
                    "admission-blocked Plan did not remain READY with a reason"
                )
            await admission.release(blocker_lease)
            blocker_lease = None
            result = await asyncio.wait_for(handle.result(), timeout=30)
            assert result["state"] == "COMPLETED"
            task = await client.get(
                f"/workspaces/{workspace_id}/tasks/{task_id}",
                headers=owner,
            )
            attempts = await client.get(
                f"/workspaces/{workspace_id}/tasks/{task_id}/attempts",
                headers=owner,
            )
            evidence = await client.get(
                f"/workspaces/{workspace_id}/tasks/{task_id}/evidence",
                headers=owner,
            )
            assert task.json()["state"] == "COMPLETED"
            async with app.state.sessions() as session:
                canonical = await session.get(TaskRow, UUID(task_id))
                assert canonical is not None
                assert canonical.schedule_block_reason is None
            assert [item["state"] for item in attempts.json()] == ["SUCCEEDED"]
            assert evidence.json()[0]["source_id"] == "procurement.get_supplier_profile"
            assert supplier.profile["legal_name"] in evidence.json()[0]["content"]
            assert await redis.zcard(admission_namespace + ":global") == 0
            assert (
                await redis.zcard(admission_namespace + ":workspace:" + str(workspace_id))
                == 0
            )
            completed = True
        finally:
            if worker is not None and worker.poll() is None:
                worker.kill()
                worker.wait(timeout=10)
            if handle is not None and not completed:
                try:
                    await handle.terminate("plan MCP test cleanup")
                except RPCError:
                    pass
            if blocker_lease is not None:
                await admission.release(blocker_lease)
            keys = [key async for key in redis.scan_iter(match=admission_namespace + ":*")]
            if keys:
                await redis.delete(*keys)
            await redis.aclose()
    await procurement_engine.dispose()


async def test_plan_waits_for_human_then_executes_one_real_procurement_write(
    monkeypatch,
) -> None:
    task_queue = "plan-mcp-hitl-" + str(uuid4())
    monkeypatch.setenv("TEMPORAL_TASK_QUEUE", task_queue)
    password = dotenv_values(".env").get("MINIO_ROOT_PASSWORD")
    assert password
    store = SkillArchiveStore(
        Minio(
            "127.0.0.1:59000",
            access_key="platform",
            secret_key=str(password),
            secure=False,
        ),
        bucket="runtime-plan-temporal-mcp-hitl",
    )
    app = create_app(os.environ["TEST_DATABASE_URL"], skill_archive_store=store)
    temporal = await Client.connect("127.0.0.1:57233")
    procurement_engine = create_async_engine(os.environ["PROCUREMENT_DATABASE_URL"])
    procurement = async_sessionmaker(procurement_engine, expire_on_commit=False)
    worker = None
    handle = None
    completed = False
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client,
    ):
        owner = await register_and_login(client, "temporal-plan-mcp-hitl")
        workspace_id = UUID(
            (
                await client.post(
                    "/workspaces",
                    headers=owner,
                    json={"name": "Temporal Plan MCP HITL"},
                )
            ).json()["workspace_id"]
        )
        async with procurement.begin() as session:
            supplier = (await seed_demo_suppliers(session, workspace_id))[0]

        registered = (
            await client.post(
                "/mcp",
                headers=owner,
                json={
                    "workspace_id": str(workspace_id),
                    "name": "Procurement HITL",
                    "provider": "procurement",
                },
            )
        ).json()
        connected = await client.post(
            f"/mcp/{registered['server_id']}/connect",
            headers=owner,
            json={"workspace_id": str(workspace_id), "expected_version": 1},
        )
        assert connected.status_code == 200

        pending = await client.post(
            "/skills/quarantine",
            params={
                "workspace_id": str(workspace_id),
                "name": "supplier-admission",
                "version": "1.0.0",
            },
            headers={**owner, "Content-Type": "application/zip"},
            content=admission_skill_archive(),
        )
        installed = await client.post(
            "/skills/install",
            headers=owner,
            json={
                "workspace_id": str(workspace_id),
                "installation_id": pending.json()["installation_id"],
                "expected_version": 2,
            },
        )
        enabled = await client.post(
            f"/skills/{pending.json()['installation_id']}/enable",
            headers=owner,
            json={"workspace_id": str(workspace_id), "expected_version": 3},
        )
        assert installed.status_code == 200 and enabled.status_code == 200

        report_sha256 = "b" * 64
        created = await client.post(
            "/tasks",
            headers=owner,
            json={
                "goal": "Submit the selected supplier for admission",
                "constraints": ["require a human decision before the external write"],
                "deliverables": ["admission evidence"],
                "workspace_id": str(workspace_id),
                "time_budget_sec": 600,
                "token_budget": 1000,
                "cost_budget": 1,
                "requested_skill": "supplier-admission",
                "user_context": json.dumps(
                    {
                        "supplier_id": str(supplier.supplier_id),
                        "report_sha256": report_sha256,
                    }
                ),
            },
        )
        assert created.status_code == 201
        task_id = created.json()["task_id"]
        published = await dispatch_once(
            app.state.sessions,
            temporal,
            "plan-hitl-publisher",
            workspace_id,
        )
        assert published is not None and published.event_type == "task.execution.requested"
        handle = temporal.get_workflow_handle("task-" + task_id)
        worker = start_worker(task_queue)
        try:
            approval = None
            for _ in range(150):
                listed = await client.get(
                    "/approvals",
                    headers=owner,
                    params={"workspace_id": str(workspace_id)},
                )
                if listed.status_code == 200 and listed.json():
                    approval = listed.json()[0]
                    break
                await asyncio.sleep(0.1)
            assert approval is not None
            assert approval["state"] == "PENDING"
            task_waiting = await client.get(
                f"/workspaces/{workspace_id}/tasks/{task_id}", headers=owner
            )
            assert task_waiting.json()["state"] == "WAITING_HITL"
            async with procurement() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(AdmissionRow)
                        .where(AdmissionRow.workspace_id == workspace_id)
                    )
                    == 0
                )

            approval_id = UUID(approval["approval_id"])
            approval_handle = temporal.get_workflow_handle(
                "approval-wait-" + str(approval_id)
            )
            for _ in range(100):
                try:
                    if await approval_handle.query("waiting_for_approval"):
                        break
                except RPCError as exc:
                    if exc.status != RPCStatusCode.NOT_FOUND:
                        raise
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("approval child workflow did not become ready")
            approved = await client.post(
                f"/approvals/{approval_id}/approve",
                headers=owner,
                json={"workspace_id": str(workspace_id), "expected_version": 1},
            )
            assert approved.status_code == 200
            approval_event = await dispatch_once(
                app.state.sessions,
                temporal,
                "plan-hitl-approval-publisher",
                workspace_id,
            )
            if approval_event is not None:
                assert approval_event.event_type == "approval.decided"
            for _ in range(100):
                async with app.state.sessions() as session:
                    outbox_state = await session.scalar(
                        select(OutboxEventRow.state).where(
                            OutboxEventRow.workspace_id == workspace_id,
                            OutboxEventRow.aggregate_id == approval_id,
                            OutboxEventRow.event_type == "approval.decided",
                        )
                    )
                if outbox_state == "PUBLISHED":
                    break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("approval decision outbox event was not published")
            result = await asyncio.wait_for(handle.result(), timeout=30)
            assert result["state"] == "COMPLETED"
            attempts = await client.get(
                f"/workspaces/{workspace_id}/tasks/{task_id}/attempts", headers=owner
            )
            assert [item["state"] for item in attempts.json()] == [
                "CANCELLED",
                "SUCCEEDED",
            ]
            async with procurement() as session:
                admissions = (
                    await session.scalars(
                        select(AdmissionRow).where(AdmissionRow.workspace_id == workspace_id)
                    )
                ).all()
                assert len(admissions) == 1
                assert admissions[0].supplier_id == supplier.supplier_id
                assert admissions[0].report_sha256 == report_sha256
            completed = True
        finally:
            if worker is not None and worker.poll() is None:
                worker.kill()
                worker.wait(timeout=10)
            if handle is not None and not completed:
                try:
                    await handle.terminate("plan MCP HITL test cleanup")
                except RPCError:
                    pass
    await procurement_engine.dispose()
