"""Persist immutable Artifact metadata for MinIO objects."""

from alembic import op

revision = "0015_artifacts"
down_revision = "0014_claim_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE artifacts (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            task_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            content_hash VARCHAR(64) NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, task_id)
                REFERENCES task_runs (workspace_id, id)
        )
        """
    )
    op.execute("CREATE INDEX ix_artifacts_workspace_id ON artifacts (workspace_id)")
    op.execute("CREATE INDEX ix_artifacts_task_id ON artifacts (task_id)")
    op.execute("ALTER TABLE artifacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE artifacts FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspace_isolation ON artifacts
        USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        """
    )
    op.execute(
        """
        CREATE TRIGGER artifacts_immutable
        BEFORE UPDATE OR DELETE ON artifacts
        FOR EACH ROW EXECUTE FUNCTION reject_immutable_mutation()
        """
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting Artifact metadata")
