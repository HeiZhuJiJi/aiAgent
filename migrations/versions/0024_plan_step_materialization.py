"""Bind runtime task steps to immutable plan versions and step specifications."""

from alembic import op

revision = "0024_plan_step_materialization"
down_revision = "0023_mcp_manager"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE task_steps ADD COLUMN plan_id UUID")
    op.execute("ALTER TABLE task_steps ADD COLUMN step_key VARCHAR(200)")
    op.execute("ALTER TABLE task_steps ADD COLUMN spec_payload JSONB")
    op.execute(
        "ALTER TABLE task_steps ADD CONSTRAINT fk_task_steps_plan "
        "FOREIGN KEY(workspace_id,plan_id) REFERENCES task_plans(workspace_id,id)"
    )
    op.execute(
        "ALTER TABLE task_steps ADD CONSTRAINT uq_task_steps_plan_key "
        "UNIQUE(workspace_id,task_id,plan_id,step_key)"
    )
    op.execute("CREATE INDEX ix_task_steps_plan_id ON task_steps(plan_id)")


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of removing plan step bindings")
