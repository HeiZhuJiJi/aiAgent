"""Add immutable conversation messages."""

from alembic import op

revision = "0018_conversation_messages"
down_revision = "0017_project_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE messages (
            id UUID PRIMARY KEY, workspace_id UUID NOT NULL, conversation_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            role VARCHAR(20) NOT NULL CHECK (role IN ('SYSTEM','USER','ASSISTANT')),
            content TEXT NOT NULL, content_hash VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(workspace_id,id),
            FOREIGN KEY(workspace_id,conversation_id) REFERENCES conversations(workspace_id,id)
        )
        """
    )
    op.execute("CREATE INDEX ix_messages_conversation_id ON messages(conversation_id)")
    op.execute("ALTER TABLE messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE messages FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY workspace_isolation ON messages "
        "USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid) "
        "WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)"
    )
    op.execute(
        "CREATE TRIGGER messages_immutable BEFORE UPDATE OR DELETE ON messages "
        "FOR EACH ROW EXECUTE FUNCTION reject_immutable_mutation()"
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting conversation history")
