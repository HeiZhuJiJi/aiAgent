from datetime import UTC, datetime
from uuid import uuid4

from enterprise_agent.completion.delivery import build_delivery_decision
from enterprise_agent.contracts.domain import DeliveryDecisionType, ReviewResult
from enterprise_agent.storage.completion import (
    create_delivery_decision,
    create_requirement_assessment,
    create_verification_result,
    get_current_delivery_decision,
    get_effective_assessments,
    list_verification_results,
    materialize_completion_after_review,
)
from tests.integration.completion_fixtures import (
    assessment_for,
    database,  # noqa: F401
    evidence_and_claim,
    prepare,
    review_attempt,
    verification_for,
)


async def test_evidence_claim_verification_assessment_delivery_chain_is_idempotent(database):  # noqa: F811
    fixture = await prepare(database)
    item, claim = await evidence_and_claim(database, fixture)
    attempt = await review_attempt(database, fixture)
    verification = verification_for(fixture, attempt, item, claim)
    assessment = assessment_for(fixture, verification)
    decision = build_delivery_decision(
        fixture.contract,
        (fixture.requirement,),
        (assessment,),
        (verification,),
        fixture.snapshot,
        decision_id=uuid4(),
        created_at=datetime.now(UTC),
    )

    async with database.begin() as session:
        assert await create_verification_result(session, verification) == verification
        assert await create_verification_result(session, verification) == verification
        assert await create_requirement_assessment(session, assessment) == assessment
        assert await create_requirement_assessment(session, assessment) == assessment
        assert await create_delivery_decision(session, decision) == decision
        assert await create_delivery_decision(session, decision) == decision

    async with database() as session:
        assert await list_verification_results(session, fixture.workspace_id, fixture.task_id) == (
            verification,
        )
        assert await get_effective_assessments(session, fixture.workspace_id, fixture.task_id) == (
            assessment,
        )
        restored = await get_current_delivery_decision(
            session, fixture.workspace_id, fixture.task_id
        )
        assert restored == decision
        assert restored is not None
        assert restored.decision is DeliveryDecisionType.FULL_ANSWER


async def test_completed_task_with_unsupported_requirement_is_not_delivered(database):  # noqa: F811
    fixture = await prepare(database)
    item, claim = await evidence_and_claim(database, fixture)
    attempt = await review_attempt(database, fixture)
    verification = verification_for(fixture, attempt, item, claim, entity_match=False)
    assessment = assessment_for(fixture, verification)
    decision = build_delivery_decision(
        fixture.contract,
        (fixture.requirement,),
        (assessment,),
        (verification,),
        fixture.snapshot,
        decision_id=uuid4(),
        created_at=datetime.now(UTC),
    )
    assert decision.decision is DeliveryDecisionType.UNAVAILABLE
    assert decision.included_claim_ids == ()
    assert decision.unresolved_requirement_ids == (fixture.requirement.id,)


async def test_reviewer_bridge_materializes_completion_facts_in_one_fenced_transaction(database):  # noqa: F811
    fixture = await prepare(database)
    item, claim = await evidence_and_claim(database, fixture)
    attempt = await review_attempt(database, fixture)
    review = ReviewResult(
        accepted=True,
        missing_deliverables=(),
        unsupported_claims=(),
        constraint_violations=(),
        citation_errors=(),
    )
    async with database.begin() as session:
        decision = await materialize_completion_after_review(
            session,
            workspace_id=fixture.workspace_id,
            task_id=fixture.task_id,
            snapshot_id=fixture.snapshot.snapshot_id,
            operation_id=uuid4(),
            attempt_id=attempt.id,
            fencing_token=attempt.fencing_token,
            review=review,
            claims=(claim,),
            evidence=(item,),
            created_at=datetime.now(UTC),
        )
        assert decision is not None
        assert decision.decision is DeliveryDecisionType.FULL_ANSWER

    async with database() as session:
        assert await get_current_delivery_decision(
            session, fixture.workspace_id, fixture.task_id
        ) == decision


async def test_reviewer_bridge_materializes_rejected_review_as_unavailable(database):  # noqa: F811
    fixture = await prepare(database)
    item, claim = await evidence_and_claim(database, fixture)
    attempt = await review_attempt(database, fixture)
    review = ReviewResult(
        accepted=False,
        missing_deliverables=("formal report",),
        unsupported_claims=(claim.claim_id,),
        constraint_violations=(),
        citation_errors=("claim was not sufficiently supported",),
    )
    async with database.begin() as session:
        decision = await materialize_completion_after_review(
            session,
            workspace_id=fixture.workspace_id,
            task_id=fixture.task_id,
            snapshot_id=fixture.snapshot.snapshot_id,
            operation_id=uuid4(),
            attempt_id=attempt.id,
            fencing_token=attempt.fencing_token,
            review=review,
            claims=(claim,),
            evidence=(item,),
            created_at=datetime.now(UTC),
        )
        assert decision is not None
        assert decision.decision is DeliveryDecisionType.UNAVAILABLE
