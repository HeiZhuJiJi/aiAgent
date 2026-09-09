"""Persist immutable Claim to Evidence provenance."""

from alembic import op

revision = "0014_claim_provenance"
down_revision = "0013_task_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE claims (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            task_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            content_hash VARCHAR(64) NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            FOREIGN KEY (workspace_id, task_id)
                REFERENCES task_runs (workspace_id, id)
        )
        """
    )
    op.execute("CREATE INDEX ix_claims_workspace_id ON claims (workspace_id)")
    op.execute("CREATE INDEX ix_claims_task_id ON claims (task_id)")
    op.execute(
        """
        CREATE TABLE claim_evidence_links (
            workspace_id UUID NOT NULL,
            claim_id UUID NOT NULL,
            evidence_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, claim_id, evidence_id),
            FOREIGN KEY (workspace_id, claim_id)
                REFERENCES claims (workspace_id, id),
            FOREIGN KEY (workspace_id, evidence_id)
                REFERENCES evidence (workspace_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_claim_evidence_links_evidence_id "
        "ON claim_evidence_links (evidence_id)"
    )
    for table in ("claims", "claim_evidence_links"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY workspace_isolation ON {table}
            USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
            WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {table}_immutable
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_immutable_mutation()
            """
        )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting Claim provenance")
