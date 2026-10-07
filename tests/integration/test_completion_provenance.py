from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError

from enterprise_agent.completion.assessment import assess_requirement
from enterprise_agent.contracts.domain import FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.completion import (
    create_requirement_assessment,
    create_verification_result,
    get_effective_assessments,
)
from enterprise_agent.storage.tables import RequirementAssessmentRow, StepRow
from tests.integration.completion_fixtures import (
    database,  # noqa: F401
    evidence_and_claim,
    prepare,
    review_attempt,
    verification_for,
)


async def test_assessment_supersession_is_append_only_and_old_fact_remains_auditable(database):  # noqa: F811
    fixture = await prepare(database)
    item, claim = await evidence_and_claim(database, fixture)
    attempt = await review_attempt(database, fixture)
    first_facts = verification_for(fixture, attempt, item, claim, entity_match=False)
    first = assess_requirement(
        fixture.requirement,
        first_facts,
        assessment_id=uuid4(),
        evaluator_version="completion-policy.v1",
    )
    second_facts = verification_for(fixture, attempt, item, claim)
    second = assess_requirement(
        fixture.requirement,
        second_facts,
        assessment_id=uuid4(),
        evaluator_version="completion-policy.v1",
        supersedes_id=first.id,
    )
    async with database.begin() as session:
        await create_verification_result(session, first_facts)
        await create_requirement_assessment(session, first)
        await create_verification_result(session, second_facts)
        await create_requirement_assessment(session, second)

    async with database() as session:
        effective = await get_effective_assessments(
            session, fixture.workspace_id, fixture.task_id
        )
        assert effective == (second,)
        rows = (
            await session.scalars(
                select_all_assessments(fixture.workspace_id, fixture.task_id)
            )
        ).all()
        assert {row.id for row in rows} == {first.id, second.id}

    with pytest.raises(DBAPIError):
        async with database.begin() as session:
            await session.execute(
                update(RequirementAssessmentRow)
                .where(RequirementAssessmentRow.id == first.id)
                .values(payload={"tampered": True})
            )
    with pytest.raises(DBAPIError):
        async with database.begin() as session:
            await session.execute(
                delete(RequirementAssessmentRow).where(RequirementAssessmentRow.id == first.id)
            )


async def test_late_verification_from_superseded_attempt_is_rejected(database):  # noqa: F811
    fixture = await prepare(database)
    item, claim = await evidence_and_claim(database, fixture)
    old_attempt = await review_attempt(database, fixture)
    old_facts = verification_for(fixture, old_attempt, item, claim)
    async with database.begin() as session:
        await session.execute(
            update(StepRow)
            .where(StepRow.workspace_id == fixture.workspace_id, StepRow.id == fixture.review_step_id)
            .values(lease_expires_at=datetime.now(UTC))
        )
    new_attempt = await review_attempt(database, fixture)
    assert new_attempt.fencing_token > old_attempt.fencing_token
    with pytest.raises(DomainError) as raised:
        async with database.begin() as session:
            await create_verification_result(session, old_facts)
    assert raised.value.code is FailureCode.STALE_ATTEMPT


def select_all_assessments(workspace_id, task_id):
    from sqlalchemy import select

    return select(RequirementAssessmentRow).where(
        RequirementAssessmentRow.workspace_id == workspace_id,
        RequirementAssessmentRow.task_id == task_id,
    )
