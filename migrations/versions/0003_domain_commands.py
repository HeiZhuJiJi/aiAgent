"""Idempotent PostgreSQL domain commands used by Temporal activities."""

from alembic import op

revision = "0003_domain_commands"
down_revision = "0002_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE domain_commands (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            task_id UUID NOT NULL,
            step_key VARCHAR(200) NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1,
            result JSONB NOT NULL,
            committed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, task_id)
                REFERENCES task_runs (workspace_id, id),
            CHECK (schema_version = 1)
        )
        """
    )
    op.execute("CREATE INDEX ix_domain_commands_workspace_id ON domain_commands (workspace_id)")
    op.execute("ALTER TABLE domain_commands ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE domain_commands FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspace_isolation ON domain_commands
        USING (
            workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid
        )
        WITH CHECK (
            workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid
        )
        """
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of dropping command history")
