import os
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.contracts.domain import AgentTurnDecision, FailureCode, ModelBinding
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.agent_decisions import (
    load_agent_turn_decision,
    store_agent_turn_decision,
)
from enterprise_agent.storage.memory import (
    append_conversation_message,
    create_conversation,
)
from enterprise_agent.storage.tables import ConversationTurnDecisionRow
from enterprise_agent.storage.turns import create_conversation_turn
from tests.integration.test_fencing import seed


@pytest.fixture
async def database():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _decision() -> AgentTurnDecision:
    return AgentTurnDecision.model_validate(
        {
            "action": "EXECUTE",
            "intent": {
                "speech_act": "QUESTION",
                "normalized_question": "解释一下量子力学",
                "temporal": {
                    "direction": "STABLE",
                    "requires_fresh_information": False,
                },
            },
            "execution": {
                "execution_level": "INLINE_CHAT",
                "source_plan": {"preferred_sources": ["MODEL"]},
                "risk": "READ",
                "maximum_tool_steps": 1,
                "confidence": 0.98,
            },
            "tool_plan": [
                {
                    "tool_name": "model.respond",
                    "purpose": "answer stable knowledge question",
                    "arguments": {},
                    "risk": "READ",
                }
            ],
        }
    )


async def test_agent_turn_decision_is_hashed_idempotent_and_immutable(database) -> None:
    workspace_id, _, _ = await seed(database)
    conversation_id = uuid4()
    user_message_id = uuid4()
    turn_id = uuid4()
    binding = ModelBinding(
        config_id=uuid4(),
        provider="OpenAICompatible",
        model_name="selected-model",
        config_version=2,
        selected_by="USER",
    )
    decision = _decision()
    async with database.begin() as session:
        await create_conversation(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            title="Agent decision",
        )
        await append_conversation_message(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_id=user_message_id,
            role="USER",
            content="解释一下量子力学",
        )
        await create_conversation_turn(
            session,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            user_message_id=user_message_id,
        )
        first = await store_agent_turn_decision(
            session,
            workspace_id=workspace_id,
            turn_id=turn_id,
            decision_version=1,
            model_binding=binding,
            decision=decision,
        )
        replay = await store_agent_turn_decision(
            session,
            workspace_id=workspace_id,
            turn_id=turn_id,
            decision_version=1,
            model_binding=binding,
            decision=decision,
        )
    assert replay == first

    async with database.begin() as session:
        loaded = await load_agent_turn_decision(
            session,
            workspace_id=workspace_id,
            turn_id=turn_id,
        )
    assert loaded is not None
    assert loaded.decision == decision
    assert loaded.model_binding == binding

    changed = decision.model_copy(
        update={
            "intent": decision.intent.model_copy(
                update={"normalized_question": "被改变的问题"}
            )
        }
    )
    with pytest.raises(DomainError) as mismatch:
        async with database.begin() as session:
            await store_agent_turn_decision(
                session,
                workspace_id=workspace_id,
                turn_id=turn_id,
                decision_version=1,
                model_binding=binding,
                decision=changed,
            )
    assert mismatch.value.code is FailureCode.CONSISTENCY_VIOLATION

    with pytest.raises(DBAPIError):
        async with database.begin() as session:
            await session.execute(
                update(ConversationTurnDecisionRow)
                .where(ConversationTurnDecisionRow.id == first.decision_id)
                .values(content_hash="0" * 64)
            )
