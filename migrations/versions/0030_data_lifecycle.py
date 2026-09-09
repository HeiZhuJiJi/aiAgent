"""Add retention policies and durable workspace deletion requests."""

from alembic import op

revision = "0030_data_lifecycle"
down_revision = "0029_core_registries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE workspaces
          ADD COLUMN lifecycle_state VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
            CHECK (lifecycle_state IN ('ACTIVE','DELETING','DELETED')),
          ADD COLUMN deleted_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        CREATE TABLE retention_policies (
          workspace_id UUID PRIMARY KEY REFERENCES workspaces(id),
          schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
          version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
          conversation_retention_days INTEGER NOT NULL DEFAULT 365 CHECK (conversation_retention_days > 0),
          trace_retention_days INTEGER NOT NULL DEFAULT 30 CHECK (trace_retention_days > 0),
          artifact_retention_days INTEGER NOT NULL DEFAULT 365 CHECK (artifact_retention_days > 0),
          document_retention_days INTEGER NOT NULL DEFAULT 365 CHECK (document_retention_days > 0),
          evaluation_retention_days INTEGER NOT NULL DEFAULT 365 CHECK (evaluation_retention_days > 0),
          audit_retention_days INTEGER CHECK (audit_retention_days IS NULL OR audit_retention_days > 0),
          retain_audit_tombstone BOOLEAN NOT NULL DEFAULT true,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        INSERT INTO retention_policies(workspace_id)
        SELECT id FROM workspaces ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        CREATE TABLE workspace_deletion_requests (
          id UUID PRIMARY KEY,
          workspace_id UUID NOT NULL UNIQUE REFERENCES workspaces(id),
          schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
          version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
          requested_by UUID NOT NULL REFERENCES users(id),
          retain_audit_tombstone BOOLEAN NOT NULL DEFAULT true,
          state VARCHAR(30) NOT NULL
            CHECK (state IN ('DRAINING','READY_TO_PURGE','BLOCKED','COMPLETED')),
          active_task_count INTEGER NOT NULL CHECK (active_task_count >= 0),
          safe_error VARCHAR(500),
          requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          completed_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_workspace_deletion_requests_state "
        "ON workspace_deletion_requests(state, requested_at)"
    )
    for table in ("retention_policies", "workspace_deletion_requests"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""CREATE POLICY workspace_isolation ON {table}
            USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
            WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)"""
        )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of reversing lifecycle state")
