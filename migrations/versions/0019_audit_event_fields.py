"""Expand immutable audit records to the frozen event field set."""

from alembic import op

revision = "0019_audit_event_fields"
down_revision = "0018_conversation_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE audit_logs ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE audit_logs ADD COLUMN actor_type VARCHAR(30) NOT NULL DEFAULT 'SYSTEM'")
    op.execute("ALTER TABLE audit_logs ADD COLUMN task_id UUID")
    op.execute("ALTER TABLE audit_logs ADD COLUMN step_id UUID")
    op.execute("ALTER TABLE audit_logs ADD COLUMN resource_type VARCHAR(100) NOT NULL DEFAULT 'UNKNOWN'")
    op.execute("ALTER TABLE audit_logs ADD COLUMN capability_id VARCHAR(200)")
    op.execute("ALTER TABLE audit_logs ADD COLUMN policy_decision VARCHAR(30)")
    op.execute("ALTER TABLE audit_logs ADD COLUMN approval_id UUID")
    op.execute("ALTER TABLE audit_logs ADD COLUMN result JSONB")
    op.execute("ALTER TABLE audit_logs ADD CONSTRAINT ck_audit_schema_version CHECK (schema_version = 1)")
    op.execute(
        "ALTER TABLE audit_logs ADD CONSTRAINT ck_audit_actor_type "
        "CHECK (actor_type IN ('USER','SERVICE_ACCOUNT','SYSTEM','AGENT'))"
    )
    op.execute("CREATE INDEX ix_audit_logs_task_id ON audit_logs(task_id)")
    op.execute("CREATE INDEX ix_audit_logs_step_id ON audit_logs(step_id)")
    op.execute("CREATE INDEX ix_audit_logs_approval_id ON audit_logs(approval_id)")


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting audit provenance")
