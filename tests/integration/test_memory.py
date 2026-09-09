import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.contracts.domain import (
    ConversationSummary,
    FailureCode,
    ProjectMemory,
    ProjectMemoryType,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.memory import (
    append_conversation_summary,
    create_conversation,
    load_active_project_memory,
    load_conversation_summaries,
    persist_project_memory,
)
from enterprise_agent.storage.tables import ConversationSummaryRow, ProjectMemoryRow
from tests.integration.test_fencing import seed


@pytest.fixture
async def database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_conversation_summaries_are_versioned_replay_safe_and_workspace_scoped(database):
    workspace_id, _, _ = await seed(database)
    foreign_workspace, _, _ = await seed(database)
    conversation_id = uuid4()
    now = datetime.now(UTC)
    first = ConversationSummary(
        summary_id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        version=1,
        content="The user is assessing supplier admission risk.",
        source="conversation-turns:1-4",
        created_at=now,
    )
    second = first.model_copy(
        update={
            "summary_id": uuid4(),
            "version": 2,
            "content": "Supplier admission now requires sanctions evidence.",
            "source": "conversation-turns:1-8",
            "created_at": now + timedelta(seconds=1),
        }
    )
    async with database.begin() as session:
        await create_conversation(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            title="Supplier admission",
        )
        assert await append_conversation_summary(session, first) == first
        assert await append_conversation_summary(session, first) == first
        assert await append_conversation_summary(session, second) == second
    async with database() as session:
        assert await load_conversation_summaries(session, workspace_id, conversation_id) == (
            first,
            second,
        )
        assert await load_conversation_summaries(session, foreign_workspace, conversation_id) == ()

    invalid = second.model_copy(update={"summary_id": uuid4(), "version": 4})
    with pytest.raises(DomainError) as raised:
        async with database.begin() as session:
            await append_conversation_summary(session, invalid)
    assert raised.value.code == FailureCode.CONSISTENCY_VIOLATION


async def test_project_memory_supersedes_without_losing_history(database):
    workspace_id, _, _ = await seed(database)
    foreign_workspace, _, _ = await seed(database)
    now = datetime.now(UTC)
    old = ProjectMemory(
        memory_id=uuid4(),
        workspace_id=workspace_id,
        content="Supplier A uses annual screening.",
        type=ProjectMemoryType.FACT,
        source="task:initial-assessment",
        confidence=0.8,
        created_at=now,
    )
    current = ProjectMemory(
        memory_id=uuid4(),
        workspace_id=workspace_id,
        content="Supplier A uses quarterly screening.",
        type=ProjectMemoryType.FACT,
        source="task:updated-assessment",
        confidence=0.95,
        created_at=now + timedelta(seconds=1),
        supersedes=old.memory_id,
    )
    decision = ProjectMemory(
        memory_id=uuid4(),
        workspace_id=workspace_id,
        content="Require a human decision before admission.",
        type=ProjectMemoryType.DECISION,
        source="policy:admission-v1",
        confidence=1,
        created_at=now + timedelta(seconds=2),
    )
    async with database.begin() as session:
        assert await persist_project_memory(session, old) == old
        assert await persist_project_memory(session, old) == old
        assert await persist_project_memory(session, current) == current
        assert await persist_project_memory(session, decision) == decision
    async with database() as session:
        assert await load_active_project_memory(session, workspace_id) == (current, decision)
        assert await load_active_project_memory(
            session, workspace_id, ProjectMemoryType.FACT
        ) == (current,)
        assert await load_active_project_memory(session, foreign_workspace) == ()

    branch = current.model_copy(
        update={"memory_id": uuid4(), "supersedes": old.memory_id, "content": "Divergent fact"}
    )
    with pytest.raises(DomainError, match="already superseded"):
        async with database.begin() as session:
            await persist_project_memory(session, branch)
    changed_type = current.model_copy(
        update={
            "memory_id": uuid4(),
            "supersedes": decision.memory_id,
            "type": ProjectMemoryType.PREFERENCE,
        }
    )
    with pytest.raises(DomainError, match="type cannot change"):
        async with database.begin() as session:
            await persist_project_memory(session, changed_type)


@pytest.mark.parametrize(
    "statement",
    [
        update(ConversationSummaryRow).values(content_hash="c" * 64),
        delete(ProjectMemoryRow),
    ],
)
async def test_database_rejects_summary_or_memory_mutation(database, statement):
    workspace_id, _, _ = await seed(database)
    conversation_id = uuid4()
    now = datetime.now(UTC)
    async with database.begin() as session:
        await create_conversation(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            title="Immutable history",
        )
        await append_conversation_summary(
            session,
            ConversationSummary(
                summary_id=uuid4(), workspace_id=workspace_id,
                conversation_id=conversation_id, version=1, content="Summary.",
                source="conversation:1", created_at=now,
            ),
        )
        await persist_project_memory(
            session,
            ProjectMemory(
                memory_id=uuid4(), workspace_id=workspace_id, content="Decision.",
                type=ProjectMemoryType.DECISION, source="task:1", confidence=1,
                created_at=now,
            ),
        )
    with pytest.raises(DBAPIError):
        async with database.begin() as session:
            await session.execute(statement.where(statement.table.c.workspace_id == workspace_id))
