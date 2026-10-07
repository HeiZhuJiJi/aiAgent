import base64
import json
import os
from uuid import UUID, uuid4

import httpx
import pytest

from apps.api.main import create_app
from enterprise_agent.capabilities.builtins import _task_model_chain
from enterprise_agent.contracts.domain import CapabilityRequest, FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.runtime.activities import _model_settings_for_task
from enterprise_agent.storage.plans import load_snapshot
from enterprise_agent.storage.tables import TaskRow
from tests.integration.test_api_identity import register_and_login


async def test_legacy_task_snapshot_keeps_history_but_agent_execution_uses_selected_model(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "SECRET_MASTER_KEY",
        base64.urlsafe_b64encode(os.urandom(32)).decode(),
    )
    app = create_app(os.environ["TEST_DATABASE_URL"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        owner = await register_and_login(client, "fallback-snapshot-owner")
        workspace = await client.post(
            "/workspaces",
            headers=owner,
            json={"name": "Fallback Snapshot Workspace"},
        )
        assert workspace.status_code == 201
        workspace_id = UUID(workspace.json()["workspace_id"])
        primary_id, fallback_id = uuid4(), uuid4()

        for config_id, provider, port, model_name in (
            (primary_id, "DeepSeek", 65431, "primary-chat"),
            (fallback_id, "Qwen", 65432, "fallback-chat"),
        ):
            created = await client.post(
                "/models",
                headers=owner,
                json={
                    "config_id": str(config_id),
                    "workspace_id": str(workspace_id),
                    "kind": "CHAT",
                    "provider": provider,
                    "base_url": f"http://127.0.0.1:{port}/v1",
                    "model_name": model_name,
                    "external": False,
                },
            )
            assert created.status_code == 201

        created_task = await client.post(
            "/tasks",
            headers=owner,
            json={
                "goal": "Produce a durable answer",
                "constraints": [],
                "deliverables": ["answer"],
                "workspace_id": str(workspace_id),
                "time_budget_sec": 60,
                "token_budget": 1000,
                "cost_budget": 1,
                "user_context": json.dumps(
                    {
                        "model_config_id": str(primary_id),
                        "fallback_model_config_id": str(fallback_id),
                    }
                ),
            },
        )
        assert created_task.status_code == 201

        async with app.state.sessions() as session:
            task = await session.get(TaskRow, UUID(created_task.json()["task_id"]))
            assert task is not None
            snapshot = await load_snapshot(
                session,
                workspace_id,
                task.execution_snapshot_id,
            )

        assert [ref.identifier for ref in snapshot.model_config_versions] == [
            f"model.config:{primary_id}",
            f"model.config:{fallback_id}",
        ]
        assert [ref.version for ref in snapshot.model_config_versions] == [
            "1:DeepSeek:primary-chat",
            "1:Qwen:fallback-chat",
        ]

        updated = await client.put(
            f"/models/{primary_id}",
            headers=owner,
            json={
                "workspace_id": str(workspace_id),
                "expected_version": 1,
                "kind": "CHAT",
                "provider": "DeepSeek",
                "base_url": "http://127.0.0.1:65431/v1",
                "model_name": "primary-chat-v2",
                "external": False,
            },
        )
        assert updated.status_code == 200
        disabled = await client.post(
            f"/models/{fallback_id}/state",
            headers=owner,
            json={
                "workspace_id": str(workspace_id),
                "expected_version": 1,
                "enabled": False,
            },
        )
        assert disabled.status_code == 200

        chain, _, _ = await _task_model_chain(
            app.state.sessions,
            CapabilityRequest(
                command_id=uuid4(),
                operation_id=uuid4(),
                workspace_id=workspace_id,
                task_id=UUID(created_task.json()["task_id"]),
                step_id=uuid4(),
                attempt_id=uuid4(),
                fencing_token=1,
                capability_id="builtin.model.generate",
                capability_version="1.0.0",
                arguments={},
            ),
        )
        assert [(config.provider, config.model) for config, _ in chain] == [
            ("DeepSeek", "primary-chat"),
        ]

        async with app.state.sessions() as session:
            task = await session.get(TaskRow, UUID(created_task.json()["task_id"]))
            assert task is not None and task.execution_snapshot_id is not None
            replan_config, _ = await _model_settings_for_task(
                session,
                workspace_id,
                task.execution_snapshot_id,
            )
        assert (replan_config.provider, replan_config.model) == (
            "DeepSeek",
            "primary-chat",
        )


async def test_durable_default_model_fails_closed_when_environment_changes(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MODEL_MODE", "local")
    monkeypatch.setenv("MODEL_BASE_URL", "http://127.0.0.1:65441/v1")
    monkeypatch.setenv("MODEL_NAME", "frozen-default")
    monkeypatch.setenv("MODEL_API_KEY", "bootstrap-secret")
    app = create_app(os.environ["TEST_DATABASE_URL"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        owner = await register_and_login(client, "default-snapshot-owner")
        workspace = await client.post(
            "/workspaces",
            headers=owner,
            json={"name": "Default Snapshot Workspace"},
        )
        assert workspace.status_code == 201
        workspace_id = UUID(workspace.json()["workspace_id"])
        created_task = await client.post(
            "/tasks",
            headers=owner,
            json={
                "goal": "Produce a durable answer with the bootstrap model",
                "constraints": [],
                "deliverables": ["answer"],
                "workspace_id": str(workspace_id),
                "time_budget_sec": 60,
                "token_budget": 1000,
                "cost_budget": 1,
            },
        )
        assert created_task.status_code == 201
        task_id = UUID(created_task.json()["task_id"])

        async with app.state.sessions() as session:
            task = await session.get(TaskRow, task_id)
            assert task is not None and task.execution_snapshot_id is not None
            snapshot = await load_snapshot(
                session,
                workspace_id,
                task.execution_snapshot_id,
            )
        assert snapshot.model_config_versions[0].identifier == "model.default"
        assert "frozen-default" in snapshot.model_config_versions[0].version
        assert "http://127.0.0.1:65441/v1" in snapshot.model_config_versions[0].version
        assert "bootstrap-secret" not in snapshot.model_dump_json()

        monkeypatch.setenv("MODEL_BASE_URL", "http://127.0.0.1:65442/v1")
        request = CapabilityRequest(
            command_id=uuid4(),
            operation_id=uuid4(),
            workspace_id=workspace_id,
            task_id=task_id,
            step_id=uuid4(),
            attempt_id=uuid4(),
            fencing_token=1,
            capability_id="builtin.model.generate",
            capability_version="1.0.0",
            arguments={},
        )
        with pytest.raises(DomainError) as builtins_error:
            await _task_model_chain(app.state.sessions, request)
        assert builtins_error.value.code is FailureCode.CONSISTENCY_VIOLATION

        async with app.state.sessions() as session:
            task = await session.get(TaskRow, task_id)
            assert task is not None and task.execution_snapshot_id is not None
            with pytest.raises(DomainError) as replan_error:
                await _model_settings_for_task(
                    session,
                    workspace_id,
                    task.execution_snapshot_id,
                )
        assert replan_error.value.code is FailureCode.CONSISTENCY_VIOLATION
