import hashlib
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from enterprise_agent.capabilities import builtins
from enterprise_agent.capabilities.executor import CapabilityExecutor
from enterprise_agent.contracts.domain import (
    CapabilityRequest,
    CapabilityRequirement,
    CapabilityResult,
    ConversationTurnOutcomeStatus,
    ExecutionSnapshot,
    FailureCode,
    Plan,
    PlanStepSpec,
    ReplanDecision,
    Risk,
    StructuredAnalysisResult,
    TaskGoal,
    VersionRef,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.governance.policy import PolicyInput
from enterprise_agent.runtime.activities import (
    ExecutePlanStepInput,
    execute_governed_plan_step,
    load_plan_execution_state,
)
from enterprise_agent.runtime.budgets import new_task_budget
from enterprise_agent.runtime.conversation_results import (
    NO_RELIABLE_RESULTS_MISSING_INFORMATION,
    is_no_reliable_results_payload,
    materialize_chat_task_turn,
)
from enterprise_agent.runtime.web_provider import HttpWebProvider, WebSearchResult
from enterprise_agent.storage.attempts import list_task_attempts, list_task_evidence
from enterprise_agent.storage.memory import append_conversation_message, create_conversation
from enterprise_agent.storage.plans import persist_plan, persist_snapshot
from enterprise_agent.storage.provenance import load_claims
from enterprise_agent.storage.replans import apply_replan
from enterprise_agent.storage.steps import materialize_plan_steps, plan_step_id
from enterprise_agent.storage.tables import (
    ConversationMessageRow,
    ConversationTurnOutcomeRow,
    ReplanDecisionRow,
    RuntimeGuardRow,
    TaskBudgetRow,
    TaskRow,
    UserRow,
    Workspace,
    WorkspaceMemberRow,
)
from enterprise_agent.storage.turns import (
    acquire_conversation_turn_attempt,
    create_conversation_turn,
    get_conversation_turn,
    load_conversation_context_projection,
)


@pytest.fixture
async def database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    yield sessions
    await engine.dispose()


def _ref(identifier: str) -> VersionRef:
    return VersionRef(identifier=identifier, version="1.0.0", sha256="a" * 64)


class _RedisStub:
    async def aclose(self) -> None:
        return None


def test_only_canonical_no_reliable_result_is_a_safe_terminal_result() -> None:
    canonical = {
        "evidence_sufficient": False,
        "missing_information": [NO_RELIABLE_RESULTS_MISSING_INFORMATION],
        "claims": [],
    }
    canonical_result = StructuredAnalysisResult(
        decision="ADMIT",
        risk_level="UNKNOWN",
        summary="No reliable web results were found.",
        key_findings=(),
        missing_information=(NO_RELIABLE_RESULTS_MISSING_INFORMATION,),
        recommendation="Narrow the request and retry.",
        evidence_sufficient=False,
        claims=(),
    )

    assert is_no_reliable_results_payload(canonical)
    assert is_no_reliable_results_payload(canonical_result)
    assert not is_no_reliable_results_payload(
        {
            "evidence_sufficient": False,
            "missing_information": ["another validation failure"],
            "claims": [],
        }
    )
    assert not is_no_reliable_results_payload(
        {
            "evidence_sufficient": True,
            "missing_information": [NO_RELIABLE_RESULTS_MISSING_INFORMATION],
            "claims": [],
        }
    )
    assert not is_no_reliable_results_payload(
        {
            "evidence_sufficient": False,
            "missing_information": [
                NO_RELIABLE_RESULTS_MISSING_INFORMATION,
                "another validation failure",
            ],
            "claims": [],
        }
    )
    assert not is_no_reliable_results_payload(
        {
            "evidence_sufficient": False,
            "missing_information": [NO_RELIABLE_RESULTS_MISSING_INFORMATION],
            "claims": [{"content": "unsupported factual Claim"}],
        }
    )
    assert not is_no_reliable_results_payload(
        {
            "evidence_sufficient": False,
            "missing_information": [NO_RELIABLE_RESULTS_MISSING_INFORMATION],
        }
    )


@pytest.mark.parametrize("requested_skill", ["document-analysis", "grounded-research"])
async def test_empty_non_web_grounded_task_remains_a_validation_failure(
    database: async_sessionmaker[AsyncSession],
    requested_skill: str,
) -> None:
    workspace_id, task_id = uuid4(), uuid4()
    goal = TaskGoal(
        goal="总结选定文档",
        constraints=("Use document Evidence",),
        deliverables=("Grounded summary",),
        workspace_id=workspace_id,
        time_budget_sec=60,
        token_budget=24_000,
        cost_budget=10,
        requested_skill=requested_skill,
    )
    async with database.begin() as session:
        session.add(Workspace(id=workspace_id, name=f"empty-{requested_skill}"))
        await session.flush()
        session.add(
            TaskRow(
                id=task_id,
                workspace_id=workspace_id,
                state="RUNNING",
                goal_payload=goal.model_dump(mode="json"),
            )
        )
    request = CapabilityRequest(
        command_id=uuid4(),
        operation_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        step_id=uuid4(),
        attempt_id=uuid4(),
        fencing_token=1,
        capability_id="model.structured_output",
        capability_version="1.0.0",
        arguments={"instruction": goal.goal},
    )

    with pytest.raises(DomainError) as raised:
        await builtins._model_handler(database, request)

    assert raised.value.code is FailureCode.VALIDATION_FAILED


@pytest.mark.parametrize(
    "with_irrelevant_web_evidence",
    [False, True],
    ids=["zero-source", "nonzero-unsupported-source"],
)
@pytest.mark.parametrize("with_bound_plan", [False, True])
async def test_no_reliable_result_requires_replan_before_terminal_materialization(
    database: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    with_irrelevant_web_evidence: bool,
    with_bound_plan: bool,
) -> None:
    workspace_id, task_id, snapshot_id, plan_id = uuid4(), uuid4(), uuid4(), uuid4()
    actor_id, conversation_id, user_message_id, turn_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    goal = TaskGoal(
        goal="最近有什么可靠新闻？",
        constraints=("Do not invent facts",),
        deliverables=("Grounded answer",),
        workspace_id=workspace_id,
        time_budget_sec=60,
        token_budget=24_000,
        cost_budget=10,
        requested_skill="web-research",
        user_context=json.dumps({
            "query": "最近有什么可靠新闻？",
            **({"research_plan": {
                "web_queries": ["最近有什么可靠新闻？"], "retrieval_queries": [],
                "source_plan": {"required_sources": ["PUBLIC_WEB"],
                                "forbidden_sources": ["PARAMETRIC_KNOWLEDGE"]},
                "result_limit": 4,
                "answer_contract": {
                    "answer_type": "FACT", "required_aspects": ["current news"],
                    "completeness": "BEST_EFFORT", "freshness": "CURRENT",
                    "minimum_answer_items": 1, "maximum_answer_items": 4,
                    "require_explicit_dates": False, "minimum_evidence_items": 1,
                    "minimum_independent_sources": 1,
                    "partial_answer_policy": "ALLOW_SUPPORTED_ONLY", "organization": "CONCISE",
                },
            }} if with_bound_plan else {}),
        }, ensure_ascii=False),
    )
    snapshot = ExecutionSnapshot(
        snapshot_id=snapshot_id,
        workspace_id=workspace_id,
        runtime_version="test",
        workflow_definition_version="1.0.0",
        planner_prompt_version=_ref("planner"),
        router_prompt_version=_ref("router"),
        reviewer_prompt_version=_ref("reviewer"),
        model_config_versions=(_ref("model"),),
        skill_versions=(_ref("web-research"),),
        capability_versions=(
            _ref("web.search"),
            _ref("model.structured_output"),
            _ref("runtime.review"),
        ),
        policy_version=_ref("policy"),
        knowledge_document_refs=(),
        context_policy_version=_ref("context"),
        schema_versions=(_ref("domain"),),
        feature_flags=frozenset(),
        created_at=datetime.now(UTC),
    )
    search = PlanStepSpec(
        step_key="search-web",
        objective="Search for relevant sources",
        capability_requirements=(
            CapabilityRequirement(capability_id="web.search", version_range=">=1,<2"),
        ),
        expected_output="Web evidence",
        success_criteria=("The bounded search completes honestly",),
        risk_hint=Risk.READ,
        execution_mode="CAPABILITY",
        parallelizable=False,
        max_attempts=1,
    )
    synthesize = PlanStepSpec(
        step_key="synthesize-answer",
        objective="Return an honest result from the retained evidence",
        dependencies=("search-web",),
        capability_requirements=(
            CapabilityRequirement(capability_id="model.structured_output", version_range=">=1,<2"),
        ),
        expected_output="Grounded answer or a no-reliable-results outcome",
        success_criteria=("No unsupported fact is returned",),
        risk_hint=Risk.READ,
        execution_mode="CAPABILITY",
        parallelizable=False,
        max_attempts=1,
    )
    review = PlanStepSpec(
        step_key="review-answer",
        objective="Verify the grounded answer or non-factual limitation",
        dependencies=("synthesize-answer",),
        capability_requirements=(
            CapabilityRequirement(capability_id="runtime.review", version_range=">=1,<2"),
        ),
        expected_output="Accepted deterministic review",
        success_criteria=("No unsupported factual Claim remains",),
        risk_hint=Risk.READ,
        execution_mode="REVIEW",
        parallelizable=False,
        max_attempts=1,
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
        steps=(search, synthesize, review),
    )

    async with database.begin() as session:
        session.add(Workspace(id=workspace_id, name="no-reliable-result"))
        session.add(
            UserRow(
                id=actor_id,
                email=f"{actor_id}@example.test",
                normalized_email=f"{actor_id}@example.test",
                password_hash="integration-test",
            )
        )
        await session.flush()
        session.add(
            WorkspaceMemberRow(
                workspace_id=workspace_id,
                user_id=actor_id,
                role="WORKSPACE_OWNER",
            )
        )
        await persist_snapshot(session, snapshot)
        session.add(
            TaskRow(
                id=task_id,
                workspace_id=workspace_id,
                state="READY",
                execution_snapshot_id=snapshot_id,
                goal_payload=goal.model_dump(mode="json"),
                created_by=actor_id,
            )
        )
        await session.flush()
        session.add(new_task_budget(task_id, goal))
        session.add(RuntimeGuardRow(task_id=task_id, workspace_id=workspace_id))
        await persist_plan(session, plan)
        await materialize_plan_steps(session, plan)
        await create_conversation(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            title="No reliable result",
        )
        await append_conversation_message(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_id=user_message_id,
            role="USER",
            content=goal.goal,
        )
        await create_conversation_turn(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            user_message_id=user_message_id,
            task_id=task_id,
        )
        await acquire_conversation_turn_attempt(
            session,
            workspace_id=workspace_id,
            turn_id=turn_id,
            attempt_id=uuid4(),
            trace_id="trace-no-reliable-turn",
        )

    search_queries: list[str] = []

    async def return_empty_search(
        self: HttpWebProvider,
        query: str,
        limit: int,
    ) -> tuple[WebSearchResult, ...]:
        del self, limit
        search_queries.append(query)
        if not with_irrelevant_web_evidence:
            return ()
        snippet = "An archived page about another subject that does not support the request."
        return (
            WebSearchResult(
                title="Unrelated archived page",
                url="https://example.com/unrelated",
                snippet=snippet,
                published_at="2020-01-01",
                observed_at=datetime.now(UTC).isoformat(),
                content_hash=hashlib.sha256(snippet.encode()).hexdigest(),
            ),
        )

    async def skip_search_assessment(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("WEB_MODE", "local")
    monkeypatch.setenv("WEB_BASE_URL", "http://127.0.0.1:59080")
    monkeypatch.setenv("WEB_API_KEY", "")
    monkeypatch.setattr(HttpWebProvider, "search", return_empty_search)
    monkeypatch.setattr(
        "enterprise_agent.capabilities.builtins._assess_web_search",
        skip_search_assessment,
    )
    monkeypatch.setattr(
        "enterprise_agent.runtime.activities.load_admission_controller",
        lambda: (_RedisStub(), None, 1_000),
    )
    monkeypatch.setattr(
        "enterprise_agent.runtime.activities.activity.heartbeat",
        lambda *args: None,
    )
    monkeypatch.setattr(
        "enterprise_agent.runtime.activities._activity_trace_id",
        lambda: "trace-no-reliable-activity",
    )

    search_step_id = plan_step_id(plan_id, search.step_key)
    search_result = await execute_governed_plan_step(
        {
            "workspace_id": str(workspace_id),
            "task_id": str(task_id),
            "step_id": str(search_step_id),
            "attempt_id": str(uuid4()),
        }
    )
    async with database() as session:
        search_evidence = await list_task_evidence(session, workspace_id, task_id)

    synthesize_step_id = plan_step_id(plan_id, synthesize.step_key)
    synthesize_input: ExecutePlanStepInput = {
        "workspace_id": str(workspace_id),
        "task_id": str(task_id),
        "step_id": str(synthesize_step_id),
        "attempt_id": str(uuid4()),
    }
    if with_irrelevant_web_evidence:

        async def return_no_reliable_result(
            self: CapabilityExecutor,
            request: CapabilityRequest,
            policy_input: PolicyInput,
            *,
            approval_id: UUID | None = None,
        ) -> CapabilityResult:
            del self, policy_input, approval_id
            return CapabilityResult(
                operation_id=request.operation_id,
                outcome="SUCCEEDED",
                output={
                    "schema_version": 1,
                    "decision": "ADMIT",
                    "risk_level": "UNKNOWN",
                    "summary": "No reliable web results were found.",
                    "key_findings": [],
                    "missing_information": [NO_RELIABLE_RESULTS_MISSING_INFORMATION],
                    "recommendation": "Narrow the request and retry.",
                    "evidence_sufficient": False,
                    "claims": [],
                },
            )

        with monkeypatch.context() as capability_patch:
            capability_patch.setattr(
                CapabilityExecutor,
                "execute",
                return_no_reliable_result,
            )
            synthesize_result = await execute_governed_plan_step(synthesize_input)
    else:
        synthesize_result = await execute_governed_plan_step(synthesize_input)
    state = await load_plan_execution_state(
        {"workspace_id": str(workspace_id), "task_id": str(task_id)}
    )
    assert synthesize_result["state"] == "FAILED"
    assert state["state"] == "REPLAN_REQUIRED"
    assert state["replan_trigger"] == "EVIDENCE_INSUFFICIENT"
    assert NO_RELIABLE_RESULTS_MISSING_INFORMATION in state["replan_reason"]
    with pytest.raises(DomainError, match="has not reached a terminal state"):
        async with database.begin() as session:
            await materialize_chat_task_turn(
                session, workspace_id=workspace_id, task_id=task_id,
                trace_id="trace-must-not-materialize-before-replan",
            )

    # Exercise a new immutable Plan and its changed query before ending honestly.
    recovery_search = search.model_copy(update={
        "step_key": "recovery-search",
        "objective": "new authoritative sources for missing current news",
    })
    recovery_synthesis = synthesize.model_copy(update={
        "dependencies": ("recovery-search",),
    })
    async with database.begin() as session:
        applied = await apply_replan(
            session, workspace_id, task_id, actor_id, "trace-evidence-replan",
            ReplanDecision(
                reason="Collect different evidence before synthesis",
                trigger="EVIDENCE_INSUFFICIENT", action="EXPAND_STEP",
                affected_steps=(synthesize.step_key,),
                new_steps=(recovery_search, recovery_synthesis),
                preserve_completed_steps=True, budget_impact=0,
            ), expected_from_plan_id=plan_id,
        )
    assert applied.status == "APPLIED" and applied.to_plan_id is not None
    recovery = await execute_governed_plan_step({
        "workspace_id": str(workspace_id), "task_id": str(task_id),
        "step_id": str(plan_step_id(applied.to_plan_id, recovery_search.step_key)),
        "attempt_id": str(uuid4()),
    })
    assert recovery["state"] == "COMPLETED"
    assert recovery_search.objective in search_queries
    async with database.begin() as session:
        # Explicit exhaustion is a recorded Replan decision, not a successful answer.
        exhausted = await apply_replan(
            session, workspace_id, task_id, actor_id, "trace-recovery-exhausted",
            ReplanDecision(
                reason="No useful evidence after changed-query recovery",
                trigger="EVIDENCE_INSUFFICIENT", action="ABORT_TASK",
                affected_steps=(), new_steps=(), preserve_completed_steps=True,
                budget_impact=0,
            ), expected_from_plan_id=applied.to_plan_id,
        )
    assert exhausted.status == "ABORTED"
    state = await load_plan_execution_state(
        {"workspace_id": str(workspace_id), "task_id": str(task_id)}
    )

    async with database.begin() as session:
        first = await materialize_chat_task_turn(
            session,
            workspace_id=workspace_id,
            task_id=task_id,
            trace_id="trace-no-reliable-materialize",
        )
    async with database.begin() as session:
        replay = await materialize_chat_task_turn(
            session,
            workspace_id=workspace_id,
            task_id=task_id,
            trace_id="trace-no-reliable-replay",
        )
        turn = await get_conversation_turn(session, workspace_id, turn_id)
        projection = await load_conversation_context_projection(
            session,
            workspace_id,
            conversation_id,
        )
        outcome = await session.scalar(
            select(ConversationTurnOutcomeRow).where(
                ConversationTurnOutcomeRow.workspace_id == workspace_id,
                ConversationTurnOutcomeRow.turn_id == turn_id,
            )
        )
        assistant_count = await session.scalar(
            select(func.count())
            .select_from(ConversationMessageRow)
            .where(
                ConversationMessageRow.workspace_id == workspace_id,
                ConversationMessageRow.conversation_id == conversation_id,
                ConversationMessageRow.role == "ASSISTANT",
            )
        )
        replan_count = await session.scalar(
            select(func.count())
            .select_from(ReplanDecisionRow)
            .where(
                ReplanDecisionRow.workspace_id == workspace_id,
                ReplanDecisionRow.task_id == task_id,
            )
        )
        attempts = await list_task_attempts(session, workspace_id, task_id)
        evidence = await list_task_evidence(session, workspace_id, task_id)
        claims = await load_claims(session, workspace_id, task_id)
        budget = await session.scalar(
            select(TaskBudgetRow).where(
                TaskBudgetRow.workspace_id == workspace_id,
                TaskBudgetRow.task_id == task_id,
            )
        )

    model_results = tuple(
        item for item in evidence if item.source_id == "model.structured_output"
    )
    web_evidence = tuple(item for item in evidence if item.source_type == "WEB")
    assert search_result["state"] == "COMPLETED"
    assert len(search_evidence) == int(with_irrelevant_web_evidence)
    assert all(item.source_type == "WEB" for item in search_evidence)
    assert state["state"] == "FAILED"
    assert state["replan_count"] == 2
    assert first is not None and replay == first
    assert first.content
    assert "No reliable web results were found." not in first.content
    assert outcome is not None
    assert outcome.status == ConversationTurnOutcomeStatus.FAILED.value
    assert outcome.context_eligible is False
    assert turn.context_eligible is False
    assert projection == ()
    assert assistant_count == 1
    assert replan_count == 2
    assert any(attempt.failure is not None for attempt in attempts)
    assert claims == ()
    assert all(item.source_type in {"WEB", "DERIVED"} for item in evidence)
    assert {item.evidence_id for item in search_evidence} <= {
        item.evidence_id for item in web_evidence
    }
    assert model_results == ()
    assert budget is not None
    if not with_irrelevant_web_evidence:
        assert budget.used_llm_calls == 0
