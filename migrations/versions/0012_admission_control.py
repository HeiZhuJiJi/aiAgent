"""Add canonical scheduling block reason for admission control."""

from alembic import op

revision = "0012_admission_control"
down_revision = "0011_runtime_loop_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE task_runs ADD COLUMN schedule_block_reason VARCHAR(200)")


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of dropping scheduling state")
