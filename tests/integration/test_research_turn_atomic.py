import asyncio
import copy
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

from apps.api.main import create_app
from enterprise_agent.capabilities.builtins import builtin_descriptors
from enterprise_agent.contracts.domain import FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.skills.package import validate_package
from enterprise_agent.storage import tasks as task_storage
from enterprise_agent.storage.tables import (
    ConversationMessageRow,
    ConversationTurnAttemptRow,
    ConversationTurnRow,
    OutboxEventRow,
    PlanRow,
    RuntimeGuardRow,
    StepRow,
    TaskBudgetRow,
    TaskRow,
)
from enterprise_agent.storage.tasks import research_task_id
from tests.integration.test_api_identity import register_and_login


def web_research_plan() -> dict[str, object]:
    return {
        "web_queries": ["today news"],
        "retrieval_queries": [],
        "source_plan": {
            "required_sources": ["PUBLIC_WEB"],
            "preferred_sources": ["OFFICIAL_OR_FIRST_PARTY", "INDEPENDENT_MEDIA"],
            "fallback_sources": ["GENERAL_WEB"],
            "forbidden_sources": ["PARAMETRIC_KNOWLEDGE"],
        },
        "result_limit": 5,
        "answer_contract": {
            "answer_type": "LIST",
            "required_aspects": ["distinct events", "supported dates"],
            "completeness": "COMPLETE",
            "freshness": "CURRENT",
            "minimum_answer_items": 2,
            "maximum_answer_items": 5,
            "require_explicit_dates": True,
            "minimum_evidence_items": 2,
            "minimum_independent_sources": 2,
            "partial_answer_policy": "ALLOW_SUPPORTED_ONLY",
            "organization": "BULLETS",
        },
    }


