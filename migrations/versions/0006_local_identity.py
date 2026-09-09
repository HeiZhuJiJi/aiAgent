"""Local users, hashed sessions, and workspace membership."""

from alembic import op

revision = "0006_local_identity"
down_revision = "0005_durable_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE users (
            id UUID PRIMARY KEY,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            email VARCHAR(320) NOT NULL,
            normalized_email VARCHAR(320) NOT NULL UNIQUE,
            password_hash VARCHAR(512) NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT true,
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE workspace_members (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces (id),
            user_id UUID NOT NULL REFERENCES users (id),
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            role VARCHAR(40) NOT NULL
                CHECK (role IN ('WORKSPACE_OWNER','WORKSPACE_ADMIN','MEMBER','AUDITOR')),
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, user_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_workspace_members_workspace_id ON workspace_members (workspace_id)")
    op.execute("CREATE INDEX ix_workspace_members_user_id ON workspace_members (user_id)")
    op.execute(
        """
        CREATE TABLE auth_sessions (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users (id),
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            token_hash VARCHAR(64) NOT NULL UNIQUE,
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id)")
    op.execute("ALTER TABLE workspace_members ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspace_members FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspace_isolation ON workspace_members
        USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting identity history")
