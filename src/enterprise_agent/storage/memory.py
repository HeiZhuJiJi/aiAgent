import hashlib
import json
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import (
    Conversation,
    ConversationMessage,
    ConversationSummary,
    FailureCode,
    ProjectMemory,
    ProjectMemoryType,
)
from enterprise_agent.domain.states import DomainError
from enterprise_agent.storage.tables import (
    ConversationMessageRow,
    ConversationRow,
    ConversationSummaryRow,
    ProjectMemoryRow,
    Workspace,
)

MessageRole = Literal["SYSTEM", "USER", "ASSISTANT"]


def _canonical(value: ConversationSummary | ProjectMemory) -> tuple[dict[str, object], str]:
    payload = cast(dict[str, object], value.model_dump(mode="json"))
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return payload, hashlib.sha256(encoded).hexdigest()


def _restore_summary(row: ConversationSummaryRow) -> ConversationSummary:
    value = ConversationSummary.model_validate(row.payload)
    _, digest = _canonical(value)
    if digest != row.content_hash:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "conversation summary hash mismatch")
    return value


def _restore_memory(row: ProjectMemoryRow) -> ProjectMemory:
    value = ProjectMemory.model_validate(row.payload)
    _, digest = _canonical(value)
    if digest != row.content_hash:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "project memory hash mismatch")
    return value


async def create_conversation(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    conversation_id: UUID,
    title: str,
) -> Conversation:
    normalized = title.strip()
    if not normalized:
        raise DomainError(FailureCode.VALIDATION_FAILED, "conversation title is required")
    workspace_exists = await session.scalar(
        select(Workspace.id).where(Workspace.id == workspace_id)
    )
    if workspace_exists is None:
        raise DomainError(FailureCode.POLICY_DENIED, "workspace unavailable")
    await session.execute(
        insert(ConversationRow)
        .values(id=conversation_id, workspace_id=workspace_id, title=normalized)
        .on_conflict_do_nothing()
    )
    row = await session.scalar(
        select(ConversationRow).where(
            ConversationRow.workspace_id == workspace_id,
            ConversationRow.id == conversation_id,
        )
    )
    if row is None or row.title != normalized:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "conversation replay mismatch")
    return Conversation(
        conversation_id=row.id,
        workspace_id=row.workspace_id,
        title=row.title,
        version=row.version,
        created_at=row.created_at,
    )


async def list_conversations(
    session: AsyncSession, workspace_id: UUID
) -> tuple[Conversation, ...]:
    rows = (
        await session.scalars(
            select(ConversationRow)
            .where(ConversationRow.workspace_id == workspace_id)
            .order_by(ConversationRow.created_at, ConversationRow.id)
        )
    ).all()
    return tuple(
        Conversation(
            conversation_id=row.id,
            workspace_id=row.workspace_id,
            title=row.title,
            version=row.version,
            created_at=row.created_at,
        )
        for row in rows
    )


def _message_hash(
    workspace_id: UUID, conversation_id: UUID, message_id: UUID, role: str, content: str
) -> str:
    value = {
        "workspace_id": str(workspace_id),
        "conversation_id": str(conversation_id),
        "message_id": str(message_id),
        "role": role,
        "content": content,
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def append_conversation_message(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    role: str,
    content: str,
) -> ConversationMessage:
    if role not in {"SYSTEM", "USER", "ASSISTANT"}:
        raise DomainError(FailureCode.VALIDATION_FAILED, "invalid conversation message role")
    normalized = content.strip()
    if not normalized:
        raise DomainError(FailureCode.VALIDATION_FAILED, "message content is required")
    conversation = await session.scalar(
        select(ConversationRow.id).where(
            ConversationRow.workspace_id == workspace_id,
            ConversationRow.id == conversation_id,
        )
    )
    if conversation is None:
        raise DomainError(FailureCode.POLICY_DENIED, "conversation unavailable")
    digest = _message_hash(workspace_id, conversation_id, message_id, role, normalized)
    await session.execute(
        insert(ConversationMessageRow)
        .values(
            id=message_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            role=role,
            content=normalized,
            content_hash=digest,
        )
        .on_conflict_do_nothing()
    )
    row = await session.scalar(
        select(ConversationMessageRow).where(
            ConversationMessageRow.workspace_id == workspace_id,
            ConversationMessageRow.id == message_id,
        )
    )
    if (
        row is None
        or row.conversation_id != conversation_id
        or row.role != role
        or row.content != normalized
        or row.content_hash != digest
    ):
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "message replay mismatch")
    return ConversationMessage(
        message_id=row.id,
        workspace_id=row.workspace_id,
        conversation_id=row.conversation_id,
        role=cast(MessageRole, row.role),
        content=row.content,
        created_at=row.created_at,
    )


