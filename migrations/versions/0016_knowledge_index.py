"""Add immutable BGE-1024 knowledge versions, chunks, FTS and pgvector indexes."""

from alembic import op

revision = "0016_knowledge_index"
down_revision = "0015_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE knowledge_documents (
            id UUID PRIMARY KEY, workspace_id UUID NOT NULL REFERENCES workspaces(id),
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            name VARCHAR(500) NOT NULL, media_type VARCHAR(200) NOT NULL,
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(workspace_id,id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE knowledge_document_versions (
            id UUID PRIMARY KEY, workspace_id UUID NOT NULL, document_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            version INTEGER NOT NULL CHECK (version > 0), source_hash VARCHAR(64) NOT NULL,
            storage_uri VARCHAR(1000) NOT NULL, parser_version VARCHAR(100) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(workspace_id,id),
            UNIQUE(workspace_id,document_id,version), UNIQUE(workspace_id,document_id,source_hash),
            FOREIGN KEY(workspace_id,document_id) REFERENCES knowledge_documents(workspace_id,id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE knowledge_chunks (
            id UUID PRIMARY KEY, workspace_id UUID NOT NULL, document_version_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            ordinal INTEGER NOT NULL CHECK (ordinal >= 0), content TEXT NOT NULL,
            content_hash VARCHAR(64) NOT NULL, locator JSONB NOT NULL,
            token_count INTEGER NOT NULL CHECK (token_count > 0), embedding vector(1024) NOT NULL,
            search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
            UNIQUE(workspace_id,id), UNIQUE(workspace_id,document_version_id,ordinal),
            FOREIGN KEY(workspace_id,document_version_id)
                REFERENCES knowledge_document_versions(workspace_id,id)
        )
        """
    )
    op.execute("CREATE INDEX ix_knowledge_documents_workspace_id ON knowledge_documents(workspace_id)")
    op.execute("CREATE INDEX ix_knowledge_document_versions_document_id ON knowledge_document_versions(document_id)")
    op.execute("CREATE INDEX ix_knowledge_chunks_workspace_id ON knowledge_chunks(workspace_id)")
    op.execute("CREATE INDEX ix_knowledge_chunks_fts ON knowledge_chunks USING GIN(search_vector)")
    op.execute("CREATE INDEX ix_knowledge_chunks_vector ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)")
    for table in ("knowledge_documents", "knowledge_document_versions", "knowledge_chunks"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY workspace_isolation ON {table} USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid) WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)")
    for table in ("knowledge_document_versions", "knowledge_chunks"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_immutable_mutation()")


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting knowledge history")
