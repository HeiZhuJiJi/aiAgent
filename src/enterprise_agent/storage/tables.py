from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy metadata root."""


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(default=1)
    lifecycle_state: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RetentionPolicyRow(Base):
    __tablename__ = "retention_policies"
    __table_args__ = (
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
    )
    workspace_id: Mapped[UUID] = mapped_column(primary_key=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    version: Mapped[int] = mapped_column(default=1)
    conversation_retention_days: Mapped[int] = mapped_column(default=365)
    trace_retention_days: Mapped[int] = mapped_column(default=30)
    artifact_retention_days: Mapped[int] = mapped_column(default=365)
    document_retention_days: Mapped[int] = mapped_column(default=365)
    evaluation_retention_days: Mapped[int] = mapped_column(default=365)
    audit_retention_days: Mapped[int | None]
    retain_audit_tombstone: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkspaceDeletionRequestRow(Base):
    __tablename__ = "workspace_deletion_requests"
    __table_args__ = (
        UniqueConstraint("workspace_id"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        ForeignKeyConstraint(["requested_by"], ["users.id"]),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
        CheckConstraint("active_task_count >= 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    version: Mapped[int] = mapped_column(default=1)
    requested_by: Mapped[UUID]
    retain_audit_tombstone: Mapped[bool] = mapped_column(default=True)
    state: Mapped[str] = mapped_column(String(30), default="DRAINING", index=True)
    active_task_count: Mapped[int] = mapped_column(default=0)
    safe_error: Mapped[str | None] = mapped_column(String(500))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserRow(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("normalized_email"),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    schema_version: Mapped[int] = mapped_column(default=1)
    email: Mapped[str] = mapped_column(String(320))
    normalized_email: Mapped[str] = mapped_column(String(320))
    password_hash: Mapped[str] = mapped_column(String(512))
    enabled: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkspaceMemberRow(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        ForeignKeyConstraint(["user_id"], ["users.id"]),
        CheckConstraint("role IN ('WORKSPACE_OWNER','WORKSPACE_ADMIN','MEMBER','AUDITOR')"),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    user_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    role: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthSessionRow(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        ForeignKeyConstraint(["user_id"], ["users.id"]),
        CheckConstraint("schema_version = 1"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboxEventRow(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
        CheckConstraint("attempts >= 0"),
        CheckConstraint("lease_token >= 0"),
        CheckConstraint("state IN ('PENDING','LEASED','PUBLISHED','FAILED')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    aggregate_type: Mapped[str] = mapped_column(String(80))
    aggregate_id: Mapped[UUID] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(20), default="PENDING")
    version: Mapped[int] = mapped_column(default=1)
    attempts: Mapped[int] = mapped_column(default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_token: Mapped[int] = mapped_column(default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskRow(Base):
    __tablename__ = "task_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        ForeignKeyConstraint(["created_by"], ["users.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "execution_snapshot_id"],
            ["execution_snapshots.workspace_id", "execution_snapshots.id"],
        ),
        CheckConstraint("version > 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    created_by: Mapped[UUID | None] = mapped_column(index=True)
    state: Mapped[str] = mapped_column(String(30))
    version: Mapped[int] = mapped_column(default=1)
    execution_snapshot_id: Mapped[UUID | None]
    goal_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    schedule_block_reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskBudgetRow(Base):
    __tablename__ = "task_budgets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "task_id"),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"], ["task_runs.workspace_id", "task_runs.id"]
        ),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
        CheckConstraint(
            "max_steps > 0 AND max_replans >= 0 AND max_llm_calls > 0 "
            "AND max_capability_calls > 0 AND max_tokens > 0 AND max_cost >= 0"
        ),
        CheckConstraint(
            "used_steps >= 0 AND used_replans >= 0 AND used_llm_calls >= 0 "
            "AND used_capability_calls >= 0 AND used_tokens >= 0 AND used_cost >= 0"
        ),
        CheckConstraint("state IN ('ACTIVE','EXCEEDED')"),
    )
    task_id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    version: Mapped[int] = mapped_column(default=1)
    max_steps: Mapped[int]
    max_replans: Mapped[int]
    max_llm_calls: Mapped[int]
    max_capability_calls: Mapped[int]
    max_tokens: Mapped[int]
    max_cost: Mapped[float] = mapped_column(Float)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_steps: Mapped[int] = mapped_column(default=0)
    used_replans: Mapped[int] = mapped_column(default=0)
    used_llm_calls: Mapped[int] = mapped_column(default=0)
    used_capability_calls: Mapped[int] = mapped_column(default=0)
    used_tokens: Mapped[int] = mapped_column(default=0)
    used_cost: Mapped[float] = mapped_column(Float, default=0)
    state: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    last_exceeded_reason: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RuntimeGuardRow(Base):
    __tablename__ = "runtime_guards"
    __table_args__ = (
        UniqueConstraint("workspace_id", "task_id"),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"], ["task_runs.workspace_id", "task_runs.id"]
        ),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
        CheckConstraint(
            "consecutive_capability_calls >= 0 "
            "AND consecutive_no_evidence_steps >= 0 AND consecutive_replans >= 0"
        ),
        CheckConstraint("state IN ('ACTIVE','ABORTED')"),
    )
    task_id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    version: Mapped[int] = mapped_column(default=1)
    last_capability_fingerprint: Mapped[str | None] = mapped_column(String(64))
    consecutive_capability_calls: Mapped[int] = mapped_column(default=0)
    last_evidence_count: Mapped[int] = mapped_column(default=0)
    consecutive_no_evidence_steps: Mapped[int] = mapped_column(default=0)
    consecutive_replans: Mapped[int] = mapped_column(default=0)
    state: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    abort_reason: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StepRow(Base):
    __tablename__ = "task_steps"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"], ["task_runs.workspace_id", "task_runs.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "plan_id"], ["task_plans.workspace_id", "task_plans.id"]
        ),
        UniqueConstraint("workspace_id", "task_id", "plan_id", "step_key"),
        CheckConstraint("fencing_token >= 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    task_id: Mapped[UUID] = mapped_column(index=True)
    plan_id: Mapped[UUID | None] = mapped_column(index=True)
    step_key: Mapped[str | None] = mapped_column(String(200))
    spec_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(30))
    version: Mapped[int] = mapped_column(default=1)
    fencing_token: Mapped[int] = mapped_column(default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class AttemptRow(Base):
    __tablename__ = "step_attempts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "step_id", "fencing_token"),
        ForeignKeyConstraint(
            ["workspace_id", "step_id"], ["task_steps.workspace_id", "task_steps.id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    step_id: Mapped[UUID] = mapped_column(index=True)
    worker_id: Mapped[str] = mapped_column(String(200))
    fencing_token: Mapped[int]
    state: Mapped[str] = mapped_column(String(30))
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    trace_id: Mapped[str] = mapped_column(String(64))


class EvidenceRow(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(
            ["workspace_id", "step_id"], ["task_steps.workspace_id", "task_steps.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "attempt_id"], ["step_attempts.workspace_id", "step_attempts.id"]
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    step_id: Mapped[UUID]
    attempt_id: Mapped[UUID]
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)


class ClaimRow(Base):
    __tablename__ = "claims"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"], ["task_runs.workspace_id", "task_runs.id"]
        ),
        CheckConstraint("schema_version = 1"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    task_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClaimEvidenceLinkRow(Base):
    __tablename__ = "claim_evidence_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "claim_id"], ["claims.workspace_id", "claims.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "evidence_id"], ["evidence.workspace_id", "evidence.id"]
        ),
        CheckConstraint("schema_version = 1"),
    )
    workspace_id: Mapped[UUID] = mapped_column(primary_key=True)
    claim_id: Mapped[UUID] = mapped_column(primary_key=True)
    evidence_id: Mapped[UUID] = mapped_column(primary_key=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditRow(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "task_id"], ["task_runs.workspace_id", "task_runs.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "step_id"], ["task_steps.workspace_id", "task_steps.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "approval_id"],
            ["approval_requests.workspace_id", "approval_requests.id"],
        ),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("actor_type IN ('USER','SERVICE_ACCOUNT','SYSTEM','AGENT')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    actor_type: Mapped[str] = mapped_column(String(30), default="SYSTEM")
    actor_id: Mapped[UUID]
    task_id: Mapped[UUID | None] = mapped_column(index=True)
    step_id: Mapped[UUID | None] = mapped_column(index=True)
    action: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(100), default="UNKNOWN")
    resource_id: Mapped[UUID]
    capability_id: Mapped[str | None] = mapped_column(String(200))
    policy_decision: Mapped[str | None] = mapped_column(String(30))
    approval_id: Mapped[UUID | None] = mapped_column(index=True)
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    trace_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OperationRow(Base):
    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "step_id", "logical_key"),
        ForeignKeyConstraint(
            ["workspace_id", "step_id"], ["task_steps.workspace_id", "task_steps.id"]
        ),
        CheckConstraint("state IN ('PREPARED','DISPATCHED','UNKNOWN','SUCCEEDED','FAILED')"),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    step_id: Mapped[UUID]
    logical_key: Mapped[str] = mapped_column(String(200))
    schema_version: Mapped[int] = mapped_column(default=1)
    capability_id: Mapped[str] = mapped_column(String(200))
    capability_version: Mapped[str] = mapped_column(String(100))
    parameter_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20))
    version: Mapped[int] = mapped_column(default=1)
    dispatch_id: Mapped[UUID | None]
    dispatch_fencing_token: Mapped[int | None]
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CapabilityCallRow(Base):
    __tablename__ = "capability_calls"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "command_id"),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"], ["task_runs.workspace_id", "task_runs.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "step_id"], ["task_steps.workspace_id", "task_steps.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "attempt_id"], ["step_attempts.workspace_id", "step_attempts.id"]
        ),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
        CheckConstraint(
            "state IN "
            "('PENDING','DENIED','WAITING_APPROVAL','DISPATCHED','SUCCEEDED','FAILED','UNKNOWN')"
        ),
        CheckConstraint("policy_decision IN ('ALLOW','DENY','REQUIRE_APPROVAL')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    task_id: Mapped[UUID] = mapped_column(index=True)
    step_id: Mapped[UUID] = mapped_column(index=True)
    attempt_id: Mapped[UUID] = mapped_column(index=True)
    operation_id: Mapped[UUID] = mapped_column(index=True)
    command_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    version: Mapped[int] = mapped_column(default=1)
    request_hash: Mapped[str] = mapped_column(String(64))
    request_payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    capability_id: Mapped[str] = mapped_column(String(200))
    capability_version: Mapped[str] = mapped_column(String(100))
    policy_decision: Mapped[str] = mapped_column(String(30))
    policy_reason: Mapped[str] = mapped_column(String(500))
    policy_version: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(30))
    dispatch_id: Mapped[UUID | None]
    result_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    trace_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DomainCommandRow(Base):
    __tablename__ = "domain_commands"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"], ["task_runs.workspace_id", "task_runs.id"]
        ),
        CheckConstraint("schema_version = 1"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    task_id: Mapped[UUID]
    step_key: Mapped[str] = mapped_column(String(200))
    schema_version: Mapped[int] = mapped_column(default=1)
    result: Mapped[dict[str, object]] = mapped_column(JSONB)
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ExecutionSnapshotRow(Base):
    __tablename__ = "execution_snapshots"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        CheckConstraint("schema_version = 1"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlanRow(Base):
    __tablename__ = "task_plans"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "task_id", "version"),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"], ["task_runs.workspace_id", "task_runs.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "execution_snapshot_id"],
            ["execution_snapshots.workspace_id", "execution_snapshots.id"],
        ),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    task_id: Mapped[UUID] = mapped_column(index=True)
    execution_snapshot_id: Mapped[UUID]
    schema_version: Mapped[int] = mapped_column(default=1)
    version: Mapped[int]
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReplanDecisionRow(Base):
    __tablename__ = "replan_decisions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "task_id", "from_plan_id"),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"], ["task_runs.workspace_id", "task_runs.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "from_plan_id"], ["task_plans.workspace_id", "task_plans.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "to_plan_id"], ["task_plans.workspace_id", "task_plans.id"]
        ),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("status IN ('APPLIED','ABORTED')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    task_id: Mapped[UUID] = mapped_column(index=True)
    from_plan_id: Mapped[UUID]
    to_plan_id: Mapped[UUID | None]
    schema_version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[UUID]
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CheckpointRow(Base):
    __tablename__ = "task_checkpoints"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "task_id", "sequence"),
        UniqueConstraint("workspace_id", "task_id", "resume_token"),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"], ["task_runs.workspace_id", "task_runs.id"]
        ),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("sequence > 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    task_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    sequence: Mapped[int]
    resume_token: Mapped[str] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"], ["task_runs.workspace_id", "task_runs.id"]
        ),
        CheckConstraint("schema_version = 1"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    task_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConversationRow(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    title: Mapped[str] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConversationSummaryRow(Base):
    __tablename__ = "conversation_summaries"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "conversation_id", "version"),
        ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversations.workspace_id", "conversations.id"],
        ),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    conversation_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    version: Mapped[int]
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConversationMessageRow(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversations.workspace_id", "conversations.id"],
        ),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("role IN ('SYSTEM','USER','ASSISTANT')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    conversation_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str]
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectMemoryRow(Base):
    __tablename__ = "project_memory"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "supersedes"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "supersedes"],
            ["project_memory.workspace_id", "project_memory.id"],
        ),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("memory_type IN ('FACT','DECISION','PREFERENCE')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    memory_type: Mapped[str] = mapped_column(String(20), index=True)
    supersedes: Mapped[UUID | None]
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class KnowledgeDocumentRow(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    name: Mapped[str] = mapped_column(String(500))
    media_type: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeDocumentVersionRow(Base):
    __tablename__ = "knowledge_document_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "document_id", "version"),
        UniqueConstraint("workspace_id", "document_id", "source_hash"),
        ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["knowledge_documents.workspace_id", "knowledge_documents.id"],
        ),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    document_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    version: Mapped[int]
    source_hash: Mapped[str] = mapped_column(String(64))
    storage_uri: Mapped[str] = mapped_column(String(1000))
    parser_version: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeIngestionJobRow(Base):
    __tablename__ = "knowledge_ingestion_jobs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "document_id", "source_hash"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "document_version_id"],
            ["knowledge_document_versions.workspace_id", "knowledge_document_versions.id"],
        ),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
        CheckConstraint("fencing_token >= 0"),
        CheckConstraint("state IN ('PENDING','RUNNING','SUCCEEDED','FAILED')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    document_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    name: Mapped[str] = mapped_column(String(500))
    media_type: Mapped[str] = mapped_column(String(200))
    source_hash: Mapped[str] = mapped_column(String(64))
    object_name: Mapped[str] = mapped_column(String(1000))
    parser_version: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(20), default="PENDING")
    version: Mapped[int] = mapped_column(default=1)
    fencing_token: Mapped[int] = mapped_column(default=0)
    document_version_id: Mapped[UUID | None]
    safe_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeChunkRow(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "document_version_id", "ordinal"),
        ForeignKeyConstraint(
            ["workspace_id", "document_version_id"],
            ["knowledge_document_versions.workspace_id", "knowledge_document_versions.id"],
        ),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("ordinal >= 0"),
        CheckConstraint("token_count > 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    document_version_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    ordinal: Mapped[int]
    content: Mapped[str]
    content_hash: Mapped[str] = mapped_column(String(64))
    locator: Mapped[dict[str, object]] = mapped_column(JSONB)
    token_count: Mapped[int]
    embedding: Mapped[list[float]] = mapped_column(Vector(1024))


class SkillRow(Base):
    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "name"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        CheckConstraint("schema_version = 1"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SkillVersionRow(Base):
    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "skill_id", "version"),
        UniqueConstraint("workspace_id", "skill_id", "archive_sha256"),
        ForeignKeyConstraint(
            ["workspace_id", "skill_id"], ["skills.workspace_id", "skills.id"]
        ),
        CheckConstraint("schema_version = 1"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    skill_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    version: Mapped[str] = mapped_column(String(100))
    archive_sha256: Mapped[str] = mapped_column(String(64))
    storage_uri: Mapped[str] = mapped_column(String(1000))
    package_payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SkillInstallationRow(Base):
    __tablename__ = "skill_installations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "skill_version_id"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "skill_id"], ["skills.workspace_id", "skills.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "skill_version_id"],
            ["skill_versions.workspace_id", "skill_versions.id"],
        ),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
        CheckConstraint(
            "state IN "
            "('QUARANTINED','REJECTED','PENDING_CONFIRMATION','INSTALLED','ENABLED','DISABLED')"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    skill_id: Mapped[UUID | None] = mapped_column(index=True)
    skill_version_id: Mapped[UUID | None] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    declared_name: Mapped[str] = mapped_column(String(64))
    declared_version: Mapped[str] = mapped_column(String(100))
    archive_sha256: Mapped[str] = mapped_column(String(64))
    object_name: Mapped[str] = mapped_column(String(1000))
    state: Mapped[str] = mapped_column(String(30))
    version: Mapped[int] = mapped_column(default=1)
    permission_diff: Mapped[list[str]] = mapped_column(JSONB)
    safe_error: Mapped[str | None] = mapped_column(String(500))
    confirmed_by: Mapped[UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MCPServerRow(Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "name"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
        CheckConstraint("transport = 'STDIO'"),
        CheckConstraint("status IN ('REGISTERED','ACTIVE','UNHEALTHY','DISABLED')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(100))
    transport: Mapped[str] = mapped_column(String(20), default="STDIO")
    command_config: Mapped[dict[str, object]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(30), default="REGISTERED")
    version: Mapped[int] = mapped_column(default=1)
    schema_hash: Mapped[str | None] = mapped_column(String(64))
    auth_secret_ref: Mapped[UUID | None]
    last_health_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    safe_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MCPCapabilityRow(Base):
    __tablename__ = "mcp_capabilities"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "server_id", "discovery_hash", "tool_name"),
        ForeignKeyConstraint(
            ["workspace_id", "server_id"], ["mcp_servers.workspace_id", "mcp_servers.id"]
        ),
        CheckConstraint("schema_version = 1"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    server_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    discovery_hash: Mapped[str] = mapped_column(String(64), index=True)
    tool_name: Mapped[str] = mapped_column(String(200))
    descriptor_payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApprovalRow(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "operation_id"),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"], ["task_runs.workspace_id", "task_runs.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "step_id"], ["task_steps.workspace_id", "task_steps.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "operation_id"], ["operations.workspace_id", "operations.id"]
        ),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
        CheckConstraint("state IN ('PENDING','APPROVED','REJECTED','EXPIRED')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    task_id: Mapped[UUID] = mapped_column(index=True)
    step_id: Mapped[UUID]
    operation_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    request_hash: Mapped[str] = mapped_column(String(64))
    request_payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(20), default="PENDING")
    version: Mapped[int] = mapped_column(default=1)
    actor_id: Mapped[UUID | None]
    decision: Mapped[str | None] = mapped_column(String(20))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ServiceAccountRow(Base):
    __tablename__ = "service_accounts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "normalized_name"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200))
    credential_hash: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CapabilityRow(Base):
    __tablename__ = "capabilities"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "capability_key", "capability_version"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    capability_key: Mapped[str] = mapped_column(String(300), index=True)
    capability_version: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(40))
    descriptor_hash: Mapped[str] = mapped_column(String(64))
    descriptor: Mapped[dict[str, object]] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeBaseRow(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "normalized_name"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SecretRow(Base):
    __tablename__ = "secrets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("key_version > 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    purpose: Mapped[str] = mapped_column(String(100))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelConfigRow(Base):
    __tablename__ = "model_configs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "kind", "model_name", "base_url"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "secret_id"], ["secrets.workspace_id", "secrets.id"]
        ),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
        CheckConstraint("kind IN ('CHAT','EMBEDDING','RERANKER')"),
        CheckConstraint("provider IN ('Qwen','DeepSeek','OpenAICompatible')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    base_url: Mapped[str] = mapped_column(String(1000))
    model_name: Mapped[str] = mapped_column(String(300))
    external: Mapped[bool]
    secret_id: Mapped[UUID | None] = mapped_column(index=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluationCaseRow(Base):
    __tablename__ = "evaluation_cases"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "case_key"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("version > 0"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    version: Mapped[int] = mapped_column(default=1)
    case_key: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(40), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluationRunRow(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("state IN ('COMPLETED','FAILED')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    label: Mapped[str] = mapped_column(String(200))
    dataset_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20))
    metrics: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluationResultRow(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "run_id", "case_id"),
        ForeignKeyConstraint(
            ["workspace_id", "run_id"], ["evaluation_runs.workspace_id", "evaluation_runs.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "case_id"],
            ["evaluation_cases.workspace_id", "evaluation_cases.id"],
        ),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("status IN ('PASSED','FAILED','NOT_RUN')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    run_id: Mapped[UUID] = mapped_column(index=True)
    case_id: Mapped[UUID] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(20), index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageRecordRow(Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"], ["task_runs.workspace_id", "task_runs.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "evaluation_run_id"],
            ["evaluation_runs.workspace_id", "evaluation_runs.id"],
        ),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("units >= 0 AND tokens >= 0 AND cost >= 0"),
        CheckConstraint("task_id IS NOT NULL OR evaluation_run_id IS NOT NULL"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    task_id: Mapped[UUID | None] = mapped_column(index=True)
    evaluation_run_id: Mapped[UUID | None] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    units: Mapped[int] = mapped_column(default=0)
    tokens: Mapped[int] = mapped_column(default=0)
    cost: Mapped[float] = mapped_column(Float, default=0)
    detail: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConsistencyIncidentRow(Base):
    __tablename__ = "consistency_incidents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "task_id"], ["task_runs.workspace_id", "task_runs.id"]
        ),
        CheckConstraint("schema_version = 1"),
        CheckConstraint("severity IN ('LOW','MEDIUM','HIGH','CRITICAL')"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[UUID] = mapped_column(index=True)
    task_id: Mapped[UUID | None] = mapped_column(index=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    incident_type: Mapped[str] = mapped_column(String(100), index=True)
    detail: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
