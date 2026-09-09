"""Persist governed capability calls and their policy decisions."""

from alembic import op

revision = "0009_capability_calls"
down_revision = "0008_transactional_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE capability_calls (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            task_id UUID NOT NULL,
            step_id UUID NOT NULL,
            attempt_id UUID NOT NULL,
            operation_id UUID NOT NULL,
            command_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            request_hash VARCHAR(64) NOT NULL,
            request_payload JSONB NOT NULL,
            capability_id VARCHAR(200) NOT NULL,
            capability_version VARCHAR(100) NOT NULL,
            policy_decision VARCHAR(30) NOT NULL
                CHECK (policy_decision IN ('ALLOW','DENY','REQUIRE_APPROVAL')),
            policy_reason VARCHAR(500) NOT NULL,
            policy_version VARCHAR(100) NOT NULL,
            state VARCHAR(30) NOT NULL
                CHECK (
                    state IN (
                        'PENDING','DENIED','WAITING_APPROVAL','DISPATCHED',
                        'SUCCEEDED','FAILED','UNKNOWN'
                    )
                ),
            dispatch_id UUID,
            result_payload JSONB,
            trace_id VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            UNIQUE (workspace_id, id),
            UNIQUE (workspace_id, command_id),
            FOREIGN KEY (workspace_id, task_id)
                REFERENCES task_runs (workspace_id, id),
            FOREIGN KEY (workspace_id, step_id)
                REFERENCES task_steps (workspace_id, id),
            FOREIGN KEY (workspace_id, attempt_id)
                REFERENCES step_attempts (workspace_id, id)
        )
        """
    )
    op.execute("CREATE INDEX ix_capability_calls_workspace_id ON capability_calls (workspace_id)")
    op.execute("CREATE INDEX ix_capability_calls_task_id ON capability_calls (task_id)")
    op.execute("CREATE INDEX ix_capability_calls_step_id ON capability_calls (step_id)")
    op.execute("CREATE INDEX ix_capability_calls_attempt_id ON capability_calls (attempt_id)")
    op.execute("CREATE INDEX ix_capability_calls_operation_id ON capability_calls (operation_id)")
    op.execute("CREATE INDEX ix_capability_calls_command_id ON capability_calls (command_id)")
    op.execute("ALTER TABLE capability_calls ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE capability_calls FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspace_isolation ON capability_calls
        USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of dropping capability call history")
