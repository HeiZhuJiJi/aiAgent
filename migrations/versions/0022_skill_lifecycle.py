"""Add workspace Skill quarantine, immutable versions and installation lifecycle."""

from alembic import op

revision = "0022_skill_lifecycle"
down_revision = "0021_knowledge_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE skills (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id),
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            name VARCHAR(64) NOT NULL,
            description VARCHAR(1000) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(workspace_id,id), UNIQUE(workspace_id,name)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE skill_versions (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            skill_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            version VARCHAR(100) NOT NULL,
            archive_sha256 VARCHAR(64) NOT NULL,
            storage_uri VARCHAR(1000) NOT NULL,
            package_payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(workspace_id,id),
            UNIQUE(workspace_id,skill_id,version),
            UNIQUE(workspace_id,skill_id,archive_sha256),
            FOREIGN KEY(workspace_id,skill_id) REFERENCES skills(workspace_id,id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE skill_installations (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id),
            skill_id UUID,
            skill_version_id UUID,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            declared_name VARCHAR(64) NOT NULL,
            declared_version VARCHAR(100) NOT NULL,
            archive_sha256 VARCHAR(64) NOT NULL,
            object_name VARCHAR(1000) NOT NULL,
            state VARCHAR(30) NOT NULL CHECK (
                state IN (
                    'QUARANTINED','REJECTED','PENDING_CONFIRMATION',
                    'INSTALLED','ENABLED','DISABLED'
                )
            ),
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            permission_diff JSONB NOT NULL,
            safe_error VARCHAR(500),
            confirmed_by UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            confirmed_at TIMESTAMPTZ,
            enabled_at TIMESTAMPTZ,
            UNIQUE(workspace_id,id),
            UNIQUE(workspace_id,skill_version_id),
            FOREIGN KEY(workspace_id,skill_id) REFERENCES skills(workspace_id,id),
            FOREIGN KEY(workspace_id,skill_version_id)
                REFERENCES skill_versions(workspace_id,id)
        )
        """
    )
    for table in ("skills", "skill_versions", "skill_installations"):
        op.execute(f"CREATE INDEX ix_{table}_workspace_id ON {table}(workspace_id)")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY workspace_isolation ON {table} "
            "USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid) "
            "WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)"
        )
    op.execute(
        "CREATE INDEX ix_skill_installations_skill_id "
        "ON skill_installations(skill_id)"
    )
    op.execute(
        "CREATE INDEX ix_skill_installations_skill_version_id "
        "ON skill_installations(skill_version_id)"
    )
    op.execute(
        "CREATE TRIGGER skill_versions_immutable BEFORE UPDATE OR DELETE ON skill_versions "
        "FOR EACH ROW EXECUTE FUNCTION reject_immutable_mutation()"
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting Skill history")
