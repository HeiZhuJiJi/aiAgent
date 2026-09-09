"""Transactional outbox with lease fencing."""

from alembic import op

revision = "0008_transactional_outbox"
down_revision = "0007_task_snapshot_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE outbox_events (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces (id),
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            aggregate_type VARCHAR(80) NOT NULL,
            aggregate_id UUID NOT NULL,
            event_type VARCHAR(120) NOT NULL,
            payload_hash VARCHAR(64) NOT NULL,
            payload JSONB NOT NULL,
            state VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                CHECK (state IN ('PENDING','LEASED','PUBLISHED','FAILED')),
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            available_at TIMESTAMPTZ NOT NULL,
            lease_owner VARCHAR(200),
            lease_token INTEGER NOT NULL DEFAULT 0 CHECK (lease_token >= 0),
            lease_expires_at TIMESTAMPTZ,
            last_error VARCHAR(500),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            published_at TIMESTAMPTZ,
            UNIQUE (workspace_id, id)
        )
        """
    )
    op.execute("CREATE INDEX ix_outbox_events_workspace_id ON outbox_events (workspace_id)")
    op.execute("CREATE INDEX ix_outbox_events_aggregate_id ON outbox_events (aggregate_id)")
    op.execute("CREATE INDEX ix_outbox_events_event_type ON outbox_events (event_type)")
    op.execute(
        "CREATE INDEX ix_outbox_events_dispatch ON outbox_events (state, available_at, created_at)"
    )
    op.execute("ALTER TABLE outbox_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE outbox_events FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspace_isolation ON outbox_events
        USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting pending events")
