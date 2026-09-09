"""Fence audit task, step and approval references by workspace."""

from alembic import op

revision = "0020_audit_fks"
down_revision = "0019_audit_event_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE audit_logs ADD CONSTRAINT fk_audit_task "
        "FOREIGN KEY(workspace_id,task_id) REFERENCES task_runs(workspace_id,id)"
    )
    op.execute(
        "ALTER TABLE audit_logs ADD CONSTRAINT fk_audit_step "
        "FOREIGN KEY(workspace_id,step_id) REFERENCES task_steps(workspace_id,id)"
    )
    op.execute(
        "ALTER TABLE audit_logs ADD CONSTRAINT fk_audit_approval "
        "FOREIGN KEY(workspace_id,approval_id) REFERENCES approval_requests(workspace_id,id)"
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of weakening audit references")
