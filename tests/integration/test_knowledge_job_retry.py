import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from enterprise_agent.contracts.domain import FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.identity.contracts import Identity
from enterprise_agent.storage.knowledge_jobs import (
    claim_knowledge_ingestion,
    format_knowledge_ingestion_failure,
    get_knowledge_ingestion,
    record_knowledge_ingestion_attempt_failure,
    request_knowledge_ingestion,
    retry_knowledge_ingestion,
)
from enterprise_agent.storage.tables import AuditRow, OutboxEventRow, Workspace


@pytest.fixture
async def database() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_failed_ingestion_retry_is_idempotent_and_fences_old_claim(
    database: async_sessionmaker[AsyncSession],
) -> None:
    workspace_id = uuid4()
    document_id = uuid4()
    user_id = uuid4()
    identity = Identity(
        user_id=user_id,
        email="knowledge-retry@example.test",
        session_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    async with database.begin() as session:
        session.add(Workspace(id=workspace_id, name="knowledge-retry"))
        requested = await request_knowledge_ingestion(
            session,
            identity=identity,
            workspace_id=workspace_id,
            document_id=document_id,
            name="original.pdf",
            media_type="application/pdf",
            source_hash="a" * 64,
            object_name=f"{workspace_id}/{document_id}/{'a' * 64}",
            parser_version="parser-1.0.0+bge-m3",
            trace_id="knowledge-retry-request",
            task_queue="knowledge-tests",
        )
    assert requested.state == "PENDING"

    async with database.begin() as session:
        first_claim = await claim_knowledge_ingestion(
            session,
            workspace_id,
            requested.ingestion_id,
        )
    assert first_claim is not None
    safe_error = format_knowledge_ingestion_failure(
        FailureCode.LLM_SERVER_ERROR,
        "EMBED",
        "model transport attempts exhausted",
    )
    async with database.begin() as session:
        intermediate = await record_knowledge_ingestion_attempt_failure(
            session,
            first_claim,
            safe_error,
            terminal=False,
        )
    assert intermediate.state == "RUNNING"
    assert intermediate.completed_at is None
    assert intermediate.safe_error == safe_error

    async with database.begin() as session:
        final_claim = await claim_knowledge_ingestion(
            session,
            workspace_id,
            requested.ingestion_id,
        )
    assert final_claim is not None
    assert final_claim.fencing_token > first_claim.fencing_token
    with pytest.raises(DomainError) as stale:
        async with database.begin() as session:
            await record_knowledge_ingestion_attempt_failure(
                session,
                first_claim,
                safe_error,
                terminal=True,
            )
    assert stale.value.code is FailureCode.STALE_ATTEMPT

    async with database.begin() as session:
        failed = await record_knowledge_ingestion_attempt_failure(
            session,
            final_claim,
            safe_error,
            terminal=True,
        )
    assert failed.state == "FAILED"
    assert failed.completed_at is not None
    assert failed.safe_error == safe_error

    async with database.begin() as session:
        retried = await retry_knowledge_ingestion(
            session,
            identity=identity,
            workspace_id=workspace_id,
            ingestion_id=requested.ingestion_id,
            expected_version=failed.version,
            trace_id="knowledge-retry",
            task_queue="knowledge-tests",
        )
    assert retried.state == "PENDING"
    assert retried.version == failed.version + 1
    assert retried.safe_error is None
    assert retried.completed_at is None

    # Retrying the same command after response loss returns the same transition.
    async with database.begin() as session:
        replay = await retry_knowledge_ingestion(
            session,
            identity=identity,
            workspace_id=workspace_id,
            ingestion_id=requested.ingestion_id,
            expected_version=failed.version,
            trace_id="knowledge-retry-replay",
            task_queue="knowledge-tests",
        )
    assert replay == retried

    # A response-loss replay remains idempotent after the worker has claimed and
    # advanced the accepted retry. The deterministic Outbox event, not a transient
    # PENDING state, identifies the logical command.
    async with database.begin() as session:
        progressed_claim = await claim_knowledge_ingestion(
            session,
            workspace_id,
            requested.ingestion_id,
        )
    assert progressed_claim is not None
    async with database() as session:
        running = await get_knowledge_ingestion(
            session,
            workspace_id,
            requested.ingestion_id,
        )
    assert running.state == "RUNNING"
    async with database.begin() as session:
        replay_after_claim = await retry_knowledge_ingestion(
            session,
            identity=identity,
            workspace_id=workspace_id,
            ingestion_id=requested.ingestion_id,
            expected_version=failed.version,
            trace_id="knowledge-retry-replay-after-claim",
            task_queue="knowledge-tests",
        )
    assert replay_after_claim == running

    async with database.begin() as session:
        running_after_failure = await record_knowledge_ingestion_attempt_failure(
            session,
            progressed_claim,
            safe_error,
            terminal=False,
        )
    async with database.begin() as session:
        replay_after_progress = await retry_knowledge_ingestion(
            session,
            identity=identity,
            workspace_id=workspace_id,
            ingestion_id=requested.ingestion_id,
            expected_version=failed.version,
            trace_id="knowledge-retry-replay-after-progress",
            task_queue="knowledge-tests",
        )
    assert replay_after_progress == running_after_failure

    with pytest.raises(DomainError) as invalid_state:
        async with database.begin() as session:
            await retry_knowledge_ingestion(
                session,
                identity=identity,
                workspace_id=workspace_id,
                ingestion_id=requested.ingestion_id,
                expected_version=running_after_failure.version,
                trace_id="knowledge-retry-again",
                task_queue="knowledge-tests",
            )
    assert invalid_state.value.code is FailureCode.VALIDATION_FAILED

    async with database() as session:
        current = await get_knowledge_ingestion(session, workspace_id, requested.ingestion_id)
        events = (
            await session.scalars(
                select(OutboxEventRow).where(
                    OutboxEventRow.workspace_id == workspace_id,
                    OutboxEventRow.aggregate_id == requested.ingestion_id,
                )
            )
        ).all()
        retry_audits = (
            await session.scalars(
                select(AuditRow).where(
                    AuditRow.workspace_id == workspace_id,
                    AuditRow.action == "knowledge.ingestion.retry_requested",
                )
            )
        ).all()
    assert current == running_after_failure
    assert len(events) == 2
    retry_events = [event for event in events if event.payload.get("retry_version")]
    assert len(retry_events) == 1
    retry_workflow_id = retry_events[0].payload["workflow_id"]
    assert isinstance(retry_workflow_id, str)
    assert retry_workflow_id.endswith(f"-v{retried.version}")
    assert len(retry_audits) == 1
