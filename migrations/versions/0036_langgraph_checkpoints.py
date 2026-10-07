"""Create the official LangGraph PostgreSQL checkpointer schema."""

from alembic import op

revision = "0036_langgraph_checkpoints"
down_revision = "0035_agent_turn_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS checkpoint_migrations (
            v INTEGER PRIMARY KEY
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS checkpoints (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            parent_checkpoint_id TEXT,
            type TEXT,
            checkpoint JSONB NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}',
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS checkpoint_blobs (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            channel TEXT NOT NULL,
            version TEXT NOT NULL,
            type TEXT NOT NULL,
            blob BYTEA,
            PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS checkpoint_writes (
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            task_path TEXT NOT NULL DEFAULT '',
            idx INTEGER NOT NULL,
            channel TEXT NOT NULL,
            type TEXT,
            blob BYTEA NOT NULL,
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx "
        "ON checkpoints(thread_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx "
        "ON checkpoint_blobs(thread_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx "
        "ON checkpoint_writes(thread_id)"
    )
    op.execute(
        """
        INSERT INTO checkpoint_migrations(v)
        SELECT generate_series(0, 9)
        ON CONFLICT (v) DO NOTHING
        """
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting LangGraph checkpoints")
