import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.contracts.domain import Evidence, FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.attempts import acquire_attempt, commit_evidence
from enterprise_agent.storage.tables import (
    AttemptRow,
    AuditRow,
    EvidenceRow,
    RuntimeGuardRow,
    StepRow,
    TaskRow,
    Workspace,
)


@pytest.fixture
async def database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def seed(database):
    w, t, s = uuid4(), uuid4(), uuid4()
    async with database.begin() as session:
        session.add(Workspace(id=w, name="fencing-test"))
        await session.flush()
        session.add(TaskRow(id=t, workspace_id=w, state="RUNNING"))
        await session.flush()
        session.add(RuntimeGuardRow(task_id=t, workspace_id=w))
        session.add(StepRow(id=s, workspace_id=w, task_id=t, state="READY"))
    return w, t, s


def evidence(w, t, s, a):
    return Evidence(
        evidence_id=uuid4(),
        workspace_id=w,
        task_id=t,
        step_id=s,
        source_type="DOCUMENT",
        source_id="fixture",
        locator="paragraph:1",
        content="Verified statement",
        content_hash="a" * 64,
        source_hash="b" * 64,
        source_version="1",
        confidence=1,
        relevance=1,
        retrieved_at=datetime.now(UTC),
        generated_by=a,
        derived_from=(),
    )


async def test_postgres_zombie_and_idempotent_commit(database):
    w, t, s = await seed(database)
    a, b = uuid4(), uuid4()
    async with database.begin() as session:
        token_a = (await acquire_attempt(session, w, s, a, "worker-a", "trace-a")).fencing_token
    async with database.begin() as session:
        await session.execute(
            update(StepRow).where(StepRow.id == s).values(lease_expires_at=func.now())
        )
    async with database.begin() as session:
        token_b = (await acquire_attempt(session, w, s, b, "worker-b", "trace-b")).fencing_token
    assert token_b > token_a
    with pytest.raises(DomainError) as raised:
        async with database.begin() as session:
            await commit_evidence(session, w, a, token_a, (evidence(w, t, s, a),))
    assert raised.value.code == FailureCode.STALE_ATTEMPT
    accepted = (evidence(w, t, s, b),)
    async with database.begin() as session:
        await commit_evidence(session, w, b, token_b, accepted)
    async with database.begin() as session:
        await commit_evidence(session, w, b, token_b, accepted)
        assert (
            len(
                (
                    await session.scalars(select(EvidenceRow).where(EvidenceRow.workspace_id == w))
                ).all()
            )
            == 1
        )
        assert (
            len((await session.scalars(select(AuditRow).where(AuditRow.workspace_id == w))).all())
            == 1
        )
        assert (await session.get(AttemptRow, a)).state == "UNKNOWN"
    with pytest.raises(DBAPIError):
        async with database.begin() as session:
            await session.execute(
                update(AuditRow).where(AuditRow.workspace_id == w).values(action="tampered")
            )


async def test_cross_workspace_acquire_denied(database):
    _, _, s = await seed(database)
    with pytest.raises(DomainError) as raised:
        async with database.begin() as session:
            await acquire_attempt(session, uuid4(), s, uuid4(), "worker", "trace")
    assert raised.value.code == FailureCode.POLICY_DENIED


async def test_cross_workspace_fk_rejected(database):
    w, _, _ = await seed(database)
    _, foreign_task, _ = await seed(database)
    with pytest.raises(DBAPIError):
        async with database.begin() as session:
            session.add(StepRow(id=uuid4(), workspace_id=w, task_id=foreign_task, state="READY"))
