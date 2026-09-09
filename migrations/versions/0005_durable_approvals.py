"""Durable, operation-bound human approvals."""

from alembic import op

revision = "0005_durable_approvals"
down_revision = "0004_immutable_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE operations ADD CONSTRAINT uq_operations_workspace_id_id "
        "UNIQUE (workspace_id, id)"
    )
    op.execute(
        """
        CREATE TABLE approval_requests (
            id UUID PRIMARY KEY,
            workspace_id UUID NOT NULL,
            task_id UUID NOT NULL,
            step_id UUID NOT NULL,
            operation_id UUID NOT NULL,
            schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
            request_hash VARCHAR(64) NOT NULL,
            request_payload JSONB NOT NULL,
            state VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                CHECK (state IN ('PENDING','APPROVED','REJECTED','EXPIRED')),
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            actor_id UUID,
            decision VARCHAR(20),
            expires_at TIMESTAMPTZ NOT NULL,
            decided_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, id),
            UNIQUE (workspace_id, operation_id),
            FOREIGN KEY (workspace_id, task_id)
                REFERENCES task_runs (workspace_id, id),
            FOREIGN KEY (workspace_id, step_id)
                REFERENCES task_steps (workspace_id, id),
            FOREIGN KEY (workspace_id, operation_id)
                REFERENCES operations (workspace_id, id)
        )
        """
    )
    op.execute("CREATE INDEX ix_approval_requests_workspace_id ON approval_requests (workspace_id)")
    op.execute("CREATE INDEX ix_approval_requests_task_id ON approval_requests (task_id)")
    op.execute("CREATE INDEX ix_approval_requests_operation_id ON approval_requests (operation_id)")
    op.execute(
        """
        CREATE FUNCTION guard_approval_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'approval history is append-only';
            END IF;
            IF OLD.state <> 'PENDING' THEN
                RAISE EXCEPTION 'terminal approval is immutable';
            END IF;
            IF NEW.id <> OLD.id OR NEW.workspace_id <> OLD.workspace_id
               OR NEW.task_id <> OLD.task_id OR NEW.step_id <> OLD.step_id
               OR NEW.operation_id <> OLD.operation_id
               OR NEW.request_hash <> OLD.request_hash
               OR NEW.request_payload <> OLD.request_payload
               OR NEW.expires_at <> OLD.expires_at THEN
                RAISE EXCEPTION 'approval request is immutable';
            END IF;
            IF NEW.state NOT IN ('APPROVED','REJECTED','EXPIRED')
               OR NEW.version <> OLD.version + 1 OR NEW.decided_at IS NULL THEN
                RAISE EXCEPTION 'invalid approval transition';
            END IF;
            IF NEW.state IN ('APPROVED','REJECTED')
               AND (NEW.actor_id IS NULL OR NEW.decision <> NEW.state) THEN
                RAISE EXCEPTION 'approval decision identity is required';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER approval_guard BEFORE UPDATE OR DELETE ON approval_requests "
        "FOR EACH ROW EXECUTE FUNCTION guard_approval_mutation()"
    )
    op.execute("ALTER TABLE approval_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE approval_requests FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY workspace_isolation ON approval_requests
        USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
        """
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup instead of deleting approval history")
