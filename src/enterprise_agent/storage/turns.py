import hashlib
import json
from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from enterprise_agent.contracts.domain import (
    ConversationAttachment,
    ConversationMessage,
    ConversationTimelineMessage,
    ConversationTurn,
    ConversationTurnAttempt,
    ConversationTurnAttemptState,
    ConversationTurnOutcome,
    ConversationTurnOutcomeStatus,
    ConversationTurnProjection,
    ConversationTurnState,
    Failure,
    FailureCode,
    TaskGoal,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.tables import (
    ConversationMessageRow,
    ConversationRow,
    ConversationTurnAttemptRow,
    ConversationTurnOutcomeRow,
    ConversationTurnRow,
    KnowledgeIngestionJobRow,
    TaskRow,
)

_TERMINAL_TURN_STATES = {
    ConversationTurnState.COMPLETED,
    ConversationTurnState.FAILED,
    ConversationTurnState.CANCELLED,
    ConversationTurnState.SUPERSEDED,
}
_FAILED_OUTCOMES = {
    ConversationTurnOutcomeStatus.UNAVAILABLE,
    ConversationTurnOutcomeStatus.FAILED,
}
MessageRole = Literal["SYSTEM", "USER", "ASSISTANT"]


def _restore_turn(row: ConversationTurnRow) -> ConversationTurn:
    return ConversationTurn(
        turn_id=row.id,
        workspace_id=row.workspace_id,
        conversation_id=row.conversation_id,
        user_message_id=row.user_message_id,
        state=ConversationTurnState(row.state),
        task_id=row.task_id,
        active_attempt_id=row.active_attempt_id,
        decision_version=row.decision_version,
        fencing_token=row.fencing_token,
        context_eligible=row.context_eligible,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


def _restore_attempt(row: ConversationTurnAttemptRow) -> ConversationTurnAttempt:
    return ConversationTurnAttempt(
        attempt_id=row.id,
        workspace_id=row.workspace_id,
        turn_id=row.turn_id,
        state=ConversationTurnAttemptState(row.state),
        fencing_token=row.fencing_token,
        trace_id=row.trace_id,
        failure=(
            None if row.failure_payload is None else Failure.model_validate(row.failure_payload)
        ),
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _message(row: ConversationMessageRow) -> ConversationMessage:
    return ConversationMessage(
        message_id=row.id,
        workspace_id=row.workspace_id,
        conversation_id=row.conversation_id,
        role=cast(MessageRole, row.role),
        content=row.content,
        created_at=row.created_at,
    )


def _outcome_payload(value: ConversationTurnOutcome) -> tuple[dict[str, object], str]:
    payload = cast(dict[str, object], value.model_dump(mode="json"))
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return payload, hashlib.sha256(encoded).hexdigest()


def _restore_outcome(row: ConversationTurnOutcomeRow) -> ConversationTurnOutcome:
    value = ConversationTurnOutcome(
        outcome_id=row.id,
        workspace_id=row.workspace_id,
        turn_id=row.turn_id,
        attempt_id=row.attempt_id,
        status=ConversationTurnOutcomeStatus(row.status),
        assistant_message_id=row.assistant_message_id,
        failure=(
            None if row.failure_payload is None else Failure.model_validate(row.failure_payload)
        ),
        context_eligible=row.context_eligible,
        created_at=row.created_at,
    )
    _, digest = _outcome_payload(value)
    if digest != row.content_hash:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "turn outcome hash mismatch")
    return value


async def lock_conversation_turn_creation(
    session: AsyncSession,
    workspace_id: UUID,
    conversation_id: UUID,
) -> None:
    conversation = await session.scalar(
        select(ConversationRow.id)
        .where(
            ConversationRow.workspace_id == workspace_id,
            ConversationRow.id == conversation_id,
            ConversationRow.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if conversation is None:
        raise DomainError(FailureCode.POLICY_DENIED, "conversation unavailable")


async def create_conversation_turn(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    conversation_id: UUID,
    turn_id: UUID,
    user_message_id: UUID,
    task_id: UUID | None = None,
    decision_version: int = 1,
) -> ConversationTurn:
    if decision_version < 1:
        raise DomainError(FailureCode.VALIDATION_FAILED, "decision version must be positive")
    conversation = await session.scalar(
        select(ConversationRow.id).where(
            ConversationRow.workspace_id == workspace_id,
            ConversationRow.id == conversation_id,
            ConversationRow.deleted_at.is_(None),
        )
    )
    if conversation is None:
        raise DomainError(FailureCode.POLICY_DENIED, "conversation unavailable")
    user_message = await session.scalar(
        select(ConversationMessageRow).where(
            ConversationMessageRow.workspace_id == workspace_id,
            ConversationMessageRow.id == user_message_id,
        )
    )
    if (
        user_message is None
        or user_message.conversation_id != conversation_id
        or user_message.role != "USER"
    ):
        raise DomainError(FailureCode.VALIDATION_FAILED, "turn user message is invalid")
    if task_id is not None:
        task = await session.scalar(
            select(TaskRow.id).where(
                TaskRow.workspace_id == workspace_id,
                TaskRow.id == task_id,
            )
        )
        if task is None:
            raise DomainError(FailureCode.POLICY_DENIED, "turn task unavailable")
        bound_turn = await session.scalar(
            select(ConversationTurnRow.id).where(
                ConversationTurnRow.workspace_id == workspace_id,
                ConversationTurnRow.task_id == task_id,
            )
        )
        if bound_turn is not None and bound_turn != turn_id:
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "task is already bound to another turn",
            )
    await session.execute(
        insert(ConversationTurnRow)
        .values(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            task_id=task_id,
            decision_version=decision_version,
        )
        .on_conflict_do_nothing()
    )
    row = await session.scalar(
        select(ConversationTurnRow).where(
            ConversationTurnRow.workspace_id == workspace_id,
            or_(
                ConversationTurnRow.id == turn_id,
                ConversationTurnRow.user_message_id == user_message_id,
            ),
        )
    )
    if (
        row is None
        or row.id != turn_id
        or row.conversation_id != conversation_id
        or row.user_message_id != user_message_id
        or row.task_id != task_id
        or row.decision_version != decision_version
    ):
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "turn replay mismatch")
    return _restore_turn(row)


async def get_conversation_turn(
    session: AsyncSession,
    workspace_id: UUID,
    turn_id: UUID,
) -> ConversationTurn:
    row = await session.scalar(
        select(ConversationTurnRow).where(
            ConversationTurnRow.workspace_id == workspace_id,
            ConversationTurnRow.id == turn_id,
        )
    )
    if row is None:
        raise DomainError(FailureCode.POLICY_DENIED, "turn unavailable")
    return _restore_turn(row)


async def list_conversation_turns(
    session: AsyncSession,
    workspace_id: UUID,
    conversation_id: UUID,
) -> tuple[ConversationTurn, ...]:
    conversation = await session.scalar(
        select(ConversationRow.id).where(
            ConversationRow.workspace_id == workspace_id,
            ConversationRow.id == conversation_id,
            ConversationRow.deleted_at.is_(None),
        )
    )
    if conversation is None:
        raise DomainError(FailureCode.POLICY_DENIED, "conversation unavailable")
    rows = (
        await session.scalars(
            select(ConversationTurnRow)
            .where(
                ConversationTurnRow.workspace_id == workspace_id,
                ConversationTurnRow.conversation_id == conversation_id,
            )
            .order_by(ConversationTurnRow.created_at, ConversationTurnRow.id)
        )
    ).all()
    return tuple(_restore_turn(row) for row in rows)


async def bind_conversation_turn_task(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    turn_id: UUID,
    task_id: UUID,
) -> ConversationTurn:
    turn = await session.scalar(
        select(ConversationTurnRow)
        .where(
            ConversationTurnRow.workspace_id == workspace_id,
            ConversationTurnRow.id == turn_id,
        )
        .with_for_update()
    )
    if turn is None:
        raise DomainError(FailureCode.POLICY_DENIED, "turn unavailable")
    if turn.task_id is not None:
        if turn.task_id != task_id:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "turn task binding changed")
        return _restore_turn(turn)
    if ConversationTurnState(turn.state) in _TERMINAL_TURN_STATES:
        raise DomainError(FailureCode.STALE_ATTEMPT, "terminal turn cannot bind a task")
    task = await session.scalar(
        select(TaskRow.id).where(
            TaskRow.workspace_id == workspace_id,
            TaskRow.id == task_id,
        )
    )
    if task is None:
        raise DomainError(FailureCode.POLICY_DENIED, "turn task unavailable")
    bound_turn = await session.scalar(
        select(ConversationTurnRow.id).where(
            ConversationTurnRow.workspace_id == workspace_id,
            ConversationTurnRow.task_id == task_id,
            ConversationTurnRow.id != turn_id,
        )
    )
    if bound_turn is not None:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "task is already bound to another turn",
        )
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    turn.task_id = task_id
    turn.version += 1
    turn.updated_at = now
    await session.flush()
    return _restore_turn(turn)


