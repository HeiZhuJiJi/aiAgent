"""Persist runtime loop-detection streaks."""

from alembic import op

revision = "0011_runtime_loop_guards"
down_revision = "0010_task_budgets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE runtime_guards (
            task_id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            last_capability_fingerprint VARCHAR(64),
            consecutive_capability_calls INTEGER NOT NULL DEFAULT 0,
            last_evidence_count INTEGER NOT NULL DEFAULT 0,
            consecutive_no_evidence_steps INTEGER NOT NULL DEFAULT 0,
            consecutive_replans INTEGER NOT NULL DEFAULT 0,
            state VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                CHECK (state IN ('ACTIVE','ABORTED')),
            abort_reason VARCHAR(100),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, task_id),
            FOREIGN KEY (workspace_id, task_id)
                REFERENCES task_runs (workspace_id, id),
            CHECK (
                consecutive_capability_calls >= 0
                AND consecutive_no_evidence_steps >= 0
                AND consecutive_replans >= 0
            )
        )
        """
    )
    op.execute("CREATE INDEX ix_runtime_guards_workspace_id ON runtime_guards (workspace_id)")
    op.execute("ALTER TABLE runtime_guards ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE runtime_guards FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspace_isolation ON runtime_guards
        USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of dropping runtime guard history")
