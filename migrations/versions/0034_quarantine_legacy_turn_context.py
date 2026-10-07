"""Quarantine every legacy-backfilled Turn from model context."""

from alembic import op

revision = "0034_quarantine_legacy_context"
down_revision = "0033_legacy_turn_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Outcome rows are immutable by design. Context Projection requires both the
    # Turn and Outcome flags, so revoking the mutable Turn flag is sufficient and
    # retains the original migration record for audit.
    op.execute(
        """
        UPDATE conversation_turns AS turn
           SET context_eligible = false,
               version = turn.version + 1,
               updated_at = now()
         WHERE turn.context_eligible = true
           AND EXISTS (
               SELECT 1
                 FROM conversation_turn_attempts AS attempt
                WHERE attempt.workspace_id = turn.workspace_id
                  AND attempt.turn_id = turn.id
                  AND attempt.trace_id = 'migration-0033-legacy-backfill'
           )
        """
    )


def downgrade() -> None:
    raise RuntimeError("Legacy context eligibility cannot be restored without validation")