async def acquire_conversation_turn_attempt(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    turn_id: UUID,
    attempt_id: UUID,
    trace_id: str,
    supersede_active: bool = False,
) -> ConversationTurnAttempt:
    normalized_trace_id = trace_id.strip()
    if not normalized_trace_id or len(normalized_trace_id) > 200:
        raise DomainError(FailureCode.VALIDATION_FAILED, "invalid turn attempt trace id")
    turn = await session.scalar(
        select(ConversationTurnRow)
        .where(
            ConversationTurnRow.workspace_id == workspace_id,
            ConversationTurnRow.id == turn_id,
        )
        .with_for_update()
    )
    if turn is None:
        raise DomainError(FailureCode.POLICY_DENIED, "turn unavailable")
    existing = await session.scalar(
        select(ConversationTurnAttemptRow).where(
            ConversationTurnAttemptRow.workspace_id == workspace_id,
            ConversationTurnAttemptRow.id == attempt_id,
        )
    )
    if existing is not None:
        if existing.turn_id != turn_id or existing.trace_id != normalized_trace_id:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "turn attempt replay mismatch")
        if (
            turn.active_attempt_id != attempt_id
            or turn.fencing_token != existing.fencing_token
            or ConversationTurnAttemptState(existing.state)
            is not ConversationTurnAttemptState.RUNNING
        ):
            raise DomainError(FailureCode.STALE_ATTEMPT, "turn attempt is no longer active")
        return _restore_attempt(existing)
    if ConversationTurnState(turn.state) in _TERMINAL_TURN_STATES:
        raise DomainError(FailureCode.STALE_ATTEMPT, "turn is terminal")
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    if turn.active_attempt_id is not None:
        active = await session.scalar(
            select(ConversationTurnAttemptRow)
            .where(
                ConversationTurnAttemptRow.workspace_id == workspace_id,
                ConversationTurnAttemptRow.id == turn.active_attempt_id,
            )
            .with_for_update()
        )
        if active is None or active.turn_id != turn.id:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "active attempt is invalid")
        if ConversationTurnAttemptState(active.state) is ConversationTurnAttemptState.RUNNING:
            if not supersede_active:
                raise DomainError(
                    FailureCode.VALIDATION_FAILED, "turn already has an active attempt"
                )
            active.state = ConversationTurnAttemptState.SUPERSEDED.value
            active.completed_at = now
    token = turn.fencing_token + 1
    attempt = ConversationTurnAttemptRow(
        id=attempt_id,
        workspace_id=workspace_id,
        turn_id=turn_id,
        state=ConversationTurnAttemptState.RUNNING.value,
        fencing_token=token,
        trace_id=normalized_trace_id,
        started_at=now,
    )
    session.add(attempt)
    await session.flush()
    turn.active_attempt_id = attempt_id
    turn.fencing_token = token
    turn.state = ConversationTurnState.RUNNING.value
    turn.context_eligible = False
    turn.completed_at = None
    turn.version += 1
    turn.updated_at = now
    await session.flush()
    return _restore_attempt(attempt)


