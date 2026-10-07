from __future__ import annotations

from uuid import UUID

from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import Contract, ConversationMessage, ProjectMemory
from enterprise_agent.storage.memory import (
    load_active_project_memory,
    load_conversation_summaries,
)
from enterprise_agent.storage.turns import load_conversation_context_projection


class AgentContextProjection(Contract):
    """The only conversation context boundary exposed to model roles."""

    current_turn: str = Field(min_length=1, max_length=1_000_000)
    completed_messages: tuple[ConversationMessage, ...] = Field(default=(), max_length=24)
    valid_summary: str | None = Field(default=None, max_length=32_000)
    structured_memory: tuple[ProjectMemory, ...] = Field(default=(), max_length=20)
    document_version_id: UUID | None = None
    workspace_knowledge_available: bool = False
    evidence_refs: tuple[UUID, ...] = Field(default=(), max_length=100)


async def load_agent_context_projection(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    conversation_id: UUID,
    current_turn: str,
    document_version_id: UUID | None,
    workspace_knowledge_available: bool,
) -> AgentContextProjection:
    """Load eligible context once; callers must not independently rebuild history."""

    turns = await load_conversation_context_projection(
        session,
        workspace_id,
        conversation_id,
    )
    summaries = await load_conversation_summaries(
        session,
        workspace_id,
        conversation_id,
    )
    memories = await load_active_project_memory(session, workspace_id)
    completed = tuple(
        message
        for turn in turns[-12:]
        for message in (turn.user_message, turn.assistant_message)
    )
    eligible_memory = tuple(item for item in memories if item.confidence >= 0.6)[-20:]
    return AgentContextProjection(
        current_turn=current_turn.strip(),
        completed_messages=completed,
        valid_summary=summaries[-1].content if summaries else None,
        structured_memory=eligible_memory,
        document_version_id=document_version_id,
        workspace_knowledge_available=workspace_knowledge_available,
    )
