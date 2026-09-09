import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.contracts.domain import Claim, FailureCode, Risk
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.attempts import acquire_attempt, commit_evidence
from enterprise_agent.storage.provenance import load_claims, persist_claim
from enterprise_agent.storage.tables import ClaimEvidenceLinkRow, ClaimRow
from tests.integration.test_fencing import evidence, seed


@pytest.fixture
async def database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def task_with_evidence(database):
    workspace_id, task_id, step_id = await seed(database)
    attempt_id = uuid4()
    async with database.begin() as session:
        token = (
            await acquire_attempt(
                session,
                workspace_id,
                step_id,
                attempt_id,
                "provenance-worker",
                "trace-provenance",
            )
        ).fencing_token
        item = evidence(workspace_id, task_id, step_id, attempt_id)
        await commit_evidence(
            session,
            workspace_id,
            attempt_id,
            token,
            (item,),
        )
    return workspace_id, task_id, item


async def test_claim_and_evidence_links_are_idempotent_and_hash_verified(database):
    workspace_id, task_id, item = await task_with_evidence(database)
    claim = Claim(
        claim_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        content="Supplier evidence was collected.",
        importance="HIGH",
        risk_level=Risk.READ,
        evidence_ids=(item.evidence_id,),
    )
    async with database.begin() as session:
        first = await persist_claim(session, claim)
        replay = await persist_claim(session, claim)
    assert first == replay == claim
    async with database() as session:
        restored = await load_claims(session, workspace_id, task_id)
        link_count = await session.scalar(
            select(func.count()).select_from(ClaimEvidenceLinkRow).where(
                ClaimEvidenceLinkRow.claim_id == claim.claim_id
            )
        )
    assert restored == (claim,)
    assert link_count == 1

    changed = claim.model_copy(update={"content": "Changed claim"})
    with pytest.raises(DomainError, match="immutable claim mismatch"):
        async with database.begin() as session:
            await persist_claim(session, changed)


async def test_claim_rejects_missing_or_cross_workspace_evidence(database):
    workspace_id, task_id, _ = await task_with_evidence(database)
    foreign_workspace, foreign_task, foreign_evidence = await task_with_evidence(database)
    claim = Claim(
        claim_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        content="Cross-boundary claim.",
        importance="MEDIUM",
        risk_level=Risk.READ,
        evidence_ids=(foreign_evidence.evidence_id,),
    )
    assert foreign_workspace != workspace_id and foreign_task != task_id
    with pytest.raises(DomainError) as raised:
        async with database.begin() as session:
            await persist_claim(session, claim)
    assert raised.value.code == FailureCode.VALIDATION_FAILED


@pytest.mark.parametrize(
    "statement",
    [update(ClaimRow).values(content_hash="c" * 64), delete(ClaimRow)],
)
async def test_database_rejects_claim_mutation(database, statement):
    workspace_id, task_id, item = await task_with_evidence(database)
    claim = Claim(
        claim_id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        content="Immutable claim.",
        importance="LOW",
        risk_level=Risk.READ,
        evidence_ids=(item.evidence_id,),
    )
    async with database.begin() as session:
        await persist_claim(session, claim)
    with pytest.raises(DBAPIError):
        async with database.begin() as session:
            await session.execute(statement.where(ClaimRow.id == claim.claim_id))
