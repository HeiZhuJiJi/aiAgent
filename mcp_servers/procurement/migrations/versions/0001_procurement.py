"""Create independently versioned procurement service tables."""

from alembic import op

revision = "0001_procurement"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS suppliers (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            profile JSONB NOT NULL,
            purchase_history JSONB NOT NULL,
            risk_flags JSONB NOT NULL,
            contracts JSONB NOT NULL,
            UNIQUE(workspace_id,id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS admissions (
            id UUID PRIMARY KEY,
            operation_id UUID NOT NULL,
            workspace_id UUID NOT NULL,
            supplier_id UUID NOT NULL,
            parameter_hash VARCHAR NOT NULL,
            report_sha256 VARCHAR NOT NULL,
            requested_by UUID NOT NULL,
            status VARCHAR NOT NULL DEFAULT 'PENDING',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(workspace_id,operation_id),
            FOREIGN KEY(workspace_id,supplier_id)
                REFERENCES suppliers(workspace_id,id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_suppliers_workspace_id ON suppliers(workspace_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_admissions_workspace_id ON admissions(workspace_id)"
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified procurement backup instead of deleting history")
