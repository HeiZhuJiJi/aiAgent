"""Add durable knowledge ingestion job state."""

from alembic import op

revision = "0021_knowledge_jobs"
down_revision = "0020_audit_fks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE knowledge_ingestion_jobs (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id),
            document_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            name VARCHAR(500) NOT NULL,
            media_type VARCHAR(200) NOT NULL,
            source_hash VARCHAR(64) NOT NULL,
            object_name VARCHAR(1000) NOT NULL,
            parser_version VARCHAR(100) NOT NULL,
            state VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                CHECK (state IN ('PENDING','RUNNING','SUCCEEDED','FAILED')),
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            fencing_token INTEGER NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
            document_version_id UUID,
            safe_error VARCHAR(500),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            UNIQUE(workspace_id,id),
            UNIQUE(workspace_id,document_id,source_hash),
            FOREIGN KEY(workspace_id,document_version_id)
                REFERENCES knowledge_document_versions(workspace_id,id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_knowledge_ingestion_jobs_workspace_id "
        "ON knowledge_ingestion_jobs(workspace_id)"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_ingestion_jobs_document_id "
        "ON knowledge_ingestion_jobs(document_id)"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_ingestion_jobs_dispatch "
        "ON knowledge_ingestion_jobs(workspace_id,state,created_at)"
    )
    op.execute("ALTER TABLE knowledge_ingestion_jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_ingestion_jobs FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspace_isolation ON knowledge_ingestion_jobs
        USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting ingestion history")
