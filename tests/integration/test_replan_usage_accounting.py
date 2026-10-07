import asyncio
import json
import os
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.contracts.domain import FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.models.settings import load_model_settings
from enterprise_agent.runtime.activities import generate_and_apply_replan
from enterprise_agent.runtime.budgets import record_actual_replan_llm_usage
from enterprise_agent.storage.model_configs import default_model_version_ref
from enterprise_agent.storage.replans import apply_replan
from enterprise_agent.storage.tables import TaskBudgetRow, UsageRecordRow
from tests.integration.test_replans import _decision, _seed_replan_task


class _ReplanReplyHandler(BaseHTTPRequestHandler):
    calls = 0
    content = "not-json"

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        assert self.path == "/v1/chat/completions"
        assert request["model"] == "local-replan-usage-test"
        type(self).calls += 1
        body = json.dumps(
            {
                "model": "local-replan-usage-test",
                "choices": [{"message": {"content": type(self).content}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class _ValidReplanReplyHandler(_ReplanReplyHandler):
    content = _decision("RETRY_STEP").model_copy(
        update={"trigger": "CAPABILITY_UNAVAILABLE"}
    ).model_dump_json()


@contextmanager
def _serve(handler: type[_ReplanReplyHandler]):
    handler.calls = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
async def database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _configure_model(monkeypatch, port: int, trace_id: str) -> None:
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("MODEL_MODE", "local")
    monkeypatch.setenv("MODEL_BASE_URL", f"http://127.0.0.1:{port}/v1")
    monkeypatch.setenv("MODEL_NAME", "local-replan-usage-test")
    monkeypatch.setenv("MODEL_API_KEY", "")
    monkeypatch.setattr(
        "enterprise_agent.runtime.activities._activity_trace_id",
        lambda: trace_id,
    )
    monkeypatch.setattr(
        "enterprise_agent.runtime.activities.activity.heartbeat",
        lambda *args: None,
    )


async def _run_replan_activity(
    database,
    monkeypatch,
    handler: type[_ReplanReplyHandler],
    *,
    trace_id: str,
    max_tokens: int,
):
    with _serve(handler) as server:
        _configure_model(monkeypatch, server.server_port, trace_id)
        workspace_id, task_id, plan = await _seed_replan_task(
            database,
            model_config_versions=(default_model_version_ref(load_model_settings()[0]),),
        )
        async with database.begin() as session:
            budget = await session.get(TaskBudgetRow, task_id)
            assert budget is not None
            budget.max_tokens = max_tokens
        result = await generate_and_apply_replan(
            {
                "workspace_id": str(workspace_id),
                "task_id": str(task_id),
                "plan_id": str(plan.plan_id),
                "failed_step_keys": ["analyze"],
                "trigger": "CAPABILITY_UNAVAILABLE",
                "reason": "provider unavailable",
            }
        )
    async with database() as session:
        budget = await session.get(TaskBudgetRow, task_id)
        usage = (
            await session.scalars(
                select(UsageRecordRow).where(
                    UsageRecordRow.workspace_id == workspace_id,
                    UsageRecordRow.task_id == task_id,
                )
            )
        ).all()
    actual = [item for item in usage if item.detail.get("post_call_actual") is True]
    return result, budget, actual


async def test_successful_replan_reply_is_settled_once_without_aggregate_double_charge(
    database, monkeypatch
):
    result, budget, actual = await _run_replan_activity(
        database,
        monkeypatch,
        _ValidReplanReplyHandler,
        trace_id="trace-replan-success-usage",
        max_tokens=10_000,
    )

    assert result["status"] == "APPLIED"
    assert _ValidReplanReplyHandler.calls == 1
    assert budget is not None
    assert budget.used_llm_calls == 1 and budget.used_tokens == 30
    assert len(actual) == 1 and actual[0].tokens == 30
    assert actual[0].detail["replan_from_plan_id"]


async def test_invalid_reply_is_settled_before_repair_is_rejected_by_budget(
    database, monkeypatch
):
    result, budget, actual = await _run_replan_activity(
        database,
        monkeypatch,
        _ReplanReplyHandler,
        trace_id="trace-replan-repair-budget",
        max_tokens=25,
    )

    assert result["status"] == "ABORTED"
    assert _ReplanReplyHandler.calls == 1
    assert budget is not None
    assert budget.used_llm_calls == 1 and budget.used_tokens == 30
    assert budget.state == "EXCEEDED" and budget.last_exceeded_reason == "tokens"
    assert len(actual) == 1 and actual[0].tokens == 30


async def test_final_schema_failure_keeps_every_consumed_reply_in_usage_ledger(
    database, monkeypatch
):
    result, budget, actual = await _run_replan_activity(
        database,
        monkeypatch,
        _ReplanReplyHandler,
        trace_id="trace-replan-schema-failure",
        max_tokens=10_000,
    )

    assert result["status"] == "ABORTED"
    assert _ReplanReplyHandler.calls == 3
    assert budget is not None
    assert budget.used_llm_calls == 3 and budget.used_tokens == 90
    assert len(actual) == 3
    assert [item.tokens for item in actual] == [30, 30, 30]


async def test_replan_usage_settlement_is_idempotent_and_rejects_late_plan_owner(
    database,
):
    workspace_id, task_id, plan = await _seed_replan_task(database)
    actor_id = uuid4()

    async def settle() -> int:
        async with database.begin() as session:
            return await record_actual_replan_llm_usage(
                session,
                workspace_id,
                task_id,
                plan.plan_id,
                actor_id,
                "trace-replan-settlement-race",
                "activity-attempt-1:reply:1",
                tokens=30,
            )

    versions = await asyncio.gather(settle(), settle())
    assert versions[0] == versions[1]
    async with database() as session:
        budget = await session.get(TaskBudgetRow, task_id)
        usage = (
            await session.scalars(
                select(UsageRecordRow).where(
                    UsageRecordRow.workspace_id == workspace_id,
                    UsageRecordRow.task_id == task_id,
                )
            )
        ).all()
    actual = [item for item in usage if item.detail.get("post_call_actual") is True]
    assert budget is not None and budget.used_tokens == 30
    assert len(actual) == 1

    async with database.begin() as session:
        applied = await apply_replan(
            session,
            workspace_id,
            task_id,
            actor_id,
            "trace-replan-winner",
            _decision("RETRY_STEP"),
            expected_from_plan_id=plan.plan_id,
        )
    assert applied.to_plan_id is not None

    with pytest.raises(DomainError) as raised:
        async with database.begin() as session:
            await record_actual_replan_llm_usage(
                session,
                workspace_id,
                task_id,
                plan.plan_id,
                actor_id,
                "trace-late-replan-usage",
                "activity-attempt-1:reply:2",
                tokens=30,
            )
    assert raised.value.code is FailureCode.STALE_ATTEMPT
    async with database() as session:
        budget = await session.get(TaskBudgetRow, task_id)
    assert budget is not None and budget.used_tokens == 30
