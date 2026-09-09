import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.main import create_app
from enterprise_agent.capabilities.executor import CapabilityExecutor
from enterprise_agent.contracts.domain import (
    CapabilityRequest,
    Classification,
    FailureCode,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.governance.policy import PolicyInput, Role, Subject
from enterprise_agent.mcp.adapter import load_mcp_registry
from enterprise_agent.procurement.demo import seed_demo_suppliers
from enterprise_agent.storage.attempts import acquire_attempt
from enterprise_agent.storage.tables import (
    AuditRow,
    MCPCapabilityRow,
    MCPServerRow,
    RuntimeGuardRow,
    StepRow,
    TaskBudgetRow,
    TaskRow,
)
from tests.integration.test_api_identity import register_and_login


async def test_active_mcp_snapshot_builds_real_stdio_capability_adapter() -> None:
    app = create_app(os.environ["TEST_DATABASE_URL"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client,
    ):
        owner = await register_and_login(client, "mcp-adapter-owner")
        workspace_id = UUID(
            (
                await client.post(
                    "/workspaces", headers=owner, json={"name": "MCP Adapter Workspace"}
                )
            ).json()["workspace_id"]
        )
        procurement_engine = create_async_engine(os.environ["PROCUREMENT_DATABASE_URL"])
        procurement_sessions = async_sessionmaker(
            procurement_engine, expire_on_commit=False
        )
        try:
            async with procurement_sessions.begin() as session:
                suppliers = await seed_demo_suppliers(session, workspace_id)

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
            active = await client.post(
                f"/mcp/{registered['server_id']}/connect",
                headers=owner,
                json={"workspace_id": str(workspace_id), "expected_version": 1},
            )
            assert active.status_code == 200

            registry = await load_mcp_registry(app.state.sessions, workspace_id)
            entry = registry.resolve("procurement.get_supplier_profile", "1.0.0")
            task_id = uuid4()
            step_id = uuid4()
            attempt_id = uuid4()
            async with app.state.sessions.begin() as session:
                session.add(TaskRow(id=task_id, workspace_id=workspace_id, state="RUNNING"))
                await session.flush()
                session.add(RuntimeGuardRow(task_id=task_id, workspace_id=workspace_id))
                session.add(
                    TaskBudgetRow(
                        task_id=task_id,
                        workspace_id=workspace_id,
                        max_steps=10,
                        max_replans=2,
                        max_llm_calls=10,
                        max_capability_calls=10,
                        max_tokens=1000,
                        max_cost=10,
                        deadline=datetime.now(UTC) + timedelta(hours=1),
                    )
                )
                session.add(
                    StepRow(
                        id=step_id,
                        workspace_id=workspace_id,
                        task_id=task_id,
                        state="READY",
                    )
                )
                await session.flush()
                token = (
                    await acquire_attempt(
                        session,
                        workspace_id,
                        step_id,
                        attempt_id,
                        "real-mcp-adapter",
                        "trace-real-mcp-adapter",
                    )
                ).fencing_token
            request = CapabilityRequest(
                command_id=uuid4(),
                operation_id=uuid4(),
                workspace_id=workspace_id,
                task_id=task_id,
                step_id=step_id,
                attempt_id=attempt_id,
                fencing_token=token,
                capability_id=entry.descriptor.id,
                capability_version=entry.descriptor.version,
                arguments={
                    "workspace_id": str(workspace_id),
                    "supplier_id": str(suppliers[0].supplier_id),
                },
            )
            policy = PolicyInput(
                subject=Subject(
                    actor_id=attempt_id,
                    actor_type="AGENT",
                    workspace_id=workspace_id,
                    role=Role.WORKSPACE_ADMIN,
                ),
                resource_workspace_id=workspace_id,
                resource_id=task_id,
                capability=entry.descriptor,
                capability_allowlist=frozenset({entry.descriptor.id}),
                classification=Classification.INTERNAL,
                external_provider=False,
                policy_version="policy-1",
            )
            executor = CapabilityExecutor(app.state.sessions, registry)
            result = await executor.execute(request, policy)
            replay = await executor.execute(request, policy)
            assert result.outcome == "SUCCEEDED"
            assert replay == result
            assert isinstance(result.output, dict)
            assert result.output["legal_name"] == "Aurora Precision Components Ltd."

            disabled = await client.post(
                f"/mcp/{registered['server_id']}/disable",
                headers=owner,
                json={"workspace_id": str(workspace_id), "expected_version": 2},
            )
            assert disabled.status_code == 200
            with pytest.raises(DomainError) as blocked:
                await entry.handler(request)
            assert blocked.value.code is FailureCode.CAPABILITY_UNAVAILABLE
        finally:
            await procurement_engine.dispose()


async def test_procurement_mcp_register_discover_health_disable_and_normalize() -> None:
    app = create_app(os.environ["TEST_DATABASE_URL"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client,
    ):
        owner = await register_and_login(client, "mcp-owner")
        outsider = await register_and_login(client, "mcp-outsider")
        workspace_id = (
            await client.post("/workspaces", headers=owner, json={"name": "MCP Workspace"})
        ).json()["workspace_id"]
        registration = {
            "workspace_id": workspace_id,
            "name": "Procurement",
            "provider": "procurement",
        }

        registered = await client.post("/mcp", headers=owner, json=registration)
        assert registered.status_code == 201
        server = registered.json()
        assert server["status"] == "REGISTERED"
        assert server["capability_count"] == 0

        replay = await client.post("/mcp", headers=owner, json=registration)
        assert replay.json()["server_id"] == server["server_id"]

        denied = await client.get(
            "/mcp", params={"workspace_id": workspace_id}, headers=outsider
        )
        assert denied.status_code == 403

        connected = await client.post(
            f"/mcp/{server['server_id']}/connect",
            headers=owner,
            json={"workspace_id": workspace_id, "expected_version": 1},
        )
        assert connected.status_code == 200
        active = connected.json()
        assert active["status"] == "ACTIVE"
        assert active["capability_count"] == 6
        assert active["schema_hash"]

        capabilities = await client.get(
            "/capabilities", params={"workspace_id": workspace_id}, headers=owner
        )
        assert capabilities.status_code == 200
        descriptors = {item["id"]: item for item in capabilities.json()}
        assert set(descriptors) == {
            "knowledge.hybrid_search",
            "web.search",
            "web.fetch",
            "model.structured_output",
            "runtime.review",
            "artifact.docx",
            "artifact.markdown",
            "procurement.get_supplier_profile",
            "procurement.get_supplier_purchase_history",
            "procurement.get_supplier_risk_flags",
            "procurement.get_supplier_contracts",
            "procurement.create_supplier_admission_request",
            "procurement.get_admission_request",
        }
        effect = descriptors["procurement.create_supplier_admission_request"]
        assert effect["side_effect_mode"] == "EXTERNAL"
        assert effect["approval_policy"] == "REQUIRED"
        assert effect["idempotency_mode"] == "NATIVE_IDEMPOTENCY"
        assert effect["reconciliation_capability"] == "procurement.get_admission_request"

        healthy = await client.post(
            f"/mcp/{server['server_id']}/health",
            headers=owner,
            json={"workspace_id": workspace_id, "expected_version": 2},
        )
        assert healthy.status_code == 200
        assert healthy.json()["schema_hash"] == active["schema_hash"]
        assert healthy.json()["capability_count"] == 6

        stale = await client.post(
            f"/mcp/{server['server_id']}/health",
            headers=owner,
            json={"workspace_id": workspace_id, "expected_version": 2},
        )
        assert stale.status_code == 409

        disabled = await client.post(
            f"/mcp/{server['server_id']}/disable",
            headers=owner,
            json={"workspace_id": workspace_id, "expected_version": 3},
        )
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "DISABLED"
        after_disable = (
            await client.get(
                "/capabilities", params={"workspace_id": workspace_id}, headers=owner
            )
        ).json()
        assert {item["id"] for item in after_disable} == {
            "knowledge.hybrid_search",
            "web.search",
            "web.fetch",
            "model.structured_output",
            "runtime.review",
            "artifact.docx",
            "artifact.markdown",
        }

        async with app.state.sessions() as session:
            stored = await session.scalar(
                select(MCPServerRow).where(MCPServerRow.workspace_id == UUID(workspace_id))
            )
            capability_count = await session.scalar(
                select(func.count())
                .select_from(MCPCapabilityRow)
                .where(MCPCapabilityRow.workspace_id == UUID(workspace_id))
            )
            audits = (
                await session.scalars(
                    select(AuditRow).where(
                        AuditRow.workspace_id == UUID(workspace_id),
                        AuditRow.action.like("mcp.%"),
                    )
                )
            ).all()

        assert stored is not None
        assert os.environ["PROCUREMENT_DATABASE_URL"] not in str(stored.command_config)
        assert stored.command_config["environment_refs"] == ["PROCUREMENT_DATABASE_URL"]
        assert capability_count == 6
        assert {row.action for row in audits} == {
            "mcp.register",
            "mcp.schema.synced",
            "mcp.disable",
        }

        with pytest.raises(DBAPIError):
            async with app.state.sessions.begin() as session:
                await session.execute(
                    update(MCPCapabilityRow)
                    .where(MCPCapabilityRow.workspace_id == UUID(workspace_id))
                    .values(tool_name="tampered")
                )
