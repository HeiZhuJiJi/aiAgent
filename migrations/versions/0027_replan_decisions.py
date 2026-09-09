"""Persist immutable Replan decisions and their append-only Plan edges."""

from alembic import op

revision = "0027_replan_decisions"
down_revision = "0026_task_creator"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE replan_decisions (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            task_id UUID NOT NULL,
            from_plan_id UUID NOT NULL,
            to_plan_id UUID,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            status VARCHAR(20) NOT NULL CHECK (status IN ('APPLIED','ABORTED')),
            actor_id UUID NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            UNIQUE (workspace_id, task_id, from_plan_id),
            FOREIGN KEY (workspace_id, task_id)
                REFERENCES task_runs (workspace_id, id),
            FOREIGN KEY (workspace_id, from_plan_id)
                REFERENCES task_plans (workspace_id, id),
            FOREIGN KEY (workspace_id, to_plan_id)
                REFERENCES task_plans (workspace_id, id)
        )
        """
    )
    op.execute("CREATE INDEX ix_replan_decisions_workspace_id ON replan_decisions(workspace_id)")
    op.execute("CREATE INDEX ix_replan_decisions_task_id ON replan_decisions(task_id)")
    op.execute("ALTER TABLE replan_decisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE replan_decisions FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspace_isolation ON replan_decisions
        USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        """
    )
    op.execute(
        """
        CREATE TRIGGER replan_decisions_immutable
        BEFORE UPDATE OR DELETE ON replan_decisions
        FOR EACH ROW EXECUTE FUNCTION reject_immutable_mutation()
        """
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting Replan history")
