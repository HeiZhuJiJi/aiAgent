"""Immutable execution snapshots and versioned plans."""

from alembic import op

revision = "0004_immutable_plans"
down_revision = "0003_domain_commands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE execution_snapshots (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces (id),
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            content_hash VARCHAR(64) NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            UNIQUE (workspace_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_execution_snapshots_workspace_id ON execution_snapshots (workspace_id)"
    )
    op.execute(
        """
        CREATE TABLE task_plans (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            task_id UUID NOT NULL,
            execution_snapshot_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            version INTEGER NOT NULL CHECK (version > 0),
            content_hash VARCHAR(64) NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            UNIQUE (workspace_id, task_id, version),
            FOREIGN KEY (workspace_id, task_id)
                REFERENCES task_runs (workspace_id, id),
            FOREIGN KEY (workspace_id, execution_snapshot_id)
                REFERENCES execution_snapshots (workspace_id, id)
        )
        """
    )
    op.execute("CREATE INDEX ix_task_plans_workspace_id ON task_plans (workspace_id)")
    op.execute("CREATE INDEX ix_task_plans_task_id ON task_plans (task_id)")
    op.execute(
        """
        CREATE FUNCTION reject_immutable_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'immutable row cannot be updated or deleted'; END $$
        """
    )
    for table in ("execution_snapshots", "task_plans"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY workspace_isolation ON {table}
            USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
            WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {table}_immutable
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_immutable_mutation()
            """
        )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting execution history")