async def record_conversation_turn_attempt_failure(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    turn_id: UUID,
    attempt_id: UUID,
    fencing_token: int,
    failure: Failure,
) -> ConversationTurnAttempt:
    turn = await session.scalar(
        select(ConversationTurnRow)
        .where(
            ConversationTurnRow.workspace_id == workspace_id,
            ConversationTurnRow.id == turn_id,
        )
        .with_for_update()
    )
    attempt = await session.scalar(
        select(ConversationTurnAttemptRow).where(
            ConversationTurnAttemptRow.workspace_id == workspace_id,
            ConversationTurnAttemptRow.id == attempt_id,
        )
    )
    if turn is None or attempt is None or attempt.turn_id != turn_id:
        raise DomainError(FailureCode.STALE_ATTEMPT, "turn attempt unavailable")
    failure_payload = failure.model_dump(mode="json")
    if ConversationTurnAttemptState(attempt.state) is ConversationTurnAttemptState.FAILED:
        if attempt.failure_payload != failure_payload:
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "turn attempt failure replay mismatch",
            )
        return _restore_attempt(attempt)
    if (
        turn.active_attempt_id != attempt_id
        or turn.fencing_token != fencing_token
        or attempt.fencing_token != fencing_token
        or ConversationTurnState(turn.state) is not ConversationTurnState.RUNNING
        or ConversationTurnAttemptState(attempt.state) is not ConversationTurnAttemptState.RUNNING
    ):
        raise DomainError(FailureCode.STALE_ATTEMPT, "turn failure commit fencing rejected")
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    attempt.state = ConversationTurnAttemptState.FAILED.value
    attempt.failure_payload = failure_payload
    attempt.completed_at = now
    turn.fencing_token += 1
    turn.context_eligible = False
    turn.version += 1
    turn.updated_at = now
    await session.flush()
    return _restore_attempt(attempt)


