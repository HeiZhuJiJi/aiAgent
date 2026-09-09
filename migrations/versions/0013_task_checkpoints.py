"""Persist immutable, reference-only runtime checkpoints."""

from alembic import op

revision = "0013_task_checkpoints"
down_revision = "0012_admission_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE task_checkpoints (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            task_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            sequence INTEGER NOT NULL CHECK (sequence > 0),
            resume_token VARCHAR(200) NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            UNIQUE (workspace_id, id),
            UNIQUE (workspace_id, task_id, sequence),
            UNIQUE (workspace_id, task_id, resume_token),
            FOREIGN KEY (workspace_id, task_id)
                REFERENCES task_runs (workspace_id, id)
        )
        """
    )
    op.execute("CREATE INDEX ix_task_checkpoints_workspace_id ON task_checkpoints (workspace_id)")
    op.execute("CREATE INDEX ix_task_checkpoints_task_id ON task_checkpoints (task_id)")
    op.execute("ALTER TABLE task_checkpoints ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE task_checkpoints FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspace_isolation ON task_checkpoints
        USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        """
    )
    op.execute(
        """
        CREATE TRIGGER task_checkpoints_immutable
        BEFORE UPDATE OR DELETE ON task_checkpoints
        FOR EACH ROW EXECUTE FUNCTION reject_immutable_mutation()
        """
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting runtime checkpoints")
