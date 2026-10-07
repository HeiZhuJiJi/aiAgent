"""Add append-only model configuration revisions.

Model configurations and encrypted secrets remain protected by their existing
immutable triggers. Edits and lifecycle changes append a successor row instead
of updating or deleting authoritative history.
"""

from alembic import op

revision = "0031_model_config_revisions"
down_revision = "0030_data_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE model_configs
          DROP CONSTRAINT model_configs_workspace_id_kind_model_name_base_url_key,
          ADD COLUMN supersedes_config_id UUID,
          ADD CONSTRAINT uq_model_configs_successor
            UNIQUE (workspace_id, supersedes_config_id),
          ADD CONSTRAINT fk_model_configs_predecessor
            FOREIGN KEY (workspace_id, supersedes_config_id)
            REFERENCES model_configs(workspace_id, id)
        """
    )
    op.execute(
        "CREATE INDEX ix_model_configs_supersedes_config_id "
        "ON model_configs(supersedes_config_id)"
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of rewriting immutable registries")
