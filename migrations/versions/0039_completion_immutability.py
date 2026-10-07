"""Enforce Completion history immutability and same-task support scope."""

from alembic import op

revision = "0039_completion_immutability"
down_revision = "0038_completion_layer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE requirement_supports
        ADD CONSTRAINT fk_requirement_support_plan
        FOREIGN KEY (workspace_id, plan_revision_id)
        REFERENCES task_plans(workspace_id, id)
        """
    )
    for table in (
        "requirements",
        "requirement_supports",
        "verification_results",
        "requirement_assessments",
        "gaps",
        "delivery_decisions",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_immutable "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_immutable_mutation()"
        )
    op.execute(
        """
        CREATE FUNCTION reject_contract_version_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'contract history is append-only';
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
               OR NEW.task_id IS DISTINCT FROM OLD.task_id
               OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
               OR NEW.version IS DISTINCT FROM OLD.version
               OR NEW.supersedes_id IS DISTINCT FROM OLD.supersedes_id
               OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
               OR NEW.payload IS DISTINCT FROM OLD.payload
               OR NEW.created_by IS DISTINCT FROM OLD.created_by
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
               OR (NEW.is_current IS DISTINCT FROM OLD.is_current
                   AND NOT (OLD.is_current AND NOT NEW.is_current)) THEN
                RAISE EXCEPTION 'contract version is immutable';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER task_contract_versions_immutable
        BEFORE UPDATE OR DELETE ON task_contract_versions
        FOR EACH ROW EXECUTE FUNCTION reject_contract_version_mutation()
        """
    )


def downgrade() -> None:
    raise RuntimeError("Completion history is append-only; restore a verified backup instead")
