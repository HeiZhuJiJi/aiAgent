import json
import os
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.contracts.domain import (
    CapabilityRequirement,
    ExecutionSnapshot,
    Plan,
    PlanStepSpec,
    ReplanDecision,
    Risk,
    StepState,
    TaskGoal,
    TaskState,
    VersionRef,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.models.settings import load_model_settings
from enterprise_agent.runtime.activities import (
    generate_and_apply_replan,
    load_plan_execution_state,
)
from enterprise_agent.runtime.budgets import new_task_budget
from enterprise_agent.storage.model_configs import default_model_version_ref
from enterprise_agent.storage.plans import load_plan_versions, persist_plan, persist_snapshot
from enterprise_agent.storage.replans import apply_replan, list_replans, select_replan_trigger
from enterprise_agent.storage.steps import materialize_plan_steps, plan_step_id
from enterprise_agent.storage.tables import (
    AttemptRow,
    ReplanDecisionRow,
    RuntimeGuardRow,
    StepRow,
    TaskBudgetRow,
    TaskRow,
    Workspace,
)


class ReplanModelHandler(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        assert self.path == "/v1/chat/completions"
        assert request["model"] == "local-replan-test"
        type(self).calls += 1
        decision = _decision("RETRY_STEP").model_copy(
            update={"trigger": "CAPABILITY_UNAVAILABLE"}
        ).model_dump_json()
        body = json.dumps(
            {
                "model": "local-replan-test",
                "choices": [{"message": {"content": decision}}],
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


@pytest.fixture
async def database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _ref(identifier: str) -> VersionRef:
    return VersionRef(identifier=identifier, version="1.0.0", sha256="a" * 64)


def _step(
    key: str,
    *,
    dependencies: tuple[str, ...] = (),
    objective: str | None = None,
) -> PlanStepSpec:
    return PlanStepSpec(
        step_key=key,
        objective=objective or key,
        dependencies=dependencies,
        capability_requirements=(
            CapabilityRequirement(capability_id="test.read", version_range=">=1,<2"),
        ),
        expected_output="evidence",
        success_criteria=("valid",),
        risk_hint=Risk.READ,
        execution_mode="CAPABILITY",
        parallelizable=True,
        max_attempts=2,
    )


async def _seed_replan_task(
    database,
    *,
    max_replans: int = 5,
    model_config_versions: tuple[VersionRef, ...] | None = None,
):
    workspace_id, task_id, snapshot_id = uuid4(), uuid4(), uuid4()
    goal = TaskGoal(
        goal="assess supplier",
        constraints=(),
        deliverables=("report",),
        workspace_id=workspace_id,
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
        model_config_versions=model_config_versions or (_ref("model"),),
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
        plan_id=uuid4(),
        task_id=task_id,
        workspace_id=workspace_id,
        execution_snapshot_id=snapshot_id,
        version=1,
        goal=goal.goal,
        constraints=goal.constraints,
        deliverables=goal.deliverables,
        steps=(
            _step("collect"),
            _step("analyze", dependencies=("collect",)),
            _step("report", dependencies=("analyze",)),
        ),
    )
    async with database.begin() as session:
        session.add(Workspace(id=workspace_id, name="replan-test"))
        await session.flush()
        await persist_snapshot(session, snapshot)
        session.add(
            TaskRow(
                id=task_id,
                workspace_id=workspace_id,
                state="RUNNING",
                execution_snapshot_id=snapshot_id,
                goal_payload=goal.model_dump(mode="json"),
            )
        )
        await session.flush()
        budget = new_task_budget(task_id, goal)
        budget.max_replans = max_replans
        budget.deadline = datetime.now(UTC) + timedelta(minutes=10)
        session.add(budget)
        session.add(RuntimeGuardRow(task_id=task_id, workspace_id=workspace_id))
        await persist_plan(session, plan)
        await materialize_plan_steps(session, plan)
        collect = await session.get(StepRow, plan_step_id(plan.plan_id, "collect"))
        analyze = await session.get(StepRow, plan_step_id(plan.plan_id, "analyze"))
        assert collect is not None and analyze is not None
        collect.state = StepState.COMPLETED.value
        analyze.state = StepState.FAILED.value
    return workspace_id, task_id, plan


def _decision(action: str) -> ReplanDecision:
    new_steps: tuple[PlanStepSpec, ...] = ()
    affected: tuple[str, ...] = ()
    if action == "ADD_STEP":
        new_steps = (_step("verify", dependencies=("collect",)),)
    elif action == "REPLACE_STEP":
        affected = ("analyze",)
        new_steps = (
            _step("analyze", dependencies=("collect",), objective="replacement analysis"),
        )
    elif action == "EXPAND_STEP":
        affected = ("analyze",)
        new_steps = (
            _step("analyze-detail", dependencies=("collect",)),
            _step("analyze", dependencies=("analyze-detail",), objective="aggregate analysis"),
        )
    elif action in {"RETRY_STEP", "SKIP_STEP"}:
        affected = ("analyze",)
    return ReplanDecision(
        reason=f"exercise {action}",
        trigger="EVIDENCE_INSUFFICIENT",
        action=action,
        affected_steps=affected,
        new_steps=new_steps,
        preserve_completed_steps=True,
        budget_impact=0.25,
    )


@pytest.mark.parametrize(
    ("action", "expected_states"),
    [
        (
            "ADD_STEP",
            {
                "collect": "COMPLETED",
                "analyze": "READY",
                "report": "BLOCKED",
                "verify": "READY",
            },
        ),
        (
            "REPLACE_STEP",
            {"collect": "COMPLETED", "analyze": "READY", "report": "BLOCKED"},
        ),
        (
            "EXPAND_STEP",
            {
                "collect": "COMPLETED",
                "analyze-detail": "READY",
                "analyze": "BLOCKED",
                "report": "BLOCKED",
            },
        ),
        (
            "RETRY_STEP",
            {"collect": "COMPLETED", "analyze": "READY", "report": "BLOCKED"},
        ),
        (
            "SKIP_STEP",
            {"collect": "COMPLETED", "analyze": "SKIPPED", "report": "READY"},
        ),
    ],
)
async def test_replan_actions_append_plan_and_preserve_completed_work(
    database, action, expected_states
):
    workspace_id, task_id, original = await _seed_replan_task(database)
    decision = _decision(action)
    actor_id = uuid4()
    async with database.begin() as session:
        applied = await apply_replan(
            session,
            workspace_id,
            task_id,
            actor_id,
            "trace-replan",
            decision,
        )
    assert applied.status == "APPLIED"
    assert applied.from_plan_id == original.plan_id
    assert applied.to_plan_id is not None

    async with database.begin() as session:
        replay = await apply_replan(
            session,
            workspace_id,
            task_id,
            actor_id,
            "trace-replan-replay",
            decision,
        )
    assert replay == applied

    async with database() as session:
        versions = await load_plan_versions(session, workspace_id, task_id)
        replans = await list_replans(session, workspace_id, task_id)
        new_rows = (
            await session.scalars(
                select(StepRow).where(
                    StepRow.workspace_id == workspace_id,
                    StepRow.plan_id == applied.to_plan_id,
                )
            )
        ).all()
        old_rows = (
            await session.scalars(
                select(StepRow).where(
                    StepRow.workspace_id == workspace_id,
                    StepRow.plan_id == original.plan_id,
                )
            )
        ).all()
        task = await session.get(TaskRow, task_id)
        budget = await session.get(TaskBudgetRow, task_id)
    assert [item.version for item in versions] == [1, 2]
    assert versions[0] == original
    assert replans == (applied,)
    assert {row.step_key: row.state for row in new_rows} == expected_states
    assert {row.step_key: row.state for row in old_rows} == {
        "collect": "COMPLETED",
        "analyze": "SUPERSEDED",
        "report": "SUPERSEDED",
    }
    assert task is not None and task.state == "READY"
    assert budget is not None and budget.used_replans == 1 and budget.used_cost == 0.25


async def test_abort_and_budget_denial_leave_no_untracked_plan(database):
    for action, max_replans in (("ABORT_TASK", 5), ("RETRY_STEP", 0)):
        workspace_id, task_id, original = await _seed_replan_task(
            database, max_replans=max_replans
        )
        decision = _decision(action)
        async with database.begin() as session:
            result = await apply_replan(
                session,
                workspace_id,
                task_id,
                uuid4(),
                "trace-replan-abort",
                decision,
            )
        async with database() as session:
            task = await session.get(TaskRow, task_id)
            versions = await load_plan_versions(session, workspace_id, task_id)
        assert result.status == "ABORTED" and result.to_plan_id is None
        assert task is not None and task.state == "FAILED"
        assert versions == (original,)


async def test_replan_decision_is_immutable_and_divergent_replay_is_rejected(database):
    workspace_id, task_id, _ = await _seed_replan_task(database)
    decision = _decision("RETRY_STEP")
    async with database.begin() as session:
        applied = await apply_replan(
            session, workspace_id, task_id, uuid4(), "trace-immutable-replan", decision
        )
    with pytest.raises(DomainError, match="different replan decision"):
        async with database.begin() as session:
            await apply_replan(
                session,
                workspace_id,
                task_id,
                uuid4(),
                "trace-divergent-replan",
                decision.model_copy(update={"reason": "changed"}),
            )
    for statement in (
        update(ReplanDecisionRow).values(status="ABORTED"),
        delete(ReplanDecisionRow),
    ):
        with pytest.raises(DBAPIError):
            async with database.begin() as session:
                await session.execute(statement.where(ReplanDecisionRow.id == applied.replan_id))


async def test_consecutive_replans_use_explicit_source_plan_fencing(database):
    workspace_id, task_id, original = await _seed_replan_task(database)
    first_decision = _decision("RETRY_STEP")
    async with database.begin() as session:
        first = await apply_replan(
            session,
            workspace_id,
            task_id,
            uuid4(),
            "trace-first-replan",
            first_decision,
            expected_from_plan_id=original.plan_id,
        )
    assert first.to_plan_id is not None

    async with database.begin() as session:
        task = await session.get(TaskRow, task_id)
        failed = await session.get(StepRow, plan_step_id(first.to_plan_id, "analyze"))
        assert task is not None and failed is not None
        task.state = TaskState.RUNNING.value
        failed.state = StepState.FAILED.value

    second_decision = first_decision.model_copy(update={"reason": "retry after second failure"})
    async with database.begin() as session:
        second = await apply_replan(
            session,
            workspace_id,
            task_id,
            uuid4(),
            "trace-second-replan",
            second_decision,
            expected_from_plan_id=first.to_plan_id,
        )
    assert second.from_plan_id == first.to_plan_id
    assert second.to_plan_id is not None
    assert second.to_plan_id != first.to_plan_id

    async with database.begin() as session:
        replay = await apply_replan(
            session,
            workspace_id,
            task_id,
            uuid4(),
            "trace-second-replan-replay",
            second_decision,
            expected_from_plan_id=first.to_plan_id,
        )
    assert replay == second


def test_deterministic_replan_trigger_uses_frozen_priority():
    assert (
        select_replan_trigger(
            terminal_failure=True,
            evidence_insufficient=True,
            capability_unavailable=True,
            repeated_validation_failure=True,
            user_modification=True,
            budget_danger=True,
        )
        == "TERMINAL_FAILURE"
    )
    assert select_replan_trigger(evidence_insufficient=True) == "EVIDENCE_INSUFFICIENT"
    assert select_replan_trigger(capability_unavailable=True) == "CAPABILITY_UNAVAILABLE"
    assert select_replan_trigger(repeated_validation_failure=True) == "VALIDATION_FAILURE"
    assert select_replan_trigger(user_modification=True) == "USER_MODIFICATION"
    assert select_replan_trigger(budget_danger=True) == "BUDGET_DANGER"
    assert select_replan_trigger() is None


async def test_failed_step_detection_and_activity_use_real_structured_model_http(
    database, monkeypatch
):
    ReplanModelHandler.calls = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), ReplanModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("MODEL_MODE", "local")
    monkeypatch.setenv("MODEL_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("MODEL_NAME", "local-replan-test")
    monkeypatch.setenv("MODEL_API_KEY", "")
    workspace_id, task_id, original = await _seed_replan_task(
        database,
        model_config_versions=(default_model_version_ref(load_model_settings()[0]),),
    )
    analyze_id = plan_step_id(original.plan_id, "analyze")
    async with database.begin() as session:
        session.add(
            AttemptRow(
                id=uuid4(),
                workspace_id=workspace_id,
                step_id=analyze_id,
                worker_id="failed-worker",
                fencing_token=1,
                state="FAILED",
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
                failure_payload={
                    "schema_version": 1,
                    "code": "CAPABILITY_UNAVAILABLE",
                    "safe_detail": "provider unavailable",
                    "trace_id": "trace-provider",
                },
                trace_id="trace-provider",
            )
        )
    monkeypatch.setattr(
        "enterprise_agent.runtime.activities._activity_trace_id",
        lambda: "trace-auto-replan",
    )
    monkeypatch.setattr(
        "enterprise_agent.runtime.activities.activity.heartbeat",
        lambda *args: None,
    )
    state = await load_plan_execution_state(
        {"workspace_id": str(workspace_id), "task_id": str(task_id)}
    )
    assert state["state"] == "REPLAN_REQUIRED"
    assert state["plan_id"] == str(original.plan_id)
    assert state["failed_step_keys"] == ["analyze"]
    assert state["replan_trigger"] == "CAPABILITY_UNAVAILABLE"

    try:
        result = await generate_and_apply_replan(
            {
                "workspace_id": str(workspace_id),
                "task_id": str(task_id),
                "plan_id": state["plan_id"],
                "failed_step_keys": state["failed_step_keys"],
                "trigger": state["replan_trigger"],
                "reason": state["replan_reason"],
            }
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert result["status"] == "APPLIED"
    assert ReplanModelHandler.calls == 1
    async with database() as session:
        versions = await load_plan_versions(session, workspace_id, task_id)
        budget = await session.get(TaskBudgetRow, task_id)
    assert [item.version for item in versions] == [1, 2]
    assert budget is not None
    assert budget.used_llm_calls == 1
    assert budget.used_tokens == 30
    assert budget.used_replans == 1


async def test_evidence_insufficiency_failure_selects_evidence_replan_trigger(
    database, monkeypatch
):
    workspace_id, task_id, original = await _seed_replan_task(database)
    analyze_id = plan_step_id(original.plan_id, "analyze")
    async with database.begin() as session:
        session.add(
            AttemptRow(
                id=uuid4(),
                workspace_id=workspace_id,
                step_id=analyze_id,
                worker_id="evidence-gap-worker",
                fencing_token=1,
                state="FAILED",
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
                failure_payload={
                    "schema_version": 1,
                    "code": "VALIDATION_FAILED",
                    "safe_detail": (
                        "EVIDENCE_INSUFFICIENT: structured analysis requested more evidence"
                    ),
                    "trace_id": "trace-evidence-gap",
                },
                trace_id="trace-evidence-gap",
            )
        )
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setattr(
        "enterprise_agent.runtime.activities._activity_trace_id",
        lambda: "trace-evidence-state",
    )

    state = await load_plan_execution_state(
        {"workspace_id": str(workspace_id), "task_id": str(task_id)}
    )

    assert state["state"] == "REPLAN_REQUIRED"
    assert state["plan_id"] == str(original.plan_id)
    assert state["failed_step_keys"] == ["analyze"]
    assert state["replan_trigger"] == "EVIDENCE_INSUFFICIENT"


async def test_due_retry_is_returned_to_the_existing_attempt_path(database, monkeypatch):
    workspace_id, task_id, original = await _seed_replan_task(database)
    analyze_id = plan_step_id(original.plan_id, "analyze")
    async with database.begin() as session:
        analyze = await session.get(StepRow, analyze_id)
        assert analyze is not None
        analyze.state = StepState.RETRY_WAIT.value
        analyze.retry_available_at = datetime.now(UTC) - timedelta(seconds=1)
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])

    state = await load_plan_execution_state(
        {"workspace_id": str(workspace_id), "task_id": str(task_id)}
    )

    assert state["state"] == "READY"
    assert state["ready_step_ids"] == [str(analyze_id)]
    async with database() as session:
        persisted = await session.get(StepRow, analyze_id)
    assert persisted is not None
    assert persisted.state == StepState.RETRY_WAIT.value


async def test_future_retry_preserves_its_remaining_wait(database, monkeypatch):
    workspace_id, task_id, original = await _seed_replan_task(database)
    analyze_id = plan_step_id(original.plan_id, "analyze")
    async with database.begin() as session:
        analyze = await session.get(StepRow, analyze_id)
        assert analyze is not None
        analyze.state = StepState.RETRY_WAIT.value
        analyze.retry_available_at = datetime.now(UTC) + timedelta(seconds=30)
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])

    state = await load_plan_execution_state(
        {"workspace_id": str(workspace_id), "task_id": str(task_id)}
    )

    assert state["state"] == "RETRY_WAIT"
    assert 0 < state["retry_after_seconds"] <= 30


async def test_replacing_completed_collection_does_not_inherit_completed_state(database):
    workspace_id, task_id, original = await _seed_replan_task(database)
    revised = _step("collect", objective="a different evidence query")
    async with database.begin() as session:
        applied = await apply_replan(
            session, workspace_id, task_id, task_id, "trace-refined-collection",
            ReplanDecision(
                reason="collect missing evidence", trigger="EVIDENCE_INSUFFICIENT",
                action="REPLACE_STEP", affected_steps=("collect",), new_steps=(revised,),
                preserve_completed_steps=True, budget_impact=0,
            ), expected_from_plan_id=original.plan_id,
        )
    async with database() as session:
        step = await session.get(StepRow, plan_step_id(applied.to_plan_id, "collect"))
    assert step is not None and step.state == StepState.READY.value


async def test_recovery_collection_uses_only_persisted_gap_and_retains_original_scope(database):
    from enterprise_agent.capabilities.builtins import (
        _optional_model_timeout_seconds,
        _planned_web_queries,
        _task_user_context,
    )
    from enterprise_agent.contracts.domain import CapabilityRequest
    workspace_id, task_id, original = await _seed_replan_task(database)
    context = {"original_request": "Compare current inventory and delivery", "research_plan": {
        "web_queries": ["current inventory", "current delivery"], "retrieval_queries": [], "result_limit": 5,
        "source_plan": {"required_sources": ["PUBLIC_WEB"], "preferred_sources": ["OFFICIAL_OR_FIRST_PARTY"], "fallback_sources": ["GENERAL_WEB"], "forbidden_sources": ["PARAMETRIC_KNOWLEDGE"]},
        "answer_contract": {"answer_type": "FACT", "required_aspects": ["inventory", "delivery"], "completeness": "COMPLETE", "freshness": "CURRENT", "minimum_answer_items": 1, "maximum_answer_items": 4, "require_explicit_dates": False, "minimum_evidence_items": 1, "minimum_independent_sources": 1, "partial_answer_policy": "ALLOW_SUPPORTED_ONLY", "organization": "CONCISE"}}}
    async with database.begin() as session:
        row = await session.get(TaskRow, task_id)
        goal = TaskGoal.model_validate(row.goal_payload)
        row.goal_payload = goal.model_copy(update={"user_context": json.dumps(context)}).model_dump(mode="json")
        applied = await apply_replan(session, workspace_id, task_id, task_id, "trace-gap-only",
            ReplanDecision(reason="missing delivery", trigger="EVIDENCE_INSUFFICIENT", action="REPLACE_STEP",
                affected_steps=("collect",), new_steps=(_step("collect", objective="delivery date confirmed official"),),
                preserve_completed_steps=True, budget_impact=0), expected_from_plan_id=original.plan_id)
    request = CapabilityRequest(command_id=uuid4(), operation_id=uuid4(), workspace_id=workspace_id,
        task_id=task_id, step_id=plan_step_id(applied.to_plan_id, "collect"), attempt_id=uuid4(), fencing_token=1,
        capability_id="web.search", capability_version="1.0.0", arguments={"query": "delivery date confirmed official"})
    recovered = await _task_user_context(database, request)
    assert _planned_web_queries(recovered, "untrusted override") == ("delivery date confirmed official",)
    assert recovered["original_request"] == context["original_request"]
    assert recovered["research_plan"]["answer_contract"] == context["research_plan"]["answer_contract"] | {"schema_version": 1}
    async with database.begin() as session:
        budget = await session.scalar(select(TaskBudgetRow).where(TaskBudgetRow.task_id == task_id))
        budget.deadline = datetime.now(UTC) + timedelta(seconds=6)
    assert await _optional_model_timeout_seconds(database, request, minimum_remaining=4) is None
