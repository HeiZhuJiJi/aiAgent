"""Conservatively backfill complete legacy conversation turns."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.engine import Connection

revision = "0033_legacy_turn_backfill"
down_revision = "0032_conversation_turn_lifecycle"
branch_labels = None
depends_on = None

_ELIGIBLE_TURNS_PER_CONVERSATION = 4
_MIGRATION_TRACE_ID = "migration-0033-legacy-backfill"
_FAILURE_DETAIL = "legacy response excluded by conservative Turn backfill"

_OBVIOUS_FAILURE_OR_REFUSAL = re.compile(
    r"(?:"
    r"^\s*(?:抱歉|对不起)[，,\s]*(?:我|我们|本系统|当前)?(?:无法|不能|未能)"
    r"|(?:没有|未)(?:检索|搜索|查询|找到|获得|获取)到.{0,32}"
    r"(?:可靠|相关|可用)?(?:的)?(?:信息|结果|数据|证据)"
    r"|(?:检索|搜索|查询).{0,32}(?:没有|未)(?:返回|找到|获得|获取).{0,24}"
    r"(?:结果|信息|数据|证据)"
    r"|^\s*(?:当前|本次|该)?(?:服务|模型|检索|搜索).{0,24}"
    r"(?:暂时)?(?:不可用|超时|执行失败)"
    r"|本轮已安全结束|请稍后重试|未通过(?:格式|证据)(?:或|和)?(?:格式|证据)?校验"
    r"|^\s*(?:sorry|apologies)[,\s].{0,40}(?:cannot|can't|unable)"
    r"|^\s*(?:i|we|the system).{0,30}(?:cannot|can't|could not|unable to)"
    r"|^\s*(?:(?:the|current)\s+)?(?:service|model|search).{0,30}"
    r"(?:unavailable|timed out|failed)"
    r"|^\s*(?:domainerror|error|failed)\s*[.!。！]?\s*$"
    r")",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class _LegacyPair:
    workspace_id: UUID
    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID
    user_created_at: datetime
    assistant_created_at: datetime
    assistant_content: str


@dataclass(frozen=True)
class _BackfillRecord:
    pair: _LegacyPair
    turn_id: UUID
    attempt_id: UUID
    outcome_id: UUID
    attempt_state: str
    outcome_status: str
    failure_payload: dict[str, object] | None
    context_eligible: bool
    content_hash: str


def _is_obvious_failure_or_refusal(content: str) -> bool:
    return _OBVIOUS_FAILURE_OR_REFUSAL.search(content.strip()) is not None


def _turn_ids(pair: _LegacyPair) -> tuple[UUID, UUID, UUID]:
    turn_id = uuid5(
        NAMESPACE_URL,
        (
            "conversation-turn:"
            f"{pair.workspace_id}:{pair.conversation_id}:{pair.user_message_id}"
        ),
    )
    return (
        turn_id,
        uuid5(NAMESPACE_URL, f"conversation-turn-attempt:{turn_id}:1"),
        uuid5(NAMESPACE_URL, f"conversation-turn-outcome:{turn_id}"),
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("legacy message timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _json_datetime(value: datetime) -> str:
    normalized = _utc(value)
    base = normalized.strftime("%Y-%m-%dT%H:%M:%S")
    fraction = f".{normalized.microsecond:06d}" if normalized.microsecond else ""
    return f"{base}{fraction}Z"


def _failure_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "code": "CAPABILITY_UNAVAILABLE",
        "safe_detail": _FAILURE_DETAIL,
        "trace_id": _MIGRATION_TRACE_ID,
    }


def _outcome_hash(
    *,
    outcome_id: UUID,
    workspace_id: UUID,
    turn_id: UUID,
    attempt_id: UUID,
    status: str,
    assistant_message_id: UUID,
    failure_payload: dict[str, object] | None,
    context_eligible: bool,
    created_at: datetime,
) -> str:
    payload: dict[str, object] = {
        "schema_version": 1,
        "outcome_id": str(outcome_id),
        "workspace_id": str(workspace_id),
        "turn_id": str(turn_id),
        "attempt_id": str(attempt_id),
        "status": status,
        "assistant_message_id": str(assistant_message_id),
        "failure": failure_payload,
        "context_eligible": context_eligible,
        "created_at": _json_datetime(created_at),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _prepare_backfill(
    pairs: list[_LegacyPair],
    *,
    eligible_limit: int = _ELIGIBLE_TURNS_PER_CONVERSATION,
) -> list[_BackfillRecord]:
    if eligible_limit < 0:
        raise ValueError("eligible limit cannot be negative")

    failures = {
        pair.user_message_id: _is_obvious_failure_or_refusal(pair.assistant_content)
        for pair in pairs
    }
    records: list[_BackfillRecord] = []
    for pair in pairs:
        is_failure = failures[pair.user_message_id]
        # Legacy messages predate authoritative Turn ownership. Adjacency proves
        # presentation order only; it cannot prove semantic correctness or that
        # concurrently submitted messages belong together. Preserve the history
        # for display/audit, but never inject it into a new model context.
        context_eligible = False
        turn_id, attempt_id, outcome_id = _turn_ids(pair)
        failure_payload = _failure_payload() if is_failure else None
        status = "UNAVAILABLE" if is_failure else "ANSWERED"
        records.append(
            _BackfillRecord(
                pair=pair,
                turn_id=turn_id,
                attempt_id=attempt_id,
                outcome_id=outcome_id,
                attempt_state="FAILED" if is_failure else "SUCCEEDED",
                outcome_status=status,
                failure_payload=failure_payload,
                context_eligible=context_eligible,
                content_hash=_outcome_hash(
                    outcome_id=outcome_id,
                    workspace_id=pair.workspace_id,
                    turn_id=turn_id,
                    attempt_id=attempt_id,
                    status=status,
                    assistant_message_id=pair.assistant_message_id,
                    failure_payload=failure_payload,
                    context_eligible=context_eligible,
                    created_at=pair.assistant_created_at,
                ),
            )
        )
    return records


def _legacy_pairs(connection: Connection) -> list[_LegacyPair]:
    rows = connection.execute(
        sa.text(
            """
            WITH ordered_messages AS (
                SELECT
                    m.workspace_id,
                    m.conversation_id,
                    m.id AS user_message_id,
                    m.role,
                    m.created_at AS user_created_at,
                    lead(m.id) OVER message_order AS assistant_message_id,
                    lead(m.role) OVER message_order AS assistant_role,
                    lead(m.content) OVER message_order AS assistant_content,
                    lead(m.created_at) OVER message_order AS assistant_created_at
                FROM messages AS m
                WINDOW message_order AS (
                    PARTITION BY m.workspace_id, m.conversation_id
                    ORDER BY m.created_at, m.id
                )
            )
            SELECT
                ordered.workspace_id,
                ordered.conversation_id,
                ordered.user_message_id,
                ordered.assistant_message_id,
                ordered.user_created_at,
                ordered.assistant_created_at,
                ordered.assistant_content
            FROM ordered_messages AS ordered
            WHERE ordered.role = 'USER'
              AND ordered.assistant_role = 'ASSISTANT'
              AND NOT EXISTS (
                  SELECT 1
                  FROM conversation_turns AS turn
                  WHERE turn.workspace_id = ordered.workspace_id
                    AND turn.user_message_id = ordered.user_message_id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM conversation_turn_outcomes AS outcome
                  WHERE outcome.workspace_id = ordered.workspace_id
                    AND outcome.assistant_message_id = ordered.assistant_message_id
              )
            ORDER BY
                ordered.workspace_id,
                ordered.conversation_id,
                ordered.user_created_at,
                ordered.user_message_id
            """
        )
    ).mappings()
    return [
        _LegacyPair(
            workspace_id=row["workspace_id"],
            conversation_id=row["conversation_id"],
            user_message_id=row["user_message_id"],
            assistant_message_id=row["assistant_message_id"],
            user_created_at=row["user_created_at"],
            assistant_created_at=row["assistant_created_at"],
            assistant_content=row["assistant_content"],
        )
        for row in rows
    ]


def _insert_records(connection: Connection, records: list[_BackfillRecord]) -> None:
    if not records:
        return

    uuid_type = PG_UUID(as_uuid=True)
    json_type = JSONB(none_as_null=True)
    turns = sa.table(
        "conversation_turns",
        sa.column("id", uuid_type),
        sa.column("workspace_id", uuid_type),
        sa.column("conversation_id", uuid_type),
        sa.column("user_message_id", uuid_type),
        sa.column("schema_version", sa.Integer()),
        sa.column("state", sa.String()),
        sa.column("task_id", uuid_type),
        sa.column("active_attempt_id", uuid_type),
        sa.column("decision_version", sa.Integer()),
        sa.column("fencing_token", sa.Integer()),
        sa.column("context_eligible", sa.Boolean()),
        sa.column("version", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("completed_at", sa.DateTime(timezone=True)),
    )
    attempts = sa.table(
        "conversation_turn_attempts",
        sa.column("id", uuid_type),
        sa.column("workspace_id", uuid_type),
        sa.column("turn_id", uuid_type),
        sa.column("schema_version", sa.Integer()),
        sa.column("state", sa.String()),
        sa.column("fencing_token", sa.Integer()),
        sa.column("trace_id", sa.String()),
        sa.column("failure_payload", json_type),
        sa.column("started_at", sa.DateTime(timezone=True)),
        sa.column("completed_at", sa.DateTime(timezone=True)),
    )
    outcomes = sa.table(
        "conversation_turn_outcomes",
        sa.column("id", uuid_type),
        sa.column("workspace_id", uuid_type),
        sa.column("turn_id", uuid_type),
        sa.column("attempt_id", uuid_type),
        sa.column("assistant_message_id", uuid_type),
        sa.column("schema_version", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("failure_payload", json_type),
        sa.column("context_eligible", sa.Boolean()),
        sa.column("content_hash", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    connection.execute(
        turns.insert(),
        [
            {
                "id": record.turn_id,
                "workspace_id": record.pair.workspace_id,
                "conversation_id": record.pair.conversation_id,
                "user_message_id": record.pair.user_message_id,
                "schema_version": 1,
                "state": "COMPLETED",
                "task_id": None,
                "active_attempt_id": None,
                "decision_version": 1,
                "fencing_token": 1,
                "context_eligible": record.context_eligible,
                "version": 3,
                "created_at": _utc(record.pair.user_created_at),
                "updated_at": _utc(record.pair.assistant_created_at),
                "completed_at": _utc(record.pair.assistant_created_at),
            }
            for record in records
        ],
    )
    connection.execute(
        attempts.insert(),
        [
            {
                "id": record.attempt_id,
                "workspace_id": record.pair.workspace_id,
                "turn_id": record.turn_id,
                "schema_version": 1,
                "state": record.attempt_state,
                "fencing_token": 1,
                "trace_id": _MIGRATION_TRACE_ID,
                "failure_payload": record.failure_payload,
                "started_at": _utc(record.pair.user_created_at),
                "completed_at": _utc(record.pair.assistant_created_at),
            }
            for record in records
        ],
    )
    active_attempts = sa.text(
        """
        UPDATE conversation_turns
        SET active_attempt_id = :attempt_id
        WHERE workspace_id = :workspace_id
          AND id = :turn_id
          AND active_attempt_id IS NULL
        """
    ).bindparams(
        sa.bindparam("workspace_id", type_=uuid_type),
        sa.bindparam("turn_id", type_=uuid_type),
        sa.bindparam("attempt_id", type_=uuid_type),
    )
    connection.execute(
        active_attempts,
        [
            {
                "workspace_id": record.pair.workspace_id,
                "turn_id": record.turn_id,
                "attempt_id": record.attempt_id,
            }
            for record in records
        ],
    )
    connection.execute(
        outcomes.insert(),
        [
            {
                "id": record.outcome_id,
                "workspace_id": record.pair.workspace_id,
                "turn_id": record.turn_id,
                "attempt_id": record.attempt_id,
                "assistant_message_id": record.pair.assistant_message_id,
                "schema_version": 1,
                "status": record.outcome_status,
                "failure_payload": record.failure_payload,
                "context_eligible": record.context_eligible,
                "content_hash": record.content_hash,
                "created_at": _utc(record.pair.assistant_created_at),
            }
            for record in records
        ],
    )


def upgrade() -> None:
    connection = op.get_bind()
    _insert_records(connection, _prepare_backfill(_legacy_pairs(connection)))


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting backfilled Turn history")
