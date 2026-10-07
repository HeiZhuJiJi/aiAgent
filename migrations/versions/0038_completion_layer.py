"""Persist immutable completion contracts, assessments, and delivery decisions."""

from alembic import op

revision = "0038_completion_layer"
down_revision = "0037_soft_delete"
branch_labels = None
depends_on = None


def _workspace_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY workspace_isolation ON {table}
        USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        """
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE task_contract_versions (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            task_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            version INTEGER NOT NULL CHECK (version > 0),
            supersedes_id UUID,
            content_hash VARCHAR(64) NOT NULL,
            payload JSONB NOT NULL,
            created_by UUID NOT NULL REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            is_current BOOLEAN NOT NULL DEFAULT true,
            UNIQUE (workspace_id, id),
            UNIQUE (workspace_id, task_id, version),
            FOREIGN KEY (workspace_id, task_id)
                REFERENCES task_runs(workspace_id, id),
            FOREIGN KEY (workspace_id, supersedes_id)
                REFERENCES task_contract_versions(workspace_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX task_contract_versions_one_current
          ON task_contract_versions (workspace_id, task_id)
          WHERE is_current
        """
    )
    op.execute(
        """
        CREATE TABLE requirements (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            task_id UUID NOT NULL,
            contract_version_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            requirement_key VARCHAR(200) NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            UNIQUE (workspace_id, contract_version_id, requirement_key),
            FOREIGN KEY (workspace_id, task_id)
                REFERENCES task_runs(workspace_id, id),
            FOREIGN KEY (workspace_id, contract_version_id)
                REFERENCES task_contract_versions(workspace_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE requirement_supports (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            task_id UUID NOT NULL,
            requirement_id UUID NOT NULL,
            plan_revision_id UUID NOT NULL,
            step_id UUID NOT NULL,
            relation_type VARCHAR(40) NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            UNIQUE (
                workspace_id, requirement_id, plan_revision_id, step_id, relation_type
            ),
            FOREIGN KEY (workspace_id, task_id)
                REFERENCES task_runs(workspace_id, id),
            FOREIGN KEY (workspace_id, requirement_id)
                REFERENCES requirements(workspace_id, id),
            FOREIGN KEY (workspace_id, step_id)
                REFERENCES task_steps(workspace_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE verification_results (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            task_id UUID NOT NULL,
            attempt_id UUID,
            fencing_token INTEGER,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, task_id)
                REFERENCES task_runs(workspace_id, id),
            FOREIGN KEY (workspace_id, attempt_id)
                REFERENCES step_attempts(workspace_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE requirement_assessments (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            task_id UUID NOT NULL,
            requirement_id UUID NOT NULL,
            verification_ref UUID,
            supersedes_id UUID,
            attempt_id UUID,
            fencing_token INTEGER,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            payload JSONB NOT NULL,
            assessed_at TIMESTAMPTZ NOT NULL,
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, task_id)
                REFERENCES task_runs(workspace_id, id),
            FOREIGN KEY (workspace_id, requirement_id)
                REFERENCES requirements(workspace_id, id),
            FOREIGN KEY (workspace_id, verification_ref)
                REFERENCES verification_results(workspace_id, id),
            FOREIGN KEY (workspace_id, supersedes_id)
                REFERENCES requirement_assessments(workspace_id, id),
            FOREIGN KEY (workspace_id, attempt_id)
                REFERENCES step_attempts(workspace_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE gaps (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            task_id UUID NOT NULL,
            requirement_id UUID,
            assessment_ref UUID,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, task_id)
                REFERENCES task_runs(workspace_id, id),
            FOREIGN KEY (workspace_id, requirement_id)
                REFERENCES requirements(workspace_id, id),
            FOREIGN KEY (workspace_id, assessment_ref)
                REFERENCES requirement_assessments(workspace_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE delivery_decisions (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            task_id UUID NOT NULL,
            completion_snapshot_id UUID NOT NULL,
            verification_ref UUID,
            supersedes_id UUID,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            policy_version VARCHAR(100) NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, task_id)
                REFERENCES task_runs(workspace_id, id),
            FOREIGN KEY (workspace_id, completion_snapshot_id)
                REFERENCES execution_snapshots(workspace_id, id),
            FOREIGN KEY (workspace_id, verification_ref)
                REFERENCES verification_results(workspace_id, id),
            FOREIGN KEY (workspace_id, supersedes_id)
                REFERENCES delivery_decisions(workspace_id, id)
        )
        """
    )
    for table in (
        "task_contract_versions",
        "requirements",
        "requirement_supports",
        "verification_results",
        "requirement_assessments",
        "gaps",
        "delivery_decisions",
    ):
        op.execute(f"CREATE INDEX ix_{table}_workspace_id ON {table} (workspace_id)")
        _workspace_rls(table)


def downgrade() -> None:
    raise RuntimeError("Completion history is append-only; restore a verified backup instead")
