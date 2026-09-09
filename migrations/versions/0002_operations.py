"""Persistent side effect operation ledger."""

from alembic import op

revision = "0002_operations"
down_revision = "0001_runtime"
branch_labels = None
depends_on = None

DDL = (
    "\nCREATE TABLE operations (\n\tid UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tstep_id UUID NOT NULL, \n\tlogical_key VARCHAR(200) NOT NULL, \n\tschema_version INTEGER NOT NULL, \n\tcapability_id VARCHAR(200) NOT NULL, \n\tcapability_version VARCHAR(100) NOT NULL, \n\tparameter_hash VARCHAR(64) NOT NULL, \n\tstate VARCHAR(20) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tdispatch_id UUID, \n\tdispatch_fencing_token INTEGER, \n\tresult JSONB, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (workspace_id, step_id, logical_key), \n\tFOREIGN KEY(workspace_id, step_id) REFERENCES task_steps (workspace_id, id), \n\tCHECK (state IN ('PREPARED','DISPATCHED','UNKNOWN','SUCCEEDED','FAILED')), \n\tCHECK (schema_version = 1), \n\tCHECK (version > 0)\n)\n\n",
    "CREATE INDEX ix_operations_workspace_id ON operations (workspace_id)",
)


def upgrade():
    for statement in DDL:
        op.execute(statement)
    op.execute("ALTER TABLE operations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE operations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY workspace_isolation ON operations USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid) WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)"
    )


def downgrade():
    raise RuntimeError("Restore a verified backup instead of dropping operation history")
