"""Persist task limits and atomically accounted runtime usage."""

from alembic import op

revision = "0010_task_budgets"
down_revision = "0009_capability_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE task_budgets (
            task_id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            max_steps INTEGER NOT NULL,
            max_replans INTEGER NOT NULL,
            max_llm_calls INTEGER NOT NULL,
            max_capability_calls INTEGER NOT NULL,
            max_tokens INTEGER NOT NULL,
            max_cost DOUBLE PRECISION NOT NULL,
            deadline TIMESTAMPTZ NOT NULL,
            used_steps INTEGER NOT NULL DEFAULT 0,
            used_replans INTEGER NOT NULL DEFAULT 0,
            used_llm_calls INTEGER NOT NULL DEFAULT 0,
            used_capability_calls INTEGER NOT NULL DEFAULT 0,
            used_tokens INTEGER NOT NULL DEFAULT 0,
            used_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
            state VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                CHECK (state IN ('ACTIVE','EXCEEDED')),
            last_exceeded_reason VARCHAR(100),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, task_id),
            FOREIGN KEY (workspace_id, task_id)
                REFERENCES task_runs (workspace_id, id),
            CHECK (
                max_steps > 0 AND max_replans >= 0 AND max_llm_calls > 0
                AND max_capability_calls > 0 AND max_tokens > 0 AND max_cost >= 0
            ),
            CHECK (
                used_steps >= 0 AND used_replans >= 0 AND used_llm_calls >= 0
                AND used_capability_calls >= 0 AND used_tokens >= 0 AND used_cost >= 0
            )
        )
        """
    )
    op.execute("CREATE INDEX ix_task_budgets_workspace_id ON task_budgets (workspace_id)")
    op.execute("ALTER TABLE task_budgets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE task_budgets FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspace_isolation ON task_budgets
        USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of dropping task budget history")
