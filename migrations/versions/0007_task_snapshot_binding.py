"""Bind API-created tasks to immutable execution snapshots."""

from alembic import op

revision = "0007_task_snapshot_binding"
down_revision = "0006_local_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE task_runs ADD COLUMN execution_snapshot_id UUID")
    op.execute("ALTER TABLE task_runs ADD COLUMN goal_payload JSONB")
    op.execute(
        """
        ALTER TABLE task_runs ADD CONSTRAINT fk_task_execution_snapshot
        FOREIGN KEY (workspace_id, execution_snapshot_id)
        REFERENCES execution_snapshots (workspace_id, id)
        """
    )
    op.execute(
        "CREATE INDEX ix_task_runs_execution_snapshot_id ON task_runs (execution_snapshot_id)"
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of removing task snapshot bindings")
