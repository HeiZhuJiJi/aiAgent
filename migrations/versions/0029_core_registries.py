"""Add required core registries and encrypted workspace model configuration."""

from alembic import op

revision = "0029_core_registries"
down_revision = "0028_evaluation_usage_incidents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = ["\n    CREATE TABLE service_accounts (\n        id UUID PRIMARY KEY, workspace_id UUID NOT NULL,\n        schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),\n        name VARCHAR(200) NOT NULL, normalized_name VARCHAR(200) NOT NULL,\n        credential_hash VARCHAR(128) NOT NULL, enabled BOOLEAN NOT NULL DEFAULT true,\n        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),\n        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),\n        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),\n        UNIQUE (workspace_id, id), UNIQUE (workspace_id, normalized_name),\n        FOREIGN KEY (workspace_id) REFERENCES workspaces(id)\n    )", "\n    CREATE TABLE capabilities (\n        id UUID PRIMARY KEY, workspace_id UUID NOT NULL,\n        schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),\n        capability_key VARCHAR(300) NOT NULL, capability_version VARCHAR(80) NOT NULL,\n        source VARCHAR(40) NOT NULL, descriptor_hash VARCHAR(64) NOT NULL,\n        descriptor JSONB NOT NULL, enabled BOOLEAN NOT NULL DEFAULT true,\n        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),\n        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),\n        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),\n        UNIQUE (workspace_id, id),\n        UNIQUE (workspace_id, capability_key, capability_version),\n        FOREIGN KEY (workspace_id) REFERENCES workspaces(id)\n    )", "\n    CREATE TABLE knowledge_bases (\n        id UUID PRIMARY KEY, workspace_id UUID NOT NULL,\n        schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),\n        name VARCHAR(200) NOT NULL, normalized_name VARCHAR(200) NOT NULL,\n        enabled BOOLEAN NOT NULL DEFAULT true,\n        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),\n        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),\n        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),\n        UNIQUE (workspace_id, id), UNIQUE (workspace_id, normalized_name),\n        FOREIGN KEY (workspace_id) REFERENCES workspaces(id)\n    )", "\n    CREATE TABLE secrets (\n        id UUID PRIMARY KEY, workspace_id UUID NOT NULL,\n        schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),\n        purpose VARCHAR(100) NOT NULL, ciphertext BYTEA NOT NULL,\n        key_version INTEGER NOT NULL DEFAULT 1 CHECK (key_version > 0),\n        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),\n        UNIQUE (workspace_id, id),\n        FOREIGN KEY (workspace_id) REFERENCES workspaces(id)\n    )", "\n    CREATE TABLE model_configs (\n        id UUID PRIMARY KEY, workspace_id UUID NOT NULL,\n        schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),\n        kind VARCHAR(20) NOT NULL CHECK (kind IN ('CHAT','EMBEDDING','RERANKER')),\n        provider VARCHAR(40) NOT NULL\n            CHECK (provider IN ('Qwen','DeepSeek','OpenAICompatible')),\n        base_url VARCHAR(1000) NOT NULL, model_name VARCHAR(300) NOT NULL,\n        external BOOLEAN NOT NULL, secret_id UUID,\n        enabled BOOLEAN NOT NULL DEFAULT true,\n        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),\n        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),\n        UNIQUE (workspace_id, id),\n        UNIQUE (workspace_id, kind, model_name, base_url),\n        FOREIGN KEY (workspace_id) REFERENCES workspaces(id),\n        FOREIGN KEY (workspace_id, secret_id) REFERENCES secrets(workspace_id, id)\n    )", "\n    "]
    for statement in statements:
        if statement.strip():
            op.execute(statement)
    indexes = (
        ("service_accounts", "normalized_name"),
        ("capabilities", "capability_key"),
        ("knowledge_bases", "normalized_name"),
        ("secrets", "purpose"),
        ("model_configs", "kind"),
        ("model_configs", "secret_id"),
    )
    for table, column in indexes:
        op.execute(f"CREATE INDEX ix_{table}_{column} ON {table}({column})")
    for table in (
        "service_accounts",
        "capabilities",
        "knowledge_bases",
        "secrets",
        "model_configs",
    ):
        op.execute(f"CREATE INDEX ix_{table}_workspace_id ON {table}(workspace_id)")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""CREATE POLICY workspace_isolation ON {table}
            USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
            WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)"""
        )
    for table in ("secrets", "model_configs"):
        op.execute(
            f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_immutable_mutation()"""
        )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting core registries")
