"""Add MCP server lifecycle and immutable normalized capabilities."""

from alembic import op

revision = "0023_mcp_manager"
down_revision = "0022_skill_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE mcp_servers (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id),
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            name VARCHAR(200) NOT NULL,
            provider VARCHAR(100) NOT NULL,
            transport VARCHAR(20) NOT NULL DEFAULT 'STDIO' CHECK (transport = 'STDIO'),
            command_config JSONB NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'REGISTERED'
                CHECK (status IN ('REGISTERED','ACTIVE','UNHEALTHY','DISABLED')),
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            schema_hash VARCHAR(64),
            auth_secret_ref UUID,
            last_health_at TIMESTAMPTZ,
            safe_error VARCHAR(500),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(workspace_id,id), UNIQUE(workspace_id,name)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE mcp_capabilities (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            server_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            discovery_hash VARCHAR(64) NOT NULL,
            tool_name VARCHAR(200) NOT NULL,
            descriptor_payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(workspace_id,id),
            UNIQUE(workspace_id,server_id,discovery_hash,tool_name),
            FOREIGN KEY(workspace_id,server_id)
                REFERENCES mcp_servers(workspace_id,id)
        )
        """
    )
    for table in ("mcp_servers", "mcp_capabilities"):
        op.execute(f"CREATE INDEX ix_{table}_workspace_id ON {table}(workspace_id)")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY workspace_isolation ON {table} "
            "USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid) "
            "WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)"
        )
    op.execute("CREATE INDEX ix_mcp_capabilities_server_id ON mcp_capabilities(server_id)")
    op.execute(
        "CREATE INDEX ix_mcp_capabilities_discovery_hash ON mcp_capabilities(discovery_hash)"
    )
    op.execute(
        "CREATE TRIGGER mcp_capabilities_immutable BEFORE UPDATE OR DELETE ON mcp_capabilities "
        "FOR EACH ROW EXECUTE FUNCTION reject_immutable_mutation()"
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting MCP history")
