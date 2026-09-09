"""Bind each API-created Task to the initiating user identity."""

from alembic import op

revision = "0026_task_creator"
down_revision = "0025_attempt_retry_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE task_runs ADD COLUMN created_by UUID")
    op.execute(
        "ALTER TABLE task_runs ADD CONSTRAINT fk_task_runs_created_by "
        "FOREIGN KEY(created_by) REFERENCES users(id)"
    )
    op.execute("CREATE INDEX ix_task_runs_created_by ON task_runs(created_by)")


def downgrade() -> None:
    raise RuntimeError(
        "restore-required downgrade: task initiating identity is an authoritative audit binding"
    )