async def commit_conversation_turn_outcome(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    turn_id: UUID,
    attempt_id: UUID,
    fencing_token: int,
    outcome_id: UUID,
    status: ConversationTurnOutcomeStatus,
    assistant_message_id: UUID,
    failure: Failure | None = None,
    context_eligible: bool,
) -> ConversationTurnOutcome:
    turn = await session.scalar(
        select(ConversationTurnRow)
        .where(
            ConversationTurnRow.workspace_id == workspace_id,
            ConversationTurnRow.id == turn_id,
        )
        .with_for_update()
    )
    if turn is None:
        raise DomainError(FailureCode.STALE_ATTEMPT, "turn unavailable")
    existing = await session.scalar(
        select(ConversationTurnOutcomeRow).where(
            ConversationTurnOutcomeRow.workspace_id == workspace_id,
            or_(
                ConversationTurnOutcomeRow.id == outcome_id,
                ConversationTurnOutcomeRow.turn_id == turn_id,
            ),
        )
    )
    if existing is not None:
        restored = _restore_outcome(existing)
        expected_failure = None if failure is None else failure.model_dump(mode="json")
        if (
            restored.outcome_id != outcome_id
            or restored.attempt_id != attempt_id
            or restored.status is not status
            or restored.assistant_message_id != assistant_message_id
            or existing.failure_payload != expected_failure
            or restored.context_eligible != context_eligible
        ):
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "turn outcome replay mismatch")
        return restored
    attempt = await session.scalar(
        select(ConversationTurnAttemptRow).where(
            ConversationTurnAttemptRow.workspace_id == workspace_id,
            ConversationTurnAttemptRow.id == attempt_id,
        )
    )
    if (
        attempt is None
        or attempt.turn_id != turn_id
        or turn.active_attempt_id != attempt_id
        or turn.fencing_token != fencing_token
        or attempt.fencing_token != fencing_token
        or ConversationTurnState(turn.state) is not ConversationTurnState.RUNNING
        or ConversationTurnAttemptState(attempt.state) is not ConversationTurnAttemptState.RUNNING
    ):
        raise DomainError(FailureCode.STALE_ATTEMPT, "turn outcome commit fencing rejected")
    if status in _FAILED_OUTCOMES and context_eligible:
        raise DomainError(
            FailureCode.VALIDATION_FAILED,
            "failed turn outcomes cannot enter future context",
        )
    assistant_message = await session.scalar(
        select(ConversationMessageRow).where(
            ConversationMessageRow.workspace_id == workspace_id,
            ConversationMessageRow.id == assistant_message_id,
        )
    )
    if (
        assistant_message is None
        or assistant_message.conversation_id != turn.conversation_id
        or assistant_message.role != "ASSISTANT"
    ):
        raise DomainError(FailureCode.VALIDATION_FAILED, "turn assistant message is invalid")
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    outcome = ConversationTurnOutcome(
        outcome_id=outcome_id,
        workspace_id=workspace_id,
        turn_id=turn_id,
        attempt_id=attempt_id,
        status=status,
        assistant_message_id=assistant_message_id,
        failure=failure,
        context_eligible=context_eligible,
        created_at=now,
    )
    _, digest = _outcome_payload(outcome)
    session.add(
        ConversationTurnOutcomeRow(
            id=outcome_id,
            workspace_id=workspace_id,
            turn_id=turn_id,
            attempt_id=attempt_id,
            assistant_message_id=assistant_message_id,
            status=status.value,
            failure_payload=None if failure is None else failure.model_dump(mode="json"),
            context_eligible=context_eligible,
            content_hash=digest,
            created_at=now,
        )
    )
    attempt.state = (
        ConversationTurnAttemptState.FAILED.value
        if status in _FAILED_OUTCOMES
        else ConversationTurnAttemptState.SUCCEEDED.value
    )
    attempt.failure_payload = None if failure is None else failure.model_dump(mode="json")
    attempt.completed_at = now
    turn.state = (
        ConversationTurnState.FAILED.value
        if status is ConversationTurnOutcomeStatus.FAILED
        else ConversationTurnState.COMPLETED.value
    )
    turn.context_eligible = context_eligible
    turn.version += 1
    turn.updated_at = now
    turn.completed_at = now
    await session.flush()
    return outcome


