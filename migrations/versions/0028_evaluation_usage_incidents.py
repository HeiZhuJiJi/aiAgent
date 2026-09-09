"""Persist evaluation evidence, usage, and consistency incidents."""

from alembic import op

revision = "0028_evaluation_usage_incidents"
down_revision = "0027_replan_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in """
        CREATE TABLE evaluation_cases (
            id UUID PRIMARY KEY, workspace_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            case_key VARCHAR(100) NOT NULL, category VARCHAR(40) NOT NULL,
            content_hash VARCHAR(64) NOT NULL, payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id), UNIQUE (workspace_id, case_key),
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
        );
        CREATE INDEX ix_evaluation_cases_workspace_id ON evaluation_cases(workspace_id);
        CREATE INDEX ix_evaluation_cases_category ON evaluation_cases(category);

        CREATE TABLE evaluation_runs (
            id UUID PRIMARY KEY, workspace_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            label VARCHAR(200) NOT NULL, dataset_hash VARCHAR(64) NOT NULL,
            state VARCHAR(20) NOT NULL CHECK (state IN ('COMPLETED','FAILED')),
            metrics JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
        );
        CREATE INDEX ix_evaluation_runs_workspace_id ON evaluation_runs(workspace_id);

        CREATE TABLE evaluation_results (
            id UUID PRIMARY KEY, workspace_id UUID NOT NULL, run_id UUID NOT NULL,
            case_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            status VARCHAR(20) NOT NULL CHECK (status IN ('PASSED','FAILED','NOT_RUN')),
            payload JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id), UNIQUE (workspace_id, run_id, case_id),
            FOREIGN KEY (workspace_id, run_id)
                REFERENCES evaluation_runs(workspace_id, id),
            FOREIGN KEY (workspace_id, case_id)
                REFERENCES evaluation_cases(workspace_id, id)
        );
        CREATE INDEX ix_evaluation_results_workspace_id ON evaluation_results(workspace_id);
        CREATE INDEX ix_evaluation_results_run_id ON evaluation_results(run_id);
        CREATE INDEX ix_evaluation_results_case_id ON evaluation_results(case_id);
        CREATE INDEX ix_evaluation_results_status ON evaluation_results(status);

        CREATE TABLE usage_records (
            id UUID PRIMARY KEY, workspace_id UUID NOT NULL, task_id UUID,
            evaluation_run_id UUID,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            kind VARCHAR(40) NOT NULL, units INTEGER NOT NULL DEFAULT 0,
            tokens INTEGER NOT NULL DEFAULT 0, cost DOUBLE PRECISION NOT NULL DEFAULT 0,
            detail JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (units >= 0 AND tokens >= 0 AND cost >= 0),
            CHECK (task_id IS NOT NULL OR evaluation_run_id IS NOT NULL),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id),
            FOREIGN KEY (workspace_id, task_id) REFERENCES task_runs(workspace_id, id),
            FOREIGN KEY (workspace_id, evaluation_run_id)
                REFERENCES evaluation_runs(workspace_id, id)
        );
        CREATE INDEX ix_usage_records_workspace_id ON usage_records(workspace_id);
        CREATE INDEX ix_usage_records_task_id ON usage_records(task_id);
        CREATE INDEX ix_usage_records_evaluation_run_id ON usage_records(evaluation_run_id);
        CREATE INDEX ix_usage_records_kind ON usage_records(kind);

        CREATE TABLE consistency_incidents (
            id UUID PRIMARY KEY, workspace_id UUID NOT NULL, task_id UUID,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            severity VARCHAR(20) NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
            incident_type VARCHAR(100) NOT NULL, detail JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id),
            FOREIGN KEY (workspace_id, task_id) REFERENCES task_runs(workspace_id, id)
        );
        CREATE INDEX ix_consistency_incidents_workspace_id ON consistency_incidents(workspace_id);
        CREATE INDEX ix_consistency_incidents_task_id ON consistency_incidents(task_id);
        CREATE INDEX ix_consistency_incidents_severity ON consistency_incidents(severity);
        CREATE INDEX ix_consistency_incidents_type ON consistency_incidents(incident_type);
        """.split(";"):  # noqa: SIM905 - asyncpg requires one statement per execute
        if statement.strip():
            op.execute(statement)
    for table in (
        "evaluation_cases",
        "evaluation_runs",
        "evaluation_results",
        "usage_records",
        "consistency_incidents",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""CREATE POLICY workspace_isolation ON {table}
            USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
            WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)"""
        )
        op.execute(
            f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_immutable_mutation()"""
        )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting evaluation history")
