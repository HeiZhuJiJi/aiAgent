import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.client import Client

from enterprise_agent.domain.states import DomainError
from enterprise_agent.runtime.outbox_dispatcher import publish_temporal
from enterprise_agent.storage.outbox import (
    OutboxLease,
    claim_next,
    enqueue,
    mark_published,
    reschedule,
)
from enterprise_agent.storage.tables import OutboxEventRow
from tests.integration.test_fencing import seed


@pytest.fixture
async def database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def claim(database, worker_id, workspace_id):
    async with database.begin() as session:
        return await claim_next(session, worker_id, workspace_id=workspace_id)


async def test_concurrent_claims_are_unique_and_payload_identity_is_stable(database):
    workspace_id, task_id, _ = await seed(database)
    event_ids = [uuid4() for _ in range(3)]
    async with database.begin() as session:
        for index, event_id in enumerate(event_ids):
            await enqueue(
                session,
                event_id,
                workspace_id,
                "TaskRun",
                task_id,
                "task.test",
                {"index": index},
            )
        await enqueue(
            session,
            event_ids[0],
            workspace_id,
            "TaskRun",
            task_id,
            "task.test",
            {"index": 0},
        )
        with pytest.raises(DomainError, match="identity mismatch"):
            await enqueue(
                session,
                event_ids[0],
                workspace_id,
                "TaskRun",
                task_id,
                "task.test",
                {"index": 99},
            )
    leases = await asyncio.gather(
        *(claim(database, f"worker-{index}", workspace_id) for index in range(4))
    )
    claimed = [lease for lease in leases if lease is not None]
    assert len(claimed) == 3
    assert {lease.event_id for lease in claimed} == set(event_ids)
    assert len({lease.event_id for lease in claimed}) == len(claimed)
    for index, lease in enumerate(claimed):
        async with database.begin() as session:
            await mark_published(session, lease, f"worker-{leases.index(lease)}")
    async with database() as session:
        states = (
            await session.scalars(
                select(OutboxEventRow.state).where(OutboxEventRow.workspace_id == workspace_id)
            )
        ).all()
        assert states == ["PUBLISHED", "PUBLISHED", "PUBLISHED"]


async def test_expired_lease_is_refenced_and_old_publisher_is_rejected(database):
    workspace_id, task_id, _ = await seed(database)
    event_id = uuid4()
    async with database.begin() as session:
        await enqueue(
            session,
            event_id,
            workspace_id,
            "TaskRun",
            task_id,
            "task.test",
            {"task_id": str(task_id)},
        )
    old = await claim(database, "worker-old", workspace_id)
    assert old is not None
    async with database.begin() as session:
        await session.execute(
            update(OutboxEventRow)
            .where(OutboxEventRow.id == event_id)
            .values(lease_expires_at=func.clock_timestamp())
        )
    current = await claim(database, "worker-current", workspace_id)
    assert current is not None and current.lease_token > old.lease_token
    with pytest.raises(DomainError, match="lease rejected"):
        async with database.begin() as session:
            await mark_published(session, old, "worker-old")
    async with database.begin() as session:
        await reschedule(session, current, "worker-current", "temporary failure", 0)
    retry = await claim(database, "worker-retry", workspace_id)
    assert retry is not None and retry.attempts == 3
    async with database.begin() as session:
        await mark_published(session, retry, "worker-retry")


async def test_knowledge_workflow_start_reconciles_duplicate_publish():
    client = await Client.connect("127.0.0.1:57233")
    ingestion_id = uuid4()
    workspace_id = uuid4()
    workflow_id = "knowledge-ingestion-test-" + str(ingestion_id)
    lease = OutboxLease(
        event_id=uuid4(),
        workspace_id=workspace_id,
        aggregate_type="KnowledgeIngestion",
        aggregate_id=ingestion_id,
        event_type="knowledge.ingestion.requested",
        payload={
            "workflow_id": workflow_id,
            "task_queue": "unassigned-" + str(uuid4()),
            "workspace_id": str(workspace_id),
            "ingestion_id": str(ingestion_id),
        },
        attempts=1,
        lease_token=1,
    )
    try:
        await publish_temporal(client, lease)
        await publish_temporal(client, lease)
    finally:
        await client.get_workflow_handle(workflow_id).terminate("test cleanup")
