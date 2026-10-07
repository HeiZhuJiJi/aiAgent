import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.completion.assessment import assess_requirement
from enterprise_agent.completion.delivery import build_delivery_decision
from enterprise_agent.contracts.domain import (
    Claim,
    ContractVersion,
    ConversationTurnAttemptState,
    ConversationTurnOutcomeStatus,
    ConversationTurnState,
    DeliveryDecisionType,
    Evidence,
    Failure,
    FailureCode,
    Requirement,
    RequirementType,
    TaskGoal,
    TaskState,
    VerificationGates,
    VerificationMetrics,
    VerificationResult,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.runtime.conversation_results import (
    conversation_turn_ids,
    materialize_chat_task_turn,
    reconcile_abandoned_direct_turns,
    reconcile_abandoned_planned_turns,
    turn_failure_message,
)
from enterprise_agent.storage.attempts import acquire_attempt, commit_evidence
from enterprise_agent.storage.completion import (
    create_contract_version,
    create_delivery_decision,
    create_requirement,
    create_requirement_assessment,
    create_verification_result,
)
from enterprise_agent.storage.knowledge import ChunkInput, persist_document_version
from enterprise_agent.storage.memory import (
    append_conversation_message,
    create_conversation,
    load_conversation_messages,
)
from enterprise_agent.storage.plans import persist_plan, persist_snapshot
from enterprise_agent.storage.provenance import persist_claim
from enterprise_agent.storage.tables import (
    AuditRow,
    ConversationTurnAttemptRow,
    ConversationTurnOutcomeRow,
    ConversationTurnRow,
    KnowledgeIngestionJobRow,
    StepRow,
    TaskRow,
    UserRow,
)
from enterprise_agent.storage.turns import (
    acquire_conversation_turn_attempt,
    cancel_conversation_turn,
    commit_conversation_turn_outcome,
    create_conversation_turn,
    get_conversation_turn,
    load_conversation_context_projection,
    load_conversation_timeline_messages,
)
from tests.integration.test_fencing import seed
from tests.integration.test_immutable_plans import plan as immutable_plan
from tests.integration.test_immutable_plans import snapshot as immutable_snapshot


@pytest.fixture
async def database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _turn_fixture(database):
    workspace_id, task_id, _ = await seed(database)
    conversation_id = uuid4()
    user_message_id = uuid4()
    turn_id = uuid4()
    async with database.begin() as session:
        await create_conversation(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            title="Turn lifecycle",
        )
        await append_conversation_message(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_id=user_message_id,
            role="USER",
            content="What is the current answer?",
        )
        turn = await create_conversation_turn(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            user_message_id=user_message_id,
            task_id=task_id,
        )
    return workspace_id, conversation_id, turn


async def test_turn_outcome_is_fenced_idempotent_and_context_projected(database):
    workspace_id, conversation_id, turn = await _turn_fixture(database)
    attempt_id = uuid4()
    outcome_id = uuid4()
    assistant_message_id = uuid4()
    async with database.begin() as session:
        attempt = await acquire_conversation_turn_attempt(
            session,
            workspace_id=workspace_id,
            turn_id=turn.turn_id,
            attempt_id=attempt_id,
            trace_id="trace-turn-answer",
        )
        await append_conversation_message(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_id=assistant_message_id,
            role="ASSISTANT",
            content="This is the authoritative answer.",
        )
        outcome = await commit_conversation_turn_outcome(
            session,
            workspace_id=workspace_id,
            turn_id=turn.turn_id,
            attempt_id=attempt_id,
            fencing_token=attempt.fencing_token,
            outcome_id=outcome_id,
            status=ConversationTurnOutcomeStatus.ANSWERED,
            assistant_message_id=assistant_message_id,
            context_eligible=True,
        )
    assert outcome.status is ConversationTurnOutcomeStatus.ANSWERED
    async with database.begin() as session:
        replay = await commit_conversation_turn_outcome(
            session,
            workspace_id=workspace_id,
            turn_id=turn.turn_id,
            attempt_id=attempt_id,
            fencing_token=attempt.fencing_token,
            outcome_id=outcome_id,
            status=ConversationTurnOutcomeStatus.ANSWERED,
            assistant_message_id=assistant_message_id,
            context_eligible=True,
        )
        stored_turn = await get_conversation_turn(session, workspace_id, turn.turn_id)
        projection = await load_conversation_context_projection(
            session, workspace_id, conversation_id
        )
    assert replay == outcome
    assert stored_turn.state is ConversationTurnState.COMPLETED
    assert stored_turn.context_eligible
    assert len(projection) == 1
    assert projection[0].user_message.message_id == turn.user_message_id
    assert projection[0].assistant_message.message_id == assistant_message_id
    with pytest.raises(DBAPIError):
        async with database.begin() as session:
            await session.execute(
                update(ConversationTurnOutcomeRow)
                .where(ConversationTurnOutcomeRow.id == outcome_id)
                .values(status=ConversationTurnOutcomeStatus.FAILED.value)
            )


async def test_superseded_attempt_cannot_commit_a_late_result(database):
    workspace_id, conversation_id, turn = await _turn_fixture(database)
    first_id, second_id = uuid4(), uuid4()
    first_message_id, second_message_id = uuid4(), uuid4()
    async with database.begin() as session:
        first = await acquire_conversation_turn_attempt(
            session,
            workspace_id=workspace_id,
            turn_id=turn.turn_id,
            attempt_id=first_id,
            trace_id="trace-first",
        )
    async with database.begin() as session:
        second = await acquire_conversation_turn_attempt(
            session,
            workspace_id=workspace_id,
            turn_id=turn.turn_id,
            attempt_id=second_id,
            trace_id="trace-second",
            supersede_active=True,
        )
        await append_conversation_message(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_id=first_message_id,
            role="ASSISTANT",
            content="Late stale answer.",
        )
        await append_conversation_message(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_id=second_message_id,
            role="ASSISTANT",
            content="Current answer.",
        )
    assert second.fencing_token > first.fencing_token
    with pytest.raises(DomainError) as raised:
        async with database.begin() as session:
            await commit_conversation_turn_outcome(
                session,
                workspace_id=workspace_id,
                turn_id=turn.turn_id,
                attempt_id=first_id,
                fencing_token=first.fencing_token,
                outcome_id=uuid4(),
                status=ConversationTurnOutcomeStatus.ANSWERED,
                assistant_message_id=first_message_id,
                context_eligible=True,
            )
    assert raised.value.code == FailureCode.STALE_ATTEMPT
    async with database.begin() as session:
        await commit_conversation_turn_outcome(
            session,
            workspace_id=workspace_id,
            turn_id=turn.turn_id,
            attempt_id=second_id,
            fencing_token=second.fencing_token,
            outcome_id=uuid4(),
            status=ConversationTurnOutcomeStatus.ANSWERED,
            assistant_message_id=second_message_id,
            context_eligible=True,
        )
        first_row = await session.get(ConversationTurnAttemptRow, first_id)
    assert first_row is not None
    assert first_row.state == ConversationTurnAttemptState.SUPERSEDED.value


async def test_failed_outcome_is_terminal_but_excluded_from_context(database):
    workspace_id, conversation_id, turn = await _turn_fixture(database)
    attempt_id = uuid4()
    assistant_message_id = uuid4()
    failure = Failure(
        code=FailureCode.CAPABILITY_UNAVAILABLE,
        safe_detail="weather provider unavailable",
        trace_id="trace-unavailable",
    )
    async with database.begin() as session:
        attempt = await acquire_conversation_turn_attempt(
            session,
            workspace_id=workspace_id,
            turn_id=turn.turn_id,
            attempt_id=attempt_id,
            trace_id="trace-unavailable",
        )
        await append_conversation_message(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_id=assistant_message_id,
            role="ASSISTANT",
            content="The requested source is temporarily unavailable.",
        )
        outcome = await commit_conversation_turn_outcome(
            session,
            workspace_id=workspace_id,
            turn_id=turn.turn_id,
            attempt_id=attempt_id,
            fencing_token=attempt.fencing_token,
            outcome_id=uuid4(),
            status=ConversationTurnOutcomeStatus.UNAVAILABLE,
            assistant_message_id=assistant_message_id,
            failure=failure,
            context_eligible=False,
        )
    assert outcome.failure == failure
    async with database.begin() as session:
        stored_turn = await get_conversation_turn(session, workspace_id, turn.turn_id)
        projection = await load_conversation_context_projection(
            session, workspace_id, conversation_id
        )
    assert stored_turn.state is ConversationTurnState.COMPLETED
    assert projection == ()


async def test_abandoned_planner_handoff_becomes_terminal_and_cannot_pollute_context(database):
    workspace_id, _, _ = await seed(database)
    conversation_id = uuid4()
    user_message_id = uuid4()
    turn_id = uuid4()
    async with database.begin() as session:
        await create_conversation(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            title="Abandoned planner handoff",
        )
        await append_conversation_message(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_id=user_message_id,
            role="USER",
            content="Run the planned research",
        )
        await create_conversation_turn(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            user_message_id=user_message_id,
        )
        await session.execute(
            update(ConversationTurnRow)
            .where(ConversationTurnRow.id == turn_id)
            .values(updated_at=datetime.now(UTC) - timedelta(hours=1))
        )

    async with database.begin() as session:
        recovered = await reconcile_abandoned_planned_turns(
            session,
            stale_after_seconds=60,
            workspace_id=workspace_id,
        )
    async with database() as session:
        turn = await get_conversation_turn(session, workspace_id, turn_id)
        projection = await load_conversation_context_projection(
            session,
            workspace_id,
            conversation_id,
        )
        outcome = await session.scalar(
            select(ConversationTurnOutcomeRow).where(
                ConversationTurnOutcomeRow.turn_id == turn_id
            )
        )

    assert recovered == 1
    assert turn.state is ConversationTurnState.COMPLETED
    assert not turn.context_eligible
    assert outcome is not None
    assert outcome.status == ConversationTurnOutcomeStatus.UNAVAILABLE.value
    assert projection == ()


async def test_cancelling_turn_fences_the_active_attempt(database):
    workspace_id, conversation_id, turn = await _turn_fixture(database)
    attempt_id = uuid4()
    assistant_message_id = uuid4()
    async with database.begin() as session:
        attempt = await acquire_conversation_turn_attempt(
            session,
            workspace_id=workspace_id,
            turn_id=turn.turn_id,
            attempt_id=attempt_id,
            trace_id="trace-cancelled",
        )
        await append_conversation_message(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_id=assistant_message_id,
            role="ASSISTANT",
            content="Late answer after cancellation.",
        )
    async with database.begin() as session:
        cancelled = await cancel_conversation_turn(
            session,
            workspace_id=workspace_id,
            turn_id=turn.turn_id,
        )
    assert cancelled.state is ConversationTurnState.CANCELLED
    assert cancelled.fencing_token > attempt.fencing_token
    with pytest.raises(DomainError) as raised:
        async with database.begin() as session:
            await commit_conversation_turn_outcome(
                session,
                workspace_id=workspace_id,
                turn_id=turn.turn_id,
                attempt_id=attempt_id,
                fencing_token=attempt.fencing_token,
                outcome_id=uuid4(),
                status=ConversationTurnOutcomeStatus.ANSWERED,
                assistant_message_id=assistant_message_id,
                context_eligible=True,
            )
    assert raised.value.code == FailureCode.STALE_ATTEMPT


async def test_completed_research_task_materializes_answer_once_and_projects_context(
    database,
):
    workspace_id, conversation_id, turn = await _turn_fixture(database)
    assert turn.task_id is not None
    turn_attempt_id = uuid4()
    task_attempt_id = uuid4()
    source_evidence_id = uuid4()
    model_content = json.dumps(
        {
            "schema_version": 1,
            "decision": "ADMIT",
            "risk_level": "LOW",
            "summary": "The verified research answer.",
            "key_findings": ["The source supports the answer."],
            "missing_information": [],
            "recommendation": "Use the verified answer.",
            "evidence_sufficient": True,
            "claims": [
                {
                    "schema_version": 1,
                    "claim_id": str(uuid4()),
                    "content": "The verified research answer.",
                    "importance": "HIGH",
                    "risk_level": "READ",
                    "evidence_ids": [str(source_evidence_id)],
                }
            ],
        }
    )
    async with database.begin() as session:
        await acquire_conversation_turn_attempt(
            session,
            workspace_id=workspace_id,
            turn_id=turn.turn_id,
            attempt_id=turn_attempt_id,
            trace_id="trace-research-completed",
        )
        step_id = await session.scalar(
            select(StepRow.id).where(
                StepRow.workspace_id == workspace_id,
                StepRow.task_id == turn.task_id,
            )
        )
        assert step_id is not None
        task_attempt = await acquire_attempt(
            session,
            workspace_id,
            step_id,
            task_attempt_id,
            "research-test-worker",
            "trace-research-completed",
        )
        source_evidence = Evidence(
            evidence_id=source_evidence_id,
            workspace_id=workspace_id,
            task_id=turn.task_id,
            step_id=step_id,
            source_type="WEB",
            source_id="web.search",
            locator="https://example.com/verified-answer",
            content="The verified research answer.",
            content_hash=hashlib.sha256(b"The verified research answer.").hexdigest(),
            source_hash=hashlib.sha256(b"verified-source").hexdigest(),
            source_version="1",
            confidence=1,
            relevance=1,
            retrieved_at=datetime.now(UTC),
            generated_by=task_attempt_id,
            derived_from=(),
        )
        model_evidence = Evidence(
            evidence_id=uuid4(),
            workspace_id=workspace_id,
            task_id=turn.task_id,
            step_id=step_id,
            source_type="DERIVED",
            source_id="model.structured_output",
            locator=f"task:{turn.task_id}",
            content=model_content,
            content_hash=hashlib.sha256(model_content.encode()).hexdigest(),
            source_hash=hashlib.sha256(b"controlled-research-input").hexdigest(),
            source_version="1",
            confidence=1,
            relevance=1,
            retrieved_at=datetime.now(UTC),
            generated_by=task_attempt_id,
            derived_from=(source_evidence_id,),
        )
        assert await commit_evidence(
            session,
            workspace_id,
            task_attempt_id,
            task_attempt.fencing_token,
            (source_evidence, model_evidence),
        )
        task = await session.get(TaskRow, turn.task_id)
        assert task is not None
        snapshot = immutable_snapshot(workspace_id)
        task_plan = immutable_plan(workspace_id, turn.task_id, snapshot.snapshot_id)
        await persist_snapshot(session, snapshot)
        await persist_plan(session, task_plan)
        review_step_id = uuid4()
        session.add(StepRow(
            id=review_step_id, workspace_id=workspace_id, task_id=turn.task_id,
            plan_id=task_plan.plan_id, step_key="completion-review", state="READY",
        ))
        await session.flush()
        review_attempt = await acquire_attempt(
            session, workspace_id, review_step_id, uuid4(),
            "completion-review-worker", "completion-review-trace",
        )
        creator_id = uuid4()
        session.add(UserRow(
            id=creator_id,
            email=f"conversation-completion-{creator_id}@example.invalid",
            normalized_email=f"conversation-completion-{creator_id}@example.invalid",
            password_hash="test-only",
        ))
        await session.flush()
        contract = ContractVersion(
            id=uuid4(),
            workspace_id=workspace_id,
            task_id=turn.task_id,
            version=1,
            content_hash="c" * 64,
            goal="Produce a verified report",
            completion_policy={"allow_partial": True, "version": "completion.v1"},
            created_by=creator_id,
            created_at=datetime.now(UTC),
        )
        requirement = Requirement(
            id=uuid4(),
            workspace_id=workspace_id,
            task_id=turn.task_id,
            contract_version_id=contract.id,
            key="verified-report",
            type=RequirementType.VERIFICATION,
            mandatory=True,
            description="The report must be supported by verified evidence.",
            completion_rule="The verification gates pass.",
            legacy=True,
        )
        claim = Claim(
            claim_id=uuid4(),
            workspace_id=workspace_id,
            task_id=turn.task_id,
            content="The verified research answer.",
            importance="HIGH",
            risk_level="READ",
            evidence_ids=(source_evidence_id,),
        )
        await create_contract_version(session, contract)
        await create_requirement(session, requirement)
        await persist_claim(session, claim)
        verification = VerificationResult(
            id=uuid4(),
            workspace_id=workspace_id,
            task_id=turn.task_id,
            claim_ids=(claim.claim_id,),
            evidence_ids=(source_evidence_id,),
            gates=VerificationGates(
                entity_match=True, temporal_match=True, evidence_support=True,
                authorization=True, operation_certainty="CERTAIN", schema_validity=True,
            ),
            metrics=VerificationMetrics(
                completeness=1.0, freshness=1.0, source_quality=1.0,
                relevance=1.0, redundancy=1.0,
            ),
            verifier_version="conversation-test.v1",
            policy_version="completion.v1",
            input_snapshot_hash="d" * 64,
            created_at=datetime.now(UTC),
            attempt_id=review_attempt.id,
            fencing_token=review_attempt.fencing_token,
        )
        assessment = assess_requirement(
            requirement, verification, assessment_id=uuid4(), evaluator_version="conversation-test.v1"
        )
        decision = build_delivery_decision(
            contract, (requirement,), (assessment,), (verification,), snapshot,
            decision_id=uuid4(), created_at=datetime.now(UTC),
        )
        assert decision.decision is DeliveryDecisionType.FULL_ANSWER
        await create_verification_result(session, verification)
        await create_requirement_assessment(session, assessment)
        await create_delivery_decision(session, decision)
        task.state = TaskState.COMPLETED.value

    async with database.begin() as session:
        first = await materialize_chat_task_turn(
            session,
            workspace_id=workspace_id,
            task_id=turn.task_id,
            trace_id="trace-research-completed",
        )
    async with database.begin() as session:
        replay = await materialize_chat_task_turn(
            session,
            workspace_id=workspace_id,
            task_id=turn.task_id,
            trace_id="trace-research-replay",
        )
        stored_turn = await get_conversation_turn(session, workspace_id, turn.turn_id)
        projection = await load_conversation_context_projection(
            session,
            workspace_id,
            conversation_id,
        )
        outcomes = (
            await session.scalars(
                select(ConversationTurnOutcomeRow).where(
                    ConversationTurnOutcomeRow.workspace_id == workspace_id,
                    ConversationTurnOutcomeRow.turn_id == turn.turn_id,
                )
            )
        ).all()

    assert first is not None
    assert replay == first
    assert first.content.startswith("The verified research answer.")
    assert "[example.com](https://example.com/verified-answer)" in first.content
    assert stored_turn.state is ConversationTurnState.COMPLETED
    assert stored_turn.context_eligible
    assert len(outcomes) == 1
    assert outcomes[0].status == ConversationTurnOutcomeStatus.ANSWERED.value
    assert len(projection) == 1
    assert projection[0].assistant_message == first


@pytest.mark.parametrize("task_state", [TaskState.FAILED, TaskState.CANCELLED])
async def test_failed_or_cancelled_research_task_materializes_unavailable_once(
    database,
    task_state,
):
    workspace_id, conversation_id, turn = await _turn_fixture(database)
    assert turn.task_id is not None
    turn_attempt_id = uuid4()
    async with database.begin() as session:
        await acquire_conversation_turn_attempt(
            session,
            workspace_id=workspace_id,
            turn_id=turn.turn_id,
            attempt_id=turn_attempt_id,
            trace_id=f"trace-research-{task_state.value.lower()}",
        )
        task = await session.get(TaskRow, turn.task_id)
        assert task is not None
        task.state = task_state.value

    async with database.begin() as session:
        first = await materialize_chat_task_turn(
            session,
            workspace_id=workspace_id,
            task_id=turn.task_id,
            trace_id=f"trace-research-{task_state.value.lower()}",
        )
    async with database.begin() as session:
        replay = await materialize_chat_task_turn(
            session,
            workspace_id=workspace_id,
            task_id=turn.task_id,
            trace_id="trace-research-terminal-replay",
        )
        stored_turn = await get_conversation_turn(session, workspace_id, turn.turn_id)
        projection = await load_conversation_context_projection(
            session,
            workspace_id,
            conversation_id,
        )
        attempt = await session.get(ConversationTurnAttemptRow, turn_attempt_id)
        outcome = await session.scalar(
            select(ConversationTurnOutcomeRow).where(
                ConversationTurnOutcomeRow.workspace_id == workspace_id,
                ConversationTurnOutcomeRow.turn_id == turn.turn_id,
            )
        )

    assert first is not None
    assert replay == first
    assert first.content == turn_failure_message(FailureCode.CAPABILITY_UNAVAILABLE)
    assert stored_turn.state is ConversationTurnState.COMPLETED
    assert not stored_turn.context_eligible
    assert attempt is not None
    assert attempt.state == ConversationTurnAttemptState.FAILED.value
    assert outcome is not None
    assert outcome.status == ConversationTurnOutcomeStatus.UNAVAILABLE.value
    assert projection == ()


async def test_timeline_groups_a_late_t1_answer_before_t2(database):
    workspace_id, _, _ = await seed(database)
    conversation_id = uuid4()
    user_1_id = uuid4()
    user_2_id = uuid4()
    turn_1_id, attempt_1_id, outcome_1_id = conversation_turn_ids(
        workspace_id,
        conversation_id,
        user_1_id,
    )
    turn_2_id, attempt_2_id, outcome_2_id = conversation_turn_ids(
        workspace_id,
        conversation_id,
        user_2_id,
    )
    assistant_1_id = uuid4()
    assistant_2_id = uuid4()
    async with database.begin() as session:
        await create_conversation(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            title="Late Turn result",
        )
        await append_conversation_message(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_id=user_1_id,
            role="USER",
            content="Question one",
        )
        await create_conversation_turn(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=turn_1_id,
            user_message_id=user_1_id,
        )
        attempt_1 = await acquire_conversation_turn_attempt(
            session,
            workspace_id=workspace_id,
            turn_id=turn_1_id,
            attempt_id=attempt_1_id,
            trace_id="trace-late-turn-one",
        )

    async with database.begin() as session:
        await append_conversation_message(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_id=user_2_id,
            role="USER",
            content="Question two",
        )
        await create_conversation_turn(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=turn_2_id,
            user_message_id=user_2_id,
        )
        attempt_2 = await acquire_conversation_turn_attempt(
            session,
            workspace_id=workspace_id,
            turn_id=turn_2_id,
            attempt_id=attempt_2_id,
            trace_id="trace-late-turn-two",
        )

    async with database.begin() as session:
        await append_conversation_message(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_id=assistant_2_id,
            role="ASSISTANT",
            content="Answer two completed first",
        )
        await commit_conversation_turn_outcome(
            session,
            workspace_id=workspace_id,
            turn_id=turn_2_id,
            attempt_id=attempt_2_id,
            fencing_token=attempt_2.fencing_token,
            outcome_id=outcome_2_id,
            status=ConversationTurnOutcomeStatus.ANSWERED,
            assistant_message_id=assistant_2_id,
            context_eligible=True,
        )

    async with database.begin() as session:
        await append_conversation_message(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_id=assistant_1_id,
            role="ASSISTANT",
            content="Answer one completed late",
        )
        await commit_conversation_turn_outcome(
            session,
            workspace_id=workspace_id,
            turn_id=turn_1_id,
            attempt_id=attempt_1_id,
            fencing_token=attempt_1.fencing_token,
            outcome_id=outcome_1_id,
            status=ConversationTurnOutcomeStatus.ANSWERED,
            assistant_message_id=assistant_1_id,
            context_eligible=True,
        )

    async with database() as session:
        raw = await load_conversation_messages(session, workspace_id, conversation_id)
        timeline = await load_conversation_timeline_messages(
            session,
            workspace_id,
            conversation_id,
        )

    assert [item.message_id for item in raw] == [
        user_1_id,
        user_2_id,
        assistant_2_id,
        assistant_1_id,
    ]
    assert [item.message_id for item in timeline] == [
        user_1_id,
        assistant_1_id,
        user_2_id,
        assistant_2_id,
    ]
    assert [item.turn_id for item in timeline] == [
        turn_1_id,
        turn_1_id,
        turn_2_id,
        turn_2_id,
    ]
    assert all(item.turn_state is ConversationTurnState.COMPLETED for item in timeline)
    assert all(
        item.turn_outcome_status is ConversationTurnOutcomeStatus.ANSWERED for item in timeline
    )


async def test_timeline_binds_an_attachment_only_to_its_originating_turn(database):
    workspace_id, task_id, _ = await seed(database)
    conversation_id = uuid4()
    attachment_user_id = uuid4()
    following_user_id = uuid4()
    attachment_turn_id = uuid4()
    following_turn_id = uuid4()
    ingestion_id = uuid4()
    document_id = uuid4()
    source_hash = "9" * 64

    async with database.begin() as session:
        document_version_id = await persist_document_version(
            session,
            workspace_id=workspace_id,
            document_id=document_id,
            name="制度说明.docx",
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            source_hash=source_hash,
            storage_uri="s3://knowledge/制度说明.docx",
            parser_version="parser-test",
            chunks=(
                ChunkInput(
                    content="附件中的请假制度。",
                    locator={"line": 1},
                    token_count=8,
                    embedding=(1.0,) + (0.0,) * 1023,
                ),
            ),
        )
        session.add(
            KnowledgeIngestionJobRow(
                id=ingestion_id,
                workspace_id=workspace_id,
                document_id=document_id,
                name="制度说明.docx",
                media_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
                source_hash=source_hash,
                object_name="knowledge/制度说明.docx",
                parser_version="parser-test",
                state="SUCCEEDED",
                version=1,
                fencing_token=1,
                document_version_id=document_version_id,
                completed_at=datetime.now(UTC),
            )
        )
        task = await session.get(TaskRow, task_id)
        assert task is not None
        task.goal_payload = TaskGoal(
            goal="总结附件",
            constraints=("只使用所选附件",),
            deliverables=("摘要",),
            workspace_id=workspace_id,
            time_budget_sec=60,
            token_budget=2_000,
            cost_budget=2,
            requested_skill="document-analysis",
            user_context=json.dumps(
                {
                    "attachment_ingestion_id": str(ingestion_id),
                    "document_version_id": str(document_version_id),
                }
            ),
        ).model_dump(mode="json")
        await create_conversation(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            title="Turn attachment projection",
        )
        await append_conversation_message(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_id=attachment_user_id,
            role="USER",
            content="请总结这份文档",
        )
        await create_conversation_turn(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=attachment_turn_id,
            user_message_id=attachment_user_id,
            task_id=task_id,
        )
        await append_conversation_message(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_id=following_user_id,
            role="USER",
            content="现在日本首相是谁",
        )
        await create_conversation_turn(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=following_turn_id,
            user_message_id=following_user_id,
        )

    async with database() as session:
        timeline = await load_conversation_timeline_messages(
            session,
            workspace_id,
            conversation_id,
        )

    attachment_message = next(item for item in timeline if item.message_id == attachment_user_id)
    following_message = next(item for item in timeline if item.message_id == following_user_id)
    assert attachment_message.attachment is not None
    assert attachment_message.attachment.ingestion_id == ingestion_id
    assert attachment_message.attachment.document_version_id == document_version_id
    assert attachment_message.attachment.name == "制度说明.docx"
    assert following_message.attachment is None


async def test_direct_turn_reconciler_recovers_crash_once_and_ignores_durable_turn(
    database,
):
    workspace_id, task_id, _ = await seed(database)
    conversation_id = uuid4()
    direct_user_id = uuid4()
    durable_user_id = uuid4()
    fresh_user_id = uuid4()
    direct_turn_id, direct_attempt_id, _ = conversation_turn_ids(
        workspace_id,
        conversation_id,
        direct_user_id,
    )
    durable_turn_id, durable_attempt_id, _ = conversation_turn_ids(
        workspace_id,
        conversation_id,
        durable_user_id,
    )
    fresh_turn_id, fresh_attempt_id, _ = conversation_turn_ids(
        workspace_id,
        conversation_id,
        fresh_user_id,
    )
    stale_at = datetime.now(UTC) - timedelta(hours=1)

    async with database.begin() as session:
        await create_conversation(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            title="Direct crash recovery",
        )
        for message_id, content in (
            (direct_user_id, "Direct request whose API process crashed"),
            (durable_user_id, "Durable request still owned by Temporal"),
            (fresh_user_id, "Fresh direct request still running"),
        ):
            await append_conversation_message(
                session,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                message_id=message_id,
                role="USER",
                content=content,
            )
        await create_conversation_turn(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=direct_turn_id,
            user_message_id=direct_user_id,
        )
        await acquire_conversation_turn_attempt(
            session,
            workspace_id=workspace_id,
            turn_id=direct_turn_id,
            attempt_id=direct_attempt_id,
            trace_id="trace-direct-api-crash",
        )
        await create_conversation_turn(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=durable_turn_id,
            user_message_id=durable_user_id,
            task_id=task_id,
        )
        durable_attempt = await acquire_conversation_turn_attempt(
            session,
            workspace_id=workspace_id,
            turn_id=durable_turn_id,
            attempt_id=durable_attempt_id,
            trace_id="trace-durable-running",
        )
        await create_conversation_turn(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=fresh_turn_id,
            user_message_id=fresh_user_id,
        )
        await acquire_conversation_turn_attempt(
            session,
            workspace_id=workspace_id,
            turn_id=fresh_turn_id,
            attempt_id=fresh_attempt_id,
            trace_id="trace-fresh-direct",
        )
        await session.execute(
            update(ConversationTurnRow)
            .where(ConversationTurnRow.id.in_([direct_turn_id, durable_turn_id]))
            .values(updated_at=stale_at)
        )

    async with database.begin() as session:
        recovered = await reconcile_abandoned_direct_turns(
            session,
            stale_after_seconds=60,
            workspace_id=workspace_id,
        )
    async with database.begin() as session:
        replayed = await reconcile_abandoned_direct_turns(
            session,
            stale_after_seconds=60,
            workspace_id=workspace_id,
        )
    async with database() as session:
        direct_turn = await get_conversation_turn(session, workspace_id, direct_turn_id)
        durable_turn = await get_conversation_turn(session, workspace_id, durable_turn_id)
        fresh_turn = await get_conversation_turn(session, workspace_id, fresh_turn_id)
        stored_direct_attempt = await session.get(
            ConversationTurnAttemptRow,
            direct_attempt_id,
        )
        stored_durable_attempt = await session.get(
            ConversationTurnAttemptRow,
            durable_attempt_id,
        )
        direct_outcome = await session.scalar(
            select(ConversationTurnOutcomeRow).where(
                ConversationTurnOutcomeRow.workspace_id == workspace_id,
                ConversationTurnOutcomeRow.turn_id == direct_turn_id,
            )
        )
        durable_outcome = await session.scalar(
            select(ConversationTurnOutcomeRow).where(
                ConversationTurnOutcomeRow.workspace_id == workspace_id,
                ConversationTurnOutcomeRow.turn_id == durable_turn_id,
            )
        )
        audit_rows = (
            await session.scalars(
                select(AuditRow).where(
                    AuditRow.workspace_id == workspace_id,
                    AuditRow.action == "conversation.turn.reconciled",
                    AuditRow.resource_id == direct_turn_id,
                )
            )
        ).all()
        timeline = await load_conversation_timeline_messages(
            session,
            workspace_id,
            conversation_id,
        )
        context = await load_conversation_context_projection(
            session,
            workspace_id,
            conversation_id,
        )

    assert recovered == 1
    assert replayed == 0
    assert direct_turn.state is ConversationTurnState.COMPLETED
    assert not direct_turn.context_eligible
    assert stored_direct_attempt is not None
    assert stored_direct_attempt.state == ConversationTurnAttemptState.FAILED.value
    assert direct_outcome is not None
    assert direct_outcome.status == ConversationTurnOutcomeStatus.UNAVAILABLE.value
    assert direct_outcome.failure_payload is not None
    assert direct_outcome.failure_payload["code"] == FailureCode.LLM_TIMEOUT.value
    assert durable_turn.state is ConversationTurnState.RUNNING
    assert durable_turn.fencing_token == durable_attempt.fencing_token
    assert stored_durable_attempt is not None
    assert stored_durable_attempt.state == ConversationTurnAttemptState.RUNNING.value
    assert durable_outcome is None
    assert fresh_turn.state is ConversationTurnState.RUNNING
    assert len(audit_rows) == 1
    assert audit_rows[0].task_id is None
    assert audit_rows[0].result is not None
    assert audit_rows[0].result["outcome_status"] == "UNAVAILABLE"
    recovered_messages = [
        item for item in timeline if item.turn_id == direct_turn_id and item.role == "ASSISTANT"
    ]
    assert len(recovered_messages) == 1
    assert recovered_messages[0].content == turn_failure_message(FailureCode.LLM_TIMEOUT)
    assert recovered_messages[0].turn_outcome_status is ConversationTurnOutcomeStatus.UNAVAILABLE
    assert context == ()


async def test_failed_research_keeps_reviewed_partial_facts_out_of_memory(database):
    from enterprise_agent.capabilities import builtins as b
    from enterprise_agent.contracts.domain import CapabilityRequest
    from enterprise_agent.runtime.answer_coverage import AnswerCoverageItem
    from enterprise_agent.storage.attempts import fail_attempt
    from tests.unit.test_reviewer import review_fixture

    workspace, conversation, turn = await _turn_fixture(database)
    turn_attempt, execution_attempt = uuid4(), uuid4()
    async with database.begin() as session:
        await acquire_conversation_turn_attempt(session, workspace_id=workspace, turn_id=turn.turn_id,
            attempt_id=turn_attempt, trace_id="partial-result-test")
        step = await session.scalar(select(StepRow.id).where(StepRow.task_id == turn.task_id))
        owner = await acquire_attempt(session, workspace, step, execution_attempt,
            "partial-test-worker", "partial-result-test")
        _, _, source = review_fixture()
        source = source.model_copy(update={"workspace_id": workspace, "task_id": turn.task_id,
            "step_id": step, "generated_by": execution_attempt, "source_type": "WEB",
            "locator": "https://records.example/alpha"})
        assert await commit_evidence(session, workspace, execution_attempt, owner.fencing_token, (source,))
        step, execution_attempt = uuid4(), uuid4()
        session.add(StepRow(id=step, workspace_id=workspace, task_id=turn.task_id, state="READY"))
        await session.flush()
        owner = await acquire_attempt(session, workspace, step, execution_attempt,
            "partial-test-model", "partial-result-test")
        task = await session.get(TaskRow, turn.task_id)
        task.goal_payload = {**(task.goal_payload or {}), "requested_skill": "web-research"}
    claim = b.GroundedAnswerClaim(content=source.content, supports=(
        b.EvidenceSupport(evidence_id=source.evidence_id, quote=source.content),))
    candidate = b.WebStructuredAnswer(answer=source.content, answer_status="ANSWERED",
        evidence_ids=(source.evidence_id,), claims=(claim,))
    quality = b.AnswerQualityAssessment(decision="REPLAN", acceptable=False,
        answered_core_request=False, language_matches=True, appropriate_depth=True,
        supported_claim_indexes=(1,), missing_aspects=("Beta",), coverage=(
            AnswerCoverageItem(requirement="Alpha", status="ANSWERED", entity_alignment="MATCH",
                period_alignment="MATCH", evidence_ids=(source.evidence_id,)),
            AnswerCoverageItem(requirement="Beta", status="MISSING_EVIDENCE")))
    request = CapabilityRequest(command_id=uuid4(), operation_id=uuid4(), workspace_id=workspace,
        task_id=turn.task_id, step_id=step, attempt_id=execution_attempt, fencing_token=owner.fencing_token,
        capability_id="model.structured_output", capability_version="1.0.0", arguments={})
    await b._record_answer_quality_assessment(database, request, quality, candidate=candidate, evidence=(source,))
    async with database.begin() as session:
        await fail_attempt(session, workspace, execution_attempt, owner.fencing_token,
            Failure(code=FailureCode.BUDGET_EXCEEDED, safe_detail="test allowance exhausted", trace_id="partial-result-test"),
            retryable=False, retry_delay_seconds=0)
        task = await session.get(TaskRow, turn.task_id)
        task.state = TaskState.FAILED.value
    async with database.begin() as session:
        message = await materialize_chat_task_turn(session, workspace_id=workspace, task_id=turn.task_id,
            trace_id="partial-result-test")
    async with database.begin() as session:
        replay = await materialize_chat_task_turn(session, workspace_id=workspace, task_id=turn.task_id,
            trace_id="partial-result-replay")
        outcome = await session.scalar(select(ConversationTurnOutcomeRow).where(ConversationTurnOutcomeRow.turn_id==turn.turn_id))
        task = await session.get(TaskRow, turn.task_id)
        projection = await load_conversation_context_projection(session, workspace, conversation)
    assert source.content in message.content and "已找到并核验部分信息" in message.content
    assert "尚未核验的信息" in message.content and "Beta" in message.content
    assert replay == message
    assert task.state == TaskState.FAILED.value
    assert outcome.status == ConversationTurnOutcomeStatus.PARTIALLY_ANSWERED.value
    assert outcome.context_eligible is False
    assert projection == ()
