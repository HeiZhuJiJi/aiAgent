"""Add workspace conversations, immutable summaries and project memory."""

from alembic import op

revision = "0017_project_memory"
down_revision = "0016_knowledge_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE conversations (
            id UUID PRIMARY KEY, workspace_id UUID NOT NULL REFERENCES workspaces(id),
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            title VARCHAR(500) NOT NULL, version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(workspace_id,id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE conversation_summaries (
            id UUID PRIMARY KEY, workspace_id UUID NOT NULL, conversation_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            version INTEGER NOT NULL CHECK (version > 0), content_hash VARCHAR(64) NOT NULL,
            payload JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL,
            UNIQUE(workspace_id,id), UNIQUE(workspace_id,conversation_id,version),
            FOREIGN KEY(workspace_id,conversation_id) REFERENCES conversations(workspace_id,id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE project_memory (
            id UUID PRIMARY KEY, workspace_id UUID NOT NULL REFERENCES workspaces(id),
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            memory_type VARCHAR(20) NOT NULL CHECK (memory_type IN ('FACT','DECISION','PREFERENCE')),
            supersedes UUID, content_hash VARCHAR(64) NOT NULL, payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL, UNIQUE(workspace_id,id),
            UNIQUE(workspace_id,supersedes),
            FOREIGN KEY(workspace_id,supersedes) REFERENCES project_memory(workspace_id,id)
        )
        """
    )
    op.execute("CREATE INDEX ix_conversations_workspace_id ON conversations(workspace_id)")
    op.execute("CREATE INDEX ix_conversation_summaries_conversation_id ON conversation_summaries(conversation_id)")
    op.execute("CREATE INDEX ix_project_memory_workspace_type ON project_memory(workspace_id,memory_type)")
    for table in ("conversations", "conversation_summaries", "project_memory"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY workspace_isolation ON {table} "
            "USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid) "
            "WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)"
        )
    for table in ("conversation_summaries", "project_memory"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_immutable_mutation()"
        )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting project memory history")