async def cancel_conversation_turn(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    turn_id: UUID,
    superseded: bool = False,
) -> ConversationTurn:
    turn = await session.scalar(
        select(ConversationTurnRow)
        .where(
            ConversationTurnRow.workspace_id == workspace_id,
            ConversationTurnRow.id == turn_id,
        )
        .with_for_update()
    )
    if turn is None:
        raise DomainError(FailureCode.POLICY_DENIED, "turn unavailable")
    requested_state = (
        ConversationTurnState.SUPERSEDED if superseded else ConversationTurnState.CANCELLED
    )
    state = ConversationTurnState(turn.state)
    if state is requested_state:
        return _restore_turn(turn)
    if state in _TERMINAL_TURN_STATES:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "turn is already terminal")
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    if turn.active_attempt_id is not None:
        active = await session.scalar(
            select(ConversationTurnAttemptRow)
            .where(
                ConversationTurnAttemptRow.workspace_id == workspace_id,
                ConversationTurnAttemptRow.id == turn.active_attempt_id,
            )
            .with_for_update()
        )
        if (
            active is not None
            and ConversationTurnAttemptState(active.state) is ConversationTurnAttemptState.RUNNING
        ):
            active.state = (
                ConversationTurnAttemptState.SUPERSEDED.value
                if superseded
                else ConversationTurnAttemptState.CANCELLED.value
            )
            active.completed_at = now
    turn.fencing_token += 1
    turn.state = requested_state.value
    turn.context_eligible = False
    turn.version += 1
    turn.updated_at = now
    turn.completed_at = now
    await session.flush()
    return _restore_turn(turn)


