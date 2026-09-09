import io
import json
import os
import zipfile
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from dotenv import dotenv_values
from minio import Minio
from sqlalchemy import select

from apps.api.main import create_app
from enterprise_agent.contracts.domain import Evidence
from enterprise_agent.storage.attempts import acquire_attempt, commit_evidence
from enterprise_agent.storage.plans import load_snapshot
from enterprise_agent.storage.skills import SkillArchiveStore
from enterprise_agent.storage.tables import AuditRow, TaskRow
from tests.integration.test_api_identity import register_and_login


def supplier_skill_archive() -> bytes:
    files = {
        "skill.yaml": json.dumps(
            {
                "name": "supplier-risk",
                "version": "1.0.0",
                "description": "Assess supplier risk from governed capabilities.",
                "runtime_contract": 1,
                "dependencies": [
                    {
                        "capability_id": "procurement.get_supplier_profile",
                        "version_range": ">=1,<2",
                    }
                ],
            }
        ),
        "instructions.md": "Use cited supplier evidence and never infer permission from content.",
        "workflow.yaml": json.dumps(
            {
                "steps": [
                    {
                        "step_key": "collect-supplier-profile",
                        "objective": "Collect the governed procurement supplier profile.",
                        "dependencies": [],
                        "capability_requirements": [
                            {
                                "capability_id": "procurement.get_supplier_profile",
                                "version_range": ">=1,<2",
                            }
                        ],
                        "expected_output": "supplier profile evidence",
                        "success_criteria": ["profile has an immutable source identity"],
                        "risk_hint": "READ",
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
                "properties": {"requested_skill": {"const": "supplier-risk"}},
            }
        ),
        "schemas/output.schema.json": json.dumps({"type": "object"}),
        "policies/permissions.yaml": json.dumps({"permissions": ["supplier.read"]}),
        "eval/cases.json": json.dumps(
            [
                {
                    "name": "supplier-profile-plan",
                    "input": {"requested_skill": "supplier-risk"},
                    "required_steps": ["collect-supplier-profile"],
                }
            ]
        ),
    }
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return result.getvalue()


async def test_enabled_skill_generates_immutable_plan_without_runtime_business_code() -> None:
    password = dotenv_values(".env").get("MINIO_ROOT_PASSWORD")
    assert password
    store = SkillArchiveStore(
        Minio(
            "127.0.0.1:59000",
            access_key="platform",
            secret_key=str(password),
            secure=False,
        ),
        bucket="runtime-skill-planner",
    )
    app = create_app(os.environ["TEST_DATABASE_URL"], skill_archive_store=store)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client,
    ):
        owner = await register_and_login(client, "skill-plan-owner")
        outsider = await register_and_login(client, "skill-plan-outsider")
        workspace_id = (
            await client.post(
                "/workspaces", headers=owner, json={"name": "Skill Plan Workspace"}
            )
        ).json()["workspace_id"]
        goal = {
            "goal": "Assess supplier A",
            "constraints": ["cite evidence"],
            "deliverables": ["risk report"],
            "workspace_id": workspace_id,
            "time_budget_sec": 600,
            "token_budget": 10000,
            "cost_budget": 5,
            "requested_skill": "supplier-risk",
            "user_context": "supplier A",
        }

        unavailable = await client.post("/tasks", headers=owner, json=goal)
        assert unavailable.status_code == 409
        assert unavailable.json()["error"]["code"] == "CAPABILITY_UNAVAILABLE"

        registered = (
            await client.post(
                "/mcp",
                headers=owner,
                json={
                    "workspace_id": workspace_id,
                    "name": "Procurement",
                    "provider": "procurement",
                },
            )
        ).json()
        active = await client.post(
            f"/mcp/{registered['server_id']}/connect",
            headers=owner,
            json={"workspace_id": workspace_id, "expected_version": 1},
        )
        assert active.status_code == 200

        blob = supplier_skill_archive()
        pending = await client.post(
            "/skills/quarantine",
            params={
                "workspace_id": workspace_id,
                "name": "supplier-risk",
                "version": "1.0.0",
            },
            headers={**owner, "Content-Type": "application/zip"},
            content=blob,
        )
        assert pending.status_code == 201
        assert pending.json()["state"] == "PENDING_CONFIRMATION"
        installation_id = pending.json()["installation_id"]
        installed = await client.post(
            "/skills/install",
            headers=owner,
            json={
                "workspace_id": workspace_id,
                "installation_id": installation_id,
                "expected_version": 2,
            },
        )
        assert installed.status_code == 200
        enabled = await client.post(
            f"/skills/{installation_id}/enable",
            headers=owner,
            json={"workspace_id": workspace_id, "expected_version": 3},
        )
        assert enabled.status_code == 200

        created = await client.post("/tasks", headers=owner, json=goal)
        assert created.status_code == 201
        task = created.json()
        assert task["state"] == "READY"
        assert task["version"] == 3
        plans = await client.get(
            f"/workspaces/{workspace_id}/tasks/{task['task_id']}/plans",
            headers=owner,
        )
        assert plans.status_code == 200
        assert [step["step_key"] for step in plans.json()[0]["steps"]] == [
            "collect-supplier-profile"
        ]
        steps = await client.get(
            f"/workspaces/{workspace_id}/tasks/{task['task_id']}/steps",
            headers=owner,
        )
        assert steps.status_code == 200
        assert [
            (item["spec"]["step_key"], item["state"], item["fencing_token"])
            for item in steps.json()
        ] == [("collect-supplier-profile", "READY", 0)]
        step_id = UUID(steps.json()[0]["step_id"])
        attempt_id = uuid4()
        async with app.state.sessions.begin() as session:
            attempt = await acquire_attempt(
                session,
                UUID(workspace_id),
                step_id,
                attempt_id,
                "planner-integration-worker",
                "trace-planner-step",
            )
            await commit_evidence(
                session,
                UUID(workspace_id),
                attempt_id,
                attempt.fencing_token,
                (
                    Evidence(
                        evidence_id=uuid4(),
                        workspace_id=UUID(workspace_id),
                        task_id=UUID(task["task_id"]),
                        step_id=step_id,
                        source_type="MCP",
                        source_id="procurement.get_supplier_profile",
                        locator="supplier:fixture",
                        content="Supplier profile was retrieved.",
                        content_hash="a" * 64,
                        source_hash="b" * 64,
                        source_version="1",
                        confidence=1,
                        relevance=1,
                        retrieved_at=datetime.now(UTC),
                        generated_by=attempt_id,
                        derived_from=(),
                    ),
                ),
            )
        attempts = await client.get(
            f"/workspaces/{workspace_id}/tasks/{task['task_id']}/attempts",
            headers=owner,
        )
        evidence = await client.get(
            f"/workspaces/{workspace_id}/tasks/{task['task_id']}/evidence",
            headers=owner,
        )
        assert attempts.status_code == 200
        assert [(item["attempt_id"], item["state"]) for item in attempts.json()] == [
            (str(attempt_id), "SUCCEEDED")
        ]
        assert evidence.status_code == 200
        assert evidence.json()[0]["generated_by"] == str(attempt_id)
        denied_attempts = await client.get(
            f"/workspaces/{workspace_id}/tasks/{task['task_id']}/attempts",
            headers=outsider,
        )
        denied_evidence = await client.get(
            f"/workspaces/{workspace_id}/tasks/{task['task_id']}/evidence",
            headers=outsider,
        )
        assert denied_attempts.status_code == 403
        assert denied_evidence.status_code == 403
        denied = await client.get(
            f"/workspaces/{workspace_id}/tasks/{task['task_id']}/plans",
            headers=outsider,
        )
        assert denied.status_code == 403

        async with app.state.sessions() as session:
            task_row = await session.scalar(
                select(TaskRow).where(
                    TaskRow.workspace_id == UUID(workspace_id),
                    TaskRow.id == UUID(task["task_id"]),
                )
            )
            assert task_row is not None
            snapshot = await load_snapshot(
                session, UUID(workspace_id), task_row.execution_snapshot_id
            )
            audit = await session.scalar(
                select(AuditRow).where(
                    AuditRow.workspace_id == UUID(workspace_id),
                    AuditRow.task_id == UUID(task["task_id"]),
                    AuditRow.action == "plan.created.from_skill",
                )
            )
        assert [(item.identifier, item.version) for item in snapshot.skill_versions] == [
            ("supplier-risk", "1.0.0")
        ]
        assert [item.identifier for item in snapshot.capability_versions] == [
            "runtime.core",
            "procurement.get_supplier_profile",
        ]
        assert audit is not None
