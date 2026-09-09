"""Apply every migration to a disposable database and verify the resulting schema."""

import asyncio
import os
import re
import sys
from uuid import uuid4

from dotenv import dotenv_values
from sqlalchemy import URL, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    values = dotenv_values(".env")
    password = values.get("POSTGRES_PASSWORD")
    if not password:
        raise ValueError("POSTGRES_PASSWORD is missing")
    database = "migration_verify_" + uuid4().hex
    if re.fullmatch(r"migration_verify_[0-9a-f]{32}", database) is None:
        raise ValueError("unsafe temporary database name")
    admin_url = URL.create(
        "postgresql+asyncpg",
        username="platform",
        password=password,
        host="127.0.0.1",
        port=55432,
        database="postgres",
    )
    target_url = admin_url.set(database=database)
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database}"'))
        environment = os.environ.copy()
        environment["DATABASE_URL"] = target_url.render_as_string(hide_password=False)
        migration = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            "head",
            env=environment,
        )
        if await migration.wait() != 0:
            raise RuntimeError("Alembic migration subprocess failed")
        target = create_async_engine(target_url)
        try:
            async with target.connect() as connection:
                tables = await connection.run_sync(
                    lambda sync: set(inspect(sync).get_table_names())
                )
                version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                step_columns = {
                    item["name"]
                    for item in await connection.run_sync(
                        lambda sync: inspect(sync).get_columns("task_steps")
                    )
                }
                attempt_columns = {
                    item["name"]
                    for item in await connection.run_sync(
                        lambda sync: inspect(sync).get_columns("step_attempts")
                    )
                }
                task_columns = {
                    item["name"]
                    for item in await connection.run_sync(
                        lambda sync: inspect(sync).get_columns("task_runs")
                    )
                }
            required = {
                "workspaces",
                "task_runs",
                "task_steps",
                "step_attempts",
                "evidence",
                "audit_logs",
                "operations",
                "domain_commands",
                "execution_snapshots",
                "task_plans",
                "approval_requests",
                "outbox_events",
                "capability_calls",
                "task_budgets",
                "runtime_guards",
                "users",
                "workspace_members",
                "auth_sessions",
                "task_checkpoints",
                "claims",
                "claim_evidence_links",
                "artifacts",
                "knowledge_documents",
                "knowledge_document_versions",
                "knowledge_ingestion_jobs",
                "knowledge_chunks",
                "conversations",
                "conversation_summaries",
                "project_memory",
                "messages",
                "skills",
                "skill_versions",
                "skill_installations",
                "mcp_servers",
                "mcp_capabilities",
                "replan_decisions",
                "evaluation_cases",
                "evaluation_runs",
                "evaluation_results",
                "usage_records",
                "consistency_incidents",
                "service_accounts",
                "capabilities",
                "knowledge_bases",
                "model_configs",
                "secrets",
                "retention_policies",
                "workspace_deletion_requests",
            }
            if (
                not required <= tables
                or version != "0030_data_lifecycle"
                or not {
                    "plan_id",
                    "step_key",
                    "spec_payload",
                    "retry_available_at",
                }
                <= step_columns
                or "failure_payload" not in attempt_columns
                or "created_by" not in task_columns
            ):
                raise AssertionError("fresh migration schema is incomplete")
        finally:
            await target.dispose()
        print("Fresh Alembic migration verification passed.")
    finally:
        async with admin.connect() as connection:
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database AND pid <> pg_backend_pid()"
                ),
                {"database": database},
            )
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
        await admin.dispose()


if __name__ == "__main__":
    asyncio.run(main())