async def load_conversation_context_projection(
    session: AsyncSession,
    workspace_id: UUID,
    conversation_id: UUID,
) -> tuple[ConversationTurnProjection, ...]:
    conversation = await session.scalar(
        select(ConversationRow.id).where(
            ConversationRow.workspace_id == workspace_id,
            ConversationRow.id == conversation_id,
            ConversationRow.deleted_at.is_(None),
        )
    )
    if conversation is None:
        raise DomainError(FailureCode.POLICY_DENIED, "conversation unavailable")
    user_message = aliased(ConversationMessageRow)
    assistant_message = aliased(ConversationMessageRow)
    rows = (
        await session.execute(
            select(
                ConversationTurnRow,
                ConversationTurnOutcomeRow,
                user_message,
                assistant_message,
            )
            .join(
                ConversationTurnOutcomeRow,
                (ConversationTurnOutcomeRow.workspace_id == ConversationTurnRow.workspace_id)
                & (ConversationTurnOutcomeRow.turn_id == ConversationTurnRow.id),
            )
            .join(
                user_message,
                (user_message.workspace_id == ConversationTurnRow.workspace_id)
                & (user_message.id == ConversationTurnRow.user_message_id),
            )
            .join(
                assistant_message,
                (assistant_message.workspace_id == ConversationTurnOutcomeRow.workspace_id)
                & (assistant_message.id == ConversationTurnOutcomeRow.assistant_message_id),
            )
            .where(
                ConversationTurnRow.workspace_id == workspace_id,
                ConversationTurnRow.conversation_id == conversation_id,
                ConversationTurnRow.state == ConversationTurnState.COMPLETED.value,
                ConversationTurnRow.context_eligible.is_(True),
                ConversationTurnOutcomeRow.context_eligible.is_(True),
            )
            .order_by(ConversationTurnRow.created_at, ConversationTurnRow.id)
        )
    ).all()
    return tuple(
        ConversationTurnProjection(
            turn=_restore_turn(turn),
            user_message=_message(user),
            outcome=_restore_outcome(outcome),
            assistant_message=_message(assistant),
        )
        for turn, outcome, user, assistant in rows
    )


