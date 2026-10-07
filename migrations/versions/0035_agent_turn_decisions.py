"""Persist immutable LangGraph Planner decisions for each conversation Turn."""

from alembic import op

revision = "0035_agent_turn_decisions"
down_revision = "0034_quarantine_legacy_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE conversation_turn_decisions (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            turn_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            decision_version INTEGER NOT NULL DEFAULT 1 CHECK (decision_version > 0),
            model_binding_payload JSONB NOT NULL,
            decision_payload JSONB NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            UNIQUE (workspace_id, turn_id, decision_version),
            FOREIGN KEY (workspace_id, turn_id)
                REFERENCES conversation_turns(workspace_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_conversation_turn_decisions_workspace_id "
        "ON conversation_turn_decisions(workspace_id)"
    )
    op.execute(
        "CREATE INDEX ix_conversation_turn_decisions_turn_id "
        "ON conversation_turn_decisions(turn_id)"
    )
    op.execute("ALTER TABLE conversation_turn_decisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE conversation_turn_decisions FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspace_isolation ON conversation_turn_decisions
        USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        WITH CHECK (
            workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER conversation_turn_decisions_immutable
        BEFORE UPDATE OR DELETE ON conversation_turn_decisions
        FOR EACH ROW EXECUTE FUNCTION reject_immutable_mutation()
        """
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting Agent decisions")
