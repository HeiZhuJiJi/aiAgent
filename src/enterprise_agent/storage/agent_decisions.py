from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import (
    AgentTurnDecision,
    FailureCode,
    ModelBinding,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.tables import (
    ConversationTurnDecisionRow,
    ConversationTurnRow,
)


@dataclass(frozen=True)
class AgentTurnDecisionSnapshot:
    decision_id: UUID
    workspace_id: UUID
    turn_id: UUID
    decision_version: int
    model_binding: ModelBinding
    decision: AgentTurnDecision
    content_hash: str


def _payload(
    *,
    decision_version: int,
    model_binding: ModelBinding,
    decision: AgentTurnDecision,
) -> tuple[dict[str, object], dict[str, object], str]:
    binding_payload = cast(dict[str, object], model_binding.model_dump(mode="json"))
    decision_payload = cast(dict[str, object], decision.model_dump(mode="json"))
    encoded = json.dumps(
        {
            "decision_version": decision_version,
            "model_binding": binding_payload,
            "decision": decision_payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return binding_payload, decision_payload, hashlib.sha256(encoded).hexdigest()


def _restore(row: ConversationTurnDecisionRow) -> AgentTurnDecisionSnapshot:
    model_binding = ModelBinding.model_validate(row.model_binding_payload)
    decision = AgentTurnDecision.model_validate(row.decision_payload)
    _, _, digest = _payload(
        decision_version=row.decision_version,
        model_binding=model_binding,
        decision=decision,
    )
    if digest != row.content_hash:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "agent Turn decision hash mismatch",
        )
    return AgentTurnDecisionSnapshot(
        decision_id=row.id,
        workspace_id=row.workspace_id,
        turn_id=row.turn_id,
        decision_version=row.decision_version,
        model_binding=model_binding,
        decision=decision,
        content_hash=row.content_hash,
    )


async def store_agent_turn_decision(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    turn_id: UUID,
    decision_version: int,
    model_binding: ModelBinding,
    decision: AgentTurnDecision,
) -> AgentTurnDecisionSnapshot:
    turn = await session.scalar(
        select(ConversationTurnRow).where(
            ConversationTurnRow.workspace_id == workspace_id,
            ConversationTurnRow.id == turn_id,
        )
    )
    if turn is None:
        raise DomainError(FailureCode.POLICY_DENIED, "turn unavailable")
    if turn.decision_version != decision_version:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "agent decision version does not match the Turn",
        )
    binding_payload, decision_payload, digest = _payload(
        decision_version=decision_version,
        model_binding=model_binding,
        decision=decision,
    )
    decision_id = uuid5(
        NAMESPACE_URL,
        f"agent-turn-decision:{workspace_id}:{turn_id}:{decision_version}",
    )
    await session.execute(
        insert(ConversationTurnDecisionRow)
        .values(
            id=decision_id,
            workspace_id=workspace_id,
            turn_id=turn_id,
            decision_version=decision_version,
            model_binding_payload=binding_payload,
            decision_payload=decision_payload,
            content_hash=digest,
        )
        .on_conflict_do_nothing()
    )
    row = await session.scalar(
        select(ConversationTurnDecisionRow).where(
            ConversationTurnDecisionRow.workspace_id == workspace_id,
            ConversationTurnDecisionRow.turn_id == turn_id,
            ConversationTurnDecisionRow.decision_version == decision_version,
        )
    )
    if row is None:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "agent Turn decision was not stored",
        )
    snapshot = _restore(row)
    if snapshot.content_hash != digest or snapshot.decision_id != decision_id:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "agent Turn decision replay mismatch",
        )
    return snapshot


async def load_agent_turn_decision(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    turn_id: UUID,
    decision_version: int = 1,
) -> AgentTurnDecisionSnapshot | None:
    row = await session.scalar(
        select(ConversationTurnDecisionRow).where(
            ConversationTurnDecisionRow.workspace_id == workspace_id,
            ConversationTurnDecisionRow.turn_id == turn_id,
            ConversationTurnDecisionRow.decision_version == decision_version,
        )
    )
    return None if row is None else _restore(row)
