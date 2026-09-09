import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from apps.api.main import create_app
from enterprise_agent.contracts.domain import (
    ExecutionSnapshot,
    Plan,
    ReplanDecision,
    TaskGoal,
)
from enterprise_agent.runtime.budgets import new_task_budget
from enterprise_agent.storage.plans import persist_plan, persist_snapshot
from enterprise_agent.storage.steps import materialize_plan_steps
from enterprise_agent.storage.tables import (
    AuditRow,
    ConsistencyIncidentRow,
    RuntimeGuardRow,
    TaskRow,
)
from tests.integration.test_api_identity import register_and_login
from tests.integration.test_replans import _ref, _step


async def test_task_modify_appends_plan_with_user_trigger_and_optimistic_lock() -> None:
    app = create_app(os.environ["TEST_DATABASE_URL"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client,
    ):
        owner = await register_and_login(client, "modify-owner")
        outsider = await register_and_login(client, "modify-outsider")
        user_id = UUID((await client.get("/auth/me", headers=owner)).json()["user_id"])
        workspace_id = UUID(
            (
                await client.post(
                    "/workspaces", headers=owner, json={"name": "Modify Workspace"}
                )
            ).json()["workspace_id"]
        )
        task_id, snapshot_id, plan_id = uuid4(), uuid4(), uuid4()
        goal = TaskGoal(
            workspace_id=workspace_id,
            goal="Assess supplier",
            constraints=(),
            deliverables=("report",),
            time_budget_sec=600,
            token_budget=10_000,
            cost_budget=10,
        )
        snapshot = ExecutionSnapshot(
            snapshot_id=snapshot_id,
            workspace_id=workspace_id,
            runtime_version="0.1.0",
            workflow_definition_version="1.0.0",
            planner_prompt_version=_ref("planner"),
            router_prompt_version=_ref("router"),
            reviewer_prompt_version=_ref("reviewer"),
            model_config_versions=(_ref("model"),),
            skill_versions=(),
            capability_versions=(_ref("test.read"),),
            policy_version=_ref("policy"),
            knowledge_document_refs=(),
            context_policy_version=_ref("context"),
            schema_versions=(_ref("domain"),),
            feature_flags=frozenset(),
            created_at=datetime.now(UTC),
        )
        plan = Plan(
            plan_id=plan_id,
            task_id=task_id,
            workspace_id=workspace_id,
            execution_snapshot_id=snapshot_id,
            version=1,
            goal=goal.goal,
            constraints=goal.constraints,
            deliverables=goal.deliverables,
            steps=(_step("collect"), _step("report", dependencies=("collect",))),
        )
        async with app.state.sessions.begin() as session:
            await persist_snapshot(session, snapshot)
            session.add(
                TaskRow(
                    id=task_id,
                    workspace_id=workspace_id,
                    created_by=user_id,
                    state="RUNNING",
                    version=1,
                    execution_snapshot_id=snapshot_id,
                    goal_payload=goal.model_dump(mode="json"),
                )
            )
            await session.flush()
            budget = new_task_budget(task_id, goal)
            budget.deadline = datetime.now(UTC) + timedelta(minutes=10)
            session.add(budget)
            session.add(RuntimeGuardRow(task_id=task_id, workspace_id=workspace_id))
            await persist_plan(session, plan)
            await materialize_plan_steps(session, plan)

        decision = ReplanDecision(
            reason="User requested an additional public-source verification",
            trigger="USER_MODIFICATION",
            action="ADD_STEP",
            affected_steps=(),
            new_steps=(_step("verify"),),
            preserve_completed_steps=True,
            budget_impact=0,
        )
        modified = await client.post(
            f"/tasks/{task_id}/modify",
            headers=owner,
            json={
                "workspace_id": str(workspace_id),
                "expected_version": 1,
                "decision": decision.model_dump(mode="json"),
            },
        )
        assert modified.status_code == 200
        assert modified.json()["state"] == "READY"
        assert modified.json()["version"] == 3
        plans = await client.get(
            f"/workspaces/{workspace_id}/tasks/{task_id}/plans", headers=owner
        )
        assert [item["version"] for item in plans.json()] == [1, 2]
        assert {item["step_key"] for item in plans.json()[1]["steps"]} == {
            "collect",
            "report",
            "verify",
        }
        stale = await client.post(
            f"/tasks/{task_id}/modify",
            headers=owner,
            json={
                "workspace_id": str(workspace_id),
                "expected_version": 1,
                "decision": decision.model_dump(mode="json"),
            },
        )
        assert stale.status_code == 409
        forbidden = await client.post(
            f"/tasks/{task_id}/modify",
            headers=outsider,
            json={
                "workspace_id": str(workspace_id),
                "expected_version": 3,
                "decision": decision.model_dump(mode="json"),
            },
        )
        assert forbidden.status_code == 403
        async with app.state.sessions() as session:
            audit = await session.scalar(
                select(AuditRow).where(
                    AuditRow.workspace_id == workspace_id,
                    AuditRow.task_id == task_id,
                    AuditRow.action == "replan.applied",
                )
            )
            assert audit is not None
            assert audit.actor_type == "USER" and audit.actor_id == user_id
        incident_id = uuid4()
        async with app.state.sessions.begin() as session:
            session.add(
                ConsistencyIncidentRow(
                    id=incident_id,
                    workspace_id=workspace_id,
                    task_id=task_id,
                    severity="HIGH",
                    incident_type="TEST_STATE_MISMATCH",
                    detail={
                        "postgres_state": "RUNNING",
                        "temporal_status": "NOT_FOUND",
                        "repair": "OUTBOX_ENQUEUED",
                    },
                )
            )
        incidents = await client.get(
            "/consistency/incidents",
            headers=owner,
            params={"workspace_id": str(workspace_id)},
        )
        assert incidents.status_code == 200
        assert incidents.json()[-1]["incident_id"] == str(incident_id)
        assert (
            await client.get(
                "/consistency/incidents",
                headers=outsider,
                params={"workspace_id": str(workspace_id)},
            )
        ).status_code == 403