async def test_research_turn_creation_is_atomic_idempotent_and_authoritative(
    monkeypatch,
) -> None:
    # Task creation freezes the default model identity even though this test
    # never calls the provider. Keep that identity deterministic and independent
    # from the operator's live Compose model configuration.
    monkeypatch.setenv("MODEL_MODE", "local")
    monkeypatch.setenv("MODEL_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("MODEL_NAME", "atomic-research-test")
    monkeypatch.setenv("MODEL_API_KEY", "")
    root = Path(__file__).resolve().parents[2]
    package = validate_package(
        (root / "apps/web/public/builtin-skills/web-research.skill.zip").read_bytes(),
        builtin_descriptors(),
    )

    async def load_web_research(session, workspace_id, name):
        assert name == "web-research"
        return package

    monkeypatch.setattr(task_storage, "load_enabled_skill", load_web_research)
    app = create_app(os.environ["TEST_DATABASE_URL"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        owner = await register_and_login(client, "atomic-research-turn")
        workspace_id = UUID(
            (
                await client.post(
                    "/workspaces",
                    headers=owner,
                    json={"name": "Atomic research Turn"},
                )
            ).json()["workspace_id"]
        )
        conversation_id = uuid4()
        assert (
            await client.post(
                "/conversations",
                headers=owner,
                json={
                    "conversation_id": str(conversation_id),
                    "workspace_id": str(workspace_id),
                    "title": "Research",
                },
            )
        ).status_code == 201
        user_message_id = uuid4()
        task_goal = {
            "goal": "What happened today?",
            "constraints": ["cite evidence"],
            "deliverables": ["answer"],
            "workspace_id": str(workspace_id),
            "time_budget_sec": 60,
            "token_budget": 24000,
            "cost_budget": 40,
            "requested_skill": "web-research",
            "user_context": json.dumps(
                {
                    "query": "today news",
                    "instruction": "summarize today's news",
                    "research_plan": web_research_plan(),
                }
            ),
        }
        payload = {
            "workspace_id": str(workspace_id),
            "conversation_id": str(conversation_id),
            "user_message_id": str(user_message_id),
            "content": "What happened today?",
            "goal": task_goal,
        }

        tampered_payload = copy.deepcopy(payload)
        tampered_message_id = uuid4()
        tampered_payload["user_message_id"] = str(tampered_message_id)
        tampered_context = json.loads(tampered_payload["goal"]["user_context"])
        tampered_context["research_plan"]["source_plan"]["required_sources"] = [
            "SELECTED_DOCUMENT"
        ]
        tampered_payload["goal"]["user_context"] = json.dumps(tampered_context)
        tampered = await client.post("/research-turns", headers=owner, json=tampered_payload)
        assert tampered.status_code == 409
        assert tampered.json()["error"]["code"] == "VALIDATION_FAILED"
        async with app.state.sessions() as session:
            assert await session.get(ConversationMessageRow, tampered_message_id) is None

        first, replay = await asyncio.gather(
            client.post("/research-turns", headers=owner, json=payload),
            client.post("/research-turns", headers=owner, json=payload),
        )
        assert first.status_code == 201, first.text
        assert replay.status_code == 201, replay.text
        expected_task_id = research_task_id(
            workspace_id,
            conversation_id,
            user_message_id,
        )
        assert UUID(first.json()["task"]["task_id"]) == expected_task_id
        assert replay.json()["task"]["task_id"] == first.json()["task"]["task_id"]
        assert replay.json()["user_message"] == first.json()["user_message"]

        mismatched = copy.deepcopy(payload)
        mismatched["goal"]["constraints"] = ["different input"]
        mismatch = await client.post(
            "/research-turns",
            headers=owner,
            json=mismatched,
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["error"]["code"] == "CONSISTENCY_VIOLATION"

        async with app.state.sessions() as session:
            messages = (
                await session.scalars(
                    select(ConversationMessageRow).where(
                        ConversationMessageRow.workspace_id == workspace_id,
                        ConversationMessageRow.id == user_message_id,
                    )
                )
            ).all()
            turns = (
                await session.scalars(
                    select(ConversationTurnRow).where(
                        ConversationTurnRow.workspace_id == workspace_id,
                        ConversationTurnRow.user_message_id == user_message_id,
                    )
                )
            ).all()
            attempts = (
                await session.scalars(
                    select(ConversationTurnAttemptRow).where(
                        ConversationTurnAttemptRow.workspace_id == workspace_id,
                        ConversationTurnAttemptRow.turn_id == turns[0].id,
                    )
                )
            ).all()
            tasks = (
                await session.scalars(
                    select(TaskRow).where(
                        TaskRow.workspace_id == workspace_id,
                        TaskRow.id == expected_task_id,
                    )
                )
            ).all()
            plans = (
                await session.scalars(
                    select(PlanRow).where(
                        PlanRow.workspace_id == workspace_id,
                        PlanRow.task_id == expected_task_id,
                    )
                )
            ).all()
            steps = (
                await session.scalars(
                    select(StepRow).where(
                        StepRow.workspace_id == workspace_id,
                        StepRow.task_id == expected_task_id,
                    )
                )
            ).all()
            events = (
                await session.scalars(
                    select(OutboxEventRow).where(
                        OutboxEventRow.workspace_id == workspace_id,
                        OutboxEventRow.aggregate_id == expected_task_id,
                        OutboxEventRow.event_type == "task.execution.requested",
                    )
                )
            ).all()
            budget = await session.get(TaskBudgetRow, expected_task_id)
            guard = await session.get(RuntimeGuardRow, expected_task_id)
        assert len(messages) == len(turns) == len(attempts) == len(tasks) == 1
        assert turns[0].task_id == expected_task_id
        assert turns[0].active_attempt_id == attempts[0].id
        assert len(plans) == len(events) == 1
        assert len(steps) == len(package.workflow.steps)
        assert budget is not None and guard is not None

        spoof_message_id = uuid4()
        spoof_goal = copy.deepcopy(task_goal)
        spoof_goal["goal"] = "Ordinary task cannot claim a Turn"
        spoof_goal["user_context"] = json.dumps(
            {
                "conversation_id": str(conversation_id),
                "user_message_id": str(spoof_message_id),
                "query": "untrusted binding",
            }
        )
        ordinary = await client.post("/tasks", headers=owner, json=spoof_goal)
        assert ordinary.status_code == 201, ordinary.text
        async with app.state.sessions() as session:
            spoofed_turn = await session.scalar(
                select(ConversationTurnRow).where(
                    ConversationTurnRow.workspace_id == workspace_id,
                    ConversationTurnRow.task_id == UUID(ordinary.json()["task_id"]),
                )
            )
        assert spoofed_turn is None

        async def fail_after_message(session, workspace_id, name):
            raise DomainError(FailureCode.CAPABILITY_UNAVAILABLE, "test failure")

        monkeypatch.setattr(task_storage, "load_enabled_skill", fail_after_message)
        failed_message_id = uuid4()
        failed_payload = copy.deepcopy(payload)
        failed_payload["user_message_id"] = str(failed_message_id)
        failed = await client.post(
            "/research-turns",
            headers=owner,
            json=failed_payload,
        )
        assert failed.status_code == 409
        failed_task_id = research_task_id(
            workspace_id,
            conversation_id,
            failed_message_id,
        )
        async with app.state.sessions() as session:
            assert await session.get(ConversationMessageRow, failed_message_id) is None
            assert await session.get(TaskRow, failed_task_id) is None
            failed_turn = await session.scalar(
                select(ConversationTurnRow.id).where(
                    ConversationTurnRow.workspace_id == workspace_id,
                    ConversationTurnRow.user_message_id == failed_message_id,
                )
            )
        assert failed_turn is None
