"""Initial runtime persistence with immutable audit and tenant RLS."""

from alembic import op

INITIAL_DDL = (
    "\nCREATE TABLE audit_logs (\n\tid UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tactor_id UUID NOT NULL, \n\taction VARCHAR(100) NOT NULL, \n\tresource_id UUID NOT NULL, \n\ttrace_id VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n",
    "CREATE INDEX ix_audit_logs_workspace_id ON audit_logs (workspace_id)",
    "\nCREATE TABLE workspaces (\n\tid UUID NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\tenabled BOOLEAN NOT NULL, \n\tversion INTEGER NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n",
    "\nCREATE TABLE task_runs (\n\tid UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tstate VARCHAR(30) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (workspace_id, id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id), \n\tCHECK (version > 0)\n)\n\n",
    "CREATE INDEX ix_task_runs_workspace_id ON task_runs (workspace_id)",
    "\nCREATE TABLE task_steps (\n\tid UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\ttask_id UUID NOT NULL, \n\tstate VARCHAR(30) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tfencing_token INTEGER NOT NULL, \n\tlease_expires_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tUNIQUE (workspace_id, id), \n\tFOREIGN KEY(workspace_id, task_id) REFERENCES task_runs (workspace_id, id), \n\tCHECK (fencing_token >= 0)\n)\n\n",
    "CREATE INDEX ix_task_steps_task_id ON task_steps (task_id)",
    "CREATE INDEX ix_task_steps_workspace_id ON task_steps (workspace_id)",
    "\nCREATE TABLE step_attempts (\n\tid UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tstep_id UUID NOT NULL, \n\tworker_id VARCHAR(200) NOT NULL, \n\tfencing_token INTEGER NOT NULL, \n\tstate VARCHAR(30) NOT NULL, \n\tlease_expires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tstarted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcompleted_at TIMESTAMP WITH TIME ZONE, \n\ttrace_id VARCHAR(64) NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (workspace_id, id), \n\tUNIQUE (workspace_id, step_id, fencing_token), \n\tFOREIGN KEY(workspace_id, step_id) REFERENCES task_steps (workspace_id, id)\n)\n\n",
    "CREATE INDEX ix_step_attempts_step_id ON step_attempts (step_id)",
    "CREATE INDEX ix_step_attempts_workspace_id ON step_attempts (workspace_id)",
    "\nCREATE TABLE evidence (\n\tid UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tstep_id UUID NOT NULL, \n\tattempt_id UUID NOT NULL, \n\tpayload JSONB NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (workspace_id, id), \n\tFOREIGN KEY(workspace_id, step_id) REFERENCES task_steps (workspace_id, id), \n\tFOREIGN KEY(workspace_id, attempt_id) REFERENCES step_attempts (workspace_id, id)\n)\n\n",
    "CREATE INDEX ix_evidence_workspace_id ON evidence (workspace_id)",
)

revision = "0001_runtime"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    for statement in INITIAL_DDL:
        op.execute(statement)
    op.execute("""
        CREATE FUNCTION reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'audit is append-only'; END $$
    """)
    op.execute(
        "CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()"
    )
    for table in ["task_runs", "task_steps", "step_attempts", "evidence", "audit_logs"]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY workspace_isolation ON {table} USING "
            "(workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid) "
            "WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)"
        )


def downgrade():
    raise RuntimeError("Destructive downgrade prohibited; restore a verified backup instead.")