async def load_conversation_messages(
    session: AsyncSession, workspace_id: UUID, conversation_id: UUID
) -> tuple[ConversationMessage, ...]:
    conversation = await session.scalar(
        select(ConversationRow.id).where(
            ConversationRow.workspace_id == workspace_id,
            ConversationRow.id == conversation_id,
        )
    )
    if conversation is None:
        raise DomainError(FailureCode.POLICY_DENIED, "conversation unavailable")
    rows = (
        await session.scalars(
            select(ConversationMessageRow)
            .where(
                ConversationMessageRow.workspace_id == workspace_id,
                ConversationMessageRow.conversation_id == conversation_id,
            )
            .order_by(ConversationMessageRow.created_at, ConversationMessageRow.id)
        )
    ).all()
    return tuple(
        ConversationMessage(
            message_id=row.id,
            workspace_id=row.workspace_id,
            conversation_id=row.conversation_id,
            role=cast(MessageRole, row.role),
            content=row.content,
            created_at=row.created_at,
        )
        for row in rows
    )


async def append_conversation_summary(
    session: AsyncSession, value: ConversationSummary
) -> ConversationSummary:
    conversation = await session.scalar(
        select(ConversationRow)
        .where(
            ConversationRow.workspace_id == value.workspace_id,
            ConversationRow.id == value.conversation_id,
        )
        .with_for_update()
    )
    if conversation is None:
        raise DomainError(FailureCode.POLICY_DENIED, "conversation unavailable")
    payload, digest = _canonical(value)
    existing = await session.scalar(
        select(ConversationSummaryRow).where(
            ConversationSummaryRow.workspace_id == value.workspace_id,
            ConversationSummaryRow.id == value.summary_id,
        )
    )
    if existing is not None:
        if existing.content_hash != digest:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "summary replay mismatch")
        return _restore_summary(existing)
    latest_version = await session.scalar(
        select(func.max(ConversationSummaryRow.version)).where(
            ConversationSummaryRow.workspace_id == value.workspace_id,
            ConversationSummaryRow.conversation_id == value.conversation_id,
        )
    )
    if value.version != (latest_version or 0) + 1:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "summary version is not next")
    session.add(
        ConversationSummaryRow(
            id=value.summary_id,
            workspace_id=value.workspace_id,
            conversation_id=value.conversation_id,
            schema_version=value.schema_version,
            version=value.version,
            content_hash=digest,
            payload=payload,
            created_at=value.created_at,
        )
    )
    return value


async def load_conversation_summaries(
    session: AsyncSession, workspace_id: UUID, conversation_id: UUID
) -> tuple[ConversationSummary, ...]:
    rows = (
        await session.scalars(
            select(ConversationSummaryRow)
            .where(
                ConversationSummaryRow.workspace_id == workspace_id,
                ConversationSummaryRow.conversation_id == conversation_id,
            )
            .order_by(ConversationSummaryRow.version)
        )
    ).all()
    return tuple(_restore_summary(row) for row in rows)


async def persist_project_memory(session: AsyncSession, value: ProjectMemory) -> ProjectMemory:
    workspace_exists = await session.scalar(
        select(Workspace.id).where(Workspace.id == value.workspace_id)
    )
    if workspace_exists is None:
        raise DomainError(FailureCode.POLICY_DENIED, "workspace unavailable")
    payload, digest = _canonical(value)
    existing = await session.scalar(
        select(ProjectMemoryRow).where(
            ProjectMemoryRow.workspace_id == value.workspace_id,
            ProjectMemoryRow.id == value.memory_id,
        )
    )
    if existing is not None:
        if existing.content_hash != digest:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "project memory replay mismatch")
        return _restore_memory(existing)
    if value.supersedes is not None:
        previous = await session.scalar(
            select(ProjectMemoryRow)
            .where(
                ProjectMemoryRow.workspace_id == value.workspace_id,
                ProjectMemoryRow.id == value.supersedes,
            )
            .with_for_update()
        )
        if previous is None:
            raise DomainError(FailureCode.VALIDATION_FAILED, "superseded memory unavailable")
        if previous.memory_type != value.type:
            raise DomainError(FailureCode.VALIDATION_FAILED, "memory type cannot change")
        replacement_exists = await session.scalar(
            select(ProjectMemoryRow.id).where(
                ProjectMemoryRow.workspace_id == value.workspace_id,
                ProjectMemoryRow.supersedes == value.supersedes,
            )
        )
        if replacement_exists is not None:
            raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "memory already superseded")
    session.add(
        ProjectMemoryRow(
            id=value.memory_id,
            workspace_id=value.workspace_id,
            schema_version=value.schema_version,
            memory_type=value.type,
            supersedes=value.supersedes,
            content_hash=digest,
            payload=payload,
            created_at=value.created_at,
        )
    )
    return value


async def load_active_project_memory(
    session: AsyncSession,
    workspace_id: UUID,
    memory_type: ProjectMemoryType | None = None,
) -> tuple[ProjectMemory, ...]:
    replacement = ProjectMemoryRow.__table__.alias("replacement")
    query = select(ProjectMemoryRow).where(
        ProjectMemoryRow.workspace_id == workspace_id,
        ~exists(
            select(replacement.c.id).where(
                replacement.c.workspace_id == ProjectMemoryRow.workspace_id,
                replacement.c.supersedes == ProjectMemoryRow.id,
            )
        ),
    )
    if memory_type is not None:
        query = query.where(ProjectMemoryRow.memory_type == memory_type)
    rows = (await session.scalars(query.order_by(ProjectMemoryRow.created_at, ProjectMemoryRow.id))).all()
    return tuple(_restore_memory(row) for row in rows)