async def load_conversation_timeline_messages(
    session: AsyncSession,
    workspace_id: UUID,
    conversation_id: UUID,
) -> tuple[ConversationTimelineMessage, ...]:
    """Project chat display order by Turn ownership instead of completion time."""

    conversation = await session.scalar(
        select(ConversationRow.id).where(
            ConversationRow.workspace_id == workspace_id,
            ConversationRow.id == conversation_id,
            ConversationRow.deleted_at.is_(None),
        )
    )
    if conversation is None:
        raise DomainError(FailureCode.POLICY_DENIED, "conversation unavailable")
    messages = (
        await session.scalars(
            select(ConversationMessageRow)
            .where(
                ConversationMessageRow.workspace_id == workspace_id,
                ConversationMessageRow.conversation_id == conversation_id,
            )
            .order_by(ConversationMessageRow.created_at, ConversationMessageRow.id)
        )
    ).all()
    turns = (
        await session.scalars(
            select(ConversationTurnRow).where(
                ConversationTurnRow.workspace_id == workspace_id,
                ConversationTurnRow.conversation_id == conversation_id,
            )
        )
    ).all()
    outcomes = (
        await session.scalars(
            select(ConversationTurnOutcomeRow)
            .join(
                ConversationTurnRow,
                (ConversationTurnRow.workspace_id == ConversationTurnOutcomeRow.workspace_id)
                & (ConversationTurnRow.id == ConversationTurnOutcomeRow.turn_id),
            )
            .where(
                ConversationTurnRow.workspace_id == workspace_id,
                ConversationTurnRow.conversation_id == conversation_id,
            )
        )
    ).all()
    task_ids = tuple(turn.task_id for turn in turns if turn.task_id is not None)
    task_rows = (
        (
            await session.scalars(
                select(TaskRow).where(
                    TaskRow.workspace_id == workspace_id,
                    TaskRow.id.in_(task_ids),
                )
            )
        ).all()
        if task_ids
        else []
    )
    attachment_ref_by_task: dict[UUID, tuple[UUID, UUID]] = {}
    for task in task_rows:
        if task.goal_payload is None:
            continue
        try:
            context = json.loads(TaskGoal.model_validate(task.goal_payload).user_context)
            raw_ingestion_id = context.get("attachment_ingestion_id")
            raw_document_version_id = context.get("document_version_id")
            if not isinstance(raw_ingestion_id, str) or not isinstance(
                raw_document_version_id, str
            ):
                continue
            attachment_ref_by_task[task.id] = (
                UUID(raw_ingestion_id),
                UUID(raw_document_version_id),
            )
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    attachment_ids = tuple(
        ingestion_id for ingestion_id, _ in attachment_ref_by_task.values()
    )
    attachment_rows = (
        (
            await session.scalars(
                select(KnowledgeIngestionJobRow).where(
                    KnowledgeIngestionJobRow.workspace_id == workspace_id,
                    KnowledgeIngestionJobRow.id.in_(attachment_ids),
                )
            )
        ).all()
        if attachment_ids
        else []
    )
    attachment_by_id = {item.id: item for item in attachment_rows}
    turn_by_id = {turn.id: turn for turn in turns}
    turn_by_user_message = {turn.user_message_id: turn for turn in turns}
    outcome_by_turn = {outcome.turn_id: outcome for outcome in outcomes}
    turn_outcome_by_assistant = {
        outcome.assistant_message_id: (turn_by_id[outcome.turn_id], outcome)
        for outcome in outcomes
        if outcome.turn_id in turn_by_id
    }

    projected: list[
        tuple[tuple[datetime, str, int, datetime, str], ConversationTimelineMessage]
    ] = []
    for row in messages:
        turn = turn_by_user_message.get(row.id)
        outcome = None if turn is None else outcome_by_turn.get(turn.id)
        phase = 0
        if turn is None:
            assistant_binding = turn_outcome_by_assistant.get(row.id)
            if assistant_binding is not None:
                turn, outcome = assistant_binding
                phase = 1
        message = _message(row)
        if turn is None:
            order = (row.created_at, str(row.id), 0, row.created_at, str(row.id))
            value = ConversationTimelineMessage(**message.model_dump())
        else:
            order = (
                turn.created_at,
                str(turn.id),
                phase,
                row.created_at,
                str(row.id),
            )
            attachment = None
            if phase == 0 and turn.task_id is not None:
                attachment_ref = attachment_ref_by_task.get(turn.task_id)
                attachment_id = None if attachment_ref is None else attachment_ref[0]
                attachment_row = (
                    None if attachment_id is None else attachment_by_id.get(attachment_id)
                )
                if (
                    attachment_row is not None
                    and attachment_row.document_version_id is not None
                    and attachment_ref is not None
                    and attachment_row.document_version_id == attachment_ref[1]
                ):
                    attachment = ConversationAttachment(
                        ingestion_id=attachment_row.id,
                        document_version_id=attachment_row.document_version_id,
                        name=attachment_row.name,
                        media_type=attachment_row.media_type,
                    )
            value = ConversationTimelineMessage(
                **message.model_dump(),
                turn_id=turn.id,
                turn_state=ConversationTurnState(turn.state),
                turn_outcome_status=(
                    None if outcome is None else ConversationTurnOutcomeStatus(outcome.status)
                ),
                attachment=attachment,
            )
        projected.append((order, value))
    projected.sort(key=lambda item: item[0])
    return tuple(value for _, value in projected)
