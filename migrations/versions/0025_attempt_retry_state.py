"""Persist classified Attempt failures and database-timed Step retries."""

from alembic import op

revision = "0025_attempt_retry_state"
down_revision = "0024_plan_step_materialization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE task_steps ADD COLUMN retry_available_at TIMESTAMPTZ")
    op.execute(
        "CREATE INDEX ix_task_steps_retry_available_at "
        "ON task_steps(retry_available_at)"
    )
    op.execute("ALTER TABLE step_attempts ADD COLUMN failure_payload JSONB")


def downgrade() -> None:
    raise RuntimeError(
        "restore-required downgrade: attempt failure and retry scheduling history is authoritative"
    )
