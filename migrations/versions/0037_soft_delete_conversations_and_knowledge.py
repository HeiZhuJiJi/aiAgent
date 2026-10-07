"""Add auditable soft deletion for conversations and knowledge documents."""

import sqlalchemy as sa
from alembic import op

revision = "0037_soft_delete"
down_revision = "0036_langgraph_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("knowledge_documents", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("knowledge_ingestion_jobs", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.create_index("ix_conversations_deleted_at", "conversations", ["deleted_at"])
    op.create_index("ix_knowledge_documents_deleted_at", "knowledge_documents", ["deleted_at"])
    op.create_index("ix_knowledge_ingestion_jobs_deleted_at", "knowledge_ingestion_jobs", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_ingestion_jobs_deleted_at", table_name="knowledge_ingestion_jobs")
    op.drop_index("ix_knowledge_documents_deleted_at", table_name="knowledge_documents")
    op.drop_index("ix_conversations_deleted_at", table_name="conversations")
    op.drop_column("knowledge_ingestion_jobs", "deleted_at")
    op.drop_column("knowledge_documents", "deleted_at")
    op.drop_column("conversations", "deleted_at")
