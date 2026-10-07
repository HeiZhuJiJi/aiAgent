"""Add fenced conversation Turn, Attempt, and user-facing Outcome state."""

from alembic import op

revision = "0032_conversation_turn_lifecycle"
down_revision = "0031_model_config_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE conversation_turns (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            conversation_id UUID NOT NULL,
            user_message_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            state VARCHAR(30) NOT NULL DEFAULT 'PENDING'
                CHECK (
                    state IN (
                        'PENDING','RUNNING','COMPLETED','FAILED','CANCELLED','SUPERSEDED'
                    )
                ),
            task_id UUID,
            active_attempt_id UUID,
            decision_version INTEGER NOT NULL DEFAULT 1 CHECK (decision_version > 0),
            fencing_token INTEGER NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
            context_eligible BOOLEAN NOT NULL DEFAULT false,
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            UNIQUE (workspace_id, id),
            UNIQUE (workspace_id, user_message_id),
            UNIQUE (workspace_id, task_id),
            CHECK (NOT context_eligible OR state = 'COMPLETED'),
            CHECK (
                (state IN ('COMPLETED','FAILED','CANCELLED','SUPERSEDED'))
                = (completed_at IS NOT NULL)
            ),
            FOREIGN KEY (workspace_id, conversation_id)
                REFERENCES conversations(workspace_id, id),
            FOREIGN KEY (workspace_id, user_message_id)
                REFERENCES messages(workspace_id, id),
            FOREIGN KEY (workspace_id, task_id)
                REFERENCES task_runs(workspace_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE conversation_turn_attempts (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            turn_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            state VARCHAR(30) NOT NULL DEFAULT 'RUNNING'
                CHECK (
                    state IN ('RUNNING','SUCCEEDED','FAILED','CANCELLED','SUPERSEDED')
                ),
            fencing_token INTEGER NOT NULL CHECK (fencing_token > 0),
            trace_id VARCHAR(200) NOT NULL,
            failure_payload JSONB,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            UNIQUE (workspace_id, id),
            UNIQUE (workspace_id, turn_id, id),
            UNIQUE (workspace_id, turn_id, fencing_token),
            CHECK (
                (state = 'FAILED' AND failure_payload IS NOT NULL)
                OR (state <> 'FAILED' AND failure_payload IS NULL)
            ),
            FOREIGN KEY (workspace_id, turn_id)
                REFERENCES conversation_turns(workspace_id, id)
        )
        """
    )
    op.execute(
        """
        ALTER TABLE conversation_turns
          ADD CONSTRAINT fk_conversation_turns_active_attempt
          FOREIGN KEY (workspace_id, id, active_attempt_id)
          REFERENCES conversation_turn_attempts(workspace_id, turn_id, id)
        """
    )
    op.execute(
        """
        CREATE TABLE conversation_turn_outcomes (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            turn_id UUID NOT NULL,
            attempt_id UUID NOT NULL,
            assistant_message_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            status VARCHAR(40) NOT NULL
                CHECK (
                    status IN (
                        'ANSWERED','PARTIALLY_ANSWERED','CLARIFICATION_REQUIRED',
                        'UNAVAILABLE','FAILED'
                    )
                ),
            failure_payload JSONB,
            context_eligible BOOLEAN NOT NULL DEFAULT false,
            content_hash VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            UNIQUE (workspace_id, turn_id),
            UNIQUE (workspace_id, assistant_message_id),
            CHECK (
                (status IN ('UNAVAILABLE','FAILED') AND failure_payload IS NOT NULL)
                OR (
                    status NOT IN ('UNAVAILABLE','FAILED')
                    AND failure_payload IS NULL
                )
            ),
            CHECK (
                NOT context_eligible OR status NOT IN ('UNAVAILABLE','FAILED')
            ),
            FOREIGN KEY (workspace_id, turn_id)
                REFERENCES conversation_turns(workspace_id, id),
            FOREIGN KEY (workspace_id, turn_id, attempt_id)
                REFERENCES conversation_turn_attempts(workspace_id, turn_id, id),
            FOREIGN KEY (workspace_id, assistant_message_id)
                REFERENCES messages(workspace_id, id)
        )
        """
    )
    for statement in (
        (
            "CREATE INDEX ix_conversation_turns_workspace_id "
            "ON conversation_turns(workspace_id)"
        ),
        (
            "CREATE INDEX ix_conversation_turns_conversation_id "
            "ON conversation_turns(conversation_id, created_at, id)"
        ),
        "CREATE INDEX ix_conversation_turns_state ON conversation_turns(state)",
        "CREATE INDEX ix_conversation_turns_task_id ON conversation_turns(task_id)",
        (
            "CREATE INDEX ix_conversation_turns_active_attempt_id "
            "ON conversation_turns(active_attempt_id)"
        ),
        (
            "CREATE INDEX ix_conversation_turn_attempts_workspace_id "
            "ON conversation_turn_attempts(workspace_id)"
        ),
        (
            "CREATE INDEX ix_conversation_turn_attempts_turn_id "
            "ON conversation_turn_attempts(turn_id, started_at, id)"
        ),
        (
            "CREATE INDEX ix_conversation_turn_attempts_state "
            "ON conversation_turn_attempts(state)"
        ),
        (
            "CREATE INDEX ix_conversation_turn_outcomes_workspace_id "
            "ON conversation_turn_outcomes(workspace_id)"
        ),
        (
            "CREATE INDEX ix_conversation_turn_outcomes_turn_id "
            "ON conversation_turn_outcomes(turn_id)"
        ),
        (
            "CREATE INDEX ix_conversation_turn_outcomes_attempt_id "
            "ON conversation_turn_outcomes(attempt_id)"
        ),
        (
            "CREATE INDEX ix_conversation_turn_outcomes_assistant_message_id "
            "ON conversation_turn_outcomes(assistant_message_id)"
        ),
        (
            "CREATE INDEX ix_conversation_turn_outcomes_status "
            "ON conversation_turn_outcomes(status)"
        ),
    ):
        op.execute(statement)
    for table in (
        "conversation_turns",
        "conversation_turn_attempts",
        "conversation_turn_outcomes",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""CREATE POLICY workspace_isolation ON {table}
            USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
            WITH CHECK (
                workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid
            )"""
        )
    op.execute(
        """
        CREATE TRIGGER conversation_turn_outcomes_immutable
        BEFORE UPDATE OR DELETE ON conversation_turn_outcomes
        FOR EACH ROW EXECUTE FUNCTION reject_immutable_mutation()
        """
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting conversation Turn history")
